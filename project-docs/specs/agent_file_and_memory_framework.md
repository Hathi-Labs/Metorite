# Agent File & Memory Framework (the durable-state contract every MAF agent MUST follow)

**Status:** Part 1 (native-MAF mutation → monorepo PR) and Part 2 (files/memory →
Postgres blob store) built 2026-07-15. This doc is the canonical contract for how
agent code, files, and memory persist — and the required reading before we build
the in-platform **agent-building workbench** or any new MAF agent (here or on a future second tenant deployment *(2026-08-09, under D15: another organization, not another deployment)*).

**Companions:** `agent_persistence_implementation.md` (the engineering reference — every
function, table, and seam; **read this before changing how persistence works**),
`agents-workspaces-artifacts.md` (workspace layout), `llm_caching_memory.md`
(Mem0 scopes), `system_architecture.md` (ADRs), and the dev-only limitation note at
`docs/DESIGN_LIMITATION_native_maf_mutation.md`.

> **This doc = the contract (what agents must do). The implementation reference =
> how it's built (what to edit to change it).** Keep them in sync: a mechanism
> change lands in the implementation reference; a contract change lands here.

> **⚠️ Multi-user gap — read before adopting §4's reference pattern.**
> The blob store is keyed by `agent_name` alone, so **`agent-data/` is shared by every
> user of an agent**: two people using the email assistant append to one `NOTES.md`, and
> `recall_notes(path)` with no query returns all of it. Separately, nothing injects
> `NOTES.md` — §3's prompt *asks* the model to read it, so an agent that skips the call
> runs with no durable memory at all. The tier is therefore both leaky when it fires and
> absent when it doesn't.
>
> Adopting a richer memory bank (§4's `agent-startup-guru` pattern) **amplifies** the first
> problem — the more the pattern succeeds, the more each user's bank holds, and all of it is
> shared. Two additions make it safe, and they belong in the same change:
> an **instance key** on `agent_blob` (`u:<email>` / `t:<team>` / `''`), and a **budget with
> compaction** on the always-on portion.
> Design: [`memory_architecture.md`](memory_architecture.md) §5.3–§6.4 ·
> agent instancing: [`../../docs/multiplayer/agent-kinds.md`](../../docs/multiplayer/agent-kinds.md).

---

## 1. The two axes of agent durability

> **Now three, and "Code" no longer implies Git.**
> [`agent_architecture.md`](agent_architecture.md) §4 adds **Knowledge** — an agent's authored
> KB/RAG corpus, which is neither Code (not executable) nor State (not accumulated). It is
> review-gated like code and compiles to a derived, version-pinned index.
>
> That doc also narrows this one's scope. It targets agents that live **inside Metorite**
> — first-party agents in `apps/agents/` and agents built by the Agent Workshop — and splits
> them into **declarative** (manifest + instructions + KB, stored in Postgres and published
> like a Custom App) and **code** (a real `agents.py`, in the monorepo). Four of six
> first-party agents are declarative in everything but form. For a declarative agent the
> "Code goes to git, human-reviewed" rule below becomes *"the manifest is versioned and
> published behind an approval gate"* — same intent, no PR, and it removes the DEV-ONLY
> monorepo-mutation limitation in §6 for the majority case.

Every agent has two fundamentally different kinds of persistent state. They are
stored by two different mechanisms, and conflating them is the mistake this
framework exists to prevent.

| Axis | What it is | Store | Reviewed? | Survives |
|------|-----------|-------|-----------|----------|
| **Code** | What the agent *is* — `agents.py`, `config.json`, `instructions.md`, skills | **Git** (monorepo PR for native MAF; own repo for GitHub agents) | Yes — human PR approval | Everything (it's source) |
| **State** | What the agent *accumulates* — files, memory, artifacts | **Postgres blob store** (authoritative) + disk cache | No — it's runtime data | Volume wipe, redeploy, box migration |

> **Rule:** Code goes to git, human-reviewed. State goes to the blob store,
> untracked. Never put accumulated state in git; never put code in the blob store.

---

## 2. The three folders (the agent's filesystem contract)

Every MAF agent workspace exposes exactly three folders in the file manager. Their
roles are distinct and load-bearing — treat them as a contract, not a convention.

### `agent-data/` — durable knowledge (an extension of the system prompt)
The agent's memory file (`NOTES.md`) plus any accumulated reference data. **Think
of this as prompt that grows over time**: files here shape the agent's behaviour on
every future run. This is where "the agent gets smarter" lives.
- Written by: `save_note` / `save_agent_memory` (memory), the agent's own tools.
- Persistence: blob store, authoritative. Survives everything.

### `inputs/` — user uploads (promotable to permanent)
Files a user uploads for the agent to work with. Ephemeral by default, but can be
**promoted to `agent-data/`** to become permanent, behaviour-shaping knowledge
(`POST /agent/workspace/{session}/promote`, or right-click → "Promote to Agent
Data" in the file manager).
- Written by: the upload endpoints.
- Persistence: blob store (so an upload survives a wipe too).

### `outputs/` — everything the agent generates
All files, folders, and projects the agent produces — reports, documents, HTML,
data, code. This is what the user views as *results*.
- Written by: `write_artifact` / `share_artifact`, the agent's own tools.
- Persistence: blob store, authoritative.

Everything **outside** these three folders (agent source, `.git`, caches) is NOT
stored as state — it comes from the agent's git repo, not from accumulated runtime.

---

## 3. How it works (the mechanism built in Part 2)

**Backing store (authoritative), disk (cache).** Same model as Mem0: Postgres is
the source of truth; the on-disk workspace at `{agents_clone_dir}/repos/{agent}` is
a rehydratable cache.

- **Tables** (`infra/postgres/71_agent_blob_store.sql`):
  - `agent_blob` — current content of every live file, keyed `(agent_name, path)`.
  - `agent_file_history` — **append-only log of every unique version** (by sha256)
    an agent created or modified over time. This is the "track every unique file"
    requirement: each row is a directly-retrievable version, with action
    (`create`/`modify`/`delete`/`promote`), actor, run/session provenance.
- **Store module:** `acb_memory/blob_store.py` — `put_file / get_file / list_files
  / delete_file / file_history / rehydrate_workspace`. Keyed by `agent_name` only
  (⚠️ **this was the sole tenant key under D11 and is no longer sufficient** — D15
  makes the tenant an `organization_id` row, so `agent_blob` gains that column plus RLS in
  **MT-1b** and its content moves to object storage in **MT-1g**;
  `saas_multitenancy_implementation.md` §1 holds the shapes). Graceful: DB down →
  no-op, agents keep working off disk.
- **Write-through** at every write path (disk write + store mirror + history row):
  - Agent-side: `write_artifact` and `save_note` → `mirror_to_blob_store(...)`.
  - Gateway: PUT save, upload, delete, and the artifacts equivalents →
    `_mirror_gateway_write` / `_mirror_gateway_delete`.
- **Rehydrate on load:** the executor calls `rehydrate_workspace(agent, root)`
  before every run, so a wiped/migrated volume comes back from the store.
- **Fault-in on read:** the gateway file-read endpoints restore a missing file from
  the store on demand (`_faultin_from_store`), so the file manager / chat /
  artifacts apps keep working even before the agent re-runs.
- **Read paths unchanged:** the file manager, chat, and artifacts apps still read
  the disk workspace via the existing endpoints — the store sits *behind* them.

---

## 4. Agent roster — who must follow this framework

**Every MAF agent, existing and future, MUST use the three-folder + blob-store +
git-code contract above.** The universal tool injection already gives all agents
the write/memory tools; the persistence is automatic once an agent writes into the
three folders. No agent is exempt.

### Currently built (registry `agent_registry.json` + in-repo)
| Agent | Runtime | Purpose | Framework status |
|-------|---------|---------|------------------|
| `task-manager` | MAF | GTD / ClickUp tasks, workload Q&A | in-repo; must follow |
| `sales` | MAF | Zoho CRM pipeline + deal follow-ups | must follow |
| `delivery` | MAF | Project delivery monitoring + notifications | must follow |
| `triage` | MAF | Email / WhatsApp / meeting triage + routing | must follow |
| `reconciler` | MAF | Nightly source-of-truth diff + escalation | must follow |
| `billing` | MAF | Billing & invoice workflows | must follow |
| `strategy` | MAF | Weekly digest + planning synthesis | must follow |
| `email-assistant` | MAF | Inbox triage + drafting (in-repo, `apps/agents/`) | must follow |
| `orchestrator` | MAF/Copilot | Router / general brain | must follow |

### Reference implementation (the pattern to copy)
`agent-startup-guru` (GitHub-sourced MAF agent) — a self-contained memory bank
under `outputs/_memory/` + `agent-data/` managed by a `memory-management` skill
(JSON/MD working memory + SQLite FTS long-term). This is the model for what a rich
`agent-data/` looks like.

> **Copy the structure, not the storage — and add two things it never needed.** It is a
> single-user agent's design: it assumes one bank, one reader, and unbounded growth. Before
> porting it here it needs an **instance key** and a **budget with compaction**
> ([`memory_architecture.md`](memory_architecture.md) §6.1, §6.4). Skip the SQLite FTS layer
> — our Mem0 + pgvector `agent:<name>` partition already beats lexical FTS on recall and is
> one store instead of two; §8 below reached the same conclusion. **Open question for
> whoever has repo access:** does its memory-management skill keep an index/manifest, and
> does it already implement compaction? If so, port that rather than inventing ours.

### Upcoming / to-be-built MAF agents
Any new specialist agent (whether authored in VS Code today or in the in-platform
workbench later) inherits this contract by construction. When we add a new agent we
MUST confirm: (a) it writes deliverables to `outputs/`, working knowledge to
`agent-data/`, treats `inputs/` as promotable; (b) it uses the memory tools for
durable facts; (c) its code mutation flows to git (PR), never the blob store.

> **ACTION for new-agent work:** add a line to the agent's `instructions.md` /
> checklist confirming it follows this framework, and verify the three folders +
> memory tools are exercised in its golden eval.

### Retrofit note (existing agents)
The mechanism is automatic (write-through fires wherever an agent writes into the
three folders), so existing agents get durability for free. What to VERIFY per
existing agent during hardening: they are actually writing into the three folders
(not the working-dir root), and that anything they rely on across sessions lives in
`agent-data/` (not a scratch file that isn't backed).

---

## 5. Building the in-platform agent-building workbench — what to consider

When we build the workbench that authors MAF agents *inside* the platform (rather
than VS Code + Git), it MUST preserve every invariant here. Considerations:

1. **Code is still git-backed and human-reviewed.** The workbench authors an
   agent's `agents.py` / `config.json` / skills, but those are *code* — they land
   via a reviewed PR (see the mutation flow), not the blob store, not a live edit
   to production. The "No in-app agent/skill editing of production" constraint
   (AGENTS.md Global Constraints #1) still holds; the workbench produces a
   reviewable change, it doesn't hot-patch a running agent.
2. **State is blob-store-backed from day one.** A workbench-authored agent gets the
   same three folders + write-through + rehydrate automatically — because that's
   keyed on `agent_name`, not on how the agent was authored.
3. **The three-folder contract is enforced, not optional.** The workbench should
   scaffold `agent-data/ inputs/ outputs/` and steer generated tool calls to write
   there. A generated agent that writes to the working-dir root is a bug.
4. **`agent_name` is the tenant key.** Everything (blob store, memory scopes,
   mutation target) hangs off `agent_name`. The workbench must allocate a unique,
   stable `agent_name` per agent and never reuse one.
5. **Memory scopes carry over.** Workbench agents get the same three memory scopes
   (user / agent-cross-user / org-global — see `llm_caching_memory.md`). Decide at
   author time what belongs in `agent-data/` (prompt-extending files) vs. Mem0
   (semantic recall).
6. **⚠️ Mutation remote is the open production question.** The current native-MAF →
   *monorepo* PR path is DEV-ONLY (see §6). Before the workbench ships to
   multi-tenant / customer use, the mutation target MUST become tenant-isolated —
   this is the single biggest unresolved design decision for the workbench.

---

## 6. Second-tenant portability + the production mutation gap *(phrasing predates D15 — read 'second tenant' as 'another organization', whose isolation is rows + RLS, not a deployment; a dedicated deployment survives only as a priced placement)*

**Portability:** the blob store, three-folder contract, and memory scopes are all
keyed on `agent_name` with no Metorite-specific coupling, so MAF agents built
on a second tenant deployment *(2026-08-09, under D15: another organization, not another
deployment)* use the identical mechanism. When we stand up
agents there, they must adopt this framework verbatim — same tables, same tools,
same folders. Do not fork the storage model per platform.

**The one thing that does NOT port as-is — code mutation:** today a native MAF
agent's approved self-mutation opens a PR against the **shared Metorite
monorepo**. That is fine only while all agents are first-party and Metorite is
WIP. For multi-tenant / customer agents this is unacceptable — third
parties must never push to the shared monorepo. This must be replaced (per-tenant
repo, or a tenant-scoped store the loader reads at runtime) before production
*(ticketed as MT-0b, built 2026-08-08 pending review)*. Full
detail: `docs/DESIGN_LIMITATION_native_maf_mutation.md`.

---

## 7. Checklist for anyone touching this

- [ ] New agent writes deliverables → `outputs/`, knowledge → `agent-data/`, treats
      `inputs/` as promotable. Never the working-dir root.
- [ ] Durable cross-session facts use the memory tools (`save_agent_memory` /
      `save_note`), which land in `agent-data/` and/or Mem0.
- [ ] Code changes flow to git via a reviewed PR — never the blob store.
- [ ] `agent_name` is unique and stable (it's the storage + memory + mutation key).
- [ ] For workbench / multi-tenant work: the mutation target is tenant-isolated
      (NOT the shared monorepo) before any multi-tenant deployment. *(ticketed as
      MT-0b, built 2026-08-08 pending review)*

---

## 8. Recipe: giving one agent a dedicated memory (worked example)

The three scopes (§5, `llm_caching_memory.md`) are the *substrate*; a "dedicated
agent memory" is just a deliberate **write/recall protocol** on the
`agent:<name>` scope. Both external repos we drew from (agent-startup-guru,
agent-project-manager) use lexical SQLite FTS for this — our Mem0 + pgvector
`agent:<name>` partition already exceeds them on semantic recall, so the work is
protocol, not plumbing. Reference implementation:
`gateway/routes/tasks/task_memory.py` (the task-manager's clarification memory).

**The five steps** (copy this shape for any agent that should learn on the job):

1. **Pick the scope.** `scope_key(agent="<name>")` — the cross-user
   `agent:<name>` Mem0 partition. Use the SAME `<name>` the app-side helper and
   the agent's own `recall_agent`/`save_agent_memory` use, so both land in one
   partition. (For a per-user habit, use `scope_key(user=email)` instead; for
   org-wide facts, `scope_key(org=True)`.)

2. **Define what it saves, and WHEN (write hygiene).** Save the *committed
   outcome*, never the proposal — the real decision is the signal worth learning.
   The task-manager saves on `organize` (the user's committed clarify decision:
   "task X → disposition D, owner Y, project P, context C"), not on the proposal.
   Write is **fire-and-forget + best-effort** (`add_scoped_memories`, swallow all
   errors) so it never slows or breaks the request that produced it.

3. **Define recall routing (when to spend a search vs. use loaded context).**
   Recall ONE bounded `get_scoped_context` right before the expensive reasoning
   step, and only when that step is already running (the task-manager recalls
   only on the LLM clarify path, which is already a round-trip). Feed the result
   into the prompt as a labelled block ("PAST CLARIFICATION PATTERNS"), with a
   system-prompt line telling the model to treat it as *the user's own prior
   decisions — prefer consistency, but the current item wins*.

4. **(Optional) a purpose-built vector table** when recall precision needs
   task-specific structure Mem0's free-text facts can't give (e.g. exact
   disposition/owner columns to `ORDER BY cosine`). Start WITHOUT it — Mem0 facts
   are enough until proven otherwise. (Phase 2's `gtd_people.capability_embedding`
   is exactly this pattern for the *people*-matching side.)

5. **Wire recall + save into the agent's loop.** Native/Copilot agents already
   have `recall_agent`/`save_agent_memory` tools; an app-side engine (like the
   gateway clarify route) calls `get_scoped_context`/`add_scoped_memories`
   directly. Keep it graceful: Mem0 disabled → recall "" and save no-op, so the
   feature is purely additive.

**Guardrail:** a dedicated memory must never change an eval-locked deterministic
path. The task-manager's recall feeds only the *LLM* clarify prompt; the
heuristic `propose()` and the golden trajectories are untouched. Add memory as an
overlay on the cognition, never as a new branch in the guaranteed baseline.
