# Artifacts — one library for what members and agents make

**Status: ACTIVE, 2026-10-15. Verified against code on 2026-10-15.** Owner
directive of 2026-10-15: "start building the comprehensive system for managing
artifacts". Board row **WS-60**. §13 lists the six choices the owner must
confirm. Each one has a recommended default, and the first two slices build on
those defaults behind a flag.

**AM-1 is BUILT, 2026-10-15** (branch `ws60-am1-artifact-registry`, migration
268, dark behind `ARTIFACT_REGISTRY_ENABLED`). `acb_memory/artifacts.py` is
the one writer. The blob store registers in the same transaction. The two
read routes are under `/artifacts`. `scripts/backfill_artifacts.py` registers
what exists. The flag is off everywhere.

**What this spec owns.** What an artifact is, where its row lives, and who
can see it. How a member finds it again, and how long it stays. How every
surface reaches the same artifact: chat, Projects, Email, WhatsApp and
outside agents.

**What this spec does not own.**

- **The bytes.** `agent_file_and_memory_framework.md` and
  `agent_persistence_implementation.md` own the blob store, the three folders
  and the disk cache. This spec adds a registry over that store, and no second
  store.
- **The run.** `projects_ai_chat.md` §21.16 owns the run's artifact context
  (H-201) and the thread folders (H-227). This spec reads them and changes
  neither.
- **Task references.** `task_references.md` (WS-57) owns `pm_task_refs`. An
  artifact on a task is one more `kind` of reference there, not a new table.
- **Live artifacts.** Metorite task #167 (an artifact that calls backend
  operations) is a later spec. This one makes the static artifact findable,
  shareable and durable first.
- **The chat surface.** `navigation_shell.md` §7.1 and NS-6 own the assistant,
  its side panel and the Files button.

---

## 0. The answer in one screen

1. **One registry, one store.** Today an artifact is a file in an agent
   directory, mirrored to `agent_blob`. It has no row of its own, so nothing
   can search it, share it, star it or expire it. One new table, `artifacts`,
   gives each file a row with provenance, scope, labels and a text index. The
   bytes stay where they are.
2. **Every write path registers.** One function, `register_artifact`, takes
   every write. That covers the agent's `write_artifact`, a chat upload, an
   Email import, a WhatsApp document and an outside agent. There is no second
   way to make one.
3. **Personal by default, shared by one act.** An artifact starts private to
   the member who made it, as in Claude. A member shares it to a project, to
   the organization, or to named people and groups. The subject grammar is the
   one D12 already defines.
4. **An artifact on a task follows the task.** Attaching is a `pm_task_refs`
   row of kind `artifact`. The artifact then shows to whoever can see the task,
   and it never expires while the link stands.
5. **A library, not a file browser.** The Artifacts app becomes a library.
   It has search, and filters by kind, source, app and person. It has star,
   rename, trash and restore. A provenance strip says who made it, where and
   when. A chat is one filter, not the only door.
6. **Clean-up is a rule, not a chore.** Scratch output expires after a window
   of no use. Starred, shared and attached artifacts never expire. Trash holds
   for 30 days. A nightly sweep purges. A storage meter shows the member and
   the organization what they hold.
7. **Every channel delivers the same artifact.** WhatsApp sends it as a
   document with an "Open in Metorite" button. Email attaches it. The shell's
   command bar finds it. An agent recalls and cites it. An outside agent lists
   it under the member's own access.

---

## 1. What the owner asked for (2026-10-15)

- A comprehensive system for the artifacts the application creates, integrated
  so they are searchable and can be revisited.
- Clean-up when an artifact is no longer needed, so storage is managed.
- A UI that fits the chat interface, the whole app and the other apps that use
  artifacts, including WhatsApp.
- Each member manages their own artifacts.
- A clear line between user-centric and organization-centric artifacts, so
  several people can use one generated artifact.
- Look at how Claude and ChatGPT manage artifacts.
- Read the Artifact project in Metorite, and add the missing tasks there.

---

## 2. What exists today (verified 2026-10-15)

| Thing | Where | What it does | Gap |
|---|---|---|---|
| Bytes of a file | `agent_blob` (`infra/postgres/71_agent_blob_store.sql`, `136_agent_blob_instance.sql`) | Current content, keyed `(agent_name, instance, path)`, BYTEA. **Checked on production 2026-10-15:** it carries `organization_id` and FORCE RLS, from the generated MT-1b files. 32 rows, 2.4 MB, 34 history rows | No numbered migration says so. The registry joins on the store key inside the same tenant session |
| Versions | `agent_file_history` | One metadata row per unique version. **No bytes** | An old version cannot be opened |
| Write path | `packages/acb_skills/acb_skills/write_artifact.py` `mirror_to_blob_store` | Disk write, blob mirror, history row, `artifact_created` event to the chat | No row that says what the file is or who may see it |
| Upload path | `routes/workspace.py` `POST /agent/workspace/{sid}/upload` | 25 MB cap, extension allowlist, lands in `inputs/<thread slug>/` | Same gap |
| Chat card | `MessageBubble.tsx`, `ArtifactCard.tsx` | Links by session id and path, shows a short sha | No star, share or "open in library" |
| Viewer | `ArtifactViewerModal.tsx`, `lib/artifactKind.ts` | markdown, html, react, code, image, pdf, docx, text, binary. Edit and save for text kinds | CSV renders as code. No rename |
| Artifacts app | `src/app/artifacts/page.tsx` (NS-6 slice 6c, live 2026-10-13) | Tab 1: the member's chats and the files of one chat. Tab 2: the agents' own dirs, client-side filter and name search | No cross-chat search, no star, no trash, no sharing, no provenance |
| List routes | `GET /agent/workspace/{sid}`, `GET /agent/artifacts?agent=&category=` | Walk the disk, cap 4000 files | No paging, no text search, no filter but agent and folder |
| Scoping | `gateway/rooms.py`, H-201, H-227 | A chat's files follow the chat room. The global routes serve only the member's own `u:<email>` dirs | No per-artifact grant. No org or project scope for a file |
| Email | `ArtifactAttachPicker.tsx`, `routes/email/transport/send.py` | Picks a file from the `email-assistant` dir only | Cannot attach a file another chat made |
| Tasks | `pm_task_attachments` and `attachments` (migration 52, 150) | A pasted file on a task, a disk path | Not linked to the chat file that may be the same bytes |
| Task references | `pm_task_refs` (migration 255, WS-57) | Links, PRs, commits, docs with a `kind` and a `via` grammar | No `artifact` kind yet |
| WhatsApp | `whatsapp_ui.py` `WITHHELD_TOOLS`, `bot_run.py` | Withholds `write_artifact`. Sends only a chart PNG | No `send_document`. An inbound document is not kept |
| Search | `routes/shell/search.py` `PROVIDERS` | One provider per app (tasks, email, people, crm) | No artifacts provider |
| Retention | `purge_thread_files` on chat delete, `.run-data` sweep | Deleting a chat purges its files at once | No expiry, no trash, no quota on the blob mirror, no cap on history |
| Entitlement | `feature:artifacts`, given to every role by migration 207 | Gates the pane | The gateway file routes check no feature |

Three facts shape the design. **First,** the store already has the right key:
`(agent_name, instance, path)` plus a sha. A registry can point at it with no
copy. **Second,** every write already passes one function,
`mirror_to_blob_store`, so one hook registers every artifact. **Third,** the
reference seam of WS-57 already carries a source grammar (`ui`, `chat`,
`text`, `mcp:<client>`). So an artifact on a task needs one new `kind`, not a
table.

---

## 3. What Claude and ChatGPT do (researched 2026-10-15)

The brief is condensed. Each line names its source.

- **Claude** makes an artifact for "significant and self-contained" content.
  It lists every artifact in a sidebar space with a filter. An artifact
  "starts private to you". Sharing has three audiences, "Only you", "Anyone
  at [org]" and "Anyone with the link", crossed with view, comment and edit
  roles. An Owner switch allows or refuses external links. A viewer can
  "customize" a shared artifact into their own copy
  (support.claude.com articles 9487310 and 9547008).
- **ChatGPT** retired Canvas in 2026 and keeps outputs as editable blocks in
  the chat. The **Library** holds every uploaded and created file: search with
  filters, folders, Favorites, a storage meter, and "Recently deleted" with
  restore and "Delete forever". Deleting a chat does not delete a file that
  is in the Library. Quotas run from 500 MB to 100 GB by plan
  (help.openai.com articles 20001052, 8809935, 8555545).
- **Gemini Canvas** has a version stepper and "Show recent changes". A shared
  link is a snapshot, and "Copy Canvas" forks it
  (support.google.com/gemini/answer/16047321).
- **Copilot Pages** turns an answer into a co-edited page that lives in a
  library until deleted.

**What we take.** From Claude: private by default, the audience and role
model, the org switch for external links, and "make a copy". From ChatGPT:
the library with search and favourites, the storage meter, the trash with a
timed purge, and "delete the chat, keep the file". From Gemini: the version
stepper.

**What we do not take.** Anonymous public links, in the first release.
External sharing is an owner switch, off (§13 Q5).

---

## 4. The model

### 4.1 One registry table, `artifacts`

A row for every file a member or an agent made or uploaded under `inputs/` or
`outputs/`. `agent-data/` is the agent's knowledge, and it is not an artifact.

| Column | Type | Meaning |
|---|---|---|
| `id` | UUID | The artifact. The URL is `/artifacts/<id>` |
| `organization_id` | UUID, RLS | The tenant (R5) |
| `owner_email` | TEXT | The member whose run, upload or channel made it. Only the owner shares, trashes or renames, until AM-3 adds `edit` |
| `agent_name`, `instance`, `path` | TEXT | The blob key. UNIQUE. The bytes come from `agent_blob` |
| `sha256`, `size`, `mime_type` | | Copied from the store at each write |
| `version` | INT | Counts the writes to this path. The history rows hold the sha of each |
| `kind` | TEXT | `document`, `data`, `html`, `react`, `image`, `pdf`, `code`, `archive`, `other`. One classifier, moved from `lib/artifactKind.ts` into a shared module both halves read |
| `title` | TEXT | From the first heading or the file name. A member may rename it. The path does not change |
| `made_by` | TEXT | `member`, or the agent name |
| `via` | TEXT | `chat`, `upload`, `email`, `whatsapp`, `import`, `app`, `mcp:<client>`. The WS-57 grammar, widened |
| `session_id`, `run_id` | TEXT | The chat and the run, nullable |
| `project_id`, `task_id` | UUID | Set when the run carried a task context, or when the member attached it. Nullable |
| `scope` | TEXT | `personal`, `project`, `org`. §5 |
| `labels` | TEXT[] | Auto labels: the app, the agent, the kind, the channel. The member adds their own |
| `search_tsv` | TSVECTOR | `title`, `labels`, and the first 64 KB of text for text kinds. GIN index. §8 |
| `status` | TEXT | `active`, `trashed`. A purged row is deleted |
| `keep` | BOOLEAN | True when starred by anyone, shared, or attached. A kept artifact never expires |
| `expires_at` | TIMESTAMPTZ | Set by the retention rule in §7. NULL when `keep` |
| `trashed_at`, `created_at`, `updated_at`, `last_activity_at` | | `last_activity_at` moves on open, edit, share and attach |

### 4.2 The per-member overlay, `artifact_member`

Two members can see one artifact and disagree about it, as they do about a
task (`pm_task_personal`, D53). So the star and the opens are per member.

`(artifact_id, member_email)` PK, `organization_id` RLS, `starred BOOLEAN`,
`last_opened_at`, `open_count`.

### 4.3 The shares, `artifact_shares`

`(artifact_id, subject, role)`, `organization_id` RLS, `granted_by`,
`created_at`. `subject` uses the one subject grammar: `org`, `group:<slug>`,
`project:<uuid>`, `user:<email>`. `role` is `view` or `edit`. The validator is
the one D12 names, and no second grammar exists here (R7, fence in §15).

### 4.4 What is not a table

- **Versions with bytes** stay out of the first release. `agent_file_history`
  keeps the metadata. §13 Q6 asks whether to keep the last five bodies.
- **Folders** are not built. Labels and filters do the work. A member who
  wants a folder makes a project.
- **Comments** on an artifact are not built. A comment goes on the task the
  artifact is attached to.

### 4.5 One function registers

`acb_memory.artifacts.register_artifact(meta, *, ctx)` is the only writer of
`artifacts`. It runs inside `mirror_to_blob_store` after the blob row lands,
in the same tenant session. It reads the run's artifact context for
`session_id`, `run_id`, `made_by`, `via` and any task context. An upload, an
Email import, a WhatsApp document and an MCP write all reach the mirror, so
they all register. `mirror_delete_from_blob_store` marks the row `trashed`
when a member deletes the file in a chat, and purges it when the sweep runs.

A failed registration does not fail the write. The file lands, the log says
`artifact_register_failed`, and AM-1's backfill picks the row up on its next
run. That matches the store's own "DB down, agents keep working" rule.

### 4.6 The row and the store cannot drift

`artifacts.sha256` is copied at write time. The viewer compares it to the
blob's sha on open, and shows "a newer version exists" when they differ. The
backfill repairs the row. `tests/unit/test_artifact_registry_r8.py` writes a
file, reads the row, overwrites the file, and asserts `version` is 2 and the
sha moved.

---

## 5. Who can see an artifact

Tenancy is which company. Visibility is who inside it (D12). Both apply.

### 5.1 Three scopes, one act to widen

| Scope | Who sees it | How it gets there |
|---|---|---|
| `personal` | The owner only | The default for every artifact a member's chat makes or uploads |
| `project` | Whoever can see the project | The member shares it to a project, or attaches it to a task of that project (§5.3) |
| `org` | Every member of the organization | The member shares it to the organization. The library calls this "Shared library" |

`artifact_shares` adds people and groups on top of the scope. A `personal`
artifact shared with `user:bob@…` stays `personal` in scope and gains one
reader. Scope is the broad audience. Shares are the exceptions.

### 5.2 The read rule, in one place

`acb_memory.artifacts.can_read(artifact, member)` answers true when any of
these holds, in order:

1. The member is the owner.
2. `scope = org`.
3. `scope = project` and the member can see `project_id` through the
   Projects access rule that already exists (`routes/projects/access.py`).
4. A share names `user:<email>`, a `group:<slug>` the member is in, or
   `project:<uuid>` the member can see.
5. The artifact is attached to a task the member can see (§5.3).

`can_edit` is the owner, or a share with role `edit`. Only the owner changes
scope, trashes, or renames. No route serves bytes without `can_read`, and the
existing session and global routes keep their own room and `u:` checks as
well. The registry never widens what H-201 and H-227 narrowed.

### 5.3 An artifact on a task follows the task

Attaching writes a `pm_task_refs` row with `kind = 'artifact'` and
`url = /artifacts/<id>`. The task's visibility then governs: a member who can
open the task can open the artifact. This answers Metorite task #60. The edge
cases:

- **A personal task.** The artifact stays personal. Publishing the task
  (a `PATCH` of `project_id` and `visibility`, D52) widens the artifact's
  audience through rule 5. The row's own scope does not change.
- **A moved task.** The link moves with it. Nothing on the artifact changes.
- **An unlinked artifact.** Rule 5 stops. The row's own scope and shares
  remain.
- **A chat the owner deletes.** The artifact goes to trash, and an attached or
  shared one stays active (§7.3).

### 5.4 Edit and copy

A viewer cannot edit. A viewer presses "Make a copy", and the copy is a new
personal artifact with `via = copy` and a label that names the source. That is
Claude's "customize" and Gemini's "Copy Canvas". An editor saves in place, and
the version counter moves.

### 5.5 External links

Not in the first release. §13 Q5 records the choice. When the owner says yes,
it is an org setting, off by default. A link is then a signed URL with an
expiry, never a public row.

---

## 6. The UI

The design system binds: one look, `AppTopBar`, headless primitives from
`src/components/ui/`, status colour from `src/lib/statusAccent.ts`, and the
`--cat-1…12` ramp for the kind chips. Nothing here draws its own palette.

### 6.1 The library (the Artifacts app, rebuilt)

```
┌ AppTopBar: ☰  Artifacts   [Search artifacts…]            [Upload] [⋯]
├────────────┬────────────────────────────────────────────────────────────
│ All        │ ▢ kind ▾  ▢ made by ▾  ▢ app ▾  ▢ date ▾    sort: recent ▾
│ Starred    │
│ Shared     │ ▦ Q3 sales summary.md     Metorite · chat "Q3 numbers" · 2 d
│  with me   │ ▦ pipeline.csv            you · upload · on #214 · 5 d
│ Shared     │ ▦ onboarding.html         Metorite · Projects · shared: org
│  library   │ ▦ invoice-0042.pdf        Email · Fracktal · 1 w
│ Projects ▾ │ …
│ Chats ▾    │
│ Trash      │                                          [storage: 1.2 / 2 GB]
└────────────┴────────────────────────────────────────────────────────────
```

- **The left rail is a filter list,** not a tree. "Projects" expands to the
  member's projects. "Chats" expands to recent chats, which keeps the door
  that slice 6c opened. "Agent workspaces", the second tab today, moves under
  an "Advanced" row and keeps its page.
- **A row** shows the kind icon, the title, who made it, and where (chat,
  app, channel). It shows the task pill when attached (`#214`, the WS-57
  pill), the scope chip when not personal, and the age. Hover shows star,
  share, open chat.
- **Search** runs on the server (§8). The filter chips are the same fields
  the API takes. The URL carries them, so a search is a link.
- **The storage meter** sits at the foot. It shows the member's own bytes, and
  for an admin the organization's. §7.5.
- **Bulk acts:** select, then Star, Share, Trash, Download as zip.

### 6.2 The artifact page, `/artifacts/<id>`

The viewer that exists, plus a header and a strip:

- **Header:** title (click to rename), kind chip, scope chip, star, Share,
  Download, Open as PDF (md and html), and "Open chat". The ⋯ menu holds
  Make a copy, Attach to task and Move to trash.
- **Provenance strip:** "Made by Metorite in *Q3 numbers* on 13 Oct, from
  task #214 in *Sales*. Version 3 of 3." Each name is a link. Version opens
  the history list.
- **Side sheet "Share":** scope (Only me · a project · Shared library), then
  people and groups with view or edit. The sheet names who can see it now,
  in plain words, before the member presses Save.
- **The viewer** gains a table view for CSV and TSV, and a "newer version"
  notice (§4.6). Nothing else in it changes.
- **Trash:** a trashed artifact opens read-only with "Restore" and "Delete
  now". The strip says the purge date.

### 6.3 In the chat

The card that `artifact_created` draws gains three acts: Star, Share, and
"Open in Artifacts" (to `/artifacts/<id>`). The side panel still opens the
viewer in place. The event carries the `id` once AM-1 lands, and falls back to
session and path when it does not. A chat's Files button opens the library
filtered to that chat.

### 6.4 In Projects

The task's attachment area lists the artifacts attached by reference, each as
a card with the kind icon. "Attach an artifact" opens a picker over the
library, filtered to what the member can see. Paste (#155) and the agent act
(#168) land through the same reference route. `pm_task_attachments` stays for
a raw pasted file until a later slice folds it in (§14).

### 6.5 In Email

`ArtifactAttachPicker` reads `GET /artifacts` for what the member can see,
instead of one agent's directory. Sending resolves the id to the blob and
keeps the 422 on a refused ref.

### 6.6 On WhatsApp

- **Out:** a run on WhatsApp makes or finds an artifact. The bot sends it as
  a document (PDF, DOCX, XLSX, CSV, image) through the `send_document` that
  WS-47 WAC-12 adds (PR #1040). AM-6 adds no second one.
  One `cta_url` button, "Open in Metorite", opens `/artifacts/<id>`. The bot
  renders an html or react artifact to PDF with the existing `format=pdf` path.
  When the render fails, the bot sends the link alone. A zip is sent as the
  link.
- **In:** the bot keeps a document or image the member sends as an artifact.
  It is personal, `via = whatsapp`, with `kind` from the mime, in the
  thread's `inputs/`. The reply names it and offers "attach to a task".
- **The tool:** `write_artifact` is withheld on WhatsApp today. The owner's
  rule of 2026-10-10 says a channel strips delivery tools and keeps function.
  So the tool returns, and only its card becomes a document send.

### 6.7 Home and the command bar

- **Home, personal altitude:** one card, "Recent artifacts", five rows, from
  `GET /artifacts?sort=recent&limit=5`.
- **⌘K:** an `artifacts` provider in `routes/shell/search.py` `PROVIDERS`,
  gated on `feature:artifacts`, returns title, kind, made-by and the open
  URL. §8 is the query.

### 6.8 Shell manifest (R9)

- **Team:** Personal Center. **Purpose:** find and share what you and
  Metorite made.
- **Jobs:** `Find an artifact` (query, kind, made_by), `Share an artifact`
  (artifact, scope, subjects), `Attach to task` (artifact, task). Form route:
  `/artifacts?new=share`.
- **Search provider:** `artifacts`. **Needs you:** none. **Home cards:**
  "Recent artifacts" at the personal altitude only. **Agent:** `orchestrator`,
  with the tools in §9.
- No palette, bell, rail or ⌘K listener of its own. `seams.test.ts` holds it.

---

## 7. How long an artifact stays

### 7.1 The rule

| Class | Test | Expiry |
|---|---|---|
| Kept | `keep = true`: starred by anyone, shared, attached to a task, or promoted to `agent-data/` | Never |
| Scratch | Everything else: a chat output or upload nobody opened, starred, shared or attached | `last_activity_at` + 90 days |
| Trashed | `status = trashed` | `trashed_at` + 30 days, then purge |

The numbers are settings, `ARTIFACT_SCRATCH_DAYS` and `ARTIFACT_TRASH_DAYS`,
and §13 Q2 asks the owner to confirm them. An open, an edit, a share or an
attach moves `last_activity_at`, so a thing in use never expires.

### 7.2 What the member sees

- Fourteen days before a scratch artifact expires, it shows an "expires in
  14 days · keep" chip in the library. One press stars it.
- Trash lists each row with its purge date. Restore puts it back as scratch
  with a fresh window.
- Nothing is purged silently from a kept class. The sweep writes one
  `artifact_purged` audit line per row, with the id, owner, size and reason.

### 7.3 Deleting a chat

Today `purge_thread_files` removes the chat's files at once. With the
registry on, deleting a chat **trashes** its personal scratch artifacts and
**keeps** every kept one. A kept artifact loses its `session_id` link and
keeps its row and bytes. That is ChatGPT's "deleting a chat does not delete a
file in the Library". It is also what the owner asked for when they said
artifacts are "smartly stored away". §13 Q4 confirms it.

### 7.4 The sweep

`gateway/artifacts/sweep.py`, started from the gateway lifespan beside the
My Day and Reach loops, in the shape of `routes/email/memory_purge.py`:

1. Every hour, per organization, inside `tenant_session`.
2. Claim one row at a time with `FOR UPDATE SKIP LOCKED`.
3. `expires_at < now()` and `status = active` → `trashed`, `trashed_at = now()`.
4. `trashed_at + trash window < now()` → delete the blob row, the history
   rows, the disk file and the zip, then the registry row. One audit line.
5. `agent_file_history` for a path keeps the newest 20 rows. Older rows go.
6. Stop after 200 rows per organization per tick, so one tenant cannot hold
   the loop.

### 7.5 Storage and quota

- The meter is a sum over `artifacts.size` by owner and by organization,
  cached for a minute.
- `ARTIFACT_MEMBER_QUOTA_MB` (default 5120) and `ARTIFACT_SEAT_QUOTA_MB`
  (default 5120). The organization limit is the seat quota × the org's
  `seats_purchased`, pooled (D108, 2026-10-20). A membership row is not a
  seat (D32.5). At 80 % the meter turns amber and the chat card says so. At
  100 % a write is refused with a reason, and a delete still works, which is
  the sandbox store's rule already (§7.1 of `maf_coding_engine.md`).
- The organization's quota is a plan fact later (WS-31). Until then it is a
  setting. The owner answered §13 Q3 on 2026-10-20 (D108).
- The bytes move to the object store of `saas_multitenancy.md` MT-1g (D108).
  The quota counts the bytes, wherever they live.

---

## 8. Search and recall

- **Index.** `search_tsv` is a stored tsvector over the title, the labels and
  the first 64 KB of extracted text. Extraction covers `document`, `data`,
  `code`, `html` and `pdf` (through `pypdf`, already a dependency). The GIN
  index and the `websearch_to_tsquery` + `ts_rank_cd` query follow
  `routes/email/transport/search.py`, which is the house template.
- **Extraction** runs in `register_artifact` for text kinds under 1 MB, and in
  the backfill for the rest. An image has its title and labels only.
- **Embeddings** come second, through the pipeline Email already has
  (`73_email_embeddings.sql`, `embed_query`). AM-7 blends them. That is the
  index Metorite task #137 asks for, so recall cites the artifact and opens
  it instead of describing it.
- **The API.** `GET /artifacts` takes `q`, `kind`, `made_by`, `source`,
  `project_id`, `session_id`, `scope`, `starred`, `status`, `sort` and
  `cursor`. It returns rows the member `can_read`, with keyset paging.
  `GET /artifacts/{id}` returns the row. The bytes still come from the
  existing file routes. They also accept `/artifacts/{id}/file`, and resolve
  through the registry plus `can_read`.

---

## 9. Agents and outside agents

- **Tools for every agent:** `find_artifacts(query, kind, scope, limit)` and
  `open_artifact(id, offset)` (text, paged, 8 KB a page). Both read under the
  run's member through `can_read`. They sit beside `write_artifact` in the
  universal injection, and the WhatsApp channel keeps them.
- **`share_artifact(path)`** keeps its meaning (draw the card for a file
  another tool made). People-sharing is `share_artifact_with(id, scope,
  subjects)`. It asks the member through the confirm card before it widens
  the audience, because a share reaches another person.
- **Outside agents (WS-55):** `list_artifacts` and `get_artifact` on the MCP
  server, under the member's own access, with the `MCP_ROUTES` allowlist and
  the agent scope on `Visibility`. A write by an outside agent registers with
  `via = mcp:<client>`.
- **Memory (#137):** the registry is the recall index. A run's recall step
  queries `search_tsv` for the member, and the answer cites `/artifacts/<id>`.

---

## 10. File paths, re-verified at dispatch

| Area | Path |
|---|---|
| Store | `packages/acb_memory/acb_memory/blob_store.py`, `infra/postgres/71_agent_blob_store.sql`, `136_agent_blob_instance.sql` |
| Registry (new) | `packages/acb_memory/acb_memory/artifacts.py`, `infra/postgres/<next free>_artifacts.sql` |
| Write hooks | `packages/acb_skills/acb_skills/write_artifact.py` (`mirror_to_blob_store`, `mirror_delete_from_blob_store`), `routes/workspace.py` (upload, PUT, DELETE), `routes/email/transport/send.py` |
| Routes (new) | `apps/services/gateway/gateway/routes/artifacts.py` |
| Sweep (new) | `apps/services/gateway/gateway/artifacts/sweep.py`, started in `gateway/main.py` lifespan |
| Search | `routes/shell/search.py` (`PROVIDERS`), `routes/email/transport/search.py` (template) |
| Task refs | `routes/projects/refs.py`, `refs_classify.py`, `infra/postgres/255_pm_task_refs.sql` |
| WhatsApp | `routes/whatsapp_channel/bot_run.py`, `cloud_api.py` (`send_document` new), `packages/acb_skills/acb_skills/whatsapp_ui.py` |
| Frontend | `workbench/control_plane/src/app/artifacts/`, `src/components/ArtifactSidebar.tsx`, `ArtifactViewerModal.tsx`, `ArtifactCard.tsx`, `MessageBubble.tsx`, `src/lib/artifactKind.ts`, `src/app/email/components/ArtifactAttachPicker.tsx` |
| Shell | `src/lib/nav.ts`, `src/lib/shell/`, `specs/launch_surface.md` §2 |

---

## 11. The slices

Each slice is one PR, dark behind its flag, verified by the commands in §12.
The order is the dependency order. AM-1 and AM-2 build on the §13 defaults
and need no owner answer first. The flags are `ARTIFACT_REGISTRY_ENABLED`
(gateway) and `NEXT_PUBLIC_ARTIFACT_LIBRARY` (frontend).

### AM-1 · The registry — AGENT-SAFE · BUILT 2026-10-15, dark

Migration (R6, expand only): `artifacts`, `artifact_member`,
`artifact_shares`, with RLS in the shape of migration 255. The live database
check is done (§2). `agent_blob` carries `organization_id` and FORCE RLS in
production, so the registry joins on the store key under one tenant session.

The hook in `mirror_to_blob_store`. The classifier moved to a shared module. The backfill
command `scripts/backfill_artifacts.py` over `agent_blob` rows under
`inputs/` and `outputs/`, idempotent. `GET /artifacts` and
`GET /artifacts/{id}` with `can_read` rule 1 and 2 only.

Done when: a `write_artifact` in a chat leaves one row with the right owner,
session, run, kind, title and sha. An upload leaves one row with
`via = upload`. Writing the same path twice moves `version` to 2. The backfill
run twice adds nothing the second time. A member of another organization
reads nothing (R8, two-org fixture). The card's event carries the `id`.

### AM-2 · The library — AGENT-SAFE

The Artifacts app reads the registry. It gets the rail, the filters, server
search, star, rename, trash and restore. It gets the artifact page with the
provenance strip, the CSV table view and the read-only storage meter. The
chat card gets its three acts.
The "Chats" filter keeps slice 6c's door. Behind `NEXT_PUBLIC_ARTIFACT_LIBRARY`.

Done when: a search for a word inside a markdown artifact finds it. A star
sets `keep`. Trash hides it from All and shows it in Trash with a purge date.
Restore brings it back. Rename changes the title and not the path. The
visual-review contexts pass (light, compact, changed accent, four widths).

### AM-3 · Scope, shares and the task link — AGENT-SAFE, with one gate

`PATCH /artifacts/{id}` for scope, and `artifact_shares` writes through the
one subject validator. The Share sheet, "Shared with me", "Shared library"
and "Make a copy". `can_read` rules 3 to 5. The `pm_task_refs` kind
`artifact`, with the picker in the task's attachment area. This closes the
model half of Metorite tasks #58, #60, #155 and #168.

Done when: a personal artifact is invisible to a second member. Shared to a
project, that member sees it when they can see the project. Attached to a
task, they see it through the task and lose it when the link is removed. A
viewer's edit is refused with 403. "Make a copy" leaves a new personal row.
The two-org fixture shows no cross-tenant read.

🔴 **OWNER-GATE:** external links (§13 Q5). Not built in AM-3.

### AM-4 · Find it from anywhere — AGENT-SAFE

The `artifacts` search provider, the Home card, the two agent tools, and the
Email picker on the registry.

Done when: ⌘K finds an artifact by a word in its title and opens it. The
Home card shows the five newest the member can read. An agent in chat
answers "find the Q3 summary" with a link to `/artifacts/<id>`, and refuses an
id the member cannot read. An email attaches an artifact a different chat made.

### AM-5 · Retention — AGENT-SAFE, numbers gated

`expires_at` on register and on activity, the expiry chip, the sweep and the
history cap. The quota check on write. Chat delete trashes scratch and keeps
kept. The purge audit line.

Done when: a scratch row with `last_activity_at` 91 days back is trashed by
one tick, and a starred one is not. A trashed row 31 days old is gone from
the registry, the blob, the history, the disk and the zip. A write over quota
is refused with a reason, and a delete works. Deleting a chat keeps its
attached artifact and trashes its scratch one. The sweep stops at 200 rows for
one organization and moves to the next.

🔴 **OWNER-GATE:** the windows and the quotas (§13 Q2, Q3), and turning the
sweep on in production.

### AM-6 · Channels — AGENT-SAFE, one gate

The document send on WAC-12's `send_document`, with the `cta_url` button.
Html and react to PDF. Inbound media kept as an artifact.
`write_artifact` back on WhatsApp, with the card as a document.

Done when: a WhatsApp run that makes a PDF sends it as a document with one
button to the artifact page. A sent image or document from the member shows in
the library with `via = whatsapp`. A zip sends the link only.

🔴 **OWNER-GATE:** a send to a real phone is a test on the owner's own
number only (CLAUDE.md §3a rule 3).

### AM-7 · Text index and recall — AGENT-SAFE

Extraction for pdf and docx, embeddings through the Email pipeline, the
blend in `GET /artifacts?q=`, and the recall step that cites an artifact.
Closes Metorite task #137 with #116's memory work.

### AM-8 · Outside agents — AGENT-SAFE

`list_artifacts` and `get_artifact` on the MCP server (WS-55 seam), with the
`MCP_ROUTES` entries and the agent scope. A write through MCP registers with
`via = mcp:<client>`.

### AM-9 · Versions with bodies — OWNER-GATE (§13 Q6)

`artifact_versions` with the last five bodies for text kinds under 1 MB, the
version stepper and a diff view on the artifact page.

### Later, not here

Live artifacts (#167, its own spec). Folding `pm_task_attachments` into the
registry (§14). External links (Q5). Object storage for the bytes (MT-1g,
unchanged by this spec).

---

## 12. Verification commands

```
# Backend, per slice. Run the DB first (R8):
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_artifact_registry.py tests/unit/test_artifact_access.py \
  tests/unit/test_artifact_sweep.py tests/unit/test_artifact_routes.py -q
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_tenancy_boundary.py -q
uv run pytest tests/unit/test_h201_artifact_routes.py tests/unit/test_h227_thread_files.py -q

# Frontend
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/artifacts src/lib/shell/seams.test.ts src/lib/nav.test.ts

# The one subject grammar (AM-3)
uv run pytest tests/unit/test_subject_grammar.py -q

# Visual (AM-2, AM-3): the visual-review skill over /artifacts and /artifacts/<id>
```

A test file named above that does not exist yet is created by the slice that
needs it. The slice's PR names each one.

---

## 13. What the owner must confirm

Each has a recommended default. AM-1 and AM-2 build on the defaults. The
others gate the slice they name.

| # | Question | Recommended | Gates |
|---|---|---|---|
| Q1 | Does every artifact start **personal**, including one an agent makes on a shared project's task? | Yes. Personal, and attaching to the task widens it through the task | AM-3 |
| Q2 | Scratch expiry and trash window | 90 days of no use, then 30 days in trash | AM-5 |
| Q3 | Quotas | **Decided: owner, 2026-10-20 (D108).** 5 GB per member, and 5 GB × `seats_purchased` per organization, pooled. Refuse writes at 100 %, deletes always work | AM-5 |
| Q4 | Deleting a chat | Trash its scratch artifacts, keep the kept ones | AM-5 |
| Q5 | Links for people outside the organization | Not in this release. Later as an org switch, off by default, signed URL with expiry | AM-3 external half |
| Q6 | Keep the bodies of old versions | Last five, text kinds under 1 MB, text only | AM-9 |

---

## 14. Known debts this spec names and does not fix

- `pm_task_attachments` and `attachments` (migration 52) hold a pasted file on
  a task by disk path, outside the blob store and the registry. A later slice
  moves them in. Until then a task shows both lists.
- `agent_blob` tenancy is in the generated MT-1b files and in no numbered
  migration. Production has the column and FORCE RLS (checked 2026-10-15),
  because the phase-4 promotion applied the generated files. The CI ladder
  replay never runs them on purpose. The `promoted` fixture of the H3
  rehearsal does, so every R8 suite of this spec uses that fixture.
- The global `GET /agent/artifacts` walk (4000 files, disk) stays for the
  "Agent workspaces" page until AM-2 reads the registry everywhere.
- `agent_file_history` claims "directly retrievable versions" and holds no
  bytes. AM-9 decides it.

---

## 15. Fences

| Rule | Fence |
|---|---|
| Only `register_artifact` writes `artifacts` | `tests/unit/test_artifact_registry.py`: a grep over the tree for `INSERT INTO artifacts` outside `acb_memory/artifacts.py` fails |
| Every new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` (exists) |
| No second subject grammar | `tests/unit/test_subject_grammar.py`: `artifact_shares.subject` writes go through the one validator |
| No bytes without `can_read` | `tests/unit/test_artifact_routes.py`: every bytes route refuses a second member on a personal row, with the two-org fixture (R8) |
| The sweep never purges a kept row | `tests/unit/test_artifact_sweep.py`, verified red first |
| The app draws no shell of its own | `src/lib/shell/seams.test.ts` (exists) |
| The classifier has one home | `tests/unit/test_artifact_registry.py` reads the viewer's sets in `lib/artifactKind.ts` and fails when one maps to a kind the registry does not expect |
