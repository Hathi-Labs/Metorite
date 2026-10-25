# Knowledge curation — company files in, and a brain that keeps itself current

**Status: ACTIVE, 2026-10-25 (D112).** Owner directive of 2026-10-25, in
chat. The owner accepted every decision of §20 and the default of every
question of §21 the same day, as the spec recommends them. `work_plan.md` §3 records them once, as **D112**.

**KC-1a BUILT 2026-10-25, dark** (branch `kc-1a-drive`, migration 300). It
is the backend half of KC-1. It holds the store lift, the adapter protocol
and the Google Drive adapter. It also holds `kb_sources`, `kb_source_nodes`,
discovery at connect, the source routes and the Google grant rule.

Both flags of KC-1a default to empty. Nobody tested it against live Google. The owner must first register
the redirect URI and the `drive.readonly` scope. KC-1b, the Sources view, is
open.

§20 holds the decisions **D-KC-1** to **D-KC-9**, and §21 holds the owner
questions with their answers. Board row **WS-61** (no new row).
Verified against the code on 2026-10-25, at `origin/main` `f32244eac`.

**KC-8a BUILT 2026-10-25** (branch `kc-8a-entities`, migration 301, dark
under `KNOWLEDGE_ENABLED`). It adds `kb_entities`, `ensure_entity`, the alias
and person resolvers, and the directory routes under the rule of §6.4.
KC-8b and KC-8c stay open.

The Metorite project "Knowledge" (Hathi Labs) holds the build plan: six
epics, #361 to #366, and one task for each slice, #368 to #411 (§23).

**This spec extends two ACTIVE specs and replaces neither.**

- `knowledge_base.md` (D104, D105, D107) owns the Knowledge app: spaces,
  pages, the one index `kb_chunks`, search, proposals, gaps and the editor.
- `company_context.md` (D106) owns what goes into the retrieval seam: the
  stamp, recency, the digest, the connector contract and session digests.

**What this spec owns.** Four things that the two specs above name and do
not design:

1. **Company files in.** Google Drive and OneDrive connect, sync and keep
   their permissions. This takes over KB-3a and KB-7a to KB-7d of
   `knowledge_base.md` §17, and §7 there stays the shape of record.
2. **Filing.** The AI reads what a connected drive holds and proposes where
   each document belongs in Knowledge. A person accepts the plan.
3. **The fact layer and the entity pages.** Metorite learns small, cited,
   dated facts about the company, its products, its people, its workstreams
   and its workflows. Each entity has a page that shows what is known.
4. **The gardener and tribal capture.** A background pass keeps the brain
   current. It merges facts, finds contradictions and stale pages, and
   turns what members say in chats, meetings and tasks into knowledge.

**What this spec does not own.**

- **The app surface, the editor and the index.** `knowledge_base.md` keeps
  them. This spec adds views and columns, and no second index.
- **The member's own memory.** `memory_architecture.md` (WS-9) owns Mem0, and
  `personalization.md` (WS-63, D110) owns the member profile. §14 draws the
  line.
- **The org-facts read seam.** `gateway/org_facts.py` (WS-53 CRM-Q32-5) is the
  one way an agent reads the facts of an org. Knowledge registers one
  provider there, and adds no second read seam (§10.5).
- **The connection registry.** `integrations_console.md` (WS-54, D96) owns
  `integration_connections` and the credential rule.
- **The cards and the delivery.** `my_day_feed.md` (WS-58) and
  `proactive_reach.md` (WS-59) own them. This spec adds card kinds only.

---

## 0. The answer in one screen

1. **The surface is built, and the brain is empty.** Measured on production,
   2026-10-25: no org has one published page, and `kb_chunks` holds 0 rows.
   The editor, the search, the proposals and the gaps exist. Nothing feeds
   them. So the next work is content in, not more editor.
2. **Four layers, from raw to read.** A **source** is a file at the provider,
   and Metorite never changes it. A **document** is its extracted text. A
   **fact** is one small, cited, dated claim. A **page** is what a person
   reads. Each layer cites the layer under it. This is the shape of the
   "LLM wiki" pattern (§4), built in Postgres.
3. **Drive and OneDrive first, Dropbox after.** The owner named the two. That
   changes the default of `knowledge_base.md` §16 Q1 (D-KC-6).
4. **Permissions come in with the files.** Each document keeps the list of
   people who can read it at the provider. The filer may put a document only
   in a space whose readers can all read it at the provider. A document that
   fits no wider space stays visible only to its own readers (D-KC-3).
5. **The AI files, and a person accepts the plan.** The AI proposes a page
   type, the entities, a space and a sensitivity for each document. A
   manager accepts a whole folder at once, and corrects the exceptions. The
   files at the provider never move (D-KC-5).
6. **Entities are keyed to the records that already exist.** A person page is
   the `people` record. A workstream page is the project. A customer page is
   the CRM company. A product has no table, so Knowledge owns it. There is no
   second directory of anything (D-KC-2).
7. **Facts carry provenance and time.** Each fact names the episodes that
   support it, and when it became true and stopped being true. A new fact
   that contradicts an old one closes the old one. Nothing is deleted (D-KC-1).
8. **An entity page has two zones.** The **brief** is a view over the facts,
   drawn for the reader, each line cited. The **body** is what people wrote,
   and only a person publishes it, as D-KB-4 says today (D-KC-4).
9. **The gardener runs at night, on a budget.** It merges duplicates. It
   flags contradictions and stale pages, and it finds missing pages. Its
   findings become proposals and My Day cards. It never publishes body text.
10. **Tribal knowledge comes from where work happens.** Chat sessions,
    meeting summaries, task closures and email threads are episodes. A fact
    from an episode has that episode's audience. A private chat gives a
    private fact. Promotion to the org is a person's act (D-KC-7).
11. **Knowledge is the org's memory for every agent.** The agent reads facts
    and pages through `search_company` and the org-facts seam. The Mem0 scope
    `org:global` stops being written, because it has no tenant (D-KC-8).

---

## 1. What the owner asked for (2026-10-25)

- An AI-enabled knowledge base that takes in the company's existing data and
  organizes it well.
- A connection to the company's Google Drive or OneDrive account. The system
  organizes those files and files them in the right place in Knowledge.
- A knowledge base that maintains itself: what it knows about the company,
  its products, its people, its workstreams and its workflows.
- Tribal knowledge captured as people use Metorite, so the base grows with
  use.
- Sections where AI and people work together to build the base.
- The base as a second source of memory for the agent, and a source of
  insight about the company.
- Research on GitHub for similar projects with many stars (§4).
- The existing tasks on the Hathi Labs account, and the new tasks folded
  into a build plan (§23).

---

## 2. Scope and non-goals

**In scope.**

- The Google Drive and OneDrive adapters, the sync loop and the cursors (§7).
- The permission capture of each document, and the filing rule (§8).
- The classifier, the filing plan and its review (§9).
- The entity registry and the page types (§6).
- The fact and episode tables, and the update step (§10).
- The brief zone of an entity page (§11).
- The gardener (§12) and tribal capture (§13).
- The agent memory bridge (§14).
- The eval for filing and facts (§18).

**Not in scope.**

- **Write-back to a provider.** Metorite never moves, renames or edits a
  file at the provider (D-KB-5, kept). A tidy plan for the drive itself is a
  later owner question (§21 Q5).
- **A graph database.** Facts and links are Postgres rows under FORCE RLS.
  Neo4j stays off, and MT-5 still plans to drop it.
- **OCR.** A scan indexes as its name and metadata, as `knowledge_base.md`
  §2 says. A vision tier is later work.
- **Real-time co-editing.** Unchanged from `knowledge_base.md` §2.
- **A drawn graph view.** `knowledge_base.md` §19 keeps it for later.
- **Facts about a person's private life.** The classifier drops health,
  family, pay and other personal data from the fact layer (§10.4).

---

## 3. What exists today — measured 2026-10-25

| What | Where | What it means here |
|---|---|---|
| Knowledge is live for every org. `KNOWLEDGE_ENABLED=true`, `KNOWLEDGE_ORGS=*`, `COMPANY_SEARCH_ENABLED=true` on production | production `.env`, read 2026-10-25 | The app and the search work today. `KNOWLEDGE_EMBED`, `KNOWLEDGE_PROPOSALS` and `KNOWLEDGE_GAPS` are absent, so they are off |
| The brain is empty. Seven orgs. Five hold one or two spaces. Two hold one draft page each. None holds a published page. `kb_chunks` holds 0 rows | production database, read-only, 2026-10-25 | Content is the bottleneck |
| Spaces, pages, versions, links, chunks, favorites, proposals, gaps | migrations 270, 272, 273, 291, 292, 293 | The page layer exists. This spec adds tables under it and beside it |
| Upload of Word, PDF, PowerPoint and Excel as draft pages, parsed in the browser | KB-AI-1, `lib/importExport/limits.ts` | One file at a time works today. A drive of ten thousand files needs the server path of §7 |
| No source table, no connector, no sync loop for Knowledge | no `kb_sources` in `infra/postgres/` | KB-7a to KB-7d are specified and not built |
| One text extractor, hardened, with no OCR | `packages/acb_skills/acb_skills/attachment_text.py` | The server path reuses it |
| One registry of connections, one encrypted column, one store module | `245_integration_connections.sql`, `gateway/connections/store.py` | A source is one row there. KB-3a lifts the CRM-only side effects first |
| A provider-generic sync with an adapter protocol, cursors, a run lock and a 60 s tick | `gateway/crm_sync/engine.py`, `scheduler.py`, `runs.py` | `gateway/kb_sync/` copies the shape and shares no code |
| One Google OAuth client for the whole product (D-EM-5). Gmail is live in Testing on it | `email_app_master_plan.md`, `263_email_gmail_grant_shared.sql` | Drive joins the same client and the same grant (§7.4) |
| A Microsoft app with `Mail.*` scopes for the Email app. No `Files.*` scope | `routes/email/transport/oauth.py` | OneDrive adds `Files.Read.All` and `Sites.Read.All` to the same app |
| The org-facts read seam, with one provider (the CRM) | `gateway/org_facts.py`, `routes/crm/facts.py` | Knowledge registers the second provider (§10.5) |
| Mem0 `org:global` is one key for every org, with no row level security | `acb_memory/compartments.py:36`, the warning in `org_facts.py:19` | It cannot be the org's memory. §14 retires its writes |
| The digest of a record (summary, questions, atoms) is specified, CX-3 and CX-4 are not built | `company_context.md` §5 and §8 | The fact layer reads atoms as candidates (§13) |
| `email_knowledge`, a small knowledge store per mailbox for reply drafts | `26_email_knowledge_style.sql:24`, 12 Python references | A second knowledge store. §14.3 folds it in later |
| The entity records that already exist: `people`, `pm_projects`, `crm_companies`, `crm_contacts`, `crm_deals`, the workflows of `132_workflows.sql`, `meeting` | `infra/postgres/` | The entity registry points at these (§6) |
| The Hathi Labs task list holds no Knowledge project and no Knowledge task. KB-adjacent work sits in WS-63 and in AI Chat Interface #133 to #140 | Metorite MCP, 2026-10-25 | §23 maps each one |

---

## 4. Research record — what we take from GitHub

Star counts and licences come from the GitHub API on 2026-10-25. **We read
designs and write our own code.** A project under AGPL, BSL, ELv2 or an
enterprise licence gives us ideas only, never code.

### 4.1 The projects

| Project | Stars | Licence | What we take |
|---|---|---|---|
| [Onyx](https://github.com/onyx-dot-app/onyx) | 32.9k | MIT core. **The `ee/` folders, which hold permission sync, are under the Onyx Enterprise License** | One access record per document (user emails, group ids, public). Content sync and permission sync are two jobs on two schedules. Each folder counts as a group |
| [Graphiti](https://github.com/getzep/graphiti) | 32.2k | Apache-2.0 | A fact is an edge with four times: `valid_at`, `invalid_at` (when it was true) and `created_at`, `expired_at` (when we knew it). A contradiction closes the old edge. Episodes are the provenance |
| [mem0](https://github.com/mem0ai/mem0) | 67.7k | Apache-2.0 | The update step: find similar facts, then the model picks ADD, UPDATE, DELETE or NONE. We make DELETE an invalidate |
| [Letta](https://github.com/letta-ai/letta) | 25.2k | Apache-2.0 | "Sleep-time" agents that tidy memory between conversations. Our gardener |
| [Karpathy, "LLM wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | 5k+ gist | n/a | Raw sources stay as they are, the model keeps the wiki, and a schema sets the page types. An ingest, query and lint loop. Good answers become pages |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 40.3k | MIT | Entity and theme retrieval at two levels, and graph updates with no rebuild. A Postgres backend proves the graph fits our database |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | 36.4k | MIT | Community reports answer global questions. The indexing cost is high, so we take it later and only for a large org |
| [STORM / Co-STORM](https://github.com/stanford-oval/storm) | 31.7k | MIT, no push since 2025-09 | Questions from several views, then an outline, then a cited article. Our first-draft path for an entity page |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 92.8k | Apache-2.0 | A chunking template per document type, and chunks a person can see and correct |
| [Docling](https://github.com/docling-project/docling) | 69.5k | MIT | One document tree and a chunker that follows headings. The server parser for complex PDFs, later |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 15.6k | Apache-2.0 | Typed elements and `chunk_by_title`. A fallback idea |
| [LlamaIndex](https://github.com/run-llama/llama_index) | 52.6k | MIT | Key each document on its id and its hash, skip an unchanged hash, delete what vanished |
| [Kotaemon](https://github.com/Cinnamon/kotaemon) | 25.8k | Apache-2.0 | A citation highlights the passage in the source |
| [AnythingLLM](https://github.com/Mintplex-Labs/anything-llm) | 67.5k | MIT | Workspace-scoped documents and a watch that re-syncs a document |
| [Cognee](https://github.com/topoteretes/cognee) | 32.7k | Apache-2.0 | An extract, "cognify" and load pipeline, grounded in an ontology. Our page types are a small ontology |
| [BookStack](https://github.com/BookStackApp/BookStack) | 19.1k | MIT | A fixed hierarchy that people find easy. Our directories |
| [Airbyte](https://github.com/airbytehq/airbyte) | 22.3k | **ELv2** | Its Drive source does not replicate deletes, and permissions are a separate stream. That proves both need their own path |

**Reference only, never code.** Dify's licence forbids multi-tenant use.
Outline is BSL 1.1, which forbids a "Document Service". SiYuan, Logseq,
AppFlowy, Wiki.js, Khoj and basic-memory are AGPL. Docmost core is the one
exception, by D107. MinerU carries an attribution duty for an online
service, so we do not ship it.

### 4.2 The ideas, by theme

- **Layers.** Source, document, fact, page (Karpathy, Graphiti). Each layer
  cites the one below.
- **Permissions before ranking.** A document keeps its readers. Retrieval
  filters first (Onyx, and `knowledge_base.md` §22.1).
- **Facts with time.** Bi-temporal fields and invalidation, never a delete
  (Graphiti).
- **One update step.** Similar facts, then a typed choice (mem0).
- **A night pass.** Lint for contradictions, stale claims, orphans and
  missing pages (Karpathy, Letta).
- **A draft from many sources.** Outline first, then cited sections (STORM).
- **Chunk by type.** A deck by slide, a sheet by row group, a contract by
  clause (RAGFlow).

### 4.3 The sync APIs, as the providers document them

**Google Drive.** `changes.getStartPageToken` gives a cursor. `changes.list`
pages until `newStartPageToken`, oldest first. Shared drives need
`supportsAllDrives` and `includeItemsFromAllDrives`. `includeRemoved` also
reports a file that the account lost access to. The feed is per account.

`changes.watch` opens a push channel for at most 7 days, and a `files`
channel for at most 1 day. A channel never renews itself. A notification has
no body, so the reader still calls `changes.list`. A change of sharing does
not always move `modifiedTime`, so permissions need their own pass.

**Microsoft Graph.** `GET /drives/{id}/root/delta` pages with `@odata.nextLink`
and ends with `@odata.deltaLink`. The feed gives the last state of each item,
and an item can show twice. A folder rename does not list its children, so
the reader tracks items by id, never by path. An expired token answers
`410 Gone`, and the reader enumerates again.

A subscription on a drive root lasts under 30 days. The endpoint must answer
the validation token in 10 s, and each notification in 3 s. Graph can drop
the notifications of a slow endpoint. A dropped one is lost. The header
`Prefer: deltashowsharingchanges` marks an item whose sharing changed.

**The consequence.** A push is only a hint to read the cursor. The stored
cursor is the truth. A reconcile poll runs on a schedule whether a push came
or not.

---

## 5. The layer model

```
 provider (never written)   Metorite holds                              a reader sees
 ────────────────────────   ──────────────                              ─────────────
 Drive / OneDrive file ──▶ kb_source_nodes  (metadata, ids, hash)
                            kb_node_access   (provider readers)  ──┐
                                 │ included                         │ filing rule (§8)
                                 ▼                                  ▼
                            kb_documents     (text) ──────────▶ kb_filings ──▶ a space
                                 │                                  │
 chat · meeting · task ───▶ kb_episodes      (one per source act)  │
 email · page · document         │ update step (§10)                │
                                 ▼                                  │
                            kb_facts ◀── kb_fact_episodes           │
                                 │       (quote, provenance)        │
                                 ▼                                  ▼
                            kb_entities ──────────────────────▶ entity page
                            (keyed to people, pm_projects,          ├ brief  = a view over facts
                             crm_companies, workflows, ...)         └ body   = kb_pages, by people
                                 │
             every layer ──────▶ kb_chunks (one index) ──▶ search_company · org_facts.read
```

Three rules hold the model together.

1. **Everything a reader sees is searched through `kb_chunks`.** A document,
   a fact and a page each write chunks under their own `source_kind`. No
   second vector table exists (D-KB-2).
2. **Every row carries an audience.** A document has its provider readers. A
   fact has the audience of its episodes. A page has its space. The one
   visibility clause filters each read.
3. **Every derived row names what it came from.** A fact names its episodes.
   A filing names its document. A brief line names its fact. A person can
   always ask "why does Metorite say this" and get the source in one step.

---

## 6. The schema — page types and the entity registry

### 6.1 The page types

A page type is a template and a set of expected facts. The org starts with
the types below. An admin may rename a type or add one, and that is the
per-org schema of the Karpathy pattern.

| Type | What it is | Keyed to | Who owns the record |
|---|---|---|---|
| `company` | The org itself: what it does, its customers, its policies | `organization.id` | Settings |
| `product` | A product or a service the company sells | `kb_entities.id` | **Knowledge** (no table exists) |
| `person` | A member or a contact, as the company knows them at work | `people.id` | People |
| `customer` | A company the org sells to | `crm_companies.id` | CRM |
| `workstream` | A body of work with a goal | `pm_projects.id` | Projects |
| `workflow` | How a thing gets done: a procedure, a runbook, an automation | the workflow row, or `kb_entities.id` for a written procedure | Workflows, or Knowledge |
| `topic` | Anything else: a policy, a supplier, a technology, a term | `kb_entities.id` | Knowledge |

### 6.2 The entity registry

`kb_entities` holds one row per thing the brain knows about. It is a pointer,
not a copy. For a `person`, the row holds `ref_table = 'people'` and the id.
The name and the role stay in `people`, and the People app stays their owner.

- **One entity per record.** A partial unique index on
  `(organization_id, ref_table, ref_id)` makes two rows for one record
  impossible.
- **Aliases.** `aliases text[]` holds the other names the documents use:
  "the Penrose", "PX-1", "Penrose Pellet Extruder". The resolver reads them
  first. `personalization.md` §5.4 `resolve_person` keeps owning the member
  aliases, and this resolver calls it for a person.
- **A page per entity, made lazily.** An entity gets a page the first time a
  fact about it is accepted, or when a person opens "Write the page". No
  empty page is made in bulk. One entity has one page for the whole org.
- **Where the page lives** (supervisor decision, 2026-10-25). A product, a
  topic or a written workflow keeps its page in its own space. The page of a
  person, a customer, a workstream, a workflow or the company goes into the
  org's `company` space by default. The caller may name another space that
  they can write in. The page never goes into a `me` space.
- **The pointer comes first.** A directory row of another app's record has
  no entity id until something needs one. "Write the page" calls
  `POST /knowledge/entities/by-ref` with the type and the owner id. The route
  reads the owner row under the rule of §6.4, then calls `ensure_entity`.
  The page route then takes the entity id. A row that the caller cannot see
  gets 404, and nothing is written.

### 6.3 The directories

The Knowledge rail gains one group, **Directories**. It holds a row for each
page type that has at least one entity. The rows are Company, Products,
People, Customers, Workstreams, Workflows and Topics. A rail row shows only
when the caller can see one entity of that type, or more.

A directory is a view over the owner records of one type. The owning app's
rule filters it (§6.4), and the visibility clause filters each page. It is
not a space and it holds no grant (D-KB-7 holds: a view, not a pane).

### 6.4 Who sees a directory row

Most entities have no page, so the page clause cannot gate them. Each type
follows the rule of the app that owns the record. A row shows only when all
four tests pass.

1. **The Knowledge gate.** `KNOWLEDGE_ORGS` and `feature:knowledge`, as the
   router does today.
2. **The owning app's rule.** The code calls the app's own function and never
   copies its SQL.

   | Type | The rule |
   |---|---|
   | `person` | `feature:people`, active people only. The row returns the name only, never an email, skills or HR fields |
   | `customer` | `feature:crm`, `require_crm_enabled` for the org and `require_crm_access(user)` (`routes/crm/access.py`) |
   | `workstream` | `feature:projects`, `resolve_visibility(db, user).project_clause("p.id")`, and `p.personal_owner IS NULL`. A personal project of another member never shows |
   | `workflow` (a row of `workflows`) | `feature:workflows`. The name only, never the graph or a hook token |
   | `company` | Any member who passes test 1 |
   | `product`, `topic`, a written `workflow` | `space_id` passes `readable_spaces_clause` (`routes/knowledge/core.py`) |

3. **The name comes from the owner row,** by a live join. `kb_entities.name`
   is a cache for the Knowledge-owned types only. So a project made private,
   or a person renamed, never leaks an old name. The same join gates alias
   matching, so an alias never reveals a hidden record.
4. **`page_id` returns only when the page passes `visible_pages_clause`.**
   Otherwise the row shows with no page.

**The list starts from the owner table.** A directory reads the owner rows
under the owner rule and LEFT JOINs `kb_entities`. So it is complete and
current with no sync loop, no backfill and no hook in another app. A row of
`kb_entities` is made by `ensure_entity` only when a page, an alias, a filing
or a fact needs the pointer. An owner row that is gone leaves an orphan
entity, which the inner join hides and KC-12 sweeps.

**A background caller has no viewer.** The classifier and the fact pass may
get entity ids from the resolver, never names. A viewer reads them back only
through this rule.

**The person resolver.** The PZ-5 route `resolve_person` is bound to the
calling member and returns a name and an email. The registry maps an email to
`people.id` with `lower(email)`, and reuses `fold()`, `email_in_org()` and
`_directory_tier()` from `routes/admin/assistant_profile_people.py` by import.
`acb_graph.resolver.resolve_person` is a different Person model, and the
registry never calls it.

**Fence (R7).** `tests/unit/test_kc_entities.py` fails on a read of
`kb_entities` in `gateway/kb_entities/` or `routes/knowledge/` that does not
join the owner table through its rule. It also fails on any `INSERT INTO
kb_entities` outside `ensure_entity`.

---

## 7. Connectors — Google Drive and OneDrive

`knowledge_base.md` §7.1 stays the shape of record. It owns the adapter
protocol, the engine, the 60 s tick and the run lock. It also owns the rule
that no session is open during a provider call. This section adds what §7
there did not decide.

### 7.1 Two kinds of source

| Kind | Who connects | What it reaches | Consent |
|---|---|---|---|
| **Member source** | Any member, for their own account | The member's own files and the shared drives the member can open | Google: `drive.readonly` on the one client. Microsoft: delegated `Files.Read.All` |
| **Org source** | An admin with `knowledge:manage` | The shared drives (Google) or the SharePoint sites (Microsoft) the admin picks | Google: the admin's own `drive.readonly`, limited to the shared drives they pick. Microsoft: `Sites.Read.All` with admin consent, one click |

**No customer configures an OAuth app or a key.** Metorite owns both apps, and
the flow is Connect, consent, done. A Google Workspace domain install through
the Marketplace, which reaches every user's drive, is a later slice (§21 Q2).

### 7.2 The sync, step by step

1. **Discover** at connect: the folder tree as `kb_source_nodes`, metadata
   only, capped at 50 000 nodes (`knowledge_base.md` §7.2).
2. **Capture access** for each node (§8.1), in the same pass.
3. **Classify by name** (§9.1), with no text read.
4. **Include** a folder when a person accepts its filing plan (§9.3).
5. **Read changes** every 15 minutes from the cursor. Google uses
   `changes.list` with the page token. Microsoft uses `delta` with the delta
   link. Track by provider id, never by path.
6. **Re-read access** every 6 hours as a slim pass: ids and permissions only.
   A change of sharing can come with no change of content.
7. **Reconcile** once a day: a slim listing of each included folder. A node
   the listing no longer holds is marked `gone`, and its document, chunks and
   facts follow (§10.3).

**Push, later and only as a hint (KC-3b).** A Drive `changes.watch` channel,
renewed every 6 days. A Graph subscription, renewed every 25 days, with a
lifecycle URL. Each push only moves the next read of the cursor forward. The
15-minute read stays.

**The expired cursor.** Google gives a new start token, and Graph answers
`410`. The engine then runs one slim listing, compares hashes, and reads only
what changed. A full text re-read never happens because of a lost cursor.

### 7.3 Extraction and chunks

- **The one extractor** reads Word, Excel, PowerPoint, PDF, ODT, RTF, HTML
  and text. Google Docs, Sheets and Slides export as text or CSV first.
- **The hash decides.** A document whose hash did not change is not read
  again, not chunked again and not embedded again.
- **A chunk template per type.** A deck chunks by slide. A sheet chunks by
  row groups under its header row. Prose chunks by heading. The chunker of
  `kb_index/` gains the three templates, and keeps one table.
- **The caps of `knowledge_base.md` §7.3 hold.** 25 MB, 100 pages, and a
  type the extractor cannot read is a node with a name only.

### 7.4 What a source never does

`knowledge_base.md` §7.5 holds unchanged. Two lines are added.

- It never asks for a write scope. `test_kc_sources_read_only_scopes.py`
  fails on any scope outside the allowlist.
- It never reads a document the connecting account cannot read. The provider
  enforces that, and Metorite does not widen it (§8).

---

## 8. Permissions — the readers come in with the files

### 8.1 What is captured

For each node, `kb_node_access` holds one row per reader at the provider, in
the provider's words:

| `principal_kind` | Google | Microsoft |
|---|---|---|
| `user` | a permission of type `user`, by email | a grant to a user, by email |
| `group` | a Google Group, by email | an Entra or SharePoint group, by id |
| `domain` | type `domain`, only when `allowFileDiscovery` is true | the tenant-wide "people in the org" link |
| `anyone` | type `anyone` | an anonymous link |

A link-only share grants nothing. Onyx reads it the same way, because a
person who never got the link cannot find the file.

### 8.2 From provider readers to members

A resolver turns the provider readers into Metorite subjects in the one
grammar (`org`, `group:<slug>`, an email):

- A `user` email that is a member of the org becomes that email.
- A `domain` share of the org's own verified domain becomes `org`.
- A `group` is expanded only when the org connected a directory that lists
  its members. Until then, a group share proves no reader.
- An address outside the org is kept as a reader at the provider, and it
  never becomes a Metorite subject.

### 8.3 The filing rule

**A document may be filed into a space only when every reader of the space
is a proven reader of the document.** "Proven" means §8.2 resolved the reader.
The rule is checked when a filing is accepted, and again on each access
re-read.

- A file shared with the whole domain fits the `company` space.
- A file shared with four people fits a space whose readers are those four,
  or fewer. It does not fit `company`.
- A file that fits no space stays in its **source view**. That is a
  read-only view of the connected tree. Each member sees it through their
  own provider access.
- When the provider narrows a document's readers, the next access pass finds
  the conflict. The document leaves the wider space, its chunks leave the
  index, and the space manager gets a `filing_conflict` card.

This amends D-KB-3 and the answer to `knowledge_base.md` §16 Q4 for
connected documents only. A page keeps its space's grants and nothing else
(D-KC-3).

### 8.4 Retrieval reads both

The visibility clause gains one branch. A member sees a `document` chunk
only when two things are true. The member can read the document's space,
**and** the member is a proven reader of the document. The second test is the fence that a stale
filing cannot pass. `test_kc_document_visibility.py` proves it: a member of a
space who is not a reader at the provider gets no hit.

---

## 9. Filing — the AI proposes, a person accepts

### 9.1 Pass one, by name — cheap, at connect

For each folder, `tier-decide` reads the path, the file names, the types and
the sizes, 16 folders to a request. It answers four typed fields:

- `page_type`: one of §6.1, or `none`.
- `entities`: names it recognises, matched to `kb_entities` and aliases.
- `space`: the existing space that fits, or a new space it names.
- `sensitivity`: `normal`, `restricted` (contracts, pay, legal, HR) or
  `skip` (personal photos, backups, installers).

No text is read in pass one. A drive of 10 000 files in 800 folders costs
about 50 requests.

### 9.2 Pass two, by text — only for what a person included

After a person includes a folder, `tier-fast` reads each document's first
4 000 tokens. It confirms or corrects the pass-one fields for that document,
and writes a one-line summary. The summary becomes the snippet in search.

### 9.3 The filing plan

The Sources view shows the plan as a tree with the proposal on each folder:

```
 Shared drive "Engineering"                        → space Engineering · 412 files
   ├ Penrose / datasheets          product: Penrose Pellet Extruder    ✓ fits
   ├ Penrose / supplier quotes     restricted · supplier               ⚠ 3 readers, stays in source view
   ├ HR / offer letters            skip (restricted, personal)         – not indexed
   └ Archive 2019                  topic · low confidence              ? needs you
```

- **Accept a folder** accepts every file under it at once.
- **Change** edits the type, the space or the entities, and the change applies
  to the children.
- **Exceptions first.** The plan lists the folders that need a person at the
  top: low confidence, a restricted sensitivity, a filing-rule conflict.
- **Who accepts.** A member with `write` on the target space who is also a
  reader of the folder at the provider. Nobody files what they cannot read.
- **Nothing moves at the provider.** The plan places documents in Knowledge
  only (D-KC-5).

### 9.4 Learning from corrections

Each change a person makes to a plan is a row in `kb_filings` with
`status = corrected`. The next pass-one request carries the org's last 20
corrections as examples. So the filer learns the org's own words with no
training run.

---

## 10. Facts and episodes

### 10.1 An episode

`kb_episodes` holds one row per act that can teach the brain something. The
row is a pointer with an audience:

| `kind` | Points to | Audience |
|---|---|---|
| `document` | `kb_documents.id` | the document's proven readers, intersected with its space |
| `page` | `kb_page_versions.id` | the page's space |
| `chat` | a session digest (CX-4) | the session's participants |
| `meeting` | a meeting summary | the meeting's owner and attendees who are members |
| `task` | a task closure or a comment | the project's visibility |
| `email` | an email insight (`email_insights`) | the mailbox owner, until a member shares it |

### 10.2 A fact

`kb_facts` holds one small claim. Its fields follow Graphiti:

| Field | Meaning |
|---|---|
| `subject_entity_id` | What the fact is about |
| `predicate` | A short verb phrase from a small list per page type, or free text |
| `object_entity_id` or `object_text` | What it relates to, or the value |
| `text` | One sentence a person reads |
| `valid_at`, `invalid_at` | When it became true, and when it stopped. Either may be null |
| `learned_at`, `expired_at` | When Metorite learned it, and when Metorite retired it |
| `confidence` | 0 to 1, from the update step and the number of episodes |
| `state` | `learned`, `confirmed`, `disputed`, `hidden` |
| `audience` | The subject list of the most narrow episode that supports it |

`kb_fact_episodes` joins a fact to each episode that supports it, with the
exact quote. A fact with no quote that the code finds in its episode is
dropped, which is the atom rule of `company_context.md` §5.2.

### 10.3 The update step

For each candidate fact from an episode:

1. **Resolve** the subject and the object to entities, through the aliases.
2. **Find** the 8 nearest facts about the same subject **in the same
   audience**, by the one index.
3. **Ask** `tier-decide` for one typed choice:
   - `ADD` a new fact.
   - `SUPPORT` an existing fact: add the episode, raise the confidence.
   - `UPDATE` an existing fact: close it (`invalid_at`, `expired_at`) and
     add the new one with a link back.
   - `CONTRADICT`: mark both `disputed` and make a finding (§12).
   - `NONE`: nothing new.
4. **Write** in one transaction, with the chunks of the new or changed fact.

**A fact never merges across audiences.** A private fact and an org fact
about the same thing stay two rows. Only a person promotes a fact to a wider
audience, through a proposal. `test_kc_fact_audience.py` is the fence.

**A gone source.** When a document is gone (§7.2 step 7), its episode is
closed. A fact whose last episode closed moves to `expired_at = now` and
leaves the brief. It is not deleted.

### 10.4 What the fact layer never holds

- Health, family, religion, pay, a performance rating, a home address, or a
  personal phone number. The extraction prompt forbids them, and a code check
  on the predicate list drops a fact that names one.
- A secret: a password, a key or a token. The masking rule of
  `company_context.md` §5.2 runs before extraction.
- A fact from a `no_egress` session, or from a mail body that the member did
  not share (the skip rules of `company_context.md` §8.1).

### 10.5 How an agent reads facts

- **Through search.** Each fact writes one chunk with `source_kind = 'fact'`.
  `search_company` finds it with the pages and the documents, under the same
  clause.
- **Through the org-facts seam.** Knowledge registers the provider
  `knowledge` in `gateway/org_facts.py`. It returns the `confirmed` facts the
  member can see, in the `Fact` row shape, with `source = ai` or `admin`.
  ⚠️ `org_facts.read` filters by `q` after each provider returns all its
  rows. Knowledge may hold thousands of facts, so KC-13 passes `q` and a
  limit into the provider. That is a change to WS-53's seam, and KC-13 asks
  for it in its PR.

---

## 11. The entity page — two zones

```
 ┌ Penrose Pellet Extruder ─────────────────────────── product · Engineering ┐
 │ WHAT METORITE KNOWS                              updated 2 h ago · 14 facts│
 │  • Prints with pellets of 1.75 to 4 mm.                 datasheet v3  ✓     │
 │  • Lead time is 3 weeks since 2026-09.        task #212 · chat 12 Oct  ?    │
 │      replaced "2 weeks" (until 2026-09)                                     │
 │  • Disputed: max temperature 280 °C or 300 °C.        2 sources       ⚠    │
 │  [Confirm] [Correct] [Hide]  on each line                                   │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │ (the body: what people wrote, in the TipTap editor, published by a person)  │
 └─────────────────────────────────────────────────────────────────────────────┘
```

**The brief is a view, not a stored text.** It is drawn at read time from
`kb_facts` through the visibility clause. Two members can see two different
briefs of the same page, and neither sees a fact they may not read. So a
brief cannot leak, and it is never stale against its facts.

**The brief rules:**

- A `learned` fact shows with a "?" and its sources. A `confirmed` fact shows
  with a tick. A `disputed` fact shows both claims. A `hidden` fact shows to
  nobody except in the page's history.
- **Confirm** sets `confirmed` and records the member. **Correct** opens a
  one-line edit, and the correction becomes a new fact with the member as its
  episode, which closes the old fact. **Hide** needs `write` on the space.
- A fact below a confidence of 0.5 shows only when the reader opens "Show
  less certain facts".
- The brief shows at most 30 facts, grouped by predicate, newest first.

**The body stays a person's text.** An agent may propose an edit to it
through KB-5a, and only a person publishes. D-KB-4 holds for the body. What
D-KC-4 adds is that a learned fact shows in the brief **before** a person
confirms it, marked as learned. §21 Q3 asks the owner.

**A first draft of the body.** "Draft this page" works on an entity with 10
facts or more. It runs the STORM shape on `tier-balanced`. First it writes an
outline from the page type's template. Then it writes one section per
heading, and each line cites a fact. The draft lands as a KB-5a proposal.

---

## 12. The gardener

One pass per org per night, in the gateway's existing loop, behind
`KNOWLEDGE_GARDENER`. It reads, it writes findings, and it never publishes.

| Check | How | What it makes |
|---|---|---|
| Duplicate entities | Same type, names within a small edit distance, shared aliases, then `tier-decide` | A `merge_entities` proposal |
| Contradictions | Facts marked `disputed`, and pairs of facts on one subject and predicate that disagree | A `contradiction` card to the space manager |
| Stale pages | A page whose body cites a fact that is now expired, or whose review date passed (KB-AI-4) | A `doc_needs_review` card (KB-9b) |
| Missing pages | An entity with 10 or more confirmed facts and no body | A "Draft this page" card |
| Orphans | A page that no page, task or record links to, and nobody opened in 90 days | A line in the Health view |
| Gaps | The open gaps of KB-9a that a new document or fact now answers | Closes the gap, with a link |
| Filing drift | Documents whose access changed since filing (§8.3) | A `filing_conflict` card |

**The budget.** Each run has a cap in credits that the org's admin sets. The
run checks the cap before each model call and stops when it reaches it. The
next night resumes where it stopped. A run with no cap set does not call a
model. Each call writes one usage row with `module_slug = 'knowledge'`.

**The Health view.** A manager sees the last run: what it checked, what it
found, what it spent, and what it skipped for the budget.

---

## 13. Tribal capture — knowledge from where work happens

The brain learns from four feeds. Each one turns an act into an episode, and
the update step of §10.3 does the rest.

| Feed | The trigger | What it reads | Default |
|---|---|---|---|
| Chat sessions | A session digest is written (CX-4) | The atoms of kind `decision`, `procedure` and `number` | On for shared rooms. Off for a member's private chats until the member turns it on |
| Meetings | A meeting summary is written | The decisions and the owners named in the summary | On when the meeting has two members or more |
| Tasks | A task moves to done, or gets a comment with 40 words or more | The title, the closing comment and the project | On for org-visible projects |
| Email | An email insight is written | The insight's atoms, never the body | Off. A member turns it on for one mailbox |
| Documents and pages | A document is included, or a page is published | The text | On |

**Three rules for every feed.**

1. **The audience comes with the episode.** A fact from a private chat stays
   in the member's audience. "Share with the company" on that fact files a
   promotion proposal, which a space manager accepts.
2. **Explicit by default for a member's own words.** The member's private
   chats feed nothing until the member says so. This is the consent model of
   `personalization.md` §7 and D110, kept.
3. **The member sees what was learned from them.** Settings → Assistant
   gets the list "What Metorite learned from you". It shows each fact that
   one of the member's episodes supports, with Hide and Correct.

`company_context.md` CX-6 ("atom to proposal") becomes the chat feed of this
section. A `decision` atom becomes a fact first, and a page proposal only
when the gardener finds a missing page.

---

## 14. Knowledge as the agent's memory

### 14.1 The tiers

| Tier | Holds | Owner | Read by |
|---|---|---|---|
| The session | This conversation | `memory_architecture.md` | The turn |
| The member profile | Name, style, aliases, a few facts about the member | `personalization.md` (WS-63) | Each turn, as a block |
| Member memory | What the agent learned from this member | Mem0, `memory_architecture.md` | Recall, per member scope |
| **Org memory** | **Facts and pages about the company** | **This spec and `knowledge_base.md`** | `search_company`, `org_facts.read`, the index block |

### 14.2 What changes

- **`org:global` stops being written** when `KC_FACTS` is on for an org. The
  tool `save_org_memory` files an org-audience fact through the update step
  instead. `recall_org` reads the `knowledge` provider of `org_facts`. The
  old Mem0 rows stay readable until WS-9 retires them.
- **"Always remember this" about the company** becomes a fact with the
  member as its episode, not a Mem0 row. "Always remember this" about the
  member stays with WS-63.
- **The grounding rule of `knowledge_base.md` §8.2 holds.** The agent asks
  `search_company` first and cites what it used. A fact cites its sources,
  so a fact in an answer is two clicks from the original document.

### 14.3 The parallel stores this folds in later

- `email_knowledge` (the per-mailbox snippets for drafts) becomes a reader of
  Knowledge: the drafter asks `search_company` with the thread's subject.
  Hathi task My Email #32 asks for exactly this. The table stays until the
  drafter no longer reads it, and a later migration drops it (R6).
- `crm_digests` and `email_insights` stay where they are (D105). They feed
  episodes, and they are not copied.

---

## 15. The surface — what a member sees

All of it lives in the Knowledge app and its rail. No new pane (D-KB-7).

| Where | What | Who |
|---|---|---|
| Rail → **Sources** | The connected drives, their tree, the filing plan, the exceptions | A member sees their own sources. A manager sees every source |
| Rail → **Directories** | Company, Products, People, Customers, Workstreams, Workflows, Topics | Each type by its owning app's rule (§6.4) |
| An entity page | The brief and the body (§11) | Through the clause |
| Rail → **Review** (built) | Proposals: pages, edits, merges, promotions, drafts | The reviewers of KB-5a |
| Rail → **Health** | The gardener's last run, conflicts, orphans, spend | `knowledge:manage` |
| Settings → Assistant | "What Metorite learned from you" | The member |
| Chat | "What do we know about Penrose?", "File my Drive", "Who owns the supplier list?" | Every member. Each write asks once, on a card (`chat-first` rule) |

The shell manifest of `knowledge_base.md` §11 gains three jobs: "Connect a
drive", "Review the filing plan" and "See what Metorite knows about…". KC-7
adds them there, and adds no manifest of its own (R9).

---

## 16. Data

Each table carries `organization_id` with FORCE ROW LEVEL SECURITY, the
generated-phase lines and a `test_tenant_coverage.py` entry (R5). Each
migration takes the next free number at build time (R1). Each new column is
nullable with a default (R6).

| Table | Key columns | Slice |
|---|---|---|
| `kb_sources` | `connection_id`, `provider`, `kind` (`member`, `org`), `owner_email`, `state` (`discovering`, `ready`, `failed`, `needs_reconnect`, `disconnected`), `default_space_id`, `account_email`, `account_id`, `node_count`, `discovered_at`, `last_error` | KC-1 (built at KC-1a, migration 300) |
| `kb_source_nodes` | `source_id`, `ext_id`, `parent_ext_id`, `drive_id`, `kind`, `name`, `mime`, `size`, `modified_at`, `content_hash`, `web_url`, `state` (`live`, `gone`), `explored` | KC-1 (built at KC-1a, migration 300) |
| `kb_sync_cursors`, `kb_sync_runs` | as `knowledge_base.md` §7.1 | KC-3 |
| `kb_watch_channels` | `source_id`, `provider_channel_id`, `resource_id`, `expires_at` | KC-3b |
| `kb_node_access` | `node_id`, `principal_kind`, `principal`, `role`, `synced_at` | KC-4 |
| `kb_documents` | `node_id`, `text`, `sha`, `extracted_at`, `parser`, `truncated`, `summary` | KC-5 |
| `kb_filings` | `node_id`, `page_type`, `space_id`, `entity_ids`, `sensitivity`, `confidence`, `status` (`proposed`, `accepted`, `corrected`, `rejected`, `conflict`), `decided_by` | KC-6 |
| `kb_entities` | `type`, `ref_table`, `ref_id`, `name`, `aliases`, `page_id`, `space_id` | KC-8 |
| `kb_episodes` | `kind`, `ref_id`, `audience`, `occurred_at`, `closed_at` | KC-9 |
| `kb_facts` | the fields of §10.2 | KC-9 |
| `kb_fact_episodes` | `fact_id`, `episode_id`, `quote` | KC-9 |
| `kb_garden_runs` | `started_at`, `checked`, `found`, `spent_credits`, `stopped_for_budget` | KC-12 |

`kb_chunks.source_kind` gains `document` (already named in migration 273) and
`fact`. No other change to the index.

**`kb_entities`, two rules.** A Knowledge-owned entity (a product, a topic, a
written workflow) holds `ref_table = 'kb_entities'` and its own id as
`ref_id`. So the unique index covers it too. It must hold a `space_id`.
The page-type list is a code constant in KC-8a, mirrored by a CHECK. A
per-org rename or a new type is a follow-up with no ticket yet.

**`kb_sources` and `kb_source_nodes`, as KC-1a built them.**

- **One Drive source for each member in each org.** The unique index of
  `integration_connections` covers the org, the provider, the app, the scope
  and the member. So one member holds at most one Google Drive connection in
  one org, and `kb_sources` is unique on `(organization_id, connection_id)`.
- **`provider`** is on the source row. Only `connections/store.py` may name
  `integration_connections` (`test_crm_connection.py`), so no route joins it.
- **`account_email` and `account_id`** name the Google account, for the
  same-account check. A consent to another account deletes the old nodes.
  The Drive connection also keeps `account_email` in `provider_meta`, which
  the shared-grant functions of migration 300 read.
- **`explored`** is false for a folder whose children a capped discovery did
  not list. Past 50 000 nodes, discovery keeps the first three levels below
  each root.
- **Disconnect** sets `state = 'disconnected'` and DELETES every node of the
  source, in the transaction that deletes the blob. The source row stays as
  a record, and a reconnect of the same connection reuses it.
- **A node that discovery no longer finds** becomes `gone`, and the tree
  shows it no more.

---

## 17. Flags

Every flag is off by default, names orgs as `KNOWLEDGE_ORGS` does, and is read
inside its module, never in `main.py`.

| Flag | Turns on | Who flips it |
|---|---|---|
| `KNOWLEDGE_SOURCES` | Connect, discovery and the Sources view | Agent, on staging. Owner, on production |
| `KNOWLEDGE_SOURCE_GOOGLE`, `KNOWLEDGE_SOURCE_MICROSOFT` | One provider each | Owner. Google waits for §21 Q1 outside the test users |
| `KNOWLEDGE_FILING` | Pass one and pass two of the classifier | Owner (spends credits) |
| `KC_FACTS` | The update step and the fact tables | Owner (spends credits) |
| `KC_CAPTURE_CHAT`, `KC_CAPTURE_MEETINGS`, `KC_CAPTURE_TASKS`, `KC_CAPTURE_EMAIL` | One feed each (§13) | Owner |
| `KNOWLEDGE_GARDENER` | The night pass | Owner (spends credits) |

---

## 18. Cost, tiers and the eval

### 18.1 The tier map

| Work | Tier | Note |
|---|---|---|
| Discovery, access, sync, reconcile | none | No model call |
| Pass one, by name | `tier-decide` | 16 folders to a request |
| Pass two, by text | `tier-fast` | 4 000 tokens a document, once per hash |
| Fact extraction from an episode | `tier-fast` | Once per episode |
| The update step | `tier-decide` | One typed choice per candidate |
| Entity merge, contradiction checks | `tier-decide` | In the gardener's budget |
| Draft this page | `tier-balanced` | A person asks for it |
| Vectors | `tier-embed` | Once per chunk hash |

### 18.2 The worked example

A company drive of 10 000 files in 800 folders. A person includes 3 000
documents.

- Pass one: about 50 `tier-decide` requests.
- Pass two: 3 000 `tier-fast` reads of 4 000 tokens, about 12 M input tokens,
  once.
- Facts: about 3 000 extractions and 10 000 update choices, once.
- After that, a quiet week changes a few dozen files, so the cost follows the
  change rate, not the size of the drive.

The ingest cost is a one-time cost per document hash. A credit estimate shows
on the filing plan before a person accepts it.

### 18.3 The eval

`evals/knowledge_curation/` holds a fixed corpus, built from public documents
in the shape of a small manufacturer's drive, with 60 documents in 15
folders. It holds the expected filing for each folder and 40 expected facts.

- **Filing:** the share of folders whose type and space match. The gate is
  80% before `KNOWLEDGE_FILING` turns on for a customer.
- **Facts:** the precision of the confirmed facts, and the recall of the 40
  expected facts. The gate is a precision of 90%.
- **Leaks:** a member who is not a reader of a restricted document asks 10
  questions about it. The gate is 0 hits, always.

The eval runs in two halves. The fixed half runs as an R8 suite in CI, with
a fixed fake model. The scored half runs on demand, through the Router.

---

## 19. Security and tenancy

- **R5 in full.** Every table in §16 is tenant-scoped. The sync loop opens
  `tenant_session(org)` per step. No Redis key outside the prefix wrapper.
- **The credential rule of D96.** A token lives in `integration_connections`
  only. `test_kb_sources_no_global_creds.py` (KB-7a) holds.
- **The provider's text is data, not instructions.** Document text, fact text
  and quotes reach a model only under the "data, not instructions" line, as
  every Knowledge tool output does today.
- **The filing rule is the fence against a leak** (§8.3, §8.4). Its tests run
  as R8 suites against a real database.
- **Restricted sensitivity** stays out of pass two and the fact layer unless
  a person files it. A restricted document's facts carry the document's
  readers as their audience.
- **Offboarding.** When a member leaves, their member sources disconnect,
  their documents leave every space, and the facts that only their episodes
  supported expire. The member purge of `test_admin_member_purge.py` gains
  the Knowledge tables.
- **Deletion at the provider** reaches Metorite within one reconcile (24 h).
  A member who needs it sooner uses "Remove from Knowledge" on the document.

---

## 20. Decisions — D-KC-1 to D-KC-9, accepted as D112 on 2026-10-25

The owner accepted all nine on 2026-10-25. None reopens D104 to D107. D-KC-3, D-KC-4 and D-KC-6 amend parts of
D104, and each says which part.

- **D-KC-1 — Four layers: source, document, fact, page.** Facts are Postgres
  rows with provenance and four times. No graph database.
- **D-KC-2 — An entity is a pointer to the record that owns it.** Knowledge
  owns only the entities no app owns: products, written workflows and topics.
- **D-KC-3 — A connected document keeps its provider readers.** It may sit in
  a space only when every reader of the space can read it at the provider.
  *Amends D-KB-3 and §16 Q4 of `knowledge_base.md`, for documents only.*
- **D-KC-4 — An entity page has a brief and a body.** The brief is a view over
  facts, drawn for each reader, and a learned fact shows in it, marked, with
  no click. The body stays person-published. *Amends D-KB-4 for the brief
  only. The body keeps D-KB-4 in full.*
- **D-KC-5 — Filing is virtual.** The AI proposes a place in Knowledge, a
  person accepts the plan, and nothing moves at the provider. *Keeps D-KB-5.*
- **D-KC-6 — Google Drive and OneDrive come first, then Dropbox.** *Amends the
  default of `knowledge_base.md` §16 Q1.*
- **D-KC-7 — A fact has the audience of its episodes and never merges across
  audiences.** Promotion is a person's act.
- **D-KC-8 — Knowledge is the org memory of every agent.** Mem0 `org:global`
  stops being written for an org when `KC_FACTS` is on for it.
- **D-KC-9 — Every paid pass has a flag, a per-org cap and a usage row.** A
  pass with no cap calls no model.

---

## 21. Owner questions — each answered by its default on 2026-10-25 (D112)

| # | Question | The answer (the default) |
|---|---|---|
| Q1 | **File Google's restricted-scope verification for `drive.readonly` now?** It takes weeks and a security assessment. Without it, Drive serves 100 test users | **File it now**, because Drive is now first (D-KC-6). Serve Hathi Labs and Fracktal as test users meanwhile |
| Q2 | **A Workspace domain install** (Marketplace, domain-wide reach) in v1, or member and org sources only? | Member and org sources in v1. The domain install after the first customer asks |
| Q3 | **Does a learned fact show in the brief before a person confirms it?** (D-KC-4) | Yes, marked "learned", with its sources, above a confidence of 0.5 |
| Q4 | **Which capture feeds are on by default** for an org that turns on facts? | Shared chat rooms, meetings with two members, done tasks in org-visible projects, and documents. Private chats and email stay off until the member turns them on |
| Q5 | **A tidy plan for the drive itself** (suggested moves and renames that a person applies at the provider)? | Not in v1. Later as an export, never as a write |
| Q6 | **The default gardener cap** for a new org | No cap, so no model call, until the admin sets one |
| Q7 | **Person pages:** may the fact layer hold work facts about a member (role, skills, what they own) learned from documents and tasks? | Yes, work facts only, and the member sees and can hide each one (§13 rule 3) |

---

## 22. Tickets

Each ticket ships dark behind §17, on its own branch, three or four in flight
at most. The order is the build plan. Phase 1 brings content in. Phase 2
files it. Phase 3 adds facts and entity pages. Phase 4 adds the loop that
keeps the brain current.

### Phase 1 — company files in

| Ticket | What | Done when | Gate |
|---|---|---|---|
| **KC-0** | This spec, the INDEX row, the decisions | The owner accepts or overrules §20 and §21 | **OWNER-GATE** |
| **KC-1** (was KB-3a, KB-7a, KB-7d) | Lift the CRM-only side effects out of `connections/store.py`. The adapter protocol of `knowledge_base.md` §7.1. The Google Drive adapter on the one Google client, for member sources. `kb_sources` and `kb_source_nodes`. Discovery at connect. The Sources view with the tree | A test user connects Drive. The tree shows with no text. Disconnecting Drive leaves Gmail connected, and a test proves it. `save_credential` for Knowledge touches no CRM row. `test_kc_sources_read_only_scopes.py` and `test_kb_sources_no_global_creds.py` hold. R8: org B sees no node of org A | AGENT-SAFE to build. The flag on production: owner |
| **KC-2** (was KB-7c) | The OneDrive and SharePoint adapter on the Email app's Microsoft app. Member sources with delegated `Files.Read.All`. Org sources with `Sites.Read.All` | The KC-1 checks pass on a Microsoft 365 account. A SharePoint site shows as an org source after one admin consent | AGENT-SAFE. The admin consent on a customer tenant: owner |
| **KC-3** (was KB-7b) | The sync loop: `kb_sync_cursors`, `kb_sync_runs`, the run lock, `changes.list` and `delta`, track by id, the expired-cursor path, the daily reconcile, `source_sync_failed`, the CX-5 health columns | A file edited at the provider re-indexes within 15 minutes, with one embed call per changed chunk. A rename of a folder keeps every document. A deleted file leaves the index within one reconcile. A revoked token sets `needs_reconnect` and stops retrying | AGENT-SAFE to build. The loop on production: owner |
| **KC-3b** | Push hints: Drive `changes.watch` and Graph subscriptions, `kb_watch_channels`, renewal, the validation answer | A push moves the next cursor read forward. A missed push changes nothing, because the 15-minute read still runs | AGENT-SAFE |
| **KC-4** | Permission capture: `kb_node_access`, the 6-hour slim access pass, the resolver of §8.2, the filing rule of §8.3, the visibility branch of §8.4, the `filing_conflict` card | `test_kc_document_visibility.py`: a member of the space who is not a reader at the provider gets no hit. A file shared with four people cannot be filed into `company`. Narrowing a share at the provider removes the document from the space within one access pass | AGENT-SAFE. HIGH tier (tenancy and visibility) |
| **KC-5** | Inclusion and extraction: `kb_documents`, the three chunk templates, the hash skip, document chunks in `kb_chunks` | An included folder of 50 files is searchable within one tick. An unchanged file is never read twice. A deck hit names its slide | AGENT-SAFE |

### Phase 2 — the AI files

| Ticket | What | Done when | Gate |
|---|---|---|---|
| **KC-6** | The classifier: pass one by name, pass two by text, `kb_filings`, the correction examples | The eval of §18.3 scores 80% or more on folder type and space. A restricted folder is never proposed for `company`. With `KNOWLEDGE_FILING` off, nothing calls a model | AGENT-SAFE to build. The flip: owner |
| **KC-7** | The filing plan view: exceptions first, accept a folder, change, the credit estimate, the three shell jobs | A manager accepts a 400-file folder in one act. A change to a folder applies to its children. Nobody accepts a folder they cannot read at the provider. The visual review passes | AGENT-SAFE |

### Phase 3 — facts and entity pages

| Ticket | What | Done when | Gate |
|---|---|---|---|
| **KC-8a** | The entity registry, backend: `kb_entities`, the page types, aliases, `ensure_entity`, the resolver, the directory routes under the rule of §6.4, lazy entity pages. It does not depend on KC-1 to KC-7 | Each person in `people` resolves to one entity. A second insert for one record fails. A member without the owning app's access gets no row of that type, and a GET by id is 404. A project the member cannot see, another member's personal project, and a CRM company outside `crm_settings.access` never show in a list, a get or an alias hit. Org B sees no entity of org A | AGENT-SAFE. HIGH tier |
| **KC-8b** | The Directories group in the rail, after KC-8a | A rail row shows only for a type with a visible entity. The visual review passes | AGENT-SAFE |
| **KC-8c** | After KC-6: a filing makes a product entity | The Products directory lists an entity made from a filing | AGENT-SAFE |
| **KC-9** | Facts and episodes: `kb_episodes`, `kb_facts`, `kb_fact_episodes`, extraction from documents and pages, the update step, the quote check, fact chunks | A datasheet v3 that changes a value closes the v2 fact and keeps it in history. A fact with no quote in its episode is dropped. `test_kc_fact_audience.py`: a private fact and an org fact never merge. The eval precision is 90% or more. **The rule of a restricted record (KC-8a re-verify, 2026-10-25):** the page of a person, a customer or a workstream defaults to the `company` space, which every member reads. So a fact about a record that its own app hides from a member must not reach that member through the page. KC-9 gates each fact and the page title of such a record by the owner rule of §6.4, or it files the page in a space whose readers all pass that rule. An R8 test proves that a member without CRM access reads no fact and no title of a customer page | AGENT-SAFE to build. HIGH tier. The flip: owner |
| **KC-10** | The brief zone: drawn per reader, Confirm, Correct, Hide, the confidence floor, the history | Two members with different access see two briefs, and neither sees a fact outside their audience (R8). Correct closes the old fact and adds the new one | AGENT-SAFE. Showing learned facts before a click: §21 Q3 |
| **KC-11** | "Draft this page" for an entity, STORM-shaped, as a KB-5a proposal | A product with 12 facts gets a cited draft. Each line of the draft cites a fact the author can see | AGENT-SAFE |

### Phase 4 — the brain keeps itself current

| Ticket | What | Done when | Gate |
|---|---|---|---|
| **KC-12** | The gardener: the checks of §12, `kb_garden_runs`, the cap, the Health view, the cards | A seeded duplicate makes one merge proposal. A seeded contradiction makes one card. A run stops at its cap and resumes the next night. With no cap, nothing calls a model | AGENT-SAFE to build. The flip: owner |
| **KC-13** | The agent memory bridge: the `knowledge` provider in `org_facts` (with `q` and a limit), `save_org_memory` files a fact, `recall_org` reads the provider, `org:global` writes stop for an org on `KC_FACTS` | An org on `KC_FACTS` writes no `org:global` row. `recall_org` returns that org's confirmed facts and no other org's (R8). `test_org_facts.py` holds | AGENT-SAFE. HIGH tier (memory, tenancy) |
| **KC-14** | Tribal capture: the chat, meeting, task and email feeds of §13, "What Metorite learned from you" | A decision in a shared room becomes a learned fact with the session as its source. A private chat feeds nothing until the member turns it on. A member hides a fact learned from them | AGENT-SAFE to build. Each feed's flip: owner. Depends on CX-4 for chat |
| **KC-15** | The eval of §18.3 as an R8 suite, and the leak gate | The suite prints the filing score, the fact precision and recall, and the leak count. It fails on one leak | AGENT-SAFE. Starts with KC-4, grows with each slice |
| **KC-16** | `email_knowledge` reads Knowledge: the drafter asks `search_company` (My Email #32) | A reply draft cites a Knowledge page about the product the thread names | AGENT-SAFE |
| **KC-17** | Dropbox (was KB-7a's adapter), on the same protocol | The KC-1 checks pass on a Dropbox account | AGENT-SAFE. The Dropbox app registration: owner |

### The order, and what runs beside it

```
 KC-0 ─▶ KC-1 ─▶ KC-3 ─▶ KC-4 ─▶ KC-5 ─▶ KC-6 ─▶ KC-7 ─▶ KC-8 ─▶ KC-9 ─▶ KC-10 ─▶ KC-12
          └▶ KC-2 (beside KC-3)              KC-15 from KC-4 on       ├▶ KC-11
                                                                       ├▶ KC-13
                                                       CX-4 ─────────▶ KC-14
```

Beside it, the open `knowledge_base.md` work keeps its order: KB-5c, KB-AI-2,
KB-AI-4 (verified pages feed the gardener), CX-3 (digests feed the snippet),
KB-4b and KB-10.

---

## 23. How this maps onto the existing tasks

Before 2026-10-25 the Hathi Labs task list held no Knowledge task. The
project "Knowledge" now holds the plan. Phase 0 is #361 (decisions and the
flags that are built but off). Phases 1 to 4 are #362 to #365. The open work
of `knowledge_base.md` and `company_context.md` is #366.

These tasks in other projects touch the same ground:

| Task | What it asks | Where it goes |
|---|---|---|
| AI Chat Interface #133 | Architect the end-to-end memory system | §14 is the org half. WS-9 keeps the rest |
| AI Chat Interface #134 | Separate org memory from private memory | D-KC-7 and D-KC-8, built in KC-13 |
| AI Chat Interface #136 | Recall the right memory each turn | KC-13 for the org tier. WS-63 PZ-2 for the profile |
| AI Chat Interface #137 | Link artifacts into memory | KB-6 (Save to Knowledge), then an artifact is an episode in KC-14 |
| AI Chat Interface #138 | Govern memory: access, retention, deletion | §19 for the org tier. WS-63 PZ-9 for the member |
| AI Chat Interface #116, #139, #140 | Per-agent memory, master memory per member, roll-up of sessions | Not this spec. WS-9, WS-63 and CX-4 |
| Artifact #53 | The upload pipeline for member documents | KB-AI-1 covers pages. KC-5 is the server path |
| My Email #32 | A better knowledge base for drafts | KC-16 |
| Personalization PZ-22 | Review dates on member facts | A sibling pattern. KB-AI-4 does the same for pages. No shared code |

---

## 24. Verification commands

```bash
# This spec (KC-0)
node .claude/hooks/ste-lint.mjs project-docs/specs/knowledge_curation.md
node .claude/hooks/ste-lint.test.mjs

# The fences each slice adds. Each exists from the slice named after it.
uv run pytest tests/unit/test_kc_sources_read_only_scopes.py tests/unit/test_kb_sources_no_global_creds.py -q  # KC-1
uv run pytest tests/unit/test_kc_document_visibility.py -q   # KC-4
uv run pytest tests/unit/test_kc_entities.py -q              # KC-8a
uv run pytest tests/unit/test_kc_fact_audience.py -q         # KC-9

# The R8 suites. A skip is not a pass.
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_kc_sources_r8.py -p no:xdist -rs -q    # KC-1, KC-3
uv run pytest tests/unit/test_kc_access_r8.py -p no:xdist -rs -q     # KC-4
uv run pytest tests/unit/test_kc_entities_r8.py -p no:xdist -rs -q   # KC-8a
uv run pytest tests/unit/test_kc_facts_r8.py -p no:xdist -rs -q      # KC-9, KC-10, KC-13
uv run pytest tests/unit/test_kc_eval_r8.py -p no:xdist -rs -q       # KC-15

# The shared fences that must not move
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_kb_visibility_one_seam.py \
  tests/unit/test_kb_one_index.py tests/unit/test_org_facts.py tests/unit/test_reach_plan.py -q
```

A merged slice updates this header (R4), `knowledge_base.md` §17 where it
replaces a KB ticket, and the board row, in the same pull request.
