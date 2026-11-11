# Prior art for a shared company knowledge base (research dated 2026-11-10)

Evidence labels: **[verified]** = read on the cited page or search snippet today (2026-11-10). **[claimed]** = vendor or third-party statement not checked against primary docs. **[memory]** = from model memory (cutoff June 2026), not re-checked today.

Honest scope note: I ran about 12 searches and 5 fetches. Primary pages I read in full: MCP resources spec (2025-06-18) and one Onyx docs page. The rest is search-result summaries. Anything marked [memory] needs a check before you cite it in a spec.

---

## A. Project/doc-attached knowledge

### Notion [memory unless noted]
- Does: pages in a tree plus databases. A project wiki is a page inside a project database row, or a linked view.
- Model: (1) block tree, page = block; (2) database = collection of pages with typed properties; (3) relation/rollup properties link databases; (4) page parent = page, database or workspace; (5) permissions inherit down the tree, override per page.
- Copy: the "doc is a row with properties" idea. Typed properties (owner, status, review date) on every article. Relations as first-class links.
- Avoid: free-form block JSON as the storage truth (hard to chunk and diff). Permission inheritance with silent overrides.
- MCP: see C.

### Linear [partly verified]
- Does: Project documents live inside a project. Initiative updates are dated posts with a health value. Verified via Linear changelog: initiative updates cross-post to Slack, comments sync; document subscriptions notify on comments, content changes, moves; project leads auto-subscribed (2024-10-10 and 2025-02-13 changelog entries). Posting schedules exist for project and initiative updates [verified, same source]. Initiative updates carry id, body, health, createdAt, author [claimed, via Pipedream].
- Model: (1) Document belongs to project or initiative; (2) update = immutable dated post with health; (3) subscriptions per document; (4) markdown content; (5) resources/links on project.
- Copy: separate "living document" (edited) from "update" (append-only, dated, authored). Subscriptions so owners learn of changes.
- Avoid: nothing found. Exact API for project documents NOT verified.

### Confluence [memory]
- Does: spaces hold a page tree. Pages link to Jira issues. Labels, page properties, restrictions per page.
- Model: (1) space; (2) page with parent_id; (3) versions; (4) labels; (5) page and space restrictions.
- Copy: space = scope boundary. Version history on every page.
- Avoid: deep trees nobody prunes. Per-page restrictions that diverge from space rules (known pain source for permission sync).

### ClickUp Docs, Coda, AppFlowy, BookStack, Wiki.js, Docmost [memory, shallow]
- ClickUp Docs: docs attach to tasks/lists, nest as sub-pages. Copy: attach-to-work-item. Avoid: docs hidden inside a task are hard to find.
- Coda: docs with tables and formulas. Copy: structured tables beside prose. Avoid: logic in docs that no consumer can read.
- BookStack: shelf > book > chapter > page. Fixed 4-level hierarchy, per-role permissions. Copy: a strict, simple hierarchy. Avoid: rigid depth.
- Wiki.js: pages by path, GraphQL API, Markdown. Copy: path addressing. Avoid: weak page-level metadata.
- Docmost, AppFlowy: open-source Notion-like, spaces and pages [memory]. Check licences (AGPL common) before any code reuse.

### Outline (open source) [verified for MCP wrappers only]
- Does: collections contain nested documents. Search, comments, backlinks, archive, restore, trash. Community MCP servers expose archive/restore, collection tree, comments, backlinks (glama listings, today). One MCP wrapper uses resource URIs `outline://collections/{id}` and `outline://documents/{id}` [verified, listing]. A variant adds `ask_wiki` (answer with source links) and `find_related` [verified, listing].
- Model [memory]: (1) collection; (2) document with parent_document_id; (3) revisions; (4) share links; (5) collection permissions per user/group.
- Copy: archive and trash as states, not deletion. Collections as permission scope. Answers that return source links.
- Avoid: wrapping a full CRUD API as MCP 1:1 (see C).

---

## B. Service-desk knowledge

### Zendesk Guide [partly verified]
- Does: public articles plus internal ones; Content Cues uses ML on tickets to propose topics to create and articles to review [verified, Zendesk blog snippets, 2021 early-access coverage; current availability unchecked]. Knowledge Capture app: agents link articles from a ticket and flag or draft new ones from templates; drafts go to a "Captured Knowledge" list for content managers; refined permissions say who may approve and publish [verified, Zendesk blog].
- Model [memory]: (1) article in section in category; (2) user segment controls who views; (3) permission group controls who edits/publishes; (4) draft flag; (5) labels.
- Copy: the ticket-side capture loop. Agent drafts land in a review list. Separate "who sees" (segment) from "who edits" (permission group). Gap-detection from tickets.
- Avoid: Content Cues is plan-gated. Do not depend on ML gap detection for v1.

### Freshdesk solutions [verified via search]
- Does: category > folder > subfolder (up to 5 levels per official doc; lower tiers have fewer) [verified]. Approval workflow is Enterprise-only per official page [verified]. Lifecycle draft, in review, approved, published [claimed, third-party]. Freshservice lets you assign approvers per folder [verified, Freshservice page, different product].
- Model: (1) category; (2) folder; (3) article with status; (4) visibility (all, logged-in, agents, groups); (5) approver per folder.
- Copy: approver per folder. In-review and approved states.
- Avoid: gating approval behind a top tier. A review queue is core, not premium, for agent-written content.

### Help Scout Docs [verified via search]
- Docs API status accepts only `published` or `notpublished`. A draft is separate. Publishing deletes the draft [verified].
- Copy: edits to a live article stay as a draft until published, so customers never see half-edits.
- Avoid: the two-value status. You need draft, in review, approved, rejected.

### Intercom Articles and Fin [verified via search]
- Does: Knowledge hub holds articles, snippets, PDFs, URLs. Native content reaches Fin at once. Internal articles synced from Confluence, Guru or Notion every 24 hours [verified]. Two audience levels: Help Center audience (who sees the article) and AI Agent audience (when Fin may use it). Audience does not override brand scoping, both must match [verified]. Audiences use people and company attributes [verified].
- Model: (1) article/snippet; (2) source (native or synced); (3) brand; (4) help-center audience; (5) AI-agent audience.
- Copy: separate "visible to customers on the site" from "usable by the bot". Per-article bot audience. Performance report per article.
- Avoid: 24-hour sync lag for permission or content changes [verified lag]. "How Fin cites articles": I did not verify the citation format. Treat as unknown.

### Guru [verified via search]
- Does: each card has a verifier (person or group) and a verification interval (30, 90 days, up to "does not expire"). Status shows in search and in AI answers. Unverified cards stay searchable but flagged and land in the verifier's task queue. Unused unverified cards can go to an auto-archive queue. Workspace verification score, 80% suggested target. Verification Health Analytics exists [verified, help.getguru.com].
- Model: (1) card; (2) verifier; (3) interval; (4) last_verified_at, expires_at; (5) status trusted/unverified.
- Copy: this is the best model for freshness. Verify = a person re-attests. Surface the status to every consumer including the bot. Review reminders by queue.
- Avoid: UI side effects reported on community forum: changing the verifier can verify a card [claimed, user reports]. Keep verify a separate explicit action.

### Tettra, Document360 [partly verified]
- Document360: statuses include Published, Published with active draft, Stale, Stale in edit mode [verified, docs.document360.com]. Copy: "published version stays live while a draft is edited". Reminder mechanics not found.
- Tettra [memory]: verification with expiry per page, "verified" badges, Slack Q&A that turns answers into pages. Copy: question-to-page capture. Unverified here.

---

## C. MCP and agent access

### MCP resources feature [verified, spec 2025-06-18]
- Resources are application-driven, read-only context identified by URI. Methods: resources/list (paged), resources/read, resources/templates/list (RFC 6570 URI templates), subscribe and update notifications, list_changed.
- Fields: uri, name, title, description, mimeType, size. Annotations: audience (user/assistant), priority 0.0-1.0, lastModified (ISO 8601).
- Security text: servers MUST validate URIs. Access controls SHOULD be implemented. Permissions SHOULD be checked before operations.
- Implication: use resources for stable addresses (`kb://project/{id}/article/{id}`), tools for search and writes. Resources are chosen by the host app or user, so a model cannot be relied on to fetch them. Put search in tools. [inference]
- Check whether a newer spec revision exists after 2025-06-18. I did not check.

### Notion MCP [verified via search]
- Hosted at mcp.notion.com/mcp, OAuth or API key. Tools prefixed `notion-`: search, fetch, create-pages, update-page, plus move-pages, create-comment, query-data-sources. Third-party listings give 12 to 37 tools, so the live `tools/list` is the authority. Workspace-owned connections list only admin-selected tools [verified]. With no parent given, a private page is created [claimed, third-party] (a safe default: agent output lands private).
- Copy: admin selects which tools an agent connection gets. Default new pages to private.
- Avoid: direct update-page on live pages by an agent.

### Atlassian / Confluence MCP [weak]
- Official remote server: getConfluencePage, getConfluenceSpaces, createConfluencePage, updateConfluencePage, searchConfluenceUsingCql per a Stacklok registry listing [claimed, third party]. OAuth 2.1, Confluence Cloud only [claimed, Toolradar]. Atlassian's own docs not read. Community mcp-atlassian covers Server/Data Center [claimed].
- Lesson: they expose plain create and update. No draft or review layer is evident. Do not copy.

### Obsidian MCP servers [verified via search]
- Many unrelated implementations. Common: read_note, write_note (overwrite, append, prepend), search. Some read the file system directly, some need the Local REST API plugin or the running app. Read returns body, frontmatter, word count, mtime.
- Copy: append and prepend modes as safer writes than overwrite. Frontmatter as metadata.
- Avoid: file-system access with no authorisation or audit.

### "Knowledge base MCP" reference
- I found no canonical reference server with a draft/review model. Outline wrappers and Obsidian servers expose raw CRUD. This is a gap you can fill. [verified absence within my search, not exhaustive]

### How others stop agent pollution [mostly inference, little verified]
- Verified pieces: Zendesk drafts go to a captured-knowledge list for managers [verified]; Freshdesk/Freshservice approval states and per-folder approvers [verified]; Help Scout keeps edits as draft [verified]; Guru unverified flag [verified].
- Not found as a documented agent-specific pattern: quotas, rate limits, provenance fields. Treat these as your own design (see WHAT TO BUILD).

---

## D. Retrieval architecture

- **Glean [verified via search]:** permissions copied from sources at sync, then the user's identity is checked at query time. An ACL naming an unknown group matches nobody (fail closed) [verified, Glean developer docs]. Vendor claims real-time delta sync [claimed]. Third-party says it can also do a live source check [claimed].
- **Amazon Bedrock KB [verified via search]:** same pattern, permission changes not visible until next ingestion job completes. Lag is the known weakness.
- **Onyx [verified, docs page]:** connectors index documents and metadata (owners, update times) into your deployment. Permission sync from source requires Enterprise Edition. Document Sets group documents for agents and filters. Ingestion API for pushes. Local embedding model by default. Hybrid search not stated on the page I read [memory: Onyx uses keyword + vector on Vespa; unverified today].
- **Vespa, Haystack, LlamaIndex [memory]:** Vespa supports filtering by ACL tokens inside the query and hybrid ranking. LlamaIndex and Haystack support metadata filters, so ACL filters go in the retriever, not the prompt. Not re-verified.
- **Patterns to copy:**
  1. Store `acl` tokens on each chunk (user, group, org, public). Filter in the index query. Never ask the model to hide things.
  2. Resolve the caller's group set at request time from your own identity seam.
  3. Fail closed on unknown group.
  4. Chunk by headings and keep `heading_path`, `article_id`, `version`.
  5. Hybrid: BM25 plus vectors, merge by rank fusion. Return citations (article id, version, anchor).
  6. Return freshness and verification status with each hit.
  7. Pointer-based (index without copying) keeps the source of truth but needs ACL sync and has lag. Copy keeps control but doubles storage. For your own first-party articles, no sync issue exists because you own both. Use pointer only for external sources (Drive, Confluence), and carry source ACL plus a sync timestamp.
- **Risk:** stale ACL after a permission change. Mitigation: for first-party content, check ACL against the live row after retrieval (cheap second check), and re-index on change events.

---

## E. Media [mostly memory; not researched today]

- Store the binary in object storage. Keep a metadata row: mime, size, sha256, uploader, duration, width/height, caption, alt text.
- Images: generate thumbnail, store alt text, run OCR or a vision caption for search. Video: transcript (speech-to-text) plus a few keyframes. Index the transcript with timestamps so a hit can deep-link to `t=`.
- Upload vs link: link (YouTube/Loom) costs no storage but breaks, cannot be transcribed without fetching, and may leak a private video. Upload gives control but costs storage and transcoding. Recommendation: allow both; for links store a title, duration and pasted transcript; flag as "external, not indexed" when no transcript.
- Set per-file and per-project quotas. Virus-scan uploads. Strip EXIF. Serve through signed, short-lived URLs checked against the same ACL as the article.
- Never expose raw media to a WhatsApp bot. Hand the bot the transcript text plus a signed link only if the item is customer-safe.

## F. Hygiene

- Verified pattern: Guru owner/verifier + interval + queue + analytics; Document360 Stale status; Zendesk Content Cues for gaps and review lists (all above).
- Build: every article has `owner`, `review_due_at`. A nightly job sets `stale` when past due and notifies the owner. Duplicates: embedding similarity at create time, show "similar to X" before save. Merge = redirect old id to new, keep history. Deprecate = state `deprecated` with `superseded_by`; retrieval excludes by default, shows only on request. Track hit counts and "unhelpful" signals from bots to prioritise reviews.

---

# WHAT TO BUILD

## Ten principles
1. **One read interface, many consumers.** Apps never query tables. They call `kb.search/get/list_children` with a caller identity and a declared consumer.
2. **Access is checked in the query, not the prompt.** ACL tokens on every chunk. Fail closed on unknown group. Same rule for humans, apps and agents.
3. **Two independent axes for customer safety.** `audience` (who may see) and `consumer allowlist` (which apps may use it). Intercom separates site visibility from AI-agent use. Do the same. Default: internal only.
4. **Agents write drafts, never live content.** Every agent write creates a draft or a proposal against a version. A human approves. Approver per space or project (Freshservice pattern).
5. **Provenance is mandatory and immutable.** Who (agent id + human principal), when, which model, which sources, which task. Cannot be edited later.
6. **Version everything; publish a pointer.** Live = pointer to an approved version. Editing makes a new draft while the live one stays up (Document360, Help Scout).
7. **Trust is visible.** Verified/unverified/stale/expired status returns with every search hit so consumers and bots can hedge or refuse.
8. **Project home is a binding, not a copy.** A project has one root article. Sub-articles are children. Items belong to the knowledge space, bound to a project by id (one task store, many lenses logic).
9. **Media is a first-class child with text.** Every upload gets a searchable text (transcript, caption, OCR) or is marked "not searchable".
10. **Limit blast radius.** Per-agent quotas, per-agent tool allowlist, pending cap, kill switch, audit log, no delete tool.

## Minimal data model
(All tables tenant-scoped with `organization_id` and RLS, per your R5.)

- `kb_item`: id, org_id, type, parent_id, slug, title, owner_id, current_version_id, published_version_id, state, audience, visibility_scope (private/center/org/group:slug), created_at, deprecated_at, superseded_by.
  - `type` in: `article`, `project_home`, `sub_article`, `solution` (service answer), `faq_snippet`, `media`, `source_pointer`.
  - `state` in: `draft`, `in_review`, `approved`, `rejected`, `published`, `deprecated`.
- `kb_version`: id, item_id, number, body_md, structured_json, change_note, created_by, created_at, state, content_hash. Immutable once approved.
- `kb_chunk`: id, version_id, heading_path, text, embedding, tsv, acl_tokens[], position. Rebuilt on publish only.
- `kb_binding`: item_id, project_id, role (`home`|`related`|`attachment`). Unique (project_id) where role=`home`.
- `kb_consumer`: id, key (`email_draft`, `ticketing`, `projects`, `chat_internal`, `whatsapp_bot`), allowed_audiences[], requires_verified bool, allowed_types[], max_staleness_days.
- `kb_item_consumer`: item_id, consumer_id, allow bool (per-item override).
- `kb_media`: item_id, storage_key, mime, size, sha256, duration_s, text_status, text_version_id, external_url.
- `kb_provenance`: id, version_id, actor_kind (`human`|`agent`), actor_id, on_behalf_of, client (MCP client name), model, run_id, task_id, source_refs[] (uri, retrieved_at, hash), prompt_hash, created_at. Insert-only.
- `kb_verification`: item_id, verifier_id, interval_days, verified_version_id, verified_at, expires_at, status (`verified`|`unverified`|`expired`).
- `kb_review`: id, version_id, requested_at, reviewer_id, decision (`approved`|`rejected`), reason, decided_at.
- `kb_audit`: append-only log of every read and write by actor and consumer.

## Customer-safe publish state
- `audience` in `internal` (default) | `customer_safe`. Setting `customer_safe` needs a human reviewer, never an agent. Changing the body of a `customer_safe` version drops it back to review.
- A customer-facing consumer (`whatsapp_bot`) gets only items where state=`published`, audience=`customer_safe`, consumer allowed, verification not expired.
- Citations to customers use a public slug, never an internal id.

## Agent-authored states
`draft` (agent can edit own draft) > `in_review` (locked, reviewer assigned) > `approved` (becomes published version) or `rejected` (reason kept, agent may read the reason and open a new draft). Edits to existing live items are `proposal`s: a diff against `published_version_id`, reviewed as a diff.

## Read API (one interface)
`POST /kb/search {q, filters{project_id,type,state}, k}` · `GET /kb/items/{id}` · `GET /kb/items/{id}/children` · `GET /kb/projects/{id}/knowledge`.
Every call carries: caller identity (from session seam, never from the request body), `consumer` key. The server intersects: caller ACL ∩ consumer policy ∩ state rules. Each hit returns: item id, version, heading anchor, snippet, verification status, `last_verified_at`, `staleness`, citation url.

## MCP tool list
Read (no state change):
- `kb_search(query, project_id?, type?, k)`
- `kb_get(id, version?)`
- `kb_list_children(id)`
- `kb_get_project_knowledge(project_id)` returns the home article, the tree, and media with text status.

Write (create only draft/proposal rows, tagged with provenance):
- `kb_create_draft(parent_id?, project_id?, type, title, body_md, sources[])`
- `kb_append(id, section, text, sources[])` appends to the agent's own draft, or makes a proposal on a live item
- `kb_attach(id, file|url, caption)` media starts `text_status=pending`
- `kb_propose_edit(id, base_version, new_body_md, reason, sources[])`

Review (human principal only, never granted to an agent token):
- `kb_approve(version_id, set_audience?)`
- `kb_reject(version_id, reason)`

Also expose MCP **resources** for stable reading: template `kb://project/{project_id}/home` and `kb://item/{id}` with annotations `lastModified` and `priority`; tools stay the way to search. Do not give any tool for delete, publish, or setting `customer_safe`.

## Agent anti-pollution controls
- Token scopes: read-only, write-draft, none. Per-agent allowlist of projects/spaces.
- Quotas: drafts per agent per day, open drafts per agent, max body size, max attachment size. Over quota returns a clear error.
- Required `sources[]`, else the draft is labelled `unsourced` and sorts last in review.
- Duplicate check on create: return "similar item exists" with ids, and require `force_new` reason.
- Rejected drafts feed an agent-level reject rate. High rate auto-lowers the quota.
- Agent drafts never enter any consumer's index until approved.
- Treat all stored text as untrusted input to other agents (prompt-injection). Strip or mark instructions inside retrieved content.

## What is risky
1. **Prompt injection via KB content.** An approved article may carry hostile text that a bot later obeys. Reviewers must see the raw text. Mark retrieved text as data in every consumer prompt.
2. **ACL drift.** If chunks keep stale ACL tokens after a permission change, the bot leaks. Re-index on change and re-check live row on read.
3. **Customer-safe leakage.** One mis-set flag exposes internal research to WhatsApp customers. Default internal, human-only flip, and a test that fails if a bot hit has audience != customer_safe.
4. **Review fatigue.** A flood of agent drafts leads to rubber-stamping. Use quotas and diff-based review.
5. **Media.** Videos and images can contain faces, credentials, personal data. Scan, and keep customer-safe off by default for media.
6. **Provenance trust.** A `model` or `source` field written by the agent can lie. Fill the actor, client and run id server-side from the authenticated session.
7. **Vendor lock on third-party formats.** Fine if you keep Markdown plus JSON as the storage truth.

## Could not verify (needs a follow-up)
- Atlassian official MCP tool list and write behaviour (only third-party listings).
- Linear API fields for project documents and resources.
- How Intercom Fin displays citations.
- Notion data model details, ClickUp, Coda, AppFlowy, Docmost, BookStack, Wiki.js, Tettra specifics (memory only).
- Onyx hybrid search and exact ACL sync mechanics (Enterprise Edition gated), Vespa, Haystack, LlamaIndex permission-aware retrieval (memory only).
- Any newer MCP spec revision than 2025-06-18.
- Media section (E) not researched on the web.
