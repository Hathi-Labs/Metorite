# Knowledge graph — one graph of pointers behind `search_company`

**Status: ACTIVE, 2026-10-29 (D128, D132).** Board row **WS-KG**, Metorite
epic 783. This spec is KG-0. It owns the slices KG-0 to KG-14. Verified
against the code on 2026-10-29, at `origin/main` `0988e0de9`. The highest
migration on main is 315.

**Built.** KG-1 (`585046f02`), KG-2 (`ccdf3c109`, dark, backfill open) and
KG-3a (`a373b0152`, migration 314) are merged. KG-3b and KG-4 to KG-14 are
not built. §9 holds the state of each slice.

**Read first.** `work_plan.md` D105, D106, D112, D128 and D132.
`knowledge_base.md` §20 owns the read seam. `company_context.md` owns the
stamp, the recency rule and the eval. `knowledge_curation.md` owns the
entity registry and the fact layer. This spec extends all three and replaces
none of them.

---

## 0. The answer in one screen

1. **A graph of pointers (D128.1).** An edge joins two records by their kind
   and id. It holds a relation, a provenance and a confidence. It never holds
   the text of a record.
2. **Two tables, both built.** `kb_links` holds the edges. `kb_entities`
   holds the entities. KG-4 widens `kb_links` from page links to any two
   records. No new graph table and no graph database exist.
3. **One seam (D128.2).** `GET /search/company` and the `search_company` tool
   stay the only retrieval path. The graph adds one step after the ranked
   hits: their neighbours, one hop, inside a token budget.
4. **Visibility on every read (D128.3).** The seam opens each neighbour again
   through its owning app, as the member. A record the member cannot open
   never shows. A link is a reference and never a grant.
5. **Structure first (D128.4).** The first edges come from foreign keys and
   exact identifiers, with no model. A model reads Knowledge pages only, at
   write time, on the cheap tier. Retrieval uses no model.
6. **Provenance on every edge (D128.5).** EXTRACTED, INFERRED or AMBIGUOUS.
   Search never uses an AMBIGUOUS edge. An admin can remove a wrong edge, and
   no sweep writes it again.
7. **Postgres only (D128.6).** FORCE ROW LEVEL SECURITY on `organization_id`.
   Mem0, Graphiti, Neo4j and the empty `acb_graph` tables retire after KG-6
   (D132.4, KG-11).
8. **Measure first.** The KG-1 eval scores every ranking change. The live
   baseline was recall@5 0.400. After KG-3a it is 0.550, with p50 0.87 s.

---

## 1. Scope and non-goals

**In scope.**

- The edges, their writers and their sweeps.
- The graph step of `GET /search/company`, and the entity card `get_entity`.
- The two MCP reads.
- The tenant guard of the memory stores, and their retirement.
- The eval and the latency targets of company retrieval.

**Not in scope.**

- **No second entity registry.** `knowledge_curation.md` §6.2 owns
  `kb_entities` and its one writer, `ensure_entity`. KG-5 adds aliases
  through that registry.
- **No second fact layer.** A claim with a value and a time is a row of
  `kb_facts` (`knowledge_curation.md` §10, KC-9). An edge says only that two
  records relate.
- **No copy of app data (D105.2).** The graph holds ids. Each app keeps its
  rows, its search and its visibility rule.
- **No new app and no new pane.** So this spec has no shell manifest (R9).
  KG-13 draws inside the Knowledge Health view of KC-12.
- **Not MEM-2 or MEM-3.** Both sit in epic 783, but they are memory work,
  not graph work. D132 recorded MEM-3. MEM-2 is the full-text search of
  past chats (D132.3).
- **No WhatsApp embedder.** D115 froze WhatsApp, so KG-2 left it out.

---

## 2. What exists today — verified 2026-10-29

| Piece | Where | State |
|---|---|---|
| The read seam | `apps/services/gateway/gateway/retrieval/company.py` (`GET /search/company`), `retrieval/sources.py` (`REGISTRY`, seven sources, `Hit`) | Live. `COMPANY_SEARCH_ENABLED` is on in production |
| The tool | `packages/acb_skills/acb_skills/company_search_tools.py` (`search_company`) | Live, in the orchestrator core tools |
| Page links | `kb_links`, migration 272. Written by `routes/knowledge/backlinks.sync_page_links` | Live. Page rows only |
| The entity registry | `kb_entities`, migration 301. `gateway/kb_entities/registry.py` and `resolver.py` | Built at KC-8a. Rows exist only where a page or an alias needs one |
| The embed door | `gateway/kb_index/embed.py` | Built. Knowledge uses it behind `KNOWLEDGE_EMBED`. Email uses it since KG-2 |
| Email full text | `email_messages.fts`, migration 314 | Live. The backfill ran (Metorite task 920) |
| The eval | `evals/search_company/` | Built at KG-1 |
| Mem0 | `packages/acb_memory/`, org key `org:global` (`compartments.py`) | Off in production (`MEM0_ENABLED=false`) |
| Graphiti | `packages/acb_memory/acb_memory/graphiti_client.py` | Off in production (`GRAPHITI_ENABLED=false`) |
| The empty `acb_graph` tables | `person`, `customer`, `project`, `task` and `deal` of `packages/acb_graph/acb_graph/models.py` | 0 rows in production on 2026-10-15 (`knowledge_base.md` §20.1). KG-11 counts them again before a drop. The session seam `acb_graph.db` and the meeting tables stay |

---

## 3. The data model

### 3.1 Nodes

A node is a record of an app. It has a kind and an id, written
`<kind>:<id>`. That is the citation of a hit (`knowledge_base.md` §20.3), so
one vocabulary names both.

- **The kinds.** The hit kinds of `sources.py`: `task`, `email`, `person`,
  `lead`, `deal`, `contact`, `company`, `artifact`, `whatsapp` and `page`.
  Two more: `entity` and `meeting`.
- **An entity is a node only when Knowledge owns it.** A product, a topic
  and a written workflow have no other record, so they are `entity:<id>`.
  Every other entity type points at its owner record, and the graph uses that
  record as the node. So one thing never has two nodes.
- **A kind with no reader never shows.** A `meeting` node waits for its
  source (KG-12). Until then the graph step drops it.

### 3.2 `kb_links` — as built, and after KG-4

Migration 272 built the table for page links. KG-4 widens it with an expand
migration (R6). It takes the next free number at build time (R1).

| Column | As built (272) | After KG-4 |
|---|---|---|
| `organization_id` | NOT NULL, FORCE RLS | Unchanged |
| `page_id` | NOT NULL, the page that holds the link | Nullable. A graph row has `page_id` NULL, also when its source is a page |
| `target_kind` | CHECK: `page`, `task`, `project`, `crm_record`, `email_thread`, `meeting`, `artifact`, `url` | The CHECK also takes the node kinds of §3.1. The old values stay valid, and no writer uses `crm_record` or `email_thread` |
| `target_id` | TEXT | Unchanged |
| `source_kind`, `source_id` | — | New, nullable. The other end of a graph row |
| `relation` | — | New, nullable. One value of §4.1. No CHECK: the code constant `RELATIONS` is the list, so a new relation needs no migration |
| `provenance` | — | New, nullable. CHECK: `EXTRACTED`, `INFERRED`, `AMBIGUOUS` |
| `confidence` | — | New, nullable. 0 to 1 |
| `removed_at`, `removed_by` | — | New, nullable. The admin removal of §4.4 |
| `label`, `created_by`, `created_at` | As built | Unchanged |

**Indexes after KG-4.** A partial unique index on (`organization_id`,
`source_kind`, `source_id`, `relation`, `target_kind`, `target_id`) WHERE
`page_id IS NULL` makes a second sweep write nothing. An index on
(`organization_id`, `source_kind`, `source_id`) serves the neighbour read.
`idx_kb_links_target` serves the other direction.

**Why `page_id` stays NULL on a graph row.** Three reads key on `page_id`:
`backlinks.py`, `core.read_links` and `proposal_expiry.py`. A graph row
never reaches them, so their behaviour does not change. A page-authored
link keeps its form, and the graph read takes it as `links_to` (§4.1).

**The tightening half.** A later release sets `provenance` on the old page
rows and may make it NOT NULL. That is a second migration (R6).

### 3.3 `kb_entities`

`knowledge_curation.md` §6.2 and §16 own the table, and this spec adds no
column. KG-5 writes aliases (`kb_entities.aliases`) through a function of
`gateway/kb_entities/registry.py`, beside `ensure_entity`. The fence
`test_kc_entities.py` keeps every read of the table under the owner rule.

### 3.4 What the graph never holds

- The text of a record, a snippet, a name or an address. A name comes from
  the owner row at read time.
- An audience. Visibility is checked at read time, at both ends (§6).
- A claim with a value or a time. That is `kb_facts`.

---

## 4. Edges — relations and provenance

### 4.1 The relations

`RELATIONS` in `gateway/kb_entities/graph.py` (new at KG-4) is the list.
The list is closed, and the database holds no CHECK on `relation`. A test
pins `RELATIONS` to the table below (§9, KG-4). The left side holds the
evidence (§4.3).

| Relation | From → to | Read from | Slice | Provenance |
|---|---|---|---|---|
| `links_to` | `page` → any node | `kb_links` page rows, written by `sync_page_links` | Built (KB-2h) | EXTRACTED |
| `deal_contact` | `deal` → `contact` | `crm_deal_contacts` (migration 144) | KG-4 | EXTRACTED |
| `deal_company` | `deal` → `company` | `crm_deals.company_id` | KG-4 | EXTRACTED |
| `contact_company` | `contact` → `company` | `crm_contacts.company_id` | KG-4 | EXTRACTED |
| `activity_on` | `deal`, `contact` or `company` → the other records that one activity names | `crm_activities` (`deal_id`, `contact_id`, `company_id`) | KG-4 | EXTRACTED |
| `origin` | `task` → `email` or `whatsapp` | `pm_tasks.origin` (migration 211) | KG-4 | EXTRACTED |
| `task_link` | `task` → `task` | `pm_task_links` (migration 146: `source_task_id`, `target_task_id`, `link_type`). `organization_id` is NOT NULL with FORCE RLS since migration 161 | KG-4 | EXTRACTED |
| `attendee` | `meeting` → `person` | KG-4: an id of `meeting.attendee_ids` that is a row of `people`. KG-5: an exact match on the addresses of `meeting.attendees` (migration 99) | KG-4, KG-5 | EXTRACTED, or AMBIGUOUS |
| `participant` | `email` or `whatsapp` → `person` or `contact` | An exact address or phone match. No email or WhatsApp table holds a person or contact id | KG-5 | EXTRACTED, or AMBIGUOUS with two candidates |
| `domain_company` | `email` → `company` | the sender domain against the CRM company domain | KG-5 | EXTRACTED, or AMBIGUOUS |
| `mentions` | `page` or `email` → any node | a code match on aliases and names | KG-10 | INFERRED |
| `relates_to` | `page` → `entity` or a record | the cheap tier, on a Knowledge page save | KG-10 | INFERRED |

Re-verify each column name at dispatch, because the code is the fact.

- `meeting.attendee_ids` has no foreign key, and it dates from the legacy
  schema of `01_schema.sql`. So KG-4 checks each id against `people` before
  it writes an edge.
- `pm_task_refs` (migration 255) holds outside links only: a pull request,
  an issue, a commit, a branch, a comment, a document or a URL. So no task
  to page and no task to artifact edge exists. See Q6.

### 4.2 Provenance and confidence

| Provenance | Meaning | Confidence | Search uses it |
|---|---|---|---|
| EXTRACTED | A stored foreign key or id, or one exact identifier with one candidate | 1.0 | Yes |
| INFERRED | A code match on a name, or a model extraction | Below 1.0. The writing slice states how it sets the value | Yes, at 0.5 or more |
| AMBIGUOUS | Two candidates or more for one identifier. One row for each candidate | 1 divided by the number of candidates | Never. An admin sees it in review |

An exact identifier is an email address, a domain, a phone number or a task
number (D128.4). A free-mail domain never maps to a company. The list is
`CONSUMER_DOMAINS` in `routes/email/mailbox_identity.py`, imported and never
copied.

### 4.3 The evidence rule

An edge names a link that one record holds. That record is one end of the
edge. Or both ends and that record sit under one access rule of one app, as
the CRM records do.

So the graph writes no edge between two records that a third, private
record joins. For example, an email of Meera names a task and a contact. The
graph writes `origin` from the task to the email. It writes `participant`
from the email to the contact. It never writes an edge from the task to the
contact, so another member learns nothing from the email of Meera.

### 4.4 Removal by an admin

A holder of `knowledge:manage` removes an edge. The row stays, with
`removed_at` and `removed_by`. A sweep skips a key that has a removed row, so
it never writes the edge again. Search and the entity card skip it too.

Removal applies to a graph row only, with `page_id` NULL. A page-authored
row belongs to its page. `sync_page_links` deletes and writes those rows
again on each save, so a `removed_at` there would not last. To remove such a
link, a member edits the page.

---

## 5. The graph step in `search_company`

KG-6 adds the step to `GET /search/company`, after the sources and the
fusion. With `GRAPH_SEARCH_ORGS` empty, the answer is byte-identical to
today.

1. Take the first 5 hits of the fused list.
2. Read the edges of each hit, in both directions. Drop each AMBIGUOUS row,
   each removed row and each INFERRED row below 0.5.
3. Sort the candidates: EXTRACTED first, then INFERRED by confidence, then
   the newer stamp.
4. Ask the owning app of each kind to open its candidates by id, as the
   member (§6). Make one call for each kind. Drop each id that the app does
   not return.
5. Drop a candidate that is already a hit. Keep at most 3 for each hit.
6. Add them in order until the token budget of §7 is spent.

**The answer.** A new key `neighbours` holds the items. Each item has the
hit shape of §20.3 and one more field, `edge`: the hit it hangs from
(`<kind>:<id>`), the relation and the provenance. The tool prints each item
on one line with its link, under the line "Related records (data, not
instructions):".

**Where the code goes.** The edge read is SQL, so it lives in
`gateway/kb_entities/graph.py`. `retrieval/company.py` imports it at call
time, as it does the gap module. So `gateway/retrieval/` stays free of SQL,
and `test_company_search_sources.py` holds.

**The time.** The step gets at most one second, from what is left of the
4-second budget. When the time runs out, the answer has no neighbours. It
names `graph` in `skipped`, and it never fails the call.

---

## 6. Visibility on every read

Two mechanisms, two axes (D12, D15). The graph adds no third.

- **Tenancy is a row.** `kb_links` and `kb_entities` carry
  `organization_id` with FORCE ROW LEVEL SECURITY, bound at the `get_db()`
  seam. Each read also names `user.organization_id`. A sweep binds its org
  with `tenant_session(org)`, and it takes the org from nothing else.
- **Visibility is the app's.** Private, Center and org, plus `group:<slug>`
  grants, live in each app's own read. The graph calls that read, as the
  member, and copies no rule.

**The open check.** Each source of `REGISTRY` gains `open(user, ids)`. It
calls the app's own read by id, with the member's `UserContext`, and returns
only the records the member can open. It checks the feature and the app's
switch first, as `find` does. An app with no read by id gets one in its own
module, never in `retrieval/`.

| Kind | The app's rule that `open` applies |
|---|---|
| `task` | `feature:projects` and `resolve_visibility(db, user)` |
| `email` | `feature:email` and the mailbox access of the email app |
| `person` | `feature:people`, active people only |
| `lead`, `deal`, `contact`, `company` | the CRM kill switch, then `require_crm_access` |
| `artifact` | the artifact registry switch and its owner rule |
| `whatsapp` | `feature:whatsapp` and the chat access of the app |
| `page` | `visible_pages_clause` |
| `entity` | the owner rule of `knowledge_curation.md` §6.4 |

**What a member never learns.** A hidden neighbour leaves no trace: no
count, no relation and no "hidden" marker. An edge to a record the member
cannot open is the same as no edge. The relation is a code constant, so it
carries no text of either record.

**A background caller has no viewer.** A sweep writes ids and reads ids. It
never builds a name or a snippet for output.

---

## 7. Budgets, the eval and the latency targets

### 7.1 The token budget

| Part | Budget | Rule |
|---|---|---|
| The hits | About 1,500 tokens | `knowledge_base.md` §20.8: 5 hits for each app, a 400-character snippet |
| The neighbours (KG-6) | 1,500 tokens | A code constant in `company.py`. The count is the characters of the printed lines divided by 4. No tokenizer runs |
| The entity card (KG-7) | 1,500 tokens | The same constant |

So one `search_company` answer stays under about 3,000 tokens. The prompt of
each turn grows by nothing, because the tool runs only when the model calls
it.

### 7.2 The eval

The KG-1 eval of `evals/search_company/` is the fence for every change in
this spec. `company_context.md` §9 holds the rule: a slice runs the eval
before and after, and writes both numbers in its pull request. A change that
lowers recall@5 does not merge.

| Set | Where | Gate |
|---|---|---|
| Synthetic, 50 questions | `evals/search_company/fixtures/synthetic.json` | `--fixture` exits 1 below recall 0.90 |
| Real, 40 questions of one Fracktal member | `~/.metorite/evals/kg1/`, off the repo, staging only | Each slice writes the before and after numbers on epic 783 |

**The measured numbers, staging.**

| Run | recall@5 | p50 | p95 |
|---|---|---|---|
| KG-1 baseline, 2026-10-28 | 0.400 (16 of 40) | 2.49 s | 2.5 to 3.4 s |
| After KG-3a, 2026-10-28 | 0.550 (22 of 40) | 0.87 s | 0.90 s |

CRM still scores 0, because `CRM_ORGS` does not name the org on staging.

**What each slice adds to the eval.** KG-6 adds a neighbour score: a gold id
found among the neighbours. KG-7 adds 10 questions of the form "what is
going on with <customer>". KG-12 adds questions for each new source.

### 7.3 The latency targets

| Measure | Target | Fence |
|---|---|---|
| p50 of `GET /search/company` | Under 1.5 s | The `--fixture` verdict, from KG-3b |
| p95 of `GET /search/company` | Under 3 s, also with the graph step | The `--fixture` verdict, from KG-3b. KG-6 keeps it |
| One source | 1.5 s (`SOURCE_TIMEOUT_S`) | As built |
| The whole answer | 4 s (`TOTAL_BUDGET_S`) | As built |
| The graph step | 1.0 s at most | KG-6 |

---

## 8. Flags

Each flag names orgs. Empty is off, and a star is every org.

| Flag | Default | Opens | Gate |
|---|---|---|---|
| `GRAPH_SWEEP_ORGS` | empty | The KG-4 and KG-5 sweeps, and the code half of KG-10. They spend nothing | AGENT-SAFE. Flip under the `enforcement-flip` grant, staging first |
| `GRAPH_SEARCH_ORGS` | empty | The graph step (KG-6) and `get_entity` (KG-7) | AGENT-SAFE. Same rule |
| `GRAPH_EXTRACT_ORGS` | empty | The model half of KG-10. It spends credits, with a cap for each org and a usage row (D-KC-9) | **OWNER-GATE** |

---

## 9. The slices

The order: KG-4 and KG-9 now. KG-3b after the KG-2 backfill. Then KG-5,
KG-6, KG-7, KG-8, KG-10, KG-11 and KG-13. KG-12 and KG-14 run beside them.
Each slice ships dark, on its own branch. A merged slice updates its state
here in the same pull request (R4).

| Slice | State | Scope | Done when | Fence (R7) | Verify | Tier · gate |
|---|---|---|---|---|---|---|
| **KG-0** (task 784) | This pull request | This spec, its INDEX row, the board note | The spec-auditor returns GO on KG-4. INDEX lists the spec ACTIVE | `test_index_completeness.py`, `test_doc_names_resolve.py` | `uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q` | LOW · AGENT-SAFE |
| **KG-1** (task 785) | ✅ BUILT 2026-10-28, `585046f02` | The eval of §7.2. `company_context.md` §12 holds the as-built record | One command prints the score. The baseline is on epic 783 | `test_search_company_eval.py`, `test_search_company_eval_r8.py` | `uv run python -m evals.search_company.run --fixture` | LOW · AGENT-SAFE |
| **KG-2** (task 787) | ✅ BUILT 2026-10-29, `ccdf3c109`, dark. The backfill and its cost are open | Email embeddings through the embed door, and `scripts/backfill_email_embeddings.py`. `company_context.md` §12 holds the as-built record | No embed call outside `embed.py`. Both orgs hold vectors. The cost is a comment on task 787 | `test_no_embed_outside_door.py`, `test_email_embeddings_backfill_r8.py` | `uv run pytest tests/unit/test_no_embed_outside_door.py -q` | HIGH · The backfill and `email_semantic_search_enabled`: **OWNER-GATE** (approved 2026-10-29) |
| **KG-3a** (task 788) | ✅ BUILT 2026-10-28, `a373b0152`, migration 314 | Email search under FORCE RLS, a stored tsvector. The backfill ran (task 920) | The email source answers inside its 1.5 s | `test_email_fts_column_r8.py` | `uv run pytest tests/unit/test_email_fts_column_r8.py -p no:xdist -rs -q` | HIGH · AGENT-SAFE |
| **KG-3b** (task 788) | Open. After the KG-2 backfill | Hybrid search for each source with vectors, email first. One global sort by the CX-1 score (reciprocal rank fusion, k = 60) in place of `_interleave`, behind `CONTEXT_RECENCY`. A cache of the query embedding for the turn. The `--fixture` verdict gains the p50 and p95 limits of §7.3 | The eval runs with `CONTEXT_RECENCY` off and on, and the pull request writes both. A paraphrase of an email subject finds the mail. With the flag off, the output is byte-identical | `test_company_search_fusion.py` (new), `test_search_company_eval.py` | `uv run pytest tests/unit/test_company_search_fusion.py tests/unit/test_search_company_eval.py -q` | STANDARD · AGENT-SAFE to build. The embed spend: **OWNER-GATE** |
| **KG-4** (task 789) | Open. Next | The expand migration of §3.2. The sweep of §4.1 that writes the KG-4 relations as EXTRACTED, with no model. The edge read and the removal of §4.4 in `gateway/kb_entities/graph.py`. Behind `GRAPH_SWEEP_ORGS`. `participant` moved to KG-5, because no stored id names a participant | R8: each KG-4 relation appears on a seeded org. Org B reads and writes no edge of org A. A second sweep writes 0 rows. A removed edge stays removed after a sweep. The page-link reads give the same answer as before | `test_kg_links_r8.py` (new). `test_kg_links_writer.py` (new): one writer for each form, no graph row with `page_id`, every relation names its evidence end, no write of a relation outside `RELATIONS`, and `RELATIONS` equals the pinned list of §4.1. `test_tenant_coverage.py`, `test_kb_backlinks_r8.py` | `uv run pytest tests/unit/test_kg_links_writer.py tests/unit/test_kg_links_r8.py tests/unit/test_kb_backlinks_r8.py -p no:xdist -rs -q` | HIGH (migration, RLS) · AGENT-SAFE |
| **KG-5** (task 790) | Open. After KG-4 | Exact identifiers to records, with no model: an address or a phone to a person or a contact, a domain to a company. Aliases through the registry. AMBIGUOUS rows for two candidates | R8 covers the exact case, the ambiguous case and the free-mail case. The eval improves on the person and company questions | `test_kg_resolve_r8.py` (new), `test_kc_entities.py` | `uv run pytest tests/unit/test_kg_resolve_r8.py tests/unit/test_kc_entities.py -p no:xdist -rs -q` | HIGH (tenancy, identity) · AGENT-SAFE |
| **KG-6** (task 791) | Open. After KG-3b and KG-4 | The graph step of §5, `Source.open` for each source (§6), the budget and the time of §7. Behind `GRAPH_SEARCH_ORGS`. The graph output never goes through `routes/knowledge/core.read_links`, because that read checks visibility for page targets only | A private task, another member's email, a CRM record without CRM access, a page in a closed space and a project outside a `group:<slug>` grant never appear as neighbours. The eval shows the gain, and p95 stays under 3 s. With the flag off, the output is byte-identical | `test_kg_neighbours_r8.py` (new), `test_company_search_graph.py` (new: the budget, the cap, the order, the flag off), `test_company_search_sources.py` | `uv run pytest tests/unit/test_company_search_graph.py tests/unit/test_company_search_sources.py tests/unit/test_kg_neighbours_r8.py -p no:xdist -rs -q` | HIGH (visibility) · AGENT-SAFE |
| **KG-7** (task 792) | Open. After KG-6 | `get_entity`: a name or a citation in, one entity with its aliases and its neighbours across apps out, each item a link. It reuses the KG-6 read and budget. It joins the core tool pack. No model | On staging, Mira answers "what is going on with <customer>" with one tool call. The eval holds 10 such questions | `test_get_entity_tool.py` (new): no model call, a refusal with no member, the KG-6 visibility | `uv run pytest tests/unit/test_get_entity_tool.py tests/unit/test_kg_neighbours_r8.py -p no:xdist -rs -q` | STANDARD · AGENT-SAFE |
| **KG-8** (task 793) | Open. After KG-7 | `search_company` and `get_entity` on the Metorite MCP, as reads with no approval (D124), with the member's own visibility | An external agent finds a Fracktal email, deal and page on staging. The MCP returns no record the member cannot open | `test_mcp_edge_coverage.py`, `test_mcp_company_search_r8.py` (new) | `uv run pytest tests/unit/test_mcp_edge_coverage.py tests/unit/test_mcp_company_search_r8.py -p no:xdist -rs -q` | HIGH (auth surface) · AGENT-SAFE |
| **KG-9** (task 786) | Open. Next, beside KG-4 | A startup check: the memory layer refuses to start while two orgs or more exist and its store has no org scope. See Q2 for the org key | A test turns `MEM0_ENABLED` on with two orgs, and the memory layer refuses to start | `test_memory_tenant_guard.py` (new), `test_memory_compartments.py` | `uv run pytest tests/unit/test_memory_tenant_guard.py tests/unit/test_memory_compartments.py -q` | HIGH (tenancy) · AGENT-SAFE |
| **KG-10** (task 794) | Open. After KG-5 | `mentions`: a code match (exact and trigram) of known aliases in pages and email bodies. `relates_to`: the cheap tier on a Knowledge page save, in batches. See Q1 | The eval improves. The cost for each 100 pages is a comment on task 794. A removed edge is not written again. With `GRAPH_EXTRACT_ORGS` empty, nothing calls a model | `test_kg_inferred_r8.py` (new) | `uv run pytest tests/unit/test_kg_inferred_r8.py -p no:xdist -rs -q` | STANDARD · The model half: **OWNER-GATE** |
| **KG-11** (task 795) | Open. After KG-6, and KC-13 for the org tools | Move `recall_timeline` and `search_timeline` to the graph. Remove the Graphiti client, the Neo4j profile, the Mem0 client and its settings, the Mem0 routes, and `recall_org` and `save_org_memory` once KC-13 serves org facts. Count the rows first. A later release drops the tables (R6) | No code imports the Graphiti or the Mem0 client. The timeline tools answer from the graph. `MEM0_ENABLED` and `GRAPHITI_ENABLED` leave the settings | `test_no_second_memory_store.py` (new) | `uv run pytest tests/unit/test_no_second_memory_store.py -q` | HIGH (data, migration) · Export or drop the old rows: **OWNER-GATE** |
| **KG-12** (task 796) | Open. After KG-3b | Meetings, calendar events and attachment text as sources, each behind its app's own read | The eval holds questions for each new source, and they pass. Each new source checks its gate | `test_company_search_sources.py`, `test_company_search_r8.py` | `uv run pytest tests/unit/test_company_search_sources.py tests/unit/test_company_search_r8.py -p no:xdist -rs -q` | HIGH (visibility) · AGENT-SAFE |
| **KG-13** (task 797) | Open. After KG-10 | Clusters of each org's graph by code (community detection). The hubs: key accounts, busy projects, main topics. The gardener and an admin report read them | An admin sees the clusters for Fracktal on staging. No model runs | `test_kg_clusters.py` (new): deterministic, no model import, one org only | `uv run pytest tests/unit/test_kg_clusters.py -q` | STANDARD · AGENT-SAFE |
| **KG-14** (task 1005) | Open. After KC-13 | A shared agent's `NOTES.md` moves into org knowledge (D132.5, H-237), with the visibility of the room it learned in. The agent reads its notes back through `search_company`. A one-off moves the old notes, with a count | No shared agent writes `NOTES.md`. A member who cannot see the room cannot read its notes | `test_shared_agent_notes_r8.py` (new) | `uv run pytest tests/unit/test_shared_agent_notes_r8.py -p no:xdist -rs -q` | HIGH (visibility) · AGENT-SAFE |

A test file marked "new" does not exist yet. The slice that names it writes
it, red first (R8).

---

## 10. Verification commands

```bash
# This spec (KG-0)
node .claude/hooks/ste-lint.mjs project-docs/specs/knowledge_graph.md
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q

# The fences that exist today and must not move
uv run pytest tests/unit/test_company_search_sources.py tests/unit/test_company_search_tool.py \
  tests/unit/test_shell_search.py tests/unit/test_search_company_eval.py \
  tests/unit/test_no_embed_outside_door.py tests/unit/test_kc_entities.py -q
uv run python -m evals.search_company.run --check

# The R8 suites. A skip is not a pass.
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_company_search_r8.py tests/unit/test_search_company_eval_r8.py \
  tests/unit/test_email_fts_column_r8.py tests/unit/test_kb_backlinks_r8.py \
  tests/unit/test_kc_entities_r8.py -p no:xdist -rs -q
uv run python -m evals.search_company.run --fixture
```

Each slice adds its own line from §9.

---

## 11. Open questions — each with a recommended default

No decision of record answers these. Do not build against an answer until
the owner or a decision gives one. Build to the default.

| # | Question | Recommended default |
|---|---|---|
| Q1 | KG-10 and KC-9 both read a Knowledge page with a model. One pass or two? | One pass for each page version. The slice that ships first owns the call. When both run, KG-10 writes `relates_to` from each `kb_facts` row that has an `object_entity_id`, and makes no call of its own |
| Q2 | D132.4 retires Mem0. Is step 2 of KG-9, the org key that holds the org id, still worth building? | Build step 1, the startup refusal, now. Build step 2 only if the owner wants a memory flag on before KG-11 lands |
| Q3 | D136.2 fixes the MCP tool set (`list_apps`, `describe_app`, `app_read`, `app_write`). Does KG-8 add two named tools? | Yes. Search spans apps, and D136.2 bounds the growth for each app. MCP-P0 then lists the two as platform tools |
| Q4 | How often does the KG-4 sweep run? | Once a night for each org, with a cursor on the changed rows. A page link keeps its write-time path |
| Q5 | Is there a numeric recall bar for the live set? | No fixed bar. The no-regression rule of `company_context.md` §9 binds, and each slice states the gain it claims |
| Q6 | A task ref that is a URL of a Knowledge page or an artifact: does it give an edge? | Not in KG-4. KG-10 may parse those URLs in its code match and write `mentions` |

---

## 12. Findings — seen while writing, not fixed here

- **`core.read_links` lists a link of any kind but `page` with no
  visibility check.** Its comment says "The other kinds are listed as they
  are." No writer of those kinds exists today, so nothing leaks. A graph row
  keeps `page_id` NULL, so it never reaches that read (§3.2).
- **The KG rows of `company_context.md` §12** are the as-built record of
  KG-1, KG-2 and KG-3a. From today this spec owns every KG slice, and the
  KG-3b row there points here.
