# Knowledge app: inventory and gap analysis against R1-R3

Date 2026-11-10. Source: `origin/main` (fetched), Metorite board (Knowledge project, 59 open rows read compact, key tasks read in full). Read-only.
Limits: I read specs by heading and grep, not line by line (knowledge_base.md 2275 lines, knowledge_curation.md 1348, knowledge_graph.md 524). I did not run code. I did not open every open task body.

## BLUF

Knowledge is a real, shared product with one read seam, but it fails R2 and R3 today.
- R1 half true: one index (`kb_chunks`) and one seam (`search_company`) exist. Only the chat reads Knowledge through it. Email drafting still reads a private table, `email_knowledge`.
- R2 mostly missing: no project article exists. Pieces exist (entity type `workstream`, `kb_links` target `project`, ALN-4 pins, dark and without UI). No auto-created article, no per-project space, no upload or video.
- R3 weak: an external agent can list and read pages and file a proposal. It cannot search (no full text or vector), attach a file, set a parent, link to a project or append. Door writes other than `propose` are off.

## (a) The specs

Three ACTIVE specs in `project-docs/specs/`: `knowledge_base.md` (WS-61, D104/D105), `knowledge_curation.md` (KC phases), `knowledge_graph.md` (WS-KG, D128/D132). Also touching: `external_agent_access.md` §19 (app door), `project_alignment.md` (ALN pins and rules), `company_context.md`.

| Topic | What the spec says | Where |
|---|---|---|
| Consumers | One tool `search_company` asks each app the member holds. Knowledge is one source. No central copy (D105). Assistant, command bar, My Day and the outside agent share one index | kb §0 items 2, 5, 9; §20 |
| Scoping | Space (kinds company, team, me, agent) with grants in the D12 grammar: `org`, `group:<slug>`, email. Page inherits its space. Draft is author-only. One predicate `visible_pages_clause`, fenced by `test_kb_visibility_one_seam.py`. Permissions: `feature:knowledge`, `knowledge:manage` | kb §5.1, §5.2 |
| Centers | Withdrawn from the surface (D49). Knowledge uses spaces, not Centers | CLAUDE.md §2 |
| Pointer graph (D128) | Edges join records by kind and id, with provenance (EXTRACTED, INFERRED, AMBIGUOUS). No text copies. `kb_links` widened in KG-4. Neighbours added as one step after the ranked hits, visibility rechecked per owning app | kg §0, §3 |
| Connectors | Drive, OneDrive, Dropbox. Linked first, copied only on "Keep a copy". Metadata discovery, then chosen folders get chunks | kb §7, kc §7 |
| Embeddings and retrieval | `kb_chunks`, lexical plus vector, RRF; the one embed door `kb_index/embed.py`; flag `KNOWLEDGE_EMBED` (off, owner flip) | kb §6 |
| Versions and history | `kb_page_versions` with reason and `via` (web, chat, mcp, sync, artifact) | kb §4.2, §5.4 |
| Attachments and media | Planned: file on a page = an artifact via `kb_page_files` (KB-2c, waits for AM-5 and H-322). Text of a file indexed under its page. Video, audio, PDF blocks listed as wave 2 | kb §4.3, §21.7 |
| Import and export | Built (ImportDialog, ExportDialog). Both say images and attachments are not carried yet | UI components |
| Archive and delete | Archive and restore built (ARC-1, ARC-2a). Permanent delete (1619) and the Archived view (1621) open | tasks |
| Agent growth | Three doors: person saves, agent proposes (card is the gate, 14-day expiry), gap recorded. "An agent never publishes" (F2) | kb §8.4, §8.1 |
| Outside agent | Proposed scope `kb:read` (`search_knowledge`, `get_page`); later `kb:write` for `propose_page`. Superseded in practice by the app door (MCP-P1a) | kb §8.6 |

## (b) What is built on main

Tables (infra/postgres): 270 feature, 272 `kb_spaces`, `kb_space_grants`, `kb_pages`, `kb_page_versions`, `kb_links`; 273 `kb_chunks`; 291 icons and favourites; 292 `kb_page_proposals`; 293 `kb_gaps`; 300 `kb_sources`; 301 `kb_entities`; 303 verification; 308 `kb_sync`; 309 `kb_node_access`; 355 `kb_links` widened. FORCE RLS on all. `kb_pages` columns include `origin` JSONB, `source_ref`, `reviewers`, `review_at`, `parent_id`, `position`.

No `kb_page_files` table: the join is described in 272 comments and spec only. I found no migration that creates it.

Routes (`gateway/routes/knowledge/`): core, pages, spaces, search, proposals, proposal_expiry, history, backlinks, home, icon, entities, gaps, maintenance, my_day, sources, flags. Plus `kb_index/` (chunk, embed, index, query, sweep), `kb_sync/`, `kb_sources/google_drive`, `kb_entities/`, `kb_access/`. Frontend: `src/app/knowledge/components/*` (Tiptap editor, subpages, rail, directories, review, gaps, maintenance, sources, import and export).

Editor media: images by URL or address only (ImageDialog asks for an address). No upload, no video, no PDF block on main. KB-2c (task 409) open.

Chat tools: `search_company` (retrieval seam, `COMPANY_SEARCH_ENABLED`), `read_page`, `list_spaces`, `propose_page`, `propose_edit` (`KNOWLEDGE_PROPOSALS`, default off; task 370 open to switch it on). Source: `acb_skills/knowledge_tools.py`, `company_search_tools.py`, `orchestrator/agents.py` lines about 1162-1185.

App door manifest (`acb_common/app_manifests/knowledge.py`, nav pane `/knowledge`, shell JSON `app_manifests/shell/knowledge.json`).

## (c) Who reads Knowledge today

| Reader | How | Verdict |
|---|---|---|
| Chat (Mira) | `search_company` plus `read_page` | One seam, live behind a flag |
| `GET /search/company` | Registry of seven sources; Knowledge source uses `kb_search` and `visible_pages_clause` | The one seam |
| Email drafting | Reads `email_knowledge` (per-mailbox snippets, migration 26) in `routes/email/automation/assistant.py:91` and `drafting.py`. Voice profile writes it too (`voice_profile.py`) | SECOND PATH. Task 391 (KC-16) plans the fold-in, backlog |
| CRM | `crm_sync/note_vectors.py` and `routes/crm/note_search.py` use only the embed door (`kb_index.embed`, `kb_index.index`), not Knowledge content | Shares the embedder, not the content |
| Email embedder | `email_ingestion/email_embeddings.py` on the embed door (KG-2, task 787 todo) | Shares the embedder |
| WhatsApp bot | Plan only (WS-WB, WB-4 task 2655, WB-25 2692). WhatsApp is a `search_company` source | Not a Knowledge reader yet |
| AI Studio | No Knowledge read found in `gateway/studio` | None |
| Projects | `routes/projects/authorities.py` (ALN-4) and `routes/project_rules.py` import `knowledge.core` to check a pinned page's visibility | One-way pointer, dark |
| My Day, shell feed | `routes/knowledge/my_day.py`, `shell/feed.py` (gap and review cards) | Reader of Knowledge state |
| Email files | `routes/email/transport/metorite_files.py` imports `knowledge.core` | Minor |
| External agents | App door ops only (below) | No search |

ONE shared read API? Mostly yes in design (`search_company` plus `visible_pages_clause`). In practice two: `email_knowledge` for drafts, and the MCP door which has no search at all.

## (d) How a project links to knowledge today

- No `project_id` or `space_id` column linking `pm_projects` and `kb_pages`. No "project article".
- `kb_entities` type `workstream` points at `pm_projects.id` (`kb_entities/registry.py` lines 75, 185, 518). `POST /knowledge/entities/{id}/page` writes the page lazily, in the `company` space by default, by a person. Entity routes are off both doors (`_ENTITY_REASON`).
- `kb_links` target kinds include `project` and `task` (a page links out). The "Knowledge about this" section on a project (spec §10.2, `GET /knowledge/about`) is specified, not built as far as I found.
- ALN-4 `pm_project_authorities`: a lead pins a published page as the rules page of a project. Flag `AUTHORITY_PINS_ORGS` (empty), no UI. Refuses a page narrower than the project audience, checked at pin time only.
- ALN-5a `project_rules`: rules may quote a page. Dark behind `PROJECT_RULES_ORGS`.
- Task 992 (LP-5, Projects project, backlog): Docs tab listing Knowledge pages linked to a project, "New doc" makes a page in "the project's space" (no such space kind exists) and links it.
- Task 2291 SP-8 epic contract has a "spec link" field (not read in full).

## (e) MCP surface

Projects (typed tools in `agent_edge/tools.py`): list_tasks, board_stats, get_plan, search_tasks, whoami, list_projects, get_project, list_members, list_tags, get_task, get_task_timeline, get_task_context, get_attachment, my_work, set_task_status, add_task_reference, create_task, update_task, update_tasks, add_comment, update_comment, delete_comment, list_project_updates, post_project_update, claim_task, plus run and task-link tools. No knowledge-specific typed tool.

Knowledge via the generic app door: `list_apps`, `describe_app`, `app_read`, `app_write`. Door ops on main:
- Reads: `list_spaces`, `list_pages` (title filter `q` only), `get_page` (body, versions, links), `list_recent`, `get_page_version`, `list_backlinks`, `list_proposals`, `list_gaps`, `list_maintenance`, `list_sources`, `get_source_tree`.
- Write: `propose` only (kind page or edit).
- Off the door, with a reason: create, patch, move and revert page (waits for MCP-P1b), publish, archive, restore, accept, reject, verify (person only, F2), grants, entities, search (`/knowledge/search` off: "the door calls no model", AD-11).
- Door is dark: `AGENT_DOOR_APPS=knowledge` not on in production; gates in task 1723 (MCP-P1b-3, backlog, in MCP project). Task 1056 (P1b) is marked Done but only P1b-1 (store and card, PR 1695) is evidenced; P1b-2 and P1b-3 are not.

Missing for agent-written research:
1. Search (full text or vector) on the MCP. `search_company` and `get_entity` on MCP is KG-8 (task 793, backlog, blocked by KG-7 in progress). The spec's KB-10 (`kb:read`, "task 403") has no task on the board; KG-8 says it covers the search half.
2. Create page with parent: `ProposalCreate` has only kind, space_id, page_id, base_version, title, body_md, summary. No `parent_id`, no links, no project, no sources.
3. Append to a page (only a whole-body edit proposal, with a `base_version` conflict).
4. Attach file, image, video, PDF (no page files exist at all; MCP-G8 task 413 covers task attachments only).
5. Provenance beyond `via=mcp` and a 1000-char summary. No source list, no agent or run id, no citation of the URL or the file researched.
6. Link a page to a project.
7. Edit of the page that the agent itself created (the proposal flow means a new page is a draft, edits need a new proposal, approved by the member).

## (f) Provenance and review

- Draft status: yes. `kb_pages.state` draft, published, archived. A proposed page is a draft with `origin.kind='agent_proposal'`; its first version has `reason='agent_proposal'`.
- Agent-authored marker: partly. `via` on versions and proposals (web, chat, mcp, sync, artifact), `origin` JSONB. No visible "AI-written" flag on a published page, no agent identity in `origin` beyond the member (the agent acts as the member).
- Approval flow: yes. Proposals list, accept, reject in the Review view (KB-5b). Accept or reject by chat or mcp is refused with 403 (F2). A 14-day expiry (KB-5c). Page verification (`303_kb_page_verification.sql`, a person verifies).
- Gaps: `kb_gaps` cards in My Day when a question had no Knowledge hit (`KNOWLEDGE_GAPS`, task 371 to switch on).
- Edit of an existing page by an agent needs `base_version`, 409 if stale.
- Flags still off: `KNOWLEDGE_PROPOSALS` (370), `KNOWLEDGE_EMBED` (369), `KNOWLEDGE_GAPS` (371).

## (g) Permissions

- Page visibility = space grants (org, group, email). Page inherits the space. A page does not inherit from a project. KC-9 text itself warns that an entity page of a hidden record in the `company` space leaks to all members.
- Agent principal: the external agent acts with the member's live access intersected with token scope (`external_agent_access.md` §7.1). It is not a grantee itself. Grant subjects are only `org`, `group:<slug>` and email. A space kind `agent` exists (`agent:<slug>` reader per spec §8.5), but I found no grant path for a bot principal in the code I read.
- Bots (WhatsApp): WB-25 (2692) designs a service principal with `group:<slug>` grants and a customer-safe flag; the flag is a new Knowledge task (2698, backlog). Fence tests not yet written.
- Personal Drive sources: owner rule vs `include_personal` for MCP is MCP-Q6 in task 1723.

## GAP LIST against R1-R3

Severity: H = blocks the requirement, M = degrades it, L = polish.

| # | Gap | Req | Sev | Evidence | Covered by open task? |
|---|---|---|---|---|---|
| 1 | No MCP search of Knowledge (vector or full text). `list_pages q` matches titles only. `/knowledge/search` is off the door | R3, R1 | H | `app_manifests/knowledge.py` `_SEARCH_CALLS_A_MODEL`; spec kb §8.6 | Partly: KG-8 (793, backlog, blocked by KG-7 792). KB-10 (403) has no task. Door itself needs 1723 |
| 2 | No project article: no link between a project and a page, no auto-created article, no "project space" kind (`SPACE_KINDS` company, team, me, agent) | R2 | H | `routes/knowledge/core.py:87`; no `project_id` in kb tables | Partly: LP-5 (992, Projects project, M2 P2, assumes "the project's space"); KC-8 entity workstream pages (384, backlog). Neither makes the article automatic or the central research home. No task states R2 |
| 3 | No files on a page: no upload, image, video, PDF, audio. `kb_page_files` not built. Import and export drop images | R2 | H | ExportDialog, ImportDialog text; kb §4.3; no migration | 409 (KB-2c, blocked on AM-5 and H-322 (746 in progress)). Video, audio, PDF blocks only as wave-2 line in kb §21.7: no task |
| 4 | An agent cannot create a page with a parent, links, or into a project; no append | R3, R2 | H | `ProposalCreate` fields (proposals.py:96) | No |
| 5 | An agent cannot attach files or media | R3, R2 | H | no route; MCP-G8 (413) is for tasks | 413 is tasks only. Needs a Knowledge task. No |
| 6 | Provenance is thin: `via=mcp` plus summary. No source URLs or files, no agent or run id, no "AI-written" badge on a published page | R3 | M | `origin` JSONB, `via` enum in 272; proposals.py | No. KC-9 (385) facts with quotes are a different layer. SW-98 (1944) is lessons |
| 7 | Door writes other than `propose` are off; approval path incomplete | R3 | M | `_WAITS_FOR_P1B`; 1056 Done but P1b-2/3 not evidenced | 1723 (gates). P1b-2 task not found on the board; check |
| 8 | Door and proposals dark: `AGENT_DOOR_APPS=knowledge`, `KNOWLEDGE_PROPOSALS`, `KNOWLEDGE_EMBED`, `KNOWLEDGE_GAPS` | R3, R1 | M | spec §13; manifest | 370, 369, 371 (backlog); 1723 |
| 9 | Email drafting uses a second store `email_knowledge`; voice profile still writes to it | R1 | M | `assistant.py:91`, `:677`, `voice_profile.py:336` | 391 (KC-16), backlog. It says keep the table until the drafter stops reading it; nothing stops new writes. Add a stop-writing item |
| 10 | No consumer contract for "solutions" (ticketing) or customer-facing reads; per-chunk "customer-safe" flag does not exist | R1 | M | 2698 / 2692 | 2698 (backlog, HIGH) and 2692 (WhatsApp project). Design question: see contradictions |
| 11 | Project visibility is not carried to its article: a page lives in a space with its own grants. ALN-4 checks only at pin time | R2 | M | `projects/authorities.py` docstring | No. Needs rule for article audience (D12 two axes) |
| 12 | "Knowledge about this" section (`GET /knowledge/about`, shared `KnowledgeAbout.tsx`) is specified, not found in code | R2 | M | kb §10.2 | No open task found for it (searched board by words only; may sit in LP-5) |
| 13 | Entity routes (workstream directory, ensure_entity, by-ref) off both doors | R3 | M | `_ENTITY_REASON` | KC-10 (386) and KC-13 (389) decide; `get_entity` in KG-8 (793) |
| 14 | Space grants panel not built (who can see a space) | R1 | M | 836 backlog | 836 |
| 15 | Hostile-content and review load: a research agent can file many drafts; expiry is 14 days; no batch review or per-agent rate cap | R3 | L | proposal_expiry.py | No |
| 16 | Embeddings and Drive text: KG-2 backfill, KC-5b/5c Drive documents not searchable | R1 | L (for R1-R3) | tasks 787, 463, 464 | Yes |

## Contradictions and tensions

1. 2698 / 2692 add a third visibility axis (a `customer-safe` publish flag) next to private/Center/org and `group:<slug>` grants. CLAUDE.md §2 says "two mechanisms, two axes, no third" (D12). It can stay as a content-state flag read by one seam only, but the task must say it never widens `visible_pages_clause` and needs a D-number. Tension, not a defect.
2. KB-6 (404) and spec kb §10.1 copy an artifact's text into a page. D105 and D128 forbid copies of app data in the graph. The spec chose copy with provenance (origin and back link). Consistent with the spec, but against the "pointers, not copies" reading of R1. For project research, prefer a file on the page through the artifact registry (KB-2c) so there is one blob.
3. KC-5b/5c (463, 464) and KC-3 sync copy provider text and chunks. Documented as "Keep a copy" choice. No contradiction with R1-R3.
4. KC-16 (391) keeps `email_knowledge` alive. A second read path remains until the drafter moves, and `voice_profile.py` and `assistant.py` keep writing to it. This is the one real second-path risk.
5. Second path in search: `/knowledge/search` (Knowledge-only) and `/search/company`. Spec says `search_knowledge` is deferred. Fine if it stays a filter of the same seam.
6. KG-14 (1005) and KC-13 (389) move agent NOTES.md and org memory into Knowledge. Aligned with R1. Note that agents then write pages with no review step (facts, not pages), so check against R3's review rule.

## Open Knowledge tasks: needed for R1-R3

(Task numbers from the Knowledge project, open, compact list.)

Directly needed:
- 793 KG-8 search_company and get_entity on the MCP; blocked by 792 KG-7 entity card (in progress)
- 788 KG-3 fused ranking, 791 KG-6 graph step, 790 KG-5 entity resolution, 787 KG-2 embedders (the read quality behind R1)
- 796 KG-12 meetings and attachment text as sources (research content)
- 409 KB-2c files and inline images (R2 core)
- 391 KC-16 email reads Knowledge (R1 example)
- 370 turn on proposals, 369 turn on embeddings, 371 turn on gaps (R3 write path needs 370)
- 836 KB-2r space grants panel (R1 sharing)
- 384 KC-8 entity registry and Directories (workstream pages), 386 KC-10 brief on an entity page
- 404 KB-6 Save to Knowledge (research from chat)
- 2698 customer-safe flag (R1 consumers such as the bot or ticketing)
- 373 customer zero (real content to prove R1)
- 389 KC-13 org memory of every agent; 1005 KG-14 (agent notes into Knowledge)
- 366 (epic) and 783 (epic) as parents

Outside the Knowledge project but needed: 1723 MCP-P1b-3 (door switch-on gates), 992 LP-5 project docs as linked Knowledge pages, 413 MCP-G8 attachments on the MCP, 2692 WB-25 bot principal, 495 MCP full parity (epic), 2478 E5 (parent of LP-5 and 413).

## Open Knowledge tasks: off to the side for R1-R3

- Drive and OneDrive: 374 KC-1, 441 KC-1c, 375 KC-2, 378 KC-5a, 463 KC-5b, 464 KC-5c, 381 KC-6, 382 KC-7, 400 CX-5, 372 Google verification
- Archive and delete: 1619, 1621
- Hardening and tests: 410 H-401, 632 flaky browser tests, 746 H-322 (but it unblocks 409), 921 RLS-IDX-1
- Help articles and policy: 674 HLP-2, 2276, 1768, 1767, 1753
- Curation extras: 385 KC-9 facts, 388 KC-12 gardener, 390 KC-14 tribal capture, 398 CX-3 digests, 399 CX-4 session digests, 394 KB-AI-3, 401 KB-AI-6, 794 KG-10, 795 KG-11 retire Mem0, 948 SH-3 setup checks
- Parents and parking: 361, 362, 363, 364, 365, 2612

## Verified versus inferred

Verified by reading: manifest file, proposals.py header and model, migration 272 columns and CHECKs, spec section headings, authorities.py and project_rules.py docstrings, `tools.py` tool names, tasks 2698, 2692, 793, 391, 389, 409, 1005, 404, 992, 2478, 1723, 1056.
Inferred or not checked: absence of `GET /knowledge/about`, absence of any Knowledge read in Studio, AGENT_DOOR_APPS current production value, whether P1b-2 exists as a task, content of tasks 2276, 2612, 836, 1619, 366, 410.
