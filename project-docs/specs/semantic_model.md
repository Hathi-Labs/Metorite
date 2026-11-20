# The semantic model: one way for the chat to read any large data source

**Status: ACTIVE for the parts the owner accepted (2026-11-18, owner in chat,
D226). The rest stays PROPOSED. SEM-7 (task 3737) and the library half of SEM-8 (task 3738) are built, dark.
SEM-12 (task 3742) and its repair SEM-12b (task 3834) are BUILT, dark: migration 464, `connections/semantic_model.py`, and
`shape_only` in `crm_sync/catalogue.py`, behind `CONNECTION_MODEL_ORGS`. SEM-1b is built in part, dark (task 3736).**

**SEM-1b status (2026-11-19, task 3736).** The route side is BUILT dark behind
`SEMANTIC_MODEL_ORGS`. `GET /crm/aggregate` on a module takes `query`, and the
old `field.<key>` pairs reach the same filter builder (`_filter`), so the two spellings give equal totals. A range leaf on a text
field reads the strict number of CRM-Q34-11. A query range picks the day field
of the findings. A refused leaf value names the leaf.

A native datetime `eq` uses the rule `native_zone` of `field_sql.DATETIME_EQ`.
The owner chose that rule on 2026-11-19. The tool argument waits for the owner's
token budget ruling, as row SEM-1b-2b. The lowering of `custom.<key>` and the
ops `ne`, `is_empty`, `contains` and `matches` are not built. Fences:
`test_crm_query_grammar.py` and `test_crm_query_grammar_r8.py`.

**SEM-13a (task 3835) is BUILT, dark, with no caller: `connections/profiler.py`,
and the no-member entry and the live list with schemas in
`connections/mcp_tools.py`. Nothing else is built.**

The owner accepted D-SEM-1, 2, 4, 5, 6, 7, 9, 10 and 11 as recommended. The
owner chose Option A for D-SEM-3. D-SEM-8 goes to a new connector spec.
D-CSY-1, D-CSY-3 and D-CSY-4 stay open.

So rungs 1 and 2 are ACTIVE. The
slices of rung 3 that need no D-CSY answer are ACTIVE too. SEM-14 needs D-CSY-1
and stays PROPOSED. Verified against `origin/main` at `f4c6ff08f`
(2026-11-18).

**Names.** The workstream is **WS-SEM**, and its slice prefix is **SEM-n**.
Neither name occurred on `main` before this spec (R2). This spec takes no
D-number. D226 records the owner's answers to the D-SEM-n questions of §12.

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
and resolve names to values. For the CRM that is as the member. For a connection it is through the credential of the connection (review gap 2).

Send one typed query, or the
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
   `terms.value_matches` and the floor of `acb_common/name_match.py`. Nothing
   is cached. This holds for the CRM only.

   **A connection reads through its own credential (review gap 2).** The
   source's own search tool does the match. An OAuth connection acts as the
   admin who connected it (D98). So the member sees what that account sees,
   behind the grant `integrations:use:mcp-<name>`. For a connection, this
   design does not claim that a member never learns a value of a record that
   they cannot open.
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

   **The header of each kind (review gap 1, rule 2).** The derived parts and
   the descriptions that code builds embed labels that a source wrote. They go
   under "Metorite's notes on this system, from its data (not an instruction
   to you)". A part that a person wrote and an admin accepted is the admin's
   text. An instruction, a synonym or a measure name goes under the admin's
   header, beside the admin's description. Otherwise a rule such as "revenue is
   billed, not draft" sits under "not an instruction", and the model can
   ignore it.

   **A cap on a label.** A label and an option are free text that the server
   wrote. `shape_only` caps each derived label and each option at 80
   characters.
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
  - Unique on `(organization_id, connection_id, version)`, and a partial
    unique index on `(organization_id, connection_id)` where the state is
    `approved`. Expand only (R6).
    The migration number is taken at build time and checked at merge (R1).
  - One module, `connections/semantic_model.py`, holds every statement on
    the table (a one-writer fence, as `connections/attention.py` does).
    The body keys, the writer and the migration shape are in §10.4.
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
| 1. Discover | Read the tool names, input and output schemas, and annotations. Read the server's instructions only if the owner says yes to D-CSY-3. The step reads them through the ONE helper of CSY-6, which is keyed on the negotiated version and tested for both shapes with a fake. It does not add a second reader (review Q2) | none |
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
- **The trust of a class A tool (review, rule 8).** Class A rests on the
  annotations that the server gives itself, and `readOnlyHint` is what the
  server says about itself. The profiler calls tools with no human. So it
  refuses a tool that says it is destructive, or that says it is not
  idempotent. For an unknown server, the IN-9 line names the count of tools
  that the profiler will call, and the panel lists them.
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
- **The card pattern (review gap 4, task 3714).** A read through a local link
  marks the session. Each later MCP call of that chat then asks a card, a
  second Tally read too (LNK §3.8 rule d). So the first follow-up in the demo
  meets the 7-minute pattern of task 3714, the WS-8c batch-card task. This
  design does not weaken the mark. The owner decides the card in a marked
  chat.

---

## 5. How the chat uses a model in each turn

| Step | What happens | Where it lives (the seam it extends) | Cost |
|---|---|---|---|
| 1. A short brief | A fixed, capped text per stored version: the approved measures by name, the key parts, the rules. Same bytes for the same version (rule 3) | CRM: the CRM facts block (`crm_platform.md` §18.14.4 point 2). A connection: one input of `server_brief`, with its own header and cap (rule 1) | CRM: at most 3 more lines in a block of 13 lines today. A connection: at most 300 characters for the model summary. The cap of one server rises to 1,300 characters, in fixed parts (review Q1): the admin 400, the model summary 300, the server 600, in that order. A fixed cap per part keeps `server_brief` pure, and no writer crowds out another. Only an APPROVED version gives the summary (D-SEM-11) |
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
The chat then learns one query form for every app. The owner chose Option A
(D-SEM-3, 2026-11-18). So SEM-1b builds it, and the CRM UI research draft S1
does not build a second grammar.

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
- **Visibility, the CRM.** A value is resolved as the member, at query time,
  so a member never learns a value of a record that they cannot open.
- **Visibility, a connection (review gap 2).** The connection reads through
  its own credential, and an OAuth connection acts as the admin who connected
  it (D98). The member sees what that account sees, behind
  `integrations:use:mcp-<name>`.
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
| 3 | An admin connects a test company through the local link to Tally, or a test MCP server. Within 5 minutes the panel shows what Metorite learned: the groups, the ledger count of each group, the voucher types, and the first and last voucher date. The admin accepts. A member asks for last quarter's sales. The chat calls the system's read tool, names the system, and says how it read the data. **Review gap 4:** after a read through the local link, the first follow-up meets the card of task 3714 (§4.3). The demo shows it, and the owner decides the card | SEM-12 to SEM-17 |
| 4 | One line: books of account. Zoho Books after IN-3 (task 3176), and totals that respect the tax ledgers. Needs D-SEM-8. `api_connections.md` D-API-5 (accepted, D228) drops the wait for IN-3 on the gateway path. Zoho Books is first (owner, 2026-11-19) | later |
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
| **SEM-1b** | 2 | The one grammar of §5.1, by the option that the owner picks in D-SEM-3. The CRM aggregate takes `query`, and the old parameters lower into it. `crm_aggregate` takes `query` beside its old arguments. **BUILT dark, route side (task 3736):** the query on a module total, the text numbers, the day field and the leaf words. The tool argument is row SEM-1b-2b | Option A: `acb_common/studio_query.py`, `field_sql.py`, `aggregate.py`, `records.py`, a shared fixture | HIGH (SQL) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_query_grammar.py` and `_r8` (new), `test_studio_query_r8.py`, `test_tool_schema_diet.py` | D-SEM-3, SEM-6 |
| **SEM-1b-2b** | 2 | `crm_aggregate` takes the `query` argument and sends it as JSON text (`_query_text`). The chat words of the answer read it already. About 64 schema tokens | `crm_tools.py`, `test_tool_schema_diet.py` | HIGH (SQL) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` | `test_crm_agent.py` (the tests that send `query` through the tool), `test_tool_schema_diet.py` | SEM-1b, and the owner's ruling on the token budget |
| **SEM-7** | 2 | The CRM model reader. The plan learns `measure`, `join` and `query`. `org_facts` serves them | `crm_sync/catalogue.py`, `routes/crm/mapping_plans.py`, `routes/crm/facts.py` | HIGH (plan) · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` (built, dark, task 3737) | `test_crm_plan_kinds_semantic.py` (new), `test_org_facts_r8.py` | SEM-R0 |
| **SEM-8** | 2 | Verified queries: save with parameters, match, run, the "verified" mark, stale on a shape change, one repair | `routes/crm/` (a new module), `crm_tools.py` | HIGH · AGENT-SAFE dark | `SEMANTIC_MODEL_ORGS` (built, dark, task 3738: the library half and the member routes match and lower. The admin routes for save and repair, and the chat offer, are SEM-8b) | `test_crm_verified_queries.py` and `_r8` (new) | SEM-7 |
| **SEM-9** | 2 | Proposals from chat: synonym, measure and verified query join the CRM-Q32 store and one "Needs you" kind. The recommended scope `two_kinds` holds the synonym and the measure only. The scope, the store, the caps, the accept, the Undo, D-SEM-7 and the done-when are in §10.6 | `routes/crm/questions.py`, `crm_sync/questions.py`, `routes/shell/waiting.py` (the full list is in §10.6) | HIGH (fact writes) · AGENT-SAFE dark | `CRM_QUESTIONS_ORGS`, `SEMANTIC_MODEL_ORGS` and `WAITING_ORGS` | `test_crm_questions_chat_r8.py`, `test_waiting_seam_r8.py`. The mutation of each is in §14 | SEM-7. SEM-8b only for the verified-query kind (§10.6) |
| **SEM-10** | 2 | Measures and Verified questions in "Your answers", with Undo | CRM settings UI | STANDARD UI · captures before merge | `SEMANTIC_MODEL_ORGS` | Vitest of the section (new) | SEM-8, SEM-9 |
| **SEM-11** | 2 | The weekly counts of refusal codes go to the failure store | a job on the queue | STANDARD · AGENT-SAFE | none | `test_semantic_refusal_feed.py` (new) | SEM-4, TOF-2a (task 3416) |
| **SEM-12** | 3 | The connection model table, its one writer and `shape_only`, and a partial unique index, so one connection has at most one approved version (review) | a migration (number at build time), `connections/semantic_model.py`, and the one `shape_only` function in `crm_sync/catalogue.py`. The body, the writer, the flag, the migration shape and the file list are in §10.4 | HIGH (migration, tenancy) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_semantic_model_r8.py` (new: org B cannot read org A, org B cannot name the connection of org A, cascade on delete, one approved version), `test_semantic_shape_only.py` (new), `test_flag_reload.py`, `test_tenant_coverage.py`, `test_tenancy_boundary.py`. The mutation of each is in §14 | D-SEM-2, SEM-7 (PR 2599, task 3737) |
| **SEM-13** | 3 | The profiler of §4.2: discover, shape reads with no member, decide, draft. **Split (audit, 2026-11-19, task 3743)** into SEM-13a, SEM-13b and SEM-13c, the three rows below. This row is the umbrella, and it holds no code of its own | `connections/`, `decide_features.py` (see the three rows) | HIGH (third-party data) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | The fences of the three rows. No class B or C tool is called, a tool that says destructive or not idempotent is refused, the caps hold, no value is stored. The sealed eval of review gap 5 is in SEM-13c | SEM-12 |
| **SEM-13a** | 3 | The profiler, first slice (task 3835): steps 1, 2 and 4 of §4.2, with no decide call and an empty `ai` section. No caller. The scope, the decision on approval and the code anchors are in §10.5 | `gateway/connections/profiler.py` (new), `gateway/connections/mcp_tools.py` (a no-member call entry and a live list read that also gives the input schemas), `tests/unit/test_connection_profiler.py` (new), `tests/unit/test_connection_semantic_model_r8.py` (one case) | HIGH (third-party data) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_profiler.py` (new, a fake lister and a fake caller, no network), `test_connection_semantic_model_r8.py` (one profiler draft case). These stay green: `test_studio_mcp_tools.py`, `test_studio_live_preview.py`, `test_studio_preview_sends_nothing.py`, `test_studio_mcp_approvals.py`, and the R8 files `test_studio_live_preview_r8.py` and `test_studio_mcp_approvals_r8.py`. The mutation of each is in §14 | SEM-12 (built) |
| **SEM-13b** | 3 | The profiler, step 3 of §4.2 (task 3836): the decide step. A `connection.` prefix and the feature `connection.map_model`, with its own day counter. The payer is the `connected_by` member. The `ai` section gets the chosen closed words | `gateway/decide_features.py` (a new `Prefix`), `gateway/connections/profiler.py` | HIGH (model calls, credits) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_profiler.py` (extended, with the decide call faked), `test_decide_seam.py` | SEM-13a |
| **SEM-13c** | 3 | The profiler, last slice (task 3837): step 0 of §4.2 (the trigger), a map from a normalised key back to the source field, and **review gap 5**: a scripted profiler eval with synthetic servers, split into a tune set and a sealed held-out set, with a baseline. Two notes of the SEM-13a review: the caller must retry with a backoff after `stopped:limited`. Two sources can give one entity name, and their records merge, for example a tool `invoices` and a key `invoices`, or `list-invoices` and `list_invoices`. The result is a less exact draft with no value. SEM-13c or SEM-15 owns the repeated or merged draft | `evals/semantic/profiler/` (new), the trigger, the key map | HIGH (third-party data) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | The scripted eval (new), with its sealed held-out set. It reuses `splits.py` and `compare.py` of task 3649 after they merge. On 2026-11-19, `git ls-tree -r origin/main --name-only` holds neither file, so no text cites them as on `main` | SEM-13b, task 3649 on `main` |
| **SEM-14** | 3 | The model's summary is one input of `server_brief`, with its own header and cap, stable for a version | `acb_skills/server_brief.py` | HIGH (untrusted text) · AGENT-SAFE dark | `CONNECTED_SYSTEMS_ORGS` | `test_server_brief.py` (extended). **Review Q1:** this PR moves the pin test of CSY §3.1 and the figure in CSY §5 to 1,300 characters (the WS-CSY planner agrees to that edit) | CSY-1 (task 3400), D-CSY-1 |
| **SEM-15** | 3 | An `org_facts` provider for connections. The `org_facts` tool loads with an approved model. **Review gap 3:** the provider runs the same grant check as the attach (`is_authorized`), and returns nothing in a run that holds an untrusted pack (CSY §3.6: a member never learns of a server they cannot use) | `gateway/org_facts.py`, a provider module, `_tool_injection.py` | HIGH (grant) · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_org_facts.py`, `test_org_facts_r8.py` (extended). Two fence cases: a member without the grant reads nothing, and a run with the email pack reads nothing | SEM-12 |
| **SEM-16** | 3 | The panel: the draft as a diff, accept per part, "Read it again" | `ConnectionSheet.tsx` | STANDARD UI · captures before merge | `CONNECTION_MODEL_ORGS` | Vitest of `ConnectionSheet.tsx` (extended) | SEM-13, CSY-9 (task 3409) |
| **SEM-17** | 3 | The Tally reads of §4.3 through the local link, with the untrusted mark | the LNK adapter | HIGH · AGENT-SAFE dark | `CONNECTION_MODEL_ORGS` | `test_connection_profiler_tally.py` (new, synthetic). A draft of a local link saves with `untrusted_origin=True`. SEM-13a refuses a local link, so SEM-17 sets this mark (§10.5) | SEM-13, the LNK build slices (PR 2550) |

**Flags.** `SEMANTIC_MODEL_ORGS` (the CRM rungs) and `CONNECTION_MODEL_ORGS`
(the connection rung). Both are `*_ORGS` flags, default empty, and both reload
with no restart (`docs/flag_reload.md`). A flip on a live org needs the
`enforcement-flip` grant and the owner's preview.

**The order.** SEM-R0 first, because it costs no code. Then SEM-0. SEM-1a,
SEM-3 and SEM-4 can start at once and run in parallel. SEM-2 starts after
SEM-R0. SEM-5 and SEM-6 close rung 1.

**Later rungs.** SEM-1b opens rung 2. D-SEM-3 is answered (Option A). Rung 3
needs CSY-1 for SEM-14. SEM-14 also needs D-CSY-1, which is still open.

### 10.3 The map to the CRM-Q34 slices (epic task 3689)

The CRM fields and filters epic already holds slices that overlap rung 1. This
spec adds no second slice for the same work. The map:

| Here | CRM slice | Task | Rule |
|---|---|---|---|
| SEM-1a | CRM-Q34-1: a number range on a module field, and a lookup by the Metorite id of a core record | 3693 | Same work. box-num builds it. It is in build |
| SEM-4 | CRM-Q34-2 (the refusals name a working field, module or filter) and CRM-Q34-3 (the tool text) | 3694, 3695 | SEM-4 includes both. Build them once |
| SEM-3 | CRM-Q34-7: `q` finds across a module | 3700 | The two overlap. The one that starts first owns the code, and the other cites it |
| SEM-1b | CRM-Q34-4 and CRM-Q34-4b: one filter builder | 3697, 3711 | Q34-4 is the builder that SEM-1b lowers into |
| none | CRM-Q34-5: the filter words a member needs | 3698 | Unblocked by D-SEM-3 (Option A, 2026-11-18). The words are part of the one grammar |

The tasks of the slices are made under the WS-SEM epic. D-SEM-1 and D-SEM-2 are answered, so SEM-12, SEM-13, SEM-15, SEM-16 and
SEM-17 may get tasks. SEM-14 waits for D-CSY-1.

### 10.4 SEM-12 in detail: the store of a connection's model

An audit of 2026-11-19 found gaps in the SEM-12 row. This section closes them.
It adds no D-number. The row of §10.2 and §14 point here.

**Fit to `building_blocks.md` (D205).** The table is the store of the block
"Data meaning" (§3.1). It adds no registry, manifest, queue or engine. The one
read seam stays `org_facts` (SEM-15). One module runs every statement on the
table. A new source adds a provider, and no table.

**The body.** The body is one jsonb object with four keys: `v` (the integer 1),
`code`, `ai` and `admin`. Each of the last three is a section. The name of the
section is the writer kind of every part in it. So the body carries the header
split of review note 1. The `made_by` column does not decide a header.

A part sits in the one section that may write it. `shape_only` refuses a part
in the wrong section, and refuses a key that is not in the table.

| Section | Part (§3.2) | Body key | Keys of one item | Header (§3.3 point 6) |
|---|---|---|---|---|
| `code` | Entity | `entities` | `key`, `label`, `records`, `first_date`, `last_date` | "Metorite's notes" |
| `code` | Field, dimension, options, value lookup, tax mark (evidence) | `fields` | `entity`, `key`, `label`, `type`, `fill`, `grain`, `options`, `lookup`, `tax` | "Metorite's notes" |
| `code` | Join | `joins` | the keys of `_JOIN_KEYS` in `crm_sync/catalogue.py` | "Metorite's notes" |
| `ai` | Purpose of an entity, meaning and unit of a field, tax mark | `entities`, `fields` | `entity`, `key`, one closed word for each choice, and `text` that code built from those words | "Metorite's notes" |
| `admin` | Measure | `measures` | the keys of `_MEASURE_KEYS` | the admin's header |
| `admin` | Join, tax mark (a change) | `joins`, `tax_marks` | the keys of `_JOIN_KEYS`. For a tax mark: `entity`, `field`, `tax` | the admin's header |
| `admin` | Synonym | `synonyms` | `word`, `target` (a key of the model) | the admin's header |
| `admin` | Sample question | `samples` | `question` | the admin's header |
| `admin` | Verified query | `queries` | the keys of `_QUERY_KEYS` | the admin's header |
| `admin` | Instruction (org scope only) | `instructions` | `text` | the admin's header |

The source notes of §3.2 are not in the body. They stay in
`integration_connections` and in the brief inputs. At read time, an item of the
`admin` section wins over an item with the same key in `code` or `ai`. An item of
`ai` wins over an item with the same key in `code`. The profiler reads the live
system, and `code` only holds the closed starting set.

**`made_by`.** The `made_by` column is the highest writer kind in the body, in
the order `code`, `ai`, `admin`. The writer sets it from the body, and never
from the caller. A `code` row has an empty `ai` section and an empty `admin`
section.

**`shape_only`: one module.** The closed key sets, `LABEL_MAX` (80),
`number_like` and `_has_literal` live in `crm_sync/catalogue.py` on the branch
of SEM-7 (PR 2599, task 3737). Main has them after that PR merges. The 300-character cap is `BODY_MAX` of
`routes/crm/rules.py`, the cap of `crm_business_rules` (migration 283).

SEM-12 reuses that module. It adds one public function there, `shape_only(body)`,
which calls the item checks of SEM-7. A second copy would drift, and a move
would edit the open PR of SEM-7, so SEM-12 adds neither a copy nor a package.

The function is pure and reads no database. It returns the first problem as a
short code, or `None`.

| Case | `shape_only` |
|---|---|
| A key, a label of at most 80 characters, an option of at most 80, a count, a fill share, a date or a closed word | Allows, in the `code` and `ai` sections |
| A description in the `ai` section | Allows only text that code built from closed words, of at most 300 characters |
| Text in the `admin` section | Allows 1 to 300 characters that pass `clean_body`, with no digit that `_has_literal` finds |
| A parameter name | Allows a name that matches the name pattern. Refuses a name that does not match the pattern, such as `Acme Ltd`, `2024` or `5.5`. The pattern is `_PARAM_NAME` of the catalogue. The `number_like` call is a second guard for a number-like name |
| A verified query | Allows named parameters. Refuses a key such as `value`, and any literal in a condition |
| An amount, a total, a balance or a figure of money | Refuses a key that names a figure, in every section. Refuses a label or an option that reads as a number, or that holds a money mark beside a digit on either side. A mark is a symbol (`₹ $ € £ ¥`) or a code (`rs inr usd eur gbp`), such as `Rs. 400`, `450000 INR due` or `5USD`. After a digit, `rs` needs a space first, so `3rs` passes. A figure in plain words, such as `four thousand`, passes in a label by design |
| Free text that the source or the model wrote, outside the cases above | Refuses |
| A key outside the closed set, or a part in the wrong section | Refuses |
| A key that names a secret: header, token, url, path or host (§8) | Refuses |
| The value of a key: an entity, a field, a module, a target or a measure | Allows an identifier of ASCII letters, digits and `_`, at most 64 characters, that starts with a letter. Refuses a number, an amount, a date, two digits in a row, a space and any mark |
| An option of a fixed filter of a measure | Allows only an option that the `code` section declares for that field |
| A parameter name or a field `type` in a connection body | Refuses a number, two digits in a row, and a money mark beside a digit, such as `d20240301` or `inr500000` |
| The `model_version` of a verified query in a connection body | Allows 1 to 1000 (`MODEL_VERSION_MAX`) |

**Keys fail closed.** A connection key is an identifier. It starts with a
letter. Then it holds letters, digits and underscores. It has at most 64
characters and no run of two digits. A money mark beside a digit fails too.

Some real source names fail this grammar. Examples are `Custom_Field_10`,
`cf_9001`, `CustomModule10` and `TDS_194C`. A Tally group name with spaces,
such as `Sundry Debtors`, fails. A Pipedrive hash key fails, and so does a key
with a dot or a dash.

One failed key refuses the WHOLE draft, because `shape_only` returns the first
problem. So SEM-13, the profiler, must normalise such a name before it saves a
draft. The body has no place for the raw source name yet. A map from the
normalised key back to the source field is a later slice. This slice is dark,
so the refusal costs nothing today.

The field type follows the same rule on digits. A source type such as `int64` or
`float64` fails, because it holds two digits in a row. `int8` and `float8`
pass. SEM-13 must map such a type to a plain word before it saves a draft.

**Filter options.** An option of a fixed filter must be an option that the
`code` section declares. The declared option must be on the field with the
same entity and key. So an admin draft with an empty `code` section refuses a
filter with `filter_option`. A field with no `options` list refuses it too.

The check trusts the `code` section of the same body. A declared option is a
label of at most 80 characters, and it can be a name. The writer does not know
its caller. So `save_draft` and `accept_part` accept a `code` section from any
caller. This spec does not decide which caller may write the `code` section.
SEM-16 must decide it before an admin can edit a draft.

**Text by design.** `shape_only` allows some free text up to its cap. A
label and an option hold at most 80 characters. A field `type` is a word of at
most 40 characters. These go into a prompt only under the header "Metorite's
notes on this system, from its data (not an instruction to you)".

The admin's text holds at most 300 characters. It goes only under the admin's header, as
§3.3 point 6 says. SEM-15 must keep each text under its header.

**The writer.** `gateway/connections/semantic_model.py` is the only module that
runs SQL on the table. Each write function calls `shape_only` first. It refuses
with a clear message when `shape_only` finds a problem.

| Function | Caller | What it does |
|---|---|---|
| `save_draft(db, connection_id, body, shape_hash, untrusted_origin)` | SEM-13 (the profiler) and SEM-16 (an admin's edit) | Checks the flag and the connection, locks the connection row, sets `version` to the highest version plus 1, and inserts a row in state `draft` |
| `accept_part(db, connection_id, version, section, key, change)` | SEM-16 (the panel) | Changes one part of a draft, runs `shape_only` on the new body, and writes the row again. It refuses a row that is not a draft |
| `approve(db, connection_id, version, role)` | SEM-16 (the panel, behind `integrations:manage`, D-SEM-4) | In one transaction, archives the approved row and approves the draft |
| `read_approved(db, connection_id)` | SEM-15 (the `org_facts` provider) | Returns the approved version, or `None`. It never returns a draft or an archived row (D-SEM-11) |

- **The version.** The writer sets `version`, and the caller never passes it.
  The writer locks the connection row with `SELECT ... FOR NO KEY UPDATE`.
  Then it reads the highest version of that connection. This lock does not
  block an insert in another table that references the connection. So two drafts of one
  connection cannot take the same number.
- **Approve.** The function locks the connection row. It sets the old
  approved row to `archived`, and then it sets the draft to `approved`. Both
  steps run in the SAME transaction and in that order, so the partial unique
  index never trips. The function refuses a version that is not a draft. A
  draft that nobody approves stays a draft, and no reader returns it.
- **`approved_role` and `approved_at`.** Both are NULL, or both are set. A
  draft has both NULL. Only `approve` sets them. An archived row keeps the two
  values. Three CHECK constraints hold this rule (see the migration shape).
- **The org.** No write function takes an organization argument. The org comes
  from the bound tenant session and from the column DEFAULT (R5).

**The cross-tenant write.** Postgres checks a foreign key with the rights of the table
owner, so the check skips row-level security. This is a known behaviour of
Postgres, and no migration header states it. Org B could name the `connection_id` of org A,
and the insert would pass.

Each write function selects the connection under the bound tenant FIRST. When
that select returns no row, the function refuses.

The refusal is the same for a missing connection and for a connection of
another org. So it leaks nothing. In the R8 case, org B writes a draft for a
connection of org A and the writer refuses it. No row exists in either org.

**The flag.** `CONNECTION_MODEL_ORGS` is the name in the row and in §10.2. It is
not `SEMANTIC_MODEL_ORGS`. It is a `*_ORGS` flag with an empty default.

No caller exists when SEM-12 merges. With the flag off for the org, nothing
writes or reads the table. A write function refuses with the clear message
"off", and `read_approved` returns `None`. The flag reloads with no restart.
The fence is a new case in `tests/unit/test_flag_reload.py`, with a row in
`docs/flag_reload.md`.

**The migration shape.** The builder books the number with
`~/lanes/reserve_migration.sh`, and the merge checks it again (R1). This text
names no number. The file is expand only (R6): one new table, and no change to
an old object. It sets `SET LOCAL lock_timeout` first, as 451 does.

- **Columns.** `id` (UUID, default `gen_random_uuid()`). `organization_id`
  (`REFERENCES organization`, then `DEFAULT app.tenant_id`, with no comma
  between the two). `connection_id` (`REFERENCES integration_connections(id)
  ON DELETE CASCADE`). `version` (integer, CHECK greater than 0). `state`
  (CHECK `draft`, `approved` or `archived`). `body` (jsonb). `shape_hash`.
  `made_by` (CHECK `code`, `ai` or `admin`). `approved_role` and
  `approved_at`. `untrusted_origin` (boolean, default false).
- **Constraints.** One CHECK says that `approved_role` and `approved_at` are
  both NULL or both set. One CHECK says that a `draft` has both NULL. One
  CHECK says that an `approved` row has both set. `UNIQUE (organization_id,
  connection_id, version)`. Partial `UNIQUE (organization_id, connection_id)
  WHERE state = 'approved'`.
- **Row level security.** `ENABLE` and `FORCE`, in a DO block guarded on
  `relforcerowsecurity`, with one policy that has `USING` and `WITH CHECK`.

**The files.** New: the migration, `gateway/connections/semantic_model.py`,
`tests/unit/test_connection_semantic_model_r8.py` and
`tests/unit/test_semantic_shape_only.py`. Edits: the four generated phases
`generated/01` to `04` (through `scripts/gen_tenant_migration.py`),
`crm_sync/catalogue.py` (the one `shape_only` function), `settings.py`,
`docs/flag_reload.md`, `tests/unit/test_flag_reload.py`,
`tests/unit/test_tenant_coverage.py`, `tests/fences.json`, `.env.example` and
`infra/AGENTS.md`. SEM-12 does not touch `aggregate.py`, `org_facts.py`,
`connections/store.py` or `members.py`.

### 10.5 SEM-13a in detail: the profiler, first slice

An audit of 2026-11-19 gave the SEM-13 row GO-NARROWED. The row named no
files and no verification commands. This section gives the first slice of the
split. It adds no D-number. The SEM-13a row of §10.2 and §14 point here.

**The scope (hand this paragraph to the builder as it is).** SEM-13a, HIGH,
dark under `CONNECTION_MODEL_ORGS`, with no caller. Add a new module
`gateway/connections/profiler.py` with `profile(org, connection_id, *, lister,
caller)`. It does steps 1, 2 and 4 of §4.2. It never does step 3: it makes no
decide call and writes an empty `ai` section.

- It lists the tools through `mcp_tools.fresh_tool_list`. The repair of
  2026-11-19 changes this step: "The schema source" below gives the one list
  read, which keeps every check of `fresh_tool_list`.
- It calls only tools that `mcp_tool_class` puts in class A.
- It refuses a tool whose `destructiveHint` is true or whose `idempotentHint`
  is false. It refuses a server with `always_confirm`.
- It calls a tool only with no required argument, or with a key from an
  earlier read. "The plan and the caps" below gives the rule.
- It makes at most 20 calls in 30 s, with at most 64 KB for each result.
  "The plan and the caps" below says what happens at each cap.
- It keeps only keys, types, counts and the first and last date.
- It normalises keys and types to the grammar of §10.4 (`Custom_Field_10`,
  `int64` and the other cases).
- It writes the body through `semantic_model.save_draft` with
  `untrusted_origin=False`.
- Add the no-member call entry to `mcp_tools.py`, and add no second MCP
  client.

**The decision on approval (fail closed, no owner decision).** The profiler
runs with no member. So two member checks of `call_read_tool` do not apply:
the grant `integrations:use:mcp-<server>` and the member counter of `_spend`.
The profiler still obeys what the admin approved.

It reads a tool only when the tool has a B1b-3 approval. The text of the tool
in the fresh list must hash as that approval, so `mcp_actions.tool_changed` is
false. A tool with no approval, or with a changed text, gets no call. A
failed read of the approvals gives no call to any tool.

This repeats the rule of `call_read_tool` and adds no new rule. The decisions of §4.2 stay as they
are: no member, reads only, the caps of `map_pipeline`, and the
`connected_by` payer.

Two results follow, and both fail closed:

- **A new connect profiles nothing until an admin approves the tools.** A new
  server has no row in `studio_mcp_tool_approvals`, so each of its tools gets
  no call. Fence: `test_connection_profiler.py::test_a_tool_with_no_approval_gets_no_call`.
- **A server whose `agent_scope` is not `*` gets no profile.** A server for
  named agents only, or for no agent, fails `_scope_open`. Fence:
  `test_connection_profiler.py::test_a_server_with_named_agents_gets_no_profile`.

**The code anchors (verified against `main` at `2e6efa641`, 2026-11-19).**
Each path is under `apps/services/gateway/gateway/` unless it says otherwise.

| Anchor | What the code shows |
|---|---|
| `connections/mcp_tools.py` `fresh_tool_list(org, meta, load, *, lister)` | The one read of a tool list that a live call may trust. It serves a cached list only when the list is younger than `FRESH_S` (300 s) and its identity is the identity of the server of now. Else it reads a live `tools/list` through `load`, and refuses a changed server with `LIVE_CHANGED` |
| `connections/mcp_tools.py` `call_read_tool(org, member, server, tool, args, *, may_use, lister, caller)` | It needs a member. It checks the grant `integrations:use:mcp-<server>` (`_may_use`), a connected server, and an `agent_scope` that holds `*` (`_scope_open`). Then it reads the B1b-3 approvals, and refuses a tool with no approval with `LIVE_TOOL_CHANGED`. A network step passes `_spend` (a counter per member and per org) and one of `LIVE_PER_ORG` slots. The answer goes to a cache keyed by a hash of the member |
| `connections/mcp_tools.py` `_class_a_item(listed, meta, tool, approved)` | It refuses a tool that is not in the list, a changed tool, a tool that is not class A, and a tool of a server with `always_confirm` |
| `connections/mcp_tools.py` `_sdk_call(server, tool, args)` | One `tools/call` through the pinned client of `_pinned`, with a byte budget of `MAX_CALL_BYTES`. The `Caller` type lets a test give a fake |
| `connections/mcp_tools.py` `shape_result(raw)` | The capped, cleaned answer: `content` and `structured`, and `truncated`. An answer with `isError` raises `ListFailed` |
| `connections/mcp_tools.py` constants | `READ_TIMEOUT_S` is 20.0 s for one read. `MAX_CALL_BYTES` equals `MAX_ANSWER_BYTES`, 1 MB. So the 64 KB cap of the profiler is a check of its own, on the result of `shape_result` |
| `connections/mcp_tools.py` `clean_tools(items)` and `_list_stored(stored, lister)` | `_list_stored` makes ONE call of `(lister or _sdk_list)(stored)` under `READ_TIMEOUT_S`, and gives the raw items to `clean_tools`. `clean_tools` keeps `name`, `description` and `annotations` only. It drops `inputSchema` and `outputSchema`. The cache holds only that cleaned list. So `fresh_tool_list` never gives a schema, and a cache hit makes no list call |
| `studio/mcp_actions.py` `tool_text_hash(tool)` | It hashes the tool name, the cleaned description and each hint of `MCP_HINT_NAMES`. It does not read a schema |
| `connections/mcp_tools.py` `_spend(org, member)` | It counts two keys in one loop: the member key (`MEMBER_CALLS_PER_WINDOW`, 10) and the org key (`ORG_CALLS_PER_WINDOW`, 60), in a window of `RATE_WINDOW_S` (60 s) |
| `connections/mcp.py` `_HTTP_TRANSPORTS` | The registry admits only the transports `http-sse` and `http`. D85 refuses `stdio`. `origin/main` holds no local-link code: `git grep` finds no Python file that names a local link |
| `studio/mcp_actions.py` `mcp_tool_class(hints, *, always_confirm)` | It returns only `A` or `B`. It is `A` only when `always_confirm` is false and `mcp_needs_confirmation` is false |
| `packages/acb_skills/acb_skills/permission_policy.py` `mcp_needs_confirmation(annotations)` | It reads only `readOnlyHint` and `destructiveHint`. It does not read `idempotentHint`. So the profiler checks `idempotentHint` itself, on the hints of `mcp_actions.tool_hints` |
| `studio/mcp_approvals.py` and migration 455 | The stored approval of B1b-3. The table `studio_mcp_tool_approvals` holds one row for each org, server and tool. The column `text_hash` holds the SHA-256 of the approved text, which the function `mcp_actions.tool_text_hash` computes. The row also holds `approved_by` and `approved_at`. `approved_hashes(db)` reads the map `(server, tool) -> text_hash` in one query. `mcp_tools._approvals_or_none(org)` gives `None` on a failed read |
| `connections/mcp.py` `StoredServer` | It has no connection id. `_server` builds it from a `store.Connection` and drops `conn.id` |
| `connections/store.py` `get_by_id(db, org, connection_id)` | The real map from a connection id to a server. It returns the row of `integration_connections` in the org, or `None`. An MCP server is the row with `provider` `mcp:<name>` (`mcp.PREFIX`), `owning_app` `integrations` (`mcp.OWNING_APP`) and `scope_kind` `org`. The unique index `integration_connections_one_live_idx` (migration 245) allows one such row for each org. No function in `mcp.py` maps a name to an id |
| `connections/semantic_model.py` `save_draft(db, connection_id, body, shape_hash, untrusted_origin)` | It takes a session of `tenant_session()` first. It refuses with `off` when the flag does not hold the org, runs `shape_only`, and locks the connection under the bound tenant |
| `crm_sync/catalogue.py` `connection_key_problem(value)` | The one key rule of §10.4. A digit after a digit fails (`_has_literal`). A digit after a letter or `_` passes |
| `decide_features.py` `PREFIXES` | It holds `my_day.`, `crm.` and `projects.` only. It has no `connection.` prefix. SEM-13a adds none. SEM-13b adds it |

**The map from the connection id to the server.** `profile` takes a
`connection_id`. It reads the row with `store.get_by_id` under
`tenant_session(org)`. It refuses unless the row is an MCP org row of the
form above, with the status `connected`. The server name is `provider` with
the `mcp:` prefix removed. Then the profiler finds the server in
`mcp.enabled_meta(org)` by that name.

This map lives in `profiler.py`. SEM-13a adds no new function to `store.py` or
`mcp.py`.

**The no-member call entry.** The new entry in `mcp_tools.py` reuses
`_class_a_item`, `_sdk_call` and `shape_result`. It drops only three member
steps of `call_read_tool`: the grant, the member counter of `_spend` and the
answer cache, which a member hash keys. It writes no answer to the cache.

It keeps the connected server, the `agent_scope` check, the approvals and the
class check. It keeps the org counter of `_spend` (`ORG_CALLS_PER_WINDOW`).
Each network step of a profile counts one in the org window: the list read
and each `tools/call`. A profile run holds one of the `LIVE_PER_ORG` slots
from its first network step to its end.

The slice splits the org key of `_spend` into a function of its own.
`call_read_tool` still counts the member key and the org key, in the same
order. Fences: `test_connection_profiler.py::test_a_profile_call_counts_against_the_org_window`
(new), and `test_studio_live_preview.py::test_the_org_cap_holds_across_members`
stays green.

**The schema source (decision, no owner decision).** Step 1 of §4.2 reads the
input schemas, and the scope calls a tool only with no required argument. But
`clean_tools` drops `inputSchema`, and the cache holds no schema. So the raw
list does not get to the profiler through `fresh_tool_list`.

SEM-13a does NOT change what `clean_tools` returns. It does not change what
`tool_text_hash` or the model prompt reads. A change to `tool_text_hash` would
make each stored `text_hash` fail. A change to `clean_tools` would not change a
stored hash, because `tool_text_hash` reads no schema. But it would change the
cache, the prompt and `test_a_cached_tool_holds_three_fields_only`.

The minimal seam is ONE new function in `mcp_tools.py` beside
`fresh_tool_list`, `live_tool_schemas`. It always reads a live list, because
the cache holds no schema. It calls `load()` and refuses a changed server with
`LIVE_CHANGED`, as `fresh_tool_list` does. It shares the ONE raw read of
`_list_stored`, so one `(lister or _sdk_list)(stored)` call gives both outputs.

It gives `_list_stored` a lister closure, `_keep_raw`, in place of the lister.
The closure calls `(lister or _sdk_list)(stored)` once, keeps the raw items,
and returns them. So `_list_stored` keeps its signature and its return type.

It gives the `ToolList` of `clean_tools`, and a map from each tool name to the
raw `inputSchema` of that tool only. A name that occurs two times in the raw
list gets no schema, so the profiler never calls that tool. It stores the
cleaned list in the cache with `_store`, as `fresh_tool_list` does. It adds
NO second MCP client: `_sdk_list` and `_pinned` stay the one network path.

One new pure function in `profiler.py` reduces an `inputSchema` to the names
of its required arguments and the JSON type of each. It keeps no description,
no default value and no enum value. A type outside the seven JSON types, or a
list of types, becomes `unknown`. A schema that is not an object gives no
call. Fences: `test_connection_profiler.py::test_the_reduced_schema_keeps_names_and_types_only`
and `test_studio_mcp_tools.py::test_a_cached_tool_holds_three_fields_only`
(stays green).

**The origin.** SEM-13a profiles only a server that it reaches through the
cloud MCP path. That is an `mcp:` org row whose transport is `http-sse` or
`http`. It refuses each other row, and so it refuses a local link. Today
`origin/main` holds no local-link code.

So the profiler writes `untrusted_origin=False`. SEM-17 sets the mark for a
local link, and passes `untrusted_origin=True`. Fence:
`test_connection_profiler.py::test_a_row_that_is_not_a_cloud_mcp_row_gets_no_profile`.

**The `shape_hash`.** §3.4 says that `shape_hash` is the hash of the tool
list and of the discovered shape. So the profiler hashes the reduced shape
only:

- each tool of the cleaned list, by name, with its class and its required
  arguments and their types
- each kept entity key, with the key and the type of each field

The hash is the SHA-256 hex, 64 lower-case characters, of the canonical JSON
of that shape: sorted keys and the separators `,` and `:`. It holds no value.
It also holds no count and no date, because a new record changes a count and
does not change the shape. `save_draft` takes it, because `_HASH` allows 16 to
128 hex characters.

So an unchanged shape gives the same hash. `profile` reads
`semantic_model.read_approved`, and it saves no draft when the approved
`shape_hash` is the same. SEM-13c owns a repeated draft of a shape that nobody
approved yet. Fences in `test_connection_profiler.py`:
`test_the_same_shape_gives_the_same_hash` (other values and counts),
`test_a_changed_shape_gives_a_new_hash` and
`test_an_approved_shape_with_the_same_hash_saves_no_draft`.

**The normalisation.** The profiler checks each key with
`connection_key_problem`, and never with a copy of the rule.

- A key with a run of digits gets an `_` between each two digits. So
  `Custom_Field_10` becomes `Custom_Field_1_0`, and `TDS_194C` becomes
  `TDS_1_9_4C`.
- A type goes through a closed table to one plain word. So `int64` becomes
  `integer`, and `float64` becomes `number`. A type that is not in the table
  becomes `unknown`.
- A `-` becomes `_` before the digit rule, so `list-invoices` becomes
  `list_invoices`. A dot and a space stay refused.
- The run leaves out a key or a type word that holds a figure in its raw
  text, and counts it. A figure is 5 or more digits in a row, or 6 or more
  digits that only `_` or `-` join. The pattern is
  `\d{5,}|\d(?:[-_]?\d){5,}`, and it reads the raw text before the dash rule.
  A date, an account number, a phone number and an amount take one of these
  forms. The digit rule would hide that figure in a key.
- So these are left out: `d20240115`, `acct_50012345`, `invoice_123456`,
  `d2024-01-15`, `day_2024-01-15`, `p_12_345_678`, `ph_9876-543-210`,
  `amt_4_500_000`, `acct_5001_2345` and `card_4111_1111_1111_1111`.
- Each other key gets an `_` between each two digits. So `Q4_2024_Target`
  (5 digits across one `_`) becomes `Q4_2_0_2_4_Target`, and `cf_9001`
  becomes `cf_9_0_0_1`.
- This rule applies to entity keys, field keys, column names, argument keys
  and type words.
- A key that still fails the rule, or that is the same as a key already in the
  entity, is left out. The run counts it.

A known limit (F3 of the verifier, 2026-11-20): a value or an instruction
written as one identifier with no space passes as a key. A hostile server can
also write a figure as letters, or as an identifier with fewer digits. No
digit rule bounds that, so the digit rule is a guard, not a proof. SEM-15 must
review the keys before a key gets into a prompt.

**The plan and the caps (fix round, 2026-11-20).** The profiler plans its
calls in one fixed order. First come the reads with no argument, in the order
of the tool name. Then come the keyed reads, in the order of the tool name and
then the key.

- A keyed read takes one required `string` argument. The call sends the raw
  key that an earlier read returned. A raw key fills an argument only when
  `connection_key_problem` passes it as it is.
- A result object with exactly one list of records is a wrapper, such as
  `{"data": [...], "total": 120}`. Its records belong to the tool, or to the
  key that filled the call. The wrapper word is no entity, and it never fills
  an argument. An object with two or more lists of records gives one entity
  for each key.
- The profiler cuts the plan to 20 calls before the first call, and counts
  each cut call in `left_out`. Then it writes what it read. So the call cap
  never stops a run, and the hash does not depend on where a cap falls. A
  hard guard still refuses a 21st call.
- A result over 64 KB, or over the 1 MB budget of the client, skips that tool
  only. The tool gives no shape, the run counts it in `left_out`, and the
  run goes on.
- Three events stop the run, and then the run writes nothing. They are the
  30 s cap (`stopped:time`), a cache that cannot count the org window
  (`stopped:cache`) and a full org window (`stopped:limited`).
- A call that raises `LIVE_CHANGED` or `LIVE_NO_SERVER` also stops the run
  (`stopped:changed`), and the run writes nothing. Another refusal of one
  call skips that call.
- The entry checks the identity of the server once, at the list read, and
  keeps that server for the calls. So it does not see a change after the
  list read, and today no call raises `LIVE_CHANGED`. The cost is one stale
  draft that an admin reviews. A later slice may check the identity from the
  registry again before each call.
- The run frees its `LIVE_PER_ORG` slot before it writes the draft.

A known limit (F4 of the verifier, 2026-11-20): the B1b-3 `tool_text_hash`
does not cover `inputSchema`. So a server can add a required argument to an
approved tool, and the profiler acts on that schema. A later slice must hash
the schema too.

A known limit (merge with TOF-2d, 2026-11-20): `call_read_tool` writes one record to the
tool failure log for a failed call. The no-member entry writes none. That record
takes the tenant of the request and the surface `studio`, and the profiler has
no request. The outcome of the run carries the reason of a failed call instead.

**The flag.** `profile` checks `connection_model_allowed(org)` first. With the
flag off, it lists no tool, calls no tool and writes nothing.

### 10.6 SEM-9 in detail: proposals from chat

An audit of 2026-11-20 gave the SEM-9 row NO-GO. The row had no done-when
that a test can check. It left five design choices open, and it did not cite
D-SEM-7. This section closes those gaps. It adds no D-number. The SEM-9 row
of §10.2 and §14 point here.

**The status of each choice.** This section PROPOSES each choice below,
with its reason. The owner must confirm a choice that has the mark OWNER-CONFIRM. The
open questions at the end of this section list each of them. A builder may
build a PROPOSED choice that has no OWNER-CONFIRM mark.

**The scope: `two_kinds` (PROPOSED, OWNER-CONFIRM).** The task asks the owner
to pick one of three options: `two_kinds` (recommended), `three_kinds` or
`admin_only_vq`. This section is for `two_kinds`. A member's chat proposes a
synonym or a measure. Only a CRM admin accepts the proposal.

The verified-query kind is not in this slice. It joins after SEM-8b gives
`save()` a route. It also needs the member's consent to show the question
words (D-SEM-7).

> **Note.** If the owner picks another option, this section changes before
> dispatch. `three_kinds` adds a verified-query proposal, which needs SEM-8b
> and a consent step. `admin_only_vq` adds the admin's own save of a verified
> question from chat, which needs SEM-8b.

**The code anchors (verified against `origin/main` at `d76bf192b`,
2026-11-20).** Each path is under `apps/services/gateway/gateway/` unless it
says otherwise.

| Anchor | What the code shows |
|---|---|
| `crm_sync/questions.py` `KINDS` and `CHAT_KINDS` | Six kinds: `purpose`, `key_field`, `slot`, `stage_value`, `revenue` and `term`. A chat card asks four of them: `purpose`, `key_field`, `revenue` and `term` |
| `crm_sync/questions.py` `REASON_OPEN` to `REASON_ANSWERED` | Why a question shows: `open`, `unsure`, `stale` or `answered`. The load computes the reason each time. No column holds it |
| `crm_sync/questions.py` `PROPOSALS`, `with_proposal` and `PROPOSALS_KEEP` | The cursor row `zoho:questions` holds `proposals`: a question id, then a list of `{value, member, at}`. One member holds one pick for each question. The list keeps the 5 newest. No table and no migration (§18.14.3) |
| `crm_sync/questions.py` `chat_choice` | A pick must be the key of an option that the server built. So the model cannot invent a value |
| `routes/crm/questions.py` `reply_as` (`POST /crm/questions/{id}/reply`) | It decides by the permission of the caller, before any write. A CRM admin (`admin:access:manage`) writes the fact through the code of `/answer`. The pick of each other member becomes a proposal, and it writes nothing in the plan |
| `routes/crm/questions.py` `term_pickers` and `write_term_answer` | A `term` answer is the field that a word means. `write_term_answer` merges `{field}` into the bucket `term_answers` of `zoho:questions`. It keeps no version, it writes no change-log row, and it has no Undo |
| `routes/crm/questions.py` `clear_proposals` and `after_admin_write` | Each admin write of a part clears the proposals of that part |
| `packages/acb_skills/acb_skills/crm_tools.py` `crm_propose_rule(text, scope)` | Today's chat path for a proposal. The text holds 1 to 300 characters. `member_said` refuses words that the member did not say. `/crm/rules/propose` holds the words in tenant Redis for 24 hours (`PENDING_TTL_S`) until the member confirms. The confirm writes `crm_business_rules` (migration 283). `BODY_MAX` of `routes/crm/rules.py` is 300 |
| `apps/services/orchestrator/orchestrator/_tool_injection.py` | `crm_propose_rule` loads only for an org on `CRM_ANSWER_FIRST_ORGS`, with `org_facts` |
| `tests/unit/test_tool_schema_diet.py` | It pins `crm_propose_rule` to the params `scope` and `text`. The pin requires `text` |
| `crm_sync/catalogue.py` `semantic_problem`, `semantic_ref_problem` and `_MEASURE_KEYS` | The item checks of SEM-7. A measure holds only `kind`, `key`, `label`, `module`, `aggregate`, `fields`, `filters` and `unit`. Another key fails with `keys`. `semantic_ref_problem` checks a measure, a join or a query against the catalogue. It does not check a synonym, because a synonym is not a semantic kind |
| `routes/crm/mapping_plans.py` `check_kinds`, `_check_semantic`, `merge_kinds(semantic=True)`, `commit` and `lock_plan` | The one path of a plan write. `commit` runs `check_kinds` and `_check_semantic_refs` under the lock of `lock_plan` |
| `routes/crm/mapping_plans.py` `POST /crm/mapping/plans/{version}/restore` | The Undo of a plan change. It approves a copy of an old version |
| `routes/crm/verified_queries.py` `save` | The pattern of an item write: `lock_plan`, then `merge_kinds`, then `commit`, then `carry_draft`. It is a library. No route calls it until SEM-8b |
| `routes/crm/change_log.py` `log_change`, `undoes`, `AREAS` and `VIAS` | The ONE log of a change of the CRM set-up (migration 341). Each area registers its Undo. The `area` CHECK is a shape and not a list, so a new area needs no migration. `test_crm_change_history.py` lists the plan routes and the question routes as PENDING (CRM-M1 PR 2) |
| `routes/shell/waiting.py` `KINDS` and `PROVIDERS` | Four kinds: `decision`, `chat_ask`, `app_request` and `held_write`. No CRM kind. `waiting_allowed` is the one reader of `WAITING_ORGS`. A provider calls the read of its app, and writes no SQL |
| `routes/shell/needs.py` `Provider` | `admin=True` tests `admin:members:read`, the org admin of `routes/shell/intent.py`. That is NOT the CRM admin. The field `permission` names one more grant |

**The store (PROPOSED).** SEM-9 adds no table and no migration (§18.14.3).
Each sub-kind keeps the store that it has today.

- **A synonym proposal is the CRM-Q32-8 proposal of a `term` question.** It
  stays in `proposals` of `zoho:questions`, as `{value, member, at}`. The
  value is a field key from the options that the server built.
- **A measure proposal goes into a new bucket `measure_proposals` of
  `zoho:questions`.** Its key is a hash of the canonical item (`_digest` of
  `crm_sync/questions.py`). Its value is `{item, members, at}`. `item` is a
  WHOLE SEM-7 `measure` item. `members` is a list of `{member, at}`.
- Two members who propose the same item make ONE proposal, with a count of 2.
  The canonical JSON of the item is the key, so the merge needs no compare.
- Only `routes/crm/questions.py` and `crm_sync/questions.py` read either
  bucket. The scan fence of CRM-Q32-8 grows to name `measure_proposals`.

Why two buckets and not one new kind `proposal`? A `term` pick is already a
proposal. A second shape for it would give one fact two readers. A measure
needs a structured payload, so it gets its own bucket, keyed by its content.

**Who proposes, and the tool (PROPOSED, OWNER-CONFIRM for the budget).**

- **A synonym.** Each member with CRM access picks an option on a `term`
  card. This path exists (CRM-Q32-8). SEM-9 adds no tool for it.
- **A measure.** Each member with CRM access calls `crm_propose_rule` with a
  new value of `scope`: `measure`. `text` is the name of the measure, in the
  words of the member.
- **The shape comes from the run, not from the model.** The tool takes the
  module, the aggregate, the fields and the fixed filters from the last
  `crm_aggregate` call of the same run. A ContextVar of `crm_tools` holds
  that shape, as `_RUN_ASKS` holds the asks of a run.
- **The cost.** The params stay `scope` and `text`, so the pin of
  `test_tool_schema_diet.py` does not change. Only the docstring changes. The
  builder rewrites it to cost 0 net tokens against `main`.
- **The budget.** The audit says that the Studio token budget has a margin of
  about 12 tokens. This draft did not find that budget with `grep`. The
  builder names the fence that measures it before the first edit. If the
  docstring cannot cost 0 net, the builder stops, and the owner decides a
  budget raise.
- **One more flag.** `crm_propose_rule` loads only for an org on
  `CRM_ANSWER_FIRST_ORGS`. So a measure proposal from chat needs that flag
  too.

Why extend `crm_propose_rule`? A new tool costs schema tokens in each turn of
the `crm` family. A new param costs tokens too. One new value of `scope` costs
nothing, when the builder also makes the docstring shorter.

**The flow of a measure proposal (PROPOSED).**

1. The tool runs `member_said` on `text`. It refuses words from a tool result
   (§6.2 rule 2), as it does for a rule.
2. With no `crm_aggregate` call in the run, the tool gives one plain line
   back, and it posts nothing.
3. The tool posts `{label, shape}` to a new route,
   `POST /crm/questions/propose`, behind `_CHAT_GUARDS`.
4. The route builds the item. The `key` comes from the label (`_SLUG`). The
   `unit` comes from a closed map of the aggregate and the field type.
5. The route runs `semantic_problem` and `semantic_ref_problem`. A problem
   refuses with a plain line, and the route holds nothing.
6. The route holds the item in tenant Redis for 24 hours, as
   `/crm/rules/propose` does. The tool raises the confirm card of
   `_rule_card`, with the keys `save` and `skip`.
7. On `save`, `/reply` decides by the permission of the caller. The confirm of
   a CRM admin is an accept (see "The accept"). The confirm of each other
   member writes the proposal into `measure_proposals`.

**The caps (PROPOSED).**

| Cap | Value | Where the server checks it | What the member reads |
|---|---|---|---|
| Open proposals of one member in one org | 10, synonyms and measures together (§6.2 rule 5) | The write of a proposal, under the lock of `zoho:questions` | "You have 10 open suggestions. A CRM admin must answer one first." |
| Open measure proposals of one org | 50 | The same write | "Your CRM admin has many suggestions open. Try again later." |
| Proposals of one member in one day | 5 | A daily counter in tenant Redis, with a TTL of 25 hours | "You made 5 suggestions today. Try again tomorrow." |
| The text of `crm_propose_rule` | `BODY_MAX` (300 characters) | The tool | The refusal of today |
| The name of a measure | `LABEL_MAX` (80 characters), with no digit that `_has_literal` finds | `semantic_problem` | A plain line that names the cap |
| Words from a tool result | Refused | `member_said` in the tool | `RULE_NOT_MEMBER` |
| The same item from a second member | One proposal, with a count | The key of the bucket | `CHANGE_WAITS` (exists) |
| An item equal to a measure in use | Refused | The route, against the approved version | "Your team already uses this measure." |
| A synonym pick equal to the answer in use | Drops | `live_proposals` (exists) | `CHANGE_WAITS` (exists) |

**The payloads (PROPOSED).**

- **A synonym.** The `term` question holds the word and the module. The pick
  holds a field key. `chat_choice` admits only an option that the server
  built from the catalogue. The server stores no record value (§6.2 rule 3, D-SEM-5).
- **A measure.** A full SEM-7 `measure` item. `semantic_problem` and
  `semantic_ref_problem` run at the propose. `commit` runs them again at the
  accept. So a field that left the catalogue between the two refuses the
  accept with `stale_measure`. The server stores no total and no figure
  (§3.3 point 5).
- **One checker.** SEM-9 adds no item check of its own. Each check is a call
  of the SEM-7 functions. A hermetic scan fences this (§14).

**The accept (PROPOSED).**

- **Who.** Only a CRM admin (`admin:access:manage`, D-SEM-4). A member who is
  not an admin gets 403, and the route writes nothing.
- **A measure.** A new route, `POST /crm/questions/proposals/{id}/accept`,
  behind `_GUARDS`. It runs the steps of `verified_queries.save`:
  `lock_plan`, `merge_kinds(others, [item], semantic=True)`, `commit` with
  `based_on`, and `carry_draft`. Then it drops the proposal from the bucket,
  in the same transaction.
- **A synonym.** The admin answers the `term` question through `/answer`, or
  through `/reply` as an admin. This path exists. `after_admin_write` clears
  the proposals of the question.
- **A replay.** The route reads the bucket under the plan lock. A proposal
  that is gone gets 409 `proposal_gone`, and the route writes no version. So a
  replay writes one version only.
- **A decline.** `POST /crm/questions/proposals/{id}/decline` drops the
  proposal. It writes no plan and no change-log row.
- **The provenance.** The keys of a measure item are a closed set, so the
  provenance cannot sit on the item. The plan row keeps `approved_by`. Each
  accept writes one change-log row through `log_change`, in the transaction
  of the accept.
- **The change-log row.** The area is `measure` or `term`, two new keys of
  `AREAS`. The actor is the admin. `after` holds the item or the field, with
  `source` `chat_proposal` and `proposed_by` (at most 5 members). The row
  time is the time of the accept.

**The Undo (PROPOSED).**

- **A measure.** `@change_log.undoes("measure")` restores the version
  `based_on` through the code of `POST /crm/mapping/plans/{version}/restore`.
  It refuses with 409 when the version in use is not the version of the
  accept. The admin can still restore any version from the plan history.
- **A synonym.** `@change_log.undoes("term")` writes the `before` of the row
  back. `before` is the old `{field}`, or no entry. With no entry, the Undo
  calls `runs.drop_cursor_entry`. It refuses with 409 when the entry changed
  since the accept.
- **Why the change log.** The change log is the one Undo seam of the CRM
  set-up (CRM-M1). A `versions` list in the bucket `term_answers` would be a
  second Undo store. The `area` CHECK of migration 341 is a shape, so SEM-9
  needs no migration. R6 holds.

**The visibility (PROPOSED).**

- **The admin.** A proposal shows as plain text. It shows the word or the
  measure name, the module, the fields by label, the count of members and
  the newest time. A member shows by display name, or as "A member" (`A_MEMBER`). An
  email never shows.
- **Before the accept.** A proposal never reaches the model brief,
  `org_facts` or the answer of another member. `facts.semantic_facts` reads
  only the approved version, so this holds by construction, and a test fences
  it.
- **After the accept.** An answer that uses the measure names the measure and
  its plan version (§5.2: the answer names what it used). So an accepted
  measure never changes a total in silence.
- **D-SEM-7.** A synonym and a measure carry no question text of a member.
  The measure name is words that the member proposed and confirmed on a card.
  So the proposal itself is the consent that D-SEM-7 asks for. No other
  words of the chat reach the admin. A verified-query proposal carries
  question words, so it waits for a consent step (open question 2).

**The waiting kind (PROPOSED).**

- **The kind.** One new kind, `crm_proposal`, in `routes/shell/waiting.py`.
  Add it to `KINDS`. Its provider calls ONE read in
  `routes/crm/questions.py`, `waiting_proposals(user)`. That read gives one
  row for each open measure proposal, and one for each `term` question with
  a live proposal.
- **The registration.** One line in `PROVIDERS`:
  `Provider("crm_proposal", ("crm",), _crm_proposals, frozenset({"crm_proposal"}), permission="admin:access:manage", needs=False)`.
  Do NOT set `admin=True`. That tests the org admin, not the CRM admin.
- **The gates.** `WAITING_ORGS` gates the seam. `CRM_QUESTIONS_ORGS` and
  `SEMANTIC_MODEL_ORGS` gate the read. With a CRM flag off, the read raises
  `SourceAbsent`, and the source reads `absent`. A member who is not a CRM
  admin gets no row.
- **The row.** The provider sets a fixed title: "Members suggested a change to how the CRM
  reads its data". The proposal text stays in the CRM section. The waiter is
  the count of members. The one act is a link "Review" to "Your answers"
  (CRM-Q32). SEM-10 draws the proposals there.
- **`waiting_on_me.md`.** WS-AP owns it (epic 1615). The SEM-9 PR adds one
  row for `crm_proposal` to §3.2, and adds the kind to the wire table of
  §7.3. A kind of the seam that §3.2 does not list is a defect. The PR body
  asks the WS-AP owner to review the two lines. SEM-9 adds no row to §8,
  because SEM-9 owns the slice.

**The tier and the gates.** HIGH, because SEM-9 writes facts: a plan version
and a term answer. AGENT-SAFE to build dark. OWNER-GATE: a flip of
`SEMANTIC_MODEL_ORGS`, `CRM_QUESTIONS_ORGS` or `WAITING_ORGS` for a live org,
and each proposal that reaches a real admin in production. The owner previews
the flow after SEM-10 draws it.

**Non-goals.**

- SEM-8b, the route of `save()` and the chat offer of a verified question.
- SEM-10, the UI of "Your answers".
- Learning across orgs (D-SEM-9). A proposal never leaves its org. Only
  counts cross tenants.
- A verified-query proposal, and a join, an instruction or a field-meaning
  proposal (§6.2).
- Any stored value (D-SEM-5).
- A change of the scopes `me` and `org` of `crm_propose_rule`.

**Done when.** Each line is a test of §14.

1. A member confirms a measure. Then the bucket holds one proposal, and the
   plan version does not change.
2. A second member confirms the same item. The bucket holds one proposal,
   with a count of 2.
3. An admin accepts it. One new plan version holds the item, and one
   change-log row holds `source` `chat_proposal`.
4. A replay of the accept gets 409 `proposal_gone`, and writes no second
   version.
5. A member who is not an admin calls accept. The route answers 403, and the
   plan version does not change.
6. Org B cannot read, accept or decline a proposal of org A.
7. The route refuses the 11th open proposal of a member, and the 6th of one
   day.
8. The propose refuses an item that fails `semantic_problem` or
   `semantic_ref_problem`. After a catalogue change, the accept refuses it too.
9. The Undo of a measure restores the version before. The Undo of a synonym
   restores the old field, or drops the entry.
10. `GET /shell/waiting` lists `crm_proposal` for a CRM admin of a flag org.
    It never lists it for a member, or for another org.
11. Before the accept, `org_facts` serves no proposed measure.
12. The pin of `crm_propose_rule` in `test_tool_schema_diet.py` does not
    change.

**The files.** Edits: `routes/crm/questions.py`, `crm_sync/questions.py`,
`routes/shell/waiting.py`, `routes/crm/change_log.py` (the areas `measure`
and `term`), `packages/acb_skills/acb_skills/crm_tools.py`, the CRM manifest
`packages/acb_common/acb_common/app_manifests/crm.py` (the new routes), and
`project-docs/specs/waiting_on_me.md`. Tests: `test_crm_questions_chat.py`,
`test_crm_questions_chat_r8.py`, `test_waiting_seam_r8.py` and
`test_crm_change_history.py` (the new routes). No migration and no new flag.
SEM-9 calls the functions of `mapping_plans.py` and `catalogue.py`, and edits
neither file.

**The dependencies.** SEM-7 (built, task 3737). `two_kinds` does not need
SEM-8b. `three_kinds` and `admin_only_vq` need SEM-8b.

**Open questions for the owner.** Each has a recommended default.

1. **The scope.** `two_kinds`, `three_kinds` or `admin_only_vq`? Default:
   `two_kinds`, which this section describes.
2. **D-SEM-7 consent for a verified-query proposal.** Default: a later slice,
   after SEM-8b, with a consent line on the confirm card.
3. **The tool budget.** If the docstring of `crm_propose_rule` cannot cost 0
   net tokens, does the owner raise the budget? Default: the builder stops
   and reports the measured cost, and the owner decides.
4. **The daily cap.** Default: 5 proposals for each member in one day.
5. **The open caps.** Default: 10 for each member (§6.2 rule 5), and 50
   measure proposals for each org.
6. **The `waiting_on_me.md` rows in the SEM-9 PR.** Default: yes, and the
   WS-AP owner reviews them.
7. **The Waiting title.** Default: a fixed title. The proposal text shows
   only in the CRM section.
8. **The fourth flag.** A measure proposal from chat also needs
   `CRM_ANSWER_FIRST_ORGS`, because the tool loads only there. Default:
   accept this, and add no new flag.

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

**Answered 2026-11-18, owner in chat (D226).** D-SEM-1, 2, 4, 5, 6, 7, 9, 10
and 11: accepted as recommended. D-SEM-3: Option A. D-SEM-8: a new connector
spec. The list below keeps each question and its recommendation. Each line
ends with its answer.

1. **D-SEM-1. A new block "Data meaning" in `building_blocks.md` §2**, whose
   seam is `org_facts` with one provider per source. Recommendation: yes. It
   makes the CRM's model general and adds no parallel read path.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
2. **D-SEM-2. One new table for the model of a connection**, keyed by the
   connection id, with versions. Recommendation: yes. The CRM keeps its plan
   and needs no table.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
3. **D-SEM-3. Which one query grammar** (§5.1). Option A: the shape of
   `studio_query` for every store. It gets one `any` group, `matches`, and a
   value hook for each store. Option B: a CRM `filter_grammar.py`, as
   the CRM UI research draft S1 proposes. Recommendation: A. It costs more, because of
   the zone, money, spelling and hot-field conflicts that §5.1 lists. B leaves
   a third spelling. Rung 1 does not wait for this answer.
   **Answer (2026-11-18, owner in chat): Option A. The shape of `studio_query` for every store.**
4. **D-SEM-4. Who approves.** The CRM admin for the CRM (as CRM-Q32). A holder
   of `integrations:manage` for a connection. AI Studio reads a model and does
   not edit it. Recommendation: yes. Three permissions approve different
   things today, and this keeps each one where it is.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
5. **D-SEM-5. No stored value index.** Values resolve at query time as the
   member. Only picklist options are kept. Recommendation: yes. A stored list
   needs its own owner decision. It can show a value to a member who cannot
   open its record.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
6. **D-SEM-6. Verified queries live in the model of the source**, with
   parameters only, and a skill may name one. Recommendation: yes. The
   alternative stores them as saved skills, which a member owns.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
7. **D-SEM-7. Suggestions from use show counts and query shapes only.** A
   member's question text reaches an admin only if the member consents.
   Recommendation: counts and shapes only.
   **Answer (2026-11-18, owner in chat): accepted as recommended (counts and shapes only).**
8. **D-SEM-8. Books of account.** Keep `ai_studio.md` non-goal 8 (no general
   ledger). Books arrive as connections: Tally through the local link, then
   Zoho Books first, with no wait for IN-3 (D-API-5, D228). Name the spec that owns the books connectors.
   Recommendation: a books section in `crm_connectors.md` (WS-MIR) or a new
   connector spec. The owner picks.
   **Answer (2026-11-18, owner in chat): a new connector spec owns the books connectors (Tally, then Zoho Books). The spec is a new slice, and no section of `crm_connectors.md` takes it.**
   *(2026-11-18: D-API-5 of
   `api_connections.md` puts Zoho Books first, as D122.1 decided. It drops the
   wait for IN-3 on the gateway path. The owner accepted D-API-5 on 2026-11-19
   (D228).)*
9. **D-SEM-9. No learning across orgs.** Only counts cross tenants. The
   profiler's global prompt changes only through task 3649. Recommendation:
   yes, as D187 point 4.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
10. **D-SEM-10. Keep the body close to Apache Ossie**, and take no dependency.
    Recommendation: yes. It is pre-1.0.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
11. **D-SEM-11. Before an admin approves a connection's draft, the chat gets
    nothing new from it.** Recommendation: yes (rule 5). The CRM's "answer
    first" (CRM-Q33) stays for the CRM.
   **Answer (2026-11-18, owner in chat): accepted as recommended (yes).**
12. **The held WS-CSY decisions. Still open (2026-11-18).** This design does not assume an answer.
    - **D-CSY-1** (the per-turn block reads the description). SEM-14 needs a
      yes. With a no, a connection's model reaches the chat only through
      `org_facts` on demand (SEM-15).
    - **D-CSY-3** (the server's own instructions). With a no, the profiler
      does not read them either.
    - **D-CSY-4** (match words for a server). A model's synonyms could feed the
      words, but only after a yes.
13. **The rung 0 flip.** If SEM-R0 finds the built CRM layers off for the live
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

# SEM-12 (the R8 suite needs the database of the line above)
uv run pytest tests/unit/test_semantic_shape_only.py tests/unit/test_flag_reload.py \
  tests/unit/test_tenant_coverage.py tests/unit/test_tenancy_boundary.py -q
uv run pytest tests/unit/test_connection_semantic_model_r8.py -q

# SEM-13a (hermetic first, then the R8 case on the database)
uv run pytest tests/unit/test_connection_profiler.py tests/unit/test_semantic_shape_only.py \
  tests/unit/test_studio_live_preview.py tests/unit/test_studio_mcp_tools.py \
  tests/unit/test_studio_preview_sends_nothing.py tests/unit/test_studio_mcp_approvals.py -q
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_connection_semantic_model_r8.py \
  tests/unit/test_studio_live_preview_r8.py tests/unit/test_studio_mcp_approvals_r8.py -q

# SEM-9 (hermetic first, then the R8 suites on a real database)
uv run pytest tests/unit/test_crm_questions_chat.py tests/unit/test_crm_plan_kinds_semantic.py \
  tests/unit/test_crm_verified_queries.py tests/unit/test_tool_schema_diet.py \
  tests/unit/test_crm_change_history.py tests/unit/test_crm_answer_first.py -q
# A lane with a private database sets DATABASE_URL and TENANT_LADDER_DATABASE_URL to it
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_crm_questions_chat_r8.py tests/unit/test_waiting_seam_r8.py \
  tests/unit/test_crm_questions_r8.py tests/unit/test_crm_verified_queries_r8.py \
  tests/unit/test_crm_answer_first_r8.py -q
```

**The SEM-12 fences (R7).** Each test names the mutation that must turn it red.

- `test_connection_semantic_model_r8.py` (new, R8). Org B cannot read a row of
  org A. Org B writes a draft for a connection of org A, and the writer
  refuses it. No row exists in either org. A delete of the connection removes
  its versions. A second `approved` row for one connection fails. `approve`
  leaves exactly one approved row, and archives the old one in the same
  transaction. Two concurrent drafts get two numbers. A draft with `approved_at`
  set fails the CHECK. A write with the flag off refuses with "off".
  A body over 262144 bytes of jsonb text refuses with `too_big` and writes no row.
  A body at the cap is written, and `octet_length(body::text)` equals the count.
  **Mutation (SEM-12b):** count the compact JSON in `body_bytes`, and the over-cap case fails.
  **Mutations:** drop the partial unique index, and the second-approved case
  fails. The cross-tenant case has two guards: the lock select, and the select
  in the INSERT. Each guard covers the other. So a drop of one guard keeps the
  case green. A drop of both turns it red.
  `FORCE` has a hermetic fence. Drop it, and
  `test_semantic_shape_only.py::test_the_migration_holds_the_shape_of_section_10_4`
  fails. The R8 template applies `generated/04`, and that file forces RLS again.
- `test_semantic_shape_only.py` (new). One case for each row of the
  `shape_only` table in §10.4, and a source check that `connections/semantic_model.py`
  defines no second `LABEL_MAX`, `number_like` or `_has_literal`. **Mutations:**
  drop the `_PARAM_NAME` full match on a parameter name, and the name `Acme Ltd`
  passes and the case fails. Drop both the `number_like` call and the `_PARAM_NAME`
  full match, and the name `2024` passes and the case fails. Drop the section check, and the part in the wrong section
  passes and the case fails. Drop the key grammar, and a key that holds an
  amount, a date or an instruction passes. Drop the check of a filter option,
  and an amount in a fixed filter passes. Drop the check of a parameter name,
  of a field `type` or of `model_version` in a connection body, and its case
  fails. Drop one place from `_key_values`, and the case of that place fails. The
  synonym target is the exception, because `_targets` already admits only keys
  that pass the key rule.
  Drop the two code-after-digit forms of `_MONEY`, and `450000 INR due` passes.
  Drop the org filter of a registry statement, and the advisory source case fails.
  FORCE RLS is the first layer, and that filter is a second layer.
- `test_flag_reload.py` (extended). `CONNECTION_MODEL_ORGS` reloads with no
  restart. **Mutation:** leave the flag out of the reload list.
- `test_tenant_coverage.py` and `test_tenancy_boundary.py`. The table is in the
  generated phases, and `REFERENCES` comes before `DEFAULT`. **Mutation:** remove
  the table from `generated/01`.

**The SEM-13a fences (R7).** Each rule of §10.5 names its test, and each test
names the mutation that must turn it red.

- `test_connection_profiler.py` (new, a fake lister and a fake caller, no
  network). It has one case for each rule:
  - The class rule. The fake caller never gets a class B tool.
    **Mutation:** drop the class check, and the case fails.
  - The hints. A tool with `destructiveHint` true, or with `idempotentHint`
    false, gets no call. **Mutation:** drop the hint check, and the case fails.
  - The approval. A tool with no B1b-3 approval, or with a changed text, gets
    no call. **Mutation:** drop the check of the approval, and the case fails.
  - The caps. The plan is cut to 20 calls in a fixed order, and two runs
    give the same hash (`test_the_plan_is_cut_to_20_calls_in_a_fixed_order`).
    A 21st call is still refused (`test_a_21st_call_is_still_refused`). A
    result over 64 KB skips that tool only
    (`test_a_result_over_64_kb_skips_that_tool_only`). A run over 30 s stops
    and writes nothing (`test_a_run_over_30_s_stops`). A cache that cannot
    count stops the run (`test_a_cache_that_cannot_count_stops_the_run`).
    **Mutation:** drop the plan cut, the skip of a big result, or a time
    check, and its case fails.
  - The fix round of 2026-11-20. Each rule has a case, and the drop of the
    rule turns it red:
    `test_only_a_raw_key_that_passes_the_rule_fills_an_argument` (F1),
    `test_a_wrapper_takes_the_tool_name_and_fills_no_argument` (P1b),
    `test_a_tool_name_with_a_dash_gives_an_entity` (P2),
    `test_a_key_with_five_digits_in_a_row_is_left_out` (F2),
    `test_a_figure_split_by_dashes_or_underscores_is_left_out` (G1),
    `test_a_key_with_five_digits_or_fewer_across_a_mark_is_kept` (G1),
    `test_a_changed_server_during_the_run_stops_it` (F5) and
    `test_the_write_holds_no_live_slot`. The F5 case patches the call to
    raise the refusal. So it proves the reaction only, because today no call
    raises it (§10.5 "The plan and the caps").
  - No value. A value in a fake result does not get into the body.
    **Mutation:** keep a value, and the case fails.
  - The normalisation. `Custom_Field_10` and `int64` normalise, and
    `shape_only` passes the body. **Mutation:** drop the normalisation, and
    `save_draft` refuses the body, so the case fails.
  - The flag. With the flag off, the fake lister and the fake caller get no
    call, and nothing is written. **Mutation:** drop the flag check, and the
    case fails.
  - The map. A connection id that is not an MCP org row gets no list.
    **Mutation:** drop the provider check, and the case fails.
- `test_connection_semantic_model_r8.py` (extended, R8). One case: a profiler
  draft of a connection saves with `made_by` `code`, an empty `ai` section and
  `untrusted_origin` false. **Mutation:** write the body with
  `untrusted_origin` true, and the case fails.
- The files that stay green. Each one holds code that SEM-13a reuses or
  splits:
  - `test_studio_mcp_tools.py` holds `clean_tools`, the cache and the caps of
    a list read. It does not hold `call_read_tool`. **Mutation:** add
    `inputSchema` to the output of `clean_tools`, and
    `test_a_cached_tool_holds_three_fields_only` fails.
  - `test_studio_live_preview.py` holds `call_read_tool`, `fresh_tool_list`
    and `_spend`. **Mutation:** the split of `_spend` drops the org key from
    `call_read_tool`, and `test_the_org_cap_holds_across_members` fails.
  - `test_studio_preview_sends_nothing.py` holds the rule that a preview makes
    no outbound call except the live read of `call_read_tool`. **Mutation:**
    add a second client as a new public function of `outbound_guard`, and
    `test_every_public_outbound_function_is_wrapped` fails.
  - `test_studio_mcp_approvals.py` holds `tool_text_hash` and `tool_changed`.
    **Mutation:** make `tool_changed` give false for a failed read of the
    approvals, and `test_no_approvals_make_each_tool_changed` fails.
  - `test_studio_live_preview_r8.py` (R8) holds `call_read_tool` on real
    rows. **Mutation:** drop the scope check, and
    `test_r8_a_denied_grant_or_no_agent_scope_makes_no_call` fails.
  - `test_studio_mcp_approvals_r8.py` (R8) holds the stored approvals.
    **Mutation:** drop the `tool_changed` check in `_class_a_item`, and
    `test_r8_the_live_read_refuses_a_changed_tool` fails. A drop of only the
    pre-check in `call_read_tool` keeps it green, because `_class_a_item`
    still refuses.
- New cases of `test_connection_profiler.py` from the repair of 2026-11-19,
  with their mutations:
  - The org window: `test_a_profile_call_counts_against_the_org_window`.
    **Mutation:** drop the org counter from the new entry, and the case fails.
  - The approval: `test_a_tool_with_no_approval_gets_no_call`. The scope:
    `test_a_server_with_named_agents_gets_no_profile`. **Mutation:** drop the
    check, and its case fails.
  - The schema: `test_the_reduced_schema_keeps_names_and_types_only`.
    **Mutation:** keep a description, a default or an enum, and the case fails.
  - The origin: `test_a_row_that_is_not_a_cloud_mcp_row_gets_no_profile`.
    **Mutation:** drop the transport check, and the case fails.
  - The hash: `test_the_same_shape_gives_the_same_hash`,
    `test_a_changed_shape_gives_a_new_hash` and
    `test_an_approved_shape_with_the_same_hash_saves_no_draft`.
    **Mutation:** put a count in the hash, or drop a field type from it, and
    a case fails.

**The SEM-9 fences (R7).** Each rule of §10.6 names its test, and each test
names the mutation that must turn it red.

- `test_crm_questions_chat_r8.py` (extended, R8). One case for each line of
  the done-when of §10.6:
  - Org B cannot read, accept or decline a proposal of org A. **Mutation:**
    take the org of the accept from the request, and the case fails.
  - The confirm of a member who is not an admin stores a proposal, and the
    plan version does not change. **Mutation:** send the member branch of
    `/reply` to the accept, and the case fails.
  - A member who is not an admin gets 403 on accept. **Mutation:** drop the
    `_GUARDS` of the accept route, and the case fails.
  - A replay of the accept writes one version only. **Mutation:** drop the
    read of the bucket under the plan lock, and a second version appears.
  - The caps: the 11th open proposal, the 6th proposal of one day, and the
    51st measure proposal of an org. **Mutation:** drop one cap, and its
    case fails.
  - Two members propose the same item, and the bucket holds one proposal with
    a count of 2. **Mutation:** key the bucket by the member, and the case
    fails.
  - The propose refuses an item that fails `semantic_problem`. After a field
    leaves the catalogue, the accept gets 422 `stale_measure`.
    **Mutation:** skip the check at the propose, or call `commit` without
    `SEMANTIC_MODEL_ORGS`, and its case fails.
  - The accept writes one change-log row with `source` `chat_proposal`. The
    Undo of a measure restores the version before. The Undo of a synonym
    restores the old field. **Mutation:** drop the `log_change` call, or drop
    the Undo of one area, and its case fails.
  - Before the accept, `org_facts` serves no proposed measure. **Mutation:**
    merge the item into the plan at the propose, and the case fails.
- `test_waiting_seam_r8.py` (extended, R8). The kind `crm_proposal` comes for
  a CRM admin of an org on the three flags. A member who is not a CRM admin
  gets no row. Another org gets no row. With a CRM flag off, the source reads
  `absent`. **Mutations:** register the provider with `admin=True` in place
  of `permission`, and the member case fails. Then an org admin who is not a
  CRM admin gets a row. Drop the flag check of the read, and the `absent`
  case fails.
- `test_crm_questions_chat.py` (extended, hermetic). A source scan finds no
  item check of a measure outside `crm_sync/catalogue.py`. The scan looks for
  a second `def semantic_problem`, a second `_MEASURE_KEYS` and a second
  `LABEL_MAX`. The scan of CRM-Q32-8 names `measure_proposals` too.
  **Mutations:** copy `_measure_problem` into `routes/crm/questions.py`, and
  the scan fails. Read `measure_proposals` in a third module, and the scan of
  CRM-Q32-8 fails.
- `test_tool_schema_diet.py` (unchanged). The pin of `crm_propose_rule` keeps
  the params `scope` and `text`. **Mutation:** add a param to the tool, and
  the pin fails.
- `test_crm_change_history.py` (extended). It lists each new route as logged,
  PENDING or out. **Mutation:** add a write route with no entry, and the
  inventory case fails.
