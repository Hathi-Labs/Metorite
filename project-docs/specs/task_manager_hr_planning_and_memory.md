# Task Manager — HR intelligence, project planning & agent memory

Status: **DESIGN / SPEC** (2026-07-16). Companion to `task_manager_app.md` (§6.1
"People & delegation") — this doc turns the §6.1 "later" list into a concrete
build plan and folds in the requested resume-ingestion, HR-management UI,
per-agent vector memory, and the port of the **agent-project-manager** repo's
planning toolset.

The guiding fact: **~60% of this epic is already scaffolded.** The job is to close
specific gaps, not to build from scratch. Every section below starts with *what
exists* so we never rebuild working plumbing.

---

## 0. Scope (the ask, decomposed)

From the request, eight threads:

1. AI task review infers the **ClickUp project** and the **assignee** from our HR
   structure.
2. Store config **beyond ClickUp**: upload résumés (PDF/DOCX) and link them to users.
3. Per ClickUp user: **title, manager, skill set** → intelligent assignment.
4. The tasks agent keeps an **internal vector-DB memory** to clarify tasks + fill
   next-action detail.
5. **Document** the per-agent memory approach so we can add dedicated memory to
   specific agents (beyond shared global memory).
6. ClickUp **sync reflects currently-active people and projects**.
7. **Integrate agent-project-manager's skills/tools** into the MAF task-manager agent.
8. **UI/UX**: update HR structure, edit skills, ingest résumés → auto-update skills;
   and a chat that **plans whole projects** and auto-creates projects/tasks/subtasks
   (local or ClickUp) with LLM-populated fields.

---

## 1. Current state (what already exists)

| Capability | Where | Status |
|---|---|---|
| People/HR table `people` (name, email, role, department, team, **reports_to**, status, **skills[]** GIN, resume_summary, years_experience, domain, capacity/load/available hours, **clickup_user_id**) | `infra/postgres/49_gtd_people.sql` | **Built** (read-only cache) |
| HR seed data (Fracktal org + résumé profiles) | `infra/seed/hr/{hr_structure,resume_profiles}.json` + `scripts/import_hr_people.py` | **Built** (seed-only import) |
| Read people API + clarify feed | `routes/tasks/people.py` `GET /tasks/people`, `fetch_people_for_clarify()` | **Built** (no write path) |
| Capability-aware assignee match (skill word-boundary hits + résumé-domain bonus, tie-break years→free-hours) | `routes/tasks/ai.py:136` `_match_capability()` | **Built** (keyword, not vector) |
| LLM clarify picks disposition, next-action, **assignee_name**, **project_match [P#]**, subtasks, matrix flags | `ai.py:410 _llm_propose`, `:603 propose_with_llm`, route `:808 /items/{id}/clarify` | **Built** |
| Agent tool `people(query)` (org-knowledge search) + delegation persona rule | `skill-task-gtd/core.py:301`, `instructions.md:44-47` | **Built** |
| Delegate LOCAL→ClickUp (`gtd_organize(delegate)`, `DelegateDialog.tsx`, `POST /items/{id}/delegate`) | skill + frontend | **Built** |
| ClickUp System B (per-account tokens, `ClickUpProvider`, schema sync of projects/members/statuses into `task_accounts.schema_cache` + `gtd_projects`) | `routes/tasks/providers.py`, `accounts.py:384 _refresh_schema` | **Built** |
| Subtasks (`parent_item_id`), local hierarchy (`gtd_spaces`/`gtd_folders`), status→stage map | migrations 59, 60, 69 | **Built** |
| Task-manager chat (`AssistantRail` → shared `AgentChat`, agent="task-manager", live GTD persona) | `tasks/components/AssistantRail.tsx` | **Built** |
| Agent memory scopes: user / **agent:&lt;name&gt;** / org:global (Mem0 + pgvector, `mem0_memories`) | `packages/acb_memory/mem0_client.py`, `acb_skills/memory_tools.py` | **Built** (shared collection, keyword-agnostic) |
| Attachment upload (multipart, 15 MB cap, owner-checked) | `routes/tasks/attachments.py`, `AttachmentComposer.tsx` | **Built** (reusable for résumés) |
| Durable agent blob/file store | `acb_memory/blob_store.py`, migration 71 | **Built** |

**Bottom line:** the *data model* (people, skills, manager, clickup id), the
*assignment reasoning* (capability match + LLM), the *ClickUp connector*, the
*subtask model*, and the *agent chat* all exist. What's missing is résumé *parsing*,
an HR *write/edit* surface, *vector* capability matching, a *dedicated* agent
memory for task clarification, richer *project-planning* tools, and the *UI*.

---

## 2. Gap analysis (ask → status → gap → approach)

| # | Ask | Status | Gap | Approach |
|---|---|---|---|---|
| 1 | Infer project + assignee from HR | Mostly built | Keyword-only match; no live workload/overload; no reporting-line reasoning | Add embeddings match (§5), live workload (§6), overload guard, `reports_to` reasoning in the clarify prompt |
| 2 | Résumé upload (PDF/DOCX) linked to users | Not built (parsing is in the *external* repo only) | No parser, no upload→person link, no write path | §4 résumé pipeline: reuse `attachments.py` upload + add PyMuPDF/docx parser + write to `people` |
| 3 | Per-user title/manager/skills | Built in schema | `reports_to` is free-text; skills seed-only; no edit | §3 make `people` **editable** (write API + UI §10); keep `reports_to` but add optional `manager_id` FK |
| 4 | Tasks agent internal vector memory | Partially (shared Mem0 agent scope) | No *dedicated* task-clarification memory; matching is not embedding-based | §9 dedicated `agent:task-manager` memory namespace + a `task_clarification_memory` recall/save loop + ASG-style retrieval-routing protocol |
| 5 | Document per-agent memory approach | Framework doc exists (`agent_file_and_memory_framework.md`) | Doesn't cover "give agent X a dedicated vector memory" recipe | §9 + extend the framework doc with the recipe |
| 6 | Sync active people/projects | Projects+members sync built | `people` is seed-only; drifts from ClickUp membership; departed people linger | §6 reconcile `people` against `ClickUpProvider.list_members()` (mark active/departed, link clickup_user_id) |
| 7 | Integrate PM-agent tools | New | PM repo has plan_project/create_project/sync_tasks/WBS/Gantt/workload/résumé/ClickUp-docs; ours has gtd_* | §8 port matrix — adopt reasoning, reuse our providers/store, don't duplicate the connector |
| 8 | HR-mgmt UI + project-planning chat | Chat built; no People UI | No People/Skills view; planning-from-chat is shallow | §10 UI spec (PeopleView, PersonEditor, ResumeUpload, OrgTree, PlanProject flow) |

---

## 3. Data-model changes

Ownership shift: today `people` is a **read-only cache** whose source of truth
is the external agent-project-manager repo. The request makes the app itself an
**editor** of HR data. Resolution: `people` becomes the source of truth for
fields the UI edits; `source` column already distinguishes provenance
(`agent-project-manager` seed vs `manual` edit vs `clickup` sync vs `resume`).

**Migration `NN_gtd_people_editable_and_vectors.sql`** (idempotent, IF NOT EXISTS):

```sql
-- Make the row editable + auditable
ALTER TABLE people ADD COLUMN IF NOT EXISTS title TEXT;            -- distinct from role if desired
ALTER TABLE people ADD COLUMN IF NOT EXISTS manager_id UUID REFERENCES people(id);  -- structured hierarchy (reports_to stays as the display name)
ALTER TABLE people ADD COLUMN IF NOT EXISTS skills_source JSONB DEFAULT '{}';  -- per-skill provenance: {skill: "resume"|"manual"|"orgchart"}
ALTER TABLE people ADD COLUMN IF NOT EXISTS updated_by TEXT;        -- who last edited (audit)

-- Résumé linkage (a person can have >1 résumé version)
CREATE TABLE IF NOT EXISTS people_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id UUID NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime TEXT,
    storage_ref TEXT,            -- attachments dir path or blob-store key
    parsed_text TEXT,           -- extracted plain text (for re-parse / search)
    extracted JSONB,            -- {skills[], experience_summary, years_experience, domain, education[]}
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    uploaded_by TEXT
);

-- Vector capability index (semantic assignee/skill matching)
-- pgvector already installed (email_embeddings uses vector(1536)).
ALTER TABLE people ADD COLUMN IF NOT EXISTS capability_embedding vector(1536);
-- text the embedding is built from = role + title + skills + resume_summary + domain
```

Notes:
- Keep `reports_to` (free-text, from the org chart) AND add `manager_id` (FK). The
  UI edits `manager_id`; `reports_to` stays as the imported display value until a
  backfill maps names→ids.
- `capability_embedding` mirrors the email `email_embeddings` pattern (gateway
  embed call, dormant unless `EMAIL_SEMANTIC_SEARCH`-style flag is on). Gate it
  behind a `TASK_SEMANTIC_MATCH` flag so keyword matching stays the default/fallback.

---

## 4. Résumé ingestion pipeline

Parsing lives ONLY in the external repo today (`ingest_resumes.py`, PyMuPDF +
keyword vocab + fuzzy name-match). Bring a monorepo-native version.

**Flow:** upload → store → parse → extract → (LLM enrich) → write to `people`.

1. **Upload** — reuse `attachments.py` plumbing (multipart, size cap, owner check).
   New route `POST /tasks/people/{id}/resume` (multipart) → store file (attachments
   dir or blob store) → insert `people_resumes` row.
2. **Parse** — new `routes/tasks/resume_parse.py`:
   - PDF → text via **PyMuPDF (`pymupdf`)** with `pdfplumber` fallback.
   - DOCX → **`python-docx`**.
   - (These are new deps for the gateway service — small, pure-Python-ish.)
3. **Extract skills** — two-tier, mirroring the PM repo but LLM-first here:
   - Fast path: keyword match against a skill vocabulary (seed from the union of
     existing `people.skills`).
   - LLM path (default when `clarify_use_llm`): prompt the gateway model to return
     `{skills[], experience_summary, years_experience, domain, education[]}` as JSON
     (JSON-mode required — see [[llm-json-mode-required]]).
4. **Merge** — union new skills into `people.skills`, stamp `skills_source`
   per-skill = "resume", set `resume_summary`/`years_experience`/`domain` if empty
   or if the user opts to overwrite. Re-embed `capability_embedding`.
5. **Idempotent** — re-uploading a résumé replaces its `people_resumes` row and
   re-merges; never duplicates skills (they're a set).

**Reusable references:** external `ingest_resumes.py` (`SKILL_KEYWORDS`, `fuzzy_match`),
our `attachments.py` (upload), `import_hr_people.py` (`build_rows()` merge logic).

---

## 5. Assignee + project inference (upgrades)

Current `_match_capability()` (keyword) stays as the deterministic fallback (it's
golden-eval-adjacent — don't break `propose()`). Layer on:

- **Semantic match** (flagged): embed the task's `next_action`/title, cosine against
  `people.capability_embedding`, blend with the keyword score. Never *drops* a
  keyword match (same principle as the email hybrid search — see [[email-search-hybrid]]).
- **Live workload + overload guard**: pull each candidate's open ClickUp tasks via
  `ClickUpProvider.list_tasks()` (already exists), estimate load (port the PM repo's
  `TASK_EFFORT_BY_PRIORITY`), and *warn* at assign time when `est > capacity`
  (surfaced in the clarify card + delegate dialog). This realizes the §6.1 "overload
  warnings at assign time".
- **Reporting-line reasoning**: feed `reports_to`/`manager_id` into the clarify LLM
  prompt so it can prefer same-team owners or route up to a manager for approval-type
  tasks. Cheap prompt change.
- **Project inference**: `project_match [P#]` already exists; extend the candidate
  list with ClickUp SYNCED projects (from `task_accounts.schema_cache`) so the LLM
  can map a task to a real ClickUp List, not just LOCAL projects.

---

## 6. ClickUp sync of active people & projects

Projects+members already sync via `_refresh_schema()`. Add a **people reconcile**
step so `people` reflects *current* ClickUp membership:

- On schema refresh (and on a scheduler tick), diff `ClickUpProvider.list_members()`
  against `people`:
  - Member present in ClickUp but not in `people` → insert (status=active,
    `source='clickup'`, link `clickup_user_id`).
  - `people` row whose `clickup_user_id` no longer appears → mark
    `status='inactive'` (don't delete — preserve history/audits).
  - Match by `clickup_user_id` first, then email, then fuzzy name.
- This keeps assignment suggestions honest as people join/leave (the request's
  "personnel may change, syncing must reflect currently-active people").
- Projects: already synced; ensure the clarify project-candidate list reads the live
  `schema_cache.projects` so newly-created ClickUp Lists are assignable immediately.

---

## 7. Project planning from chat

Goal: "plan a complete project" in chat → auto-create project + tasks + subtasks
(LOCAL or ClickUp) with LLM-populated fields.

We already have the building blocks: `gtd_organize` (staged apply), `parent_item_id`
subtasks, `gtd_spaces/folders`, ClickUp create/sync via `providers.py`. Add a
**planning tool** that composes them:

- New agent tool `gtd_plan_project(name, description, target="local"|"clickup", account_id?)`:
  1. LLM emits a plan in the PM repo's proven schema — phases→tasks→subtasks with
     `{title (verb+object), description (done-when), owner_role/assignee, due, effort,
     priority, dependencies}` (see PM `create_tasks_with_subtasks.py` contract).
  2. Resolve each `owner_role`/assignee via §5 (skills+capacity) → `clickup_user_id`.
  3. Stage as LOCAL `gtd_items` (parent + `parent_item_id` subtasks under a new
     `gtd_projects` row / `gtd_spaces`+`gtd_folders`), OR push to ClickUp via the
     existing provider (Space+Folder+List+tasks+subtasks) — honoring the "confirm
     before provider writes" rule and the Action-Broker gate.
- The heavy planning generators (WBS / Gantt / risk register) from the PM repo are
  **Phase 3 / optional** — port as tools only if the user wants engineering-grade
  project docs; they emit markdown and can feed ClickUp Docs (v3 API).

---

## 8. Agent-project-manager → MAF task-manager port matrix

The PM repo is *already* a Python MAF agent on our gateway convention, so this is
reconciliation, not a rewrite. **Do NOT port its connector or memory** — we have
better (System B provider + Mem0/pgvector).

| PM asset | Port decision | Into |
|---|---|---|
| `hr_structure.json` / `resume_profiles.json` data model | **Adopted already** (seed → `people`) | keep; add edit path (§3) |
| `ingest_resumes.py` (PDF→skills) | **Port logic** (PyMuPDF + extract), LLM-first | §4 `resume_parse.py` |
| `workload_analysis.suggest_assignee()` (skill∩ + capacity rank) | **Port as reasoning** | fold into §5 (keep our `_match_capability` as fallback) |
| morning-report two-tier ASSIST/BACKLOG suggester | **Port (Phase 3)** as a `gtd_rebalance` tool | new tool |
| `clickup_client.py` + 6 silent-failure API rules | **Cross-check ours** (`providers.py`) against the rules; adopt any missing (flat-int assignees, subtask-via-parent, 18:00 due, 429 sleep, v3 Docs) | audit `ClickUpProvider` |
| `plan_project` / `create_tasks_with_subtasks` schema | **Port the plan CONTRACT** | §7 `gtd_plan_project` |
| WBS / Gantt / risk-register generators | **Optional (Phase 3)** | new tools → ClickUp Docs |
| `research_web` / `search_papers` (SerpAPI/arXiv) | **Optional** — only if planning needs prior-art | new tools |
| project-planning priority matrix + delegation checklist + task standards (prose) | **Adopt into `instructions.md`** | prompt content |
| DOE subprocess-per-tool execution model | **Reject** — our tools run in-process | n/a |

**Watch-outs:** the PM repo hardcodes Fracktal `clickup_user_id`s (already handled
by our per-account model); its `query_hr.py` CLI has a known arg-mismatch bug — don't
copy verbatim, port the algorithm.

---

## 9. Dedicated per-agent vector memory (+ framework doc)

**Reality check:** both external repos ("agent-startup-guru", "agent-project-manager")
use **lexical SQLite FTS5**, *not* a vector DB. Our Mem0 + pgvector already exceeds
them on semantic recall. So "give the tasks agent an internal vector-DB memory" =
**use the agent-scoped Mem0 namespace we already have**, plus adopt the *protocol*
layer (retrieval-routing, write-hygiene, decision→outcome) those repos got right.

**Design — a task-clarification memory for `agent:task-manager`:**
- **Namespace:** `scope_key(agent="task-manager")` (the existing `agent:<name>`
  Mem0 partition in `mem0_memories`) — no new store needed for facts.
- **Write path (hygiene from ASG):** on every clarify/organize, save a compact memory
  in the same turn — e.g. "Tasks about `<X>` route to disposition `<D>`, owner
  `<person>`, project `<P>`; next-action pattern `<...>`." Also save **decision→outcome**
  ("proposed owner Y; user accepted/overrode to Z") so the agent learns the user's
  real preferences over time.
- **Recall path (routing from ASG):** before proposing, `recall_agent(task_text)` to
  retrieve similar past clarifications and the user's overrides; feed as few-shot
  context into `_llm_propose`. This directly serves "clarify tasks and fill in
  next-action details" from memory.
- **Optional dedicated table** (only if we want a separate, queryable clarification
  index rather than Mem0 facts): `task_clarification_memory(id, user_id, task_text,
  embedding vector(1536), disposition, next_action, assignee, project, accepted bool,
  created_at)` — a purpose-built vector index the clarify route can `ORDER BY cosine`.
  Recommend starting with Mem0 agent scope; add the table only if recall precision
  needs task-specific structure.
- **Capability matching** (§5) is the *other* vector memory: `people.capability_embedding`.

**Framework doc addition** (append to `agent_file_and_memory_framework.md`): a
"Dedicated agent memory recipe" section — how to (1) pick the `agent:<name>` scope,
(2) define what the agent saves and when (write-hygiene), (3) define the recall
routing (when to spend a recall call vs use loaded context), (4) optionally add a
purpose-built vector table, (5) wire `recall_agent`/`save_agent_memory` into the
agent's tool loop. This satisfies "document it so we can add dedicated memory to
specific agents."

---

## 10. UI/UX component spec (the requested documentation)

New surface: a **People / HR** area in the tasks app + a **Plan Project** chat flow.
Plug points identified: `tasks/components/ListsSidebar.tsx` nav arrays; a `people`
slice in `taskStore.ts` (mirror `loadLocalHierarchy`); backend `people.py` (add write).

**Nav & views**
- `ListsSidebar` → new **"People"** `NavRow` (icon: Users) opening `PeopleView`.

**`PeopleView.tsx`** — roster
- Table/cards of `people`: name, title/role, department·team, manager, skills
  chips, capacity bar (`current_load / capacity`), status badge (active/inactive),
  ClickUp-linked indicator.
- Filters: department, skill, availability, status. Search.
- Row actions: Edit, Upload résumé, View tasks assigned.
- Header actions: **Add person**, **Sync from ClickUp** (§6), **Import seed**.

**`PersonEditor.tsx`** — drawer/modal (the "update HR structure / edit skills")
- Edit title, role, department, team, **manager** (picker over `people`), status,
  capacity hours, `clickup_user_id` (link to a ClickUp member via `list_members`).
- **Skills editor**: add/remove chips; each chip shows provenance
  (`orgchart`/`resume`/`manual`); manual edits stamp `skills_source`.
- Writes via new `PATCH /tasks/people/{id}` (+ `POST /tasks/people`).

**`ResumeUpload.tsx`** — the résumé ingestion UX
- Drag-drop PDF/DOCX → `POST /tasks/people/{id}/resume` → progress → shows the
  extracted `{skills, years, domain, summary}` as a **diff preview** (new skills
  highlighted) → user confirms merge → skills auto-update. Reuses `AttachmentComposer`
  upload pattern.

**`OrgTree.tsx`** (optional, Phase 2) — department→team→member tree, drag to
re-parent (sets `manager_id`/department), read from `people`.

**Plan-Project chat flow** (extends existing `AssistantRail`)
- Quick-action "Plan a project" → the agent runs `gtd_plan_project`, streams a
  proposed plan as a **generative-UI card** (phases→tasks→subtasks with owner +
  due + effort chips; see [[generative-ui-three-tiers]]), each row editable, with a
  target toggle **Local | ClickUp** → Confirm → creates via §7. Assignee chips show
  the skill-match reason + overload warning (§5).

**Assign-time affordances** (in `ClarifyPanel`/`DelegateDialog`)
- Show the suggested owner with **why** (matched skills) and an **overload** flag
  when `est > capacity`; one-click accept or pick another (feeds the decision→outcome
  memory §9).

---

## 11. Phased implementation plan

Each phase is independently shippable and leaves the app working.

**Phase 1 — HR editable + résumé ingestion (highest concrete value)**
- Migration: editable columns + `people_resumes` + `capability_embedding`.
- Backend: `POST /tasks/people`, `PATCH /tasks/people/{id}`, `POST /tasks/people/{id}/resume`, `resume_parse.py` (PyMuPDF/docx + LLM extract).
- Frontend: `PeopleView`, `PersonEditor`, `ResumeUpload` + `taskStore` people slice + nav.
- Outcome: you can add/edit people, edit skills, and drop a résumé to auto-update skills.

**Phase 2 — sharper assignment + live sync — SHIPPED (2026-07-16, uncommitted)**
- People reconcile against ClickUp membership (§6); active/inactive — DONE
  (`accounts._reconcile_people`, called from `_refresh_schema` +
  `refresh_account_members`; org-wide member union; only auto-added `source='clickup'`
  rows are deactivated — manual/seed people never touched; `ON CONFLICT (name)`).
- Semantic capability match — DONE, flag-gated `task_semantic_match_enabled`
  (default OFF; migration 75 `capability_embedding vector(1536)` + `capability_text_hash`;
  `capability.py` embeds on person write + `POST /tasks/people/embed` backfill;
  `semantic_scores` cosine re-ranks the roster, never drops a keyword candidate).
- Live workload + overload + `reports_to` — DONE (`ai.annotate_people_context`
  counts open assigned gtd_items per person → `overloaded`; `_people_brief` renders
  manager + load; clarify prompt prefers non-overloaded + manager-for-approval;
  `_attach_assignee_load` puts the flag on the proposal's `suggested_assignee`).
- UI: overload chip in `ClarifyPanel` delegate section (`assigneeLoad` on the proposal).
- Guardrail: `propose()`/`_match_capability` UNCHANGED — all 21 GTD golden trajectory
  evals still pass; annotation is route-only and additive.

**Phase 3 — project planning from chat + PM-tool port — SHIPPED (2026-07-16, uncommitted)**
- `gtd_plan_project` agent tool + `routes/tasks/planning.py` — DONE. `POST /tasks/plan`
  (LLM brief → phases→tasks→subtasks with owner/effort/priority/due, ported
  `create_tasks_with_subtasks` contract) + `POST /tasks/plan/apply` (LOCAL creates
  gtd_projects + gtd_items + parent_item_id subtasks; CLICKUP creates the List + tasks +
  subtasks via the provider, broker-gated, mirrors locally). Agent tool PROPOSES then
  applies LOCAL only; ClickUp push stays a human/UI action (C-04). Frontend client
  bindings `apiPlanProject`/`apiApplyPlan` added.
- ClickUpProvider audit vs the 6 API rules — DONE. flat-int assignees ✓ (already),
  subtask-via-parent ✓ (already), **429 back-off ADOPTED** (`_clickup_send` wraps every
  call, honours Retry-After), **18:00 due + `due_date_time` ADOPTED** (`_clickup_due`
  nudges a date-only ms to 18:00 and sets the flag on create + update). v3 Docs: NOT
  adopted (no Docs feature yet — deferred).
- Deferred (optional): plan-project generative-UI card, WBS/Gantt/risk generators,
  research tools, `gtd_rebalance`.

**Phase 4 — dedicated agent memory + framework doc — SHIPPED (2026-07-17, uncommitted)**
- Task-clarification memory loop on `scope_key(agent="task-manager")` — DONE.
  `routes/tasks/task_memory.py`: `recall_clarify_context` (one bounded
  `get_scoped_context` fed into the clarify LLM prompt as "PAST CLARIFICATION
  PATTERNS", only on the LLM path) + `remember_decision`/`remember_decision_background`
  (fire-and-forget `add_scoped_memories` of the COMMITTED decision — title →
  disposition/owner/project/context/next-action). Wired: recall in
  `ai._llm_propose` (new `memory_context=""` param, appended at end so the golden
  eval's positional calls stay valid) via `clarify_item`; write in
  `items.organize_item` after commit. Best-effort + graceful (Mem0 off → recall ""
  / save no-op); NEVER touches the eval-locked deterministic `propose()`.
- Framework doc — DONE. `agent_file_and_memory_framework.md` §8 "Recipe: giving one
  agent a dedicated memory (worked example)" — the five-step protocol (pick scope,
  write hygiene = save committed outcome, recall routing, optional vector table,
  wire into the loop) + the eval-guardrail, using this feature as the reference.
- Deferred (optional): a purpose-built `task_clarification_memory` vector table
  (only if Mem0-fact recall precision proves insufficient); decision→OVERRIDE
  capture (needs the proposal threaded to organize) — the committed-decision memory
  already encodes the user's real choice.

---

## 12. Open decisions (for the user)

1. **Source of truth for HR**: make the monorepo `people` authoritative (edits
   win, external repo becomes a one-time seed) — recommended — or keep two-way sync
   with the external HR system? (Two-way is more work and needs conflict rules.)
2. **`clickup_user_id` linking**: auto-match on résumé/name, or require a manual
   "link to ClickUp member" step in `PersonEditor`? (Recommend auto-match + manual override.)
3. **Semantic matching default**: ship keyword-only first (flagged semantic), or make
   embeddings the default once built? (Recommend flagged, keyword fallback — mirrors email search.)
4. **Résumé storage**: attachments dir (simple) vs blob store (durable, survives
   deploy `git reset`). Given [[deploy-git-reset-wipes-tracked-runtime-files]], prefer
   blob store or an untracked dir + DB row.
5. **Scope of PM-tool port**: assignment + planning only (Phases 1–3), or also the
   engineering-grade WBS/Gantt/risk/research tooling (heavier)?

---

Related memory: [[tasks-reclarify-nesting-aifill]], [[delegate-badge-hr-suggestion-followup]],
[[clickup-two-systems]], [[tasks-process-deepening-phases]], [[agent-memory-scopes-and-report-kit]],
[[agent-blob-store-part2]], [[llm-json-mode-required]], [[email-search-hybrid]].
