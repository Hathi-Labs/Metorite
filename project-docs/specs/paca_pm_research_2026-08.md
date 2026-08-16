# Paca PM-platform research — what to adopt, adapt, and refuse (2026-08)

> ⚠️ **RESEARCH — REFERENCE-ONLY (2026-08-10 consolidation, D26).** No work dispatches from
> this document; owns no work by its own declaration. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Product:** Metorite · **Concern:** research appendix for the native project-management
> app (WS-27) · **Created:** 2026-08-05 · **Status:** 🟢 research complete — **reference-only,
> owns no work and no status**; every adaptation verdict below is annealed into
> `specs/project_management_app.md`, which is the owning spec · **Owner:** vjvarada
>
> **Research provenance (2026-08-05):**
> - `Paca-AI/paca` @ master (v0.11.0) — **Apache-2.0: code may be copied with attribution**,
>   but the stack (Go/chi + sqlx, React/TanStack Start, Node/Socket.IO, OpenHands sandboxes)
>   does not survive translation into our Python/FastAPI + Next.js + MAF platform. We take
>   **design patterns and schema shapes, not code.** Facts below were verified against a
>   shallow clone of the actual tree, not the README.
> - ⚠️ Paca's own docs (`docs/architecture/repository-structure.md`) claim "Go + Gin"; the
>   API is actually **chi v5** (`services/api/internal/transport/http/router/router.go`).
>   And `docs/architecture/automation-workflows.md` documents a **dropped** v0.10 design
>   (migration `000027` `DROP TABLE … CASCADE`d it). Anyone re-deriving from Paca's docs
>   instead of its migrations will copy two things that no longer exist.

---

## 1. What Paca is, and why it maps onto us

Paca is a self-hosted project-management platform (Jira/ClickUp/Monday alternative) whose
central bet is that **AI agents are first-class project members** — assignable, mentionable,
permission-checked — not chatbots bolted on the side. Surfaces: `services/api` (Go/chi,
system of record), `services/realtime` (Socket.IO fan-out), `services/ai-agent`
(Python/FastAPI + OpenHands sandboxes), `apps/web`, `apps/mcp` (`@paca-ai/paca-mcp`),
`apps/acp-bridge` (local Claude Code / Codex / Gemini CLI bridge). Postgres + Valkey
(cache, pub/sub, and durable streams).

Why it maps onto Metorite: it is the same architectural species — an event-driven
platform where humans and agents share one store, one API, and one activity stream. Its
task/project data model, its automation graph, and its assignment→agent dispatch chain are
exactly the three things WS-27 needs. Its multi-service split is the part we **don't** need:
Metorite already owns realtime (AG-UI/SSE), agent runtime (MAF), and an automation app
(`/workflows`), so the adaptation is "absorb the data model and the patterns into the
existing platform", never "stand up sibling services".

## 2. Data model — the part to study hardest

Source of truth: `services/api/migrations/000001_init.sql` (695-line baseline) +
`docs/architecture/database-schema.md` (narrative + DBML). Migrations are embedded and
**re-run on every API boot**, so every statement is idempotent — same discipline as our
`infra/postgres/README.md` `02+` rule, independently converged.

### 2.1 Containers: no workspace, just projects

Paca is single-tenant per install; the top-level scope is `projects` (name, description,
`task_id_prefix`, `settings jsonb`, `is_public`, soft-delete). There is **no**
Space→Folder→List container zoo — ClickUp's is widely disliked and Paca deliberately has one
container. "Workspace" exists only as a computed aggregate endpoint. Departments don't exist
either; that concern is ours alone (we have `org_group`/Centers for it).

### 2.2 Hierarchy: one self-FK, types are semantics

**There is no epic/story/subtask table and no depth column.** `tasks.parent_task_id` is a
single adjacency-list self-FK (`ON DELETE SET NULL`) of arbitrary depth; "Epic" vs "Story"
vs "Subtask" is purely `task_type_id` semantics (types are per-project rows: name, icon,
color, `is_system`). Exactly two hard rules, both in `task_service.go`:

1. A task whose type is the system **Epic** type cannot have a parent (`ErrEpicCannotHaveParent`)
   — Epic is structurally the root level. `Subtask` was once a system type and was demoted
   to an ordinary editable type (`000012`) — the hierarchy needed no help from the type system.
2. `wouldCreateCycle()` — a depth-bounded (50) ancestor walk at write time. No closure table.

Human-readable IDs: `task_counters(project_id, last_value)` + `tasks.task_number`
`UNIQUE(project_id, task_number)` → `PACA-42`.

**Verdict: adopt wholesale.** This is the strongest single lesson: multi-level
department→project→subproject→task→subtask hierarchy needs exactly two self-FKs (one on
projects, one on tasks) plus types-as-data — not per-level tables.

### 2.3 Statuses, types, custom fields: rows, not enums

`task_statuses` are per-project rows — name, color, `position` (lane order), and a
**`category` CHECK** (`backlog|refinement|ready|todo|inprogress|done`) that carries the
machine-readable semantic while name/color/position stay free. `category='done'` is what
drives sprint completion and the `predecessor_done` automation trigger — no transition-chain
table. Partial unique `ON (project_id) WHERE is_default` guarantees one default. This is the
same shape our CRM already chose (D-CRM-2, `crm_deal_statuses.type`), independently.

Custom fields: `custom_field_definitions` (`field_key`, `field_type ∈
{text,number,date,select,multi_select,boolean,url}`, `options jsonb`) with values
denormalized into `tasks.custom_fields jsonb` keyed by `field_key`. Deleting a definition
does not clean task data — accepted cost.

Tags: a bare `jsonb` string array on tasks. No registry, no colors, no rename/merge —
**the weakest part of Paca's model**; don't copy it as-is.

### 2.4 Ordering: per-view fractional indexing, no rank column

**There is no position/rank column on `tasks`.** Ordering is per-view, in a side table:
`view_task_positions(view_id, task_id, position DOUBLE PRECISION, group_key,
UNIQUE(view_id, task_id))`. Fractional indexing on float64
(`docs/architecture/manual-sort-algorithm.md`): between → `(prev+next)/2`; append →
`(prev+MAX_SAFE_INTEGER)/2`; prepend → `next/2`. One row written per drag. Tasks with no
row sort to the bottom by `created_at`; the first drag into that zone bulk-materialises
positions for the group. `group_key` records the board column, so a cross-column drag is
one upsert. Renormalisation is documented and deliberately unimplemented (~52 halvings per
gap makes it moot).

**Verdict: adopt.** The same task can sit in the People-Center master board and in a
Sales-Center slice with *different manual orders* without the two views fighting over one
rank column — this is precisely the "slices per Center" requirement, solved structurally.

### 2.5 Members: one actor table for humans and agents

`project_members(project_id, user_id NULL, member_type 'human'|'agent', agent_id NULL,
project_role_id, deleted_at)` — and **`project_members.id` is the actor identity
everywhere**: assignee (`task_assignees` M:N), reporter, comment author, activity actor,
notification actor. Removing a member soft-deletes; re-adding **restores the same row**, so
actor FKs stay stable. An AI agent becomes assignable by getting a member row — zero
special-casing downstream.

**Verdict: adapt.** We don't need the member table (identity is `app_user` + `org_group`),
but the *principle* — assignee/actor is one vocabulary that admits both humans and agents —
maps directly onto our existing `email | agent:<name>` actor-string convention
(`crm_activities.created_by`, `pending_actions.actor`). Adopt the principle, not the table.

### 2.6 Activity spine: comments and system events are one table

`task_activities(task_id, actor_id NULL, activity_type, content jsonb, deleted_at)` —
`comment`, `task.updated` (with `{"changes":[{field,old,new}]}` powering **diff & revert**),
attachment/link events, `agent.session.started`, `automation.applied`. `actor_id` nil =
system. Activities are written **asynchronously** (handlers append to a Valkey stream; a
consumer writes the row). Same single-spine shape as our `crm_activities` (trycompai
lineage) — three tools have now converged on it.

### 2.7 The rest, briefly

Sprints (`planned|active|completed`, multiple active allowed); `task_links`
(`blocks|relates_to|duplicates`, `CHECK(source<>target)`); central `files` registry + thin
`task_attachments` join (S3/MinIO presigned upload); `notifications` keyed to recipient
user + actor member; `api_keys` (SHA-256 hash, `paca_` prefix, shown once). Checklists,
GitHub integration, BDD, and time tracking were all **migrated out of core into plugins** —
the growth path is subtraction.

## 3. Views and boards

One `sprint_views` table serves all interactions: `view_context ∈
{sprint,backlog,timeline}` × `view_type ∈ {table,board,roadmap,plugin}`, project-level when
`sprint_id IS NULL`. The `config` JSONB splits **presentation** (top level: `fields`,
`column_by`, `swimlanes`, `sort_by`, `field_sum`) from **query constraints**
(`config.filters`: sprint/status/assignee/type id arrays). One task-list endpoint
(`GET /projects/:pid/tasks`) serves every page; passing `view_id` enriches each task with
its `view_position`/`view_group_key`. Board columns come from `column_by` generically —
dragging into a column patches *whatever field the view groups by*, not hard-coded status.
Filters support **virtual group keys** ("every non-system type") expanded at query time so
stored views don't go stale when new types are created.

**Verdict: adopt the shape** (one view resource + one list endpoint + config JSONB), skip
sprint-context in v1 (sprints are a WS-27 non-goal).

## 4. Automation engine (v0.11) — trigger/condition/action graphs

Schema (`000027_add_automation_graph.sql`): `automations` (`draft|active|archived`) +
`automation_nodes(kind ∈ trigger|condition|action, type, config jsonb, pos_x, pos_y)` +
`automation_edges(source_handle NULL …)` + `automation_runs` + `automation_run_steps`
(per-node `input_snapshot`/`output_snapshot`/`error`) + at-most-once bookkeeping tables for
due-date and cron fires + hashed webhook tokens (`pacahk_` prefix, rotation revokes prior).
**One JSONB `config` per node** serves 9 trigger types + conditions + 3 actions + unbounded
plugin types with no wide null-column set.

- **9 triggers:** `status_changed`, `task_created`, `assignee_changed`, `priority_changed`,
  `tag_added`, `due_date_reached` (offset minutes, polled), `predecessor_done`
  (AND-join over watched tasks — **stateless**: re-derives every watched task's live status
  category, no persisted counter, so it's idempotent under at-least-once redelivery),
  `cron` (5-field UTC), `api_trigger` (inbound webhook).
- **Condition:** an N-branch switch — ordered branches, first-true-wins, reserved `else`
  handle; each branch is a **flat single comparison** (field × operator), no AND/OR nesting.
  A `validOperatorsByField` table rejects unimplemented combos at validation time instead of
  silently evaluating false at runtime.
- **3 actions:** `update_task` (merged five prior single-field actions into one multi-field
  patch — an explicit consolidation lesson), `trigger_ai_agent` (`{message, member_id}`),
  `call_api` (outbound HTTP; its stored headers are visible to project readers — a known,
  commented gap; don't reproduce it).
- **Task retargeting:** a condition or action can aim at `self | parent | children |
  blocks | is_blocked_by | relates_to | duplicates | other(id)`; multi-valued targets
  fan out (action per task; condition combines via all/any).
- **Execution** (`worker/automation_consumer.go`, 1602 lines): consumes the *ordinary*
  activity stream — the engine is "a sibling reader, not a special case wired into the HTTP
  handler". Maps field changes → candidate trigger types (zero candidates ⇒ cheap ack),
  re-fetches the authoritative task, walks the graph with a `visited` set, records a step
  row per node, and **mutates through the ordinary task service** so automation edits get
  identical validation and an `automation.applied` activity with nil actor. Every action
  checks "already in target state" before writing, which is what makes a crashed walk safe
  to retry.
- **Run history + dependency map:** `automation_runs`/`_run_steps` power a per-run trace
  panel; the dependency map is **derived** on read from active `predecessor_done` nodes,
  never separately maintained.

**Verdict: adapt into `/workflows`, never build a sibling.** Metorite already has a
graph automation app (WS-11: DB graphs compiled to MAF workflows, manual/webhook/cron
triggers, run console) and ADR-028/D6 makes `workflows_app.md` the single owner of the
engine. What Paca proves is the *binding*: task events feeding the trigger vocabulary,
task-mutation and dispatch-agent action nodes, per-step traces, and the stateless
AND-join/idempotency discipline. WS-27 emits task events into the existing
`event_hooks.emit_event → workflows/triggers.dispatch_event` path and contributes node
types; deeper engine uplifts (multi-branch switch, step snapshots, dependency map) are
recorded as `workflows_app.md` backlog, not duplicated.

**Written up 2026-08-06 → [`workflows_app.md`](workflows_app.md) §13.** This section's
findings now have a home that owns work: eight items **U1–U8**, each pairing the Paca design
above with that engine's *measured* current state and a done-when. Read §13, not this
section, when implementing — §13 also records the five Paca features **deliberately refused**
(`call_api`'s reader-visible headers, a sibling worker process, the WASM plugin runtime, a
second engine, and a per-fire bookkeeping table where our CAS on `last_fired_at` is already
better), so the refusals do not read as oversights to a later implementer.

## 5. Agent integration — the dispatch chain

The chain is fully event-driven; the HTTP handler never calls the agent runtime:

1. A human (or the automation engine) assigns a task → `task.assigned` appended to a
   durable stream with one payload shape for all sources (`extra` carries attribution like
   `automation_name`).
2. The notification consumer writes the in-app notification, and — **if the assignee is an
   agent member** — creates an `agent_conversations` row (`queued`) and appends a trigger
   (`{conversation_id, project_id, agent_id, task_id, trigger_type, message}`) to
   `paca:agent:triggers`. It also records an `agent.session.started` activity on the task,
   so the handoff is visible in the task timeline immediately.
3. `services/ai-agent` consumes with a semaphore-bounded worker, spins an OpenHands sandbox
   (or reuses a warm one for chat), and streams every conversation event both to a durable
   stream and to the realtime channel; events persist to `agent_conversation_events` with a
   DB-seeded event index so resumed turns can't collide.
4. **The agent updates the task through the ordinary MCP tools against the API** — API key +
   `X-Agent-ID` header, permission-checked as its own project member. The AI service never
   writes to Postgres directly (a stated "Boundary Rule").

Trigger types beyond assignment: `comment_mention` (@handle), `chat_message` (in-app
project chat, warm sandbox + heartbeat), `description_write` ("write with AI"),
`automation_message`. Controls (`stop|pause|heartbeat`) ride the same stream.

**ACP mode** (v0.10): an agent can instead be a developer-side CLI (Claude Code, Codex,
Gemini CLI) connected via `paca-acp-bridge` — an **outbound** WebSocket from the dev's own
checkout, authenticated by a `hello` frame token, with a server-side watchdog that fails the
conversation if no terminal status arrives. No code enters a cloud sandbox; the CLI uses its
own local credentials. Events persist through the same path, so the UI renders both sources
identically.

**Verdict: adopt the chain shape, map onto MAF.** Assignment-to-`agent:<name>` → event →
orchestrator dispatch → activity-visible session → agent writes back through the same
gateway API under its own identity (our `EffectiveAccess.intersect()` already narrows an
agent by the acting member — Paca has nothing this strong). The ACP bridge is prior art for
"hand a task to the owner's local Claude Code" and worth a later ticket, not v1.

## 6. MCP server — tool-design lessons

`@paca-ai/paca-mcp` (stdio, TS). Worth stealing regardless of transport:

- **Permission-filtered `ListTools`** — the tool list is computed from the caller's actual
  permissions, and single-project mode hard-rejects calls naming another project.
- **Collapse tools.** An earlier automation surface exposed 16 tools and confused calling
  agents; it was deliberately collapsed to 4 (`get/create/update/delete_automation`) taking
  rich nested payloads with **per-item outcomes** (one bad entry doesn't block siblings) and
  **lenient removes** (removing a missing thing is a no-op) so partial-failure retries are
  safe.
- **Internal UUIDs are never agent-facing** — nodes are addressed by task id, transitions by
  status id; the MCP layer resolves. The agent never needs a read round-trip to write.
- Agents write plain Markdown; the MCP layer converts to the store's block format.

## 7. Realtime and eventing

Valkey carries **two deliberately separate transports**: Pub/Sub (`paca.events`) for
immediate Socket.IO fan-out, and durable Streams (activities, assignments, agent triggers,
plugin events, automation triggers) for at-least-once work — realtime reads only the
former. Socket.IO rooms are per-project-per-domain (`project:<id>:tasks` etc.);
**permission is checked once at join**, never per message; an expired-token join
disconnects the socket to force a fresh reconnect. The realtime service verifies JWTs by
calling the API (one source of truth) and never persists raw tokens.

**Verdict: no new service.** Our equivalents exist (Redis streams + `event_hooks`, AG-UI/SSE,
BO-20 consumer). The lesson to keep is the **two-transport separation** and "nothing is
written synchronously that a consumer can write".

## 8. Plugin system (context only)

Backend plugins are WASM (wazero) with a capability manifest (`db:read:tasks`,
`events:subscribe:task.*`…), typed query builders instead of raw SQL, and per-plugin KV +
cache namespaces; frontends load via Module Federation; plugins can contribute automation
node types, MCP tools, and views. This is Paca's answer to the problem our App
Workshop/skills registry already answers differently — noted for awareness, **not** an
adoption target (ADR-028: no second runtime).

## 9. The architectural decisions worth carrying, in one table

| # | Paca decision | Verdict for WS-27 |
|---|---|---|
| 1 | Hierarchy = one `parent_task_id` self-FK + types-as-data; Epic-root + cycle-walk are the only rules | **Adopt** (plus a project self-FK for subprojects) |
| 2 | Statuses as per-project rows with a semantic `category` CHECK | **Adopt** (converges with D-CRM-2) |
| 3 | Per-view fractional-index ordering; no rank column on tasks | **Adopt** — it is what makes Center slices order-independent |
| 4 | One view resource + one task-list endpoint; presentation vs filters split in config JSONB | **Adopt** |
| 5 | Agents are ordinary members/actors; one actor vocabulary everywhere | **Adapt** onto `email \| agent:<name>` actor strings |
| 6 | Single activity spine; comments = activities; field-diff content enables revert | **Adopt** (converges with `crm_activities`) |
| 7 | Automation = trigger/condition/action graph over the ordinary event stream, mutating through the ordinary service, idempotent everywhere | **Adapt into `/workflows`** — bind, don't rebuild |
| 8 | Assignment→agent dispatch via events, session visible as a task activity, agent writes back through the same API under its own identity | **Adopt**, mapped onto MAF + Action Broker conventions |
| 9 | Stateless AND-join + "already in target state" checks + at-most-once fire tables | **Adopt** as the idempotency discipline for sync + automation |
| 10 | Config-as-JSONB for polymorphic nodes | **Adopt** where node/config polymorphism appears |
| 11 | MCP: few rich tools, per-item outcomes, lenient removes, no internal UUIDs | **Adopt** for the agent-facing tool surface |
| 12 | Two Valkey transports (pub/sub vs streams), permission-once-at-join rooms | Already have equivalents; keep the separation principle |
| 13 | Tags as a bare JSONB array | **Refuse** — weakest part of the model |
| 14 | Sibling services for realtime/agents; WASM plugin runtime; BlockNote docs | **Refuse** — Metorite already owns these concerns |

Everything above is annealed into `specs/project_management_app.md` (WS-27), which owns all
work, decisions, and status. This file is evidence, not a plan.

---

## 10. Second-pass corrections — the agent layer (2026-08-10)

*Pinned commit `09dab28e3caee9e43891697998dcfa7fcf76991c`. A full re-read of
`services/ai-agent`, `apps/mcp`, `services/realtime`, `apps/acp-bridge` and `apps/e2e`
**re-derived §4–§8 from the code rather than from this document.** Ten claims above are
wrong, stale or overstated. The original text is left in place and corrected here rather
than silently edited, because which way a claim was wrong is itself the useful information —
several of these made Paca look like prior art for something it does not have.*

**C1 — §5's "the AI service never writes to Postgres directly (a stated Boundary Rule)" is
false, and the phrase is not in their tree.** The agent service owns its own asyncpg pool
and writes on every run (conversation status, one row per event), reads five tables
directly, and decrypts the LLM key itself. The real boundary is narrower and worth stating
precisely: **domain writes go through MCP→API under the agent's identity; the runtime's own
bookkeeping is direct DB.** ⚠️ For us that direct pool is exactly the shape R5 forbids — a
DB connection site outside the `get_db()` seam.

**C2 — §6's "internal UUIDs are never agent-facing; no read round-trip to write" is
inverted.** Every write tool takes raw UUIDs and the tool descriptions *mandate* the
discovery call ("Use `list_projects` to get the project ID"). The pattern we described is
real **only for documents**, which are addressed by filesystem path with the MCP layer
resolving path→UUID. That narrow case is the good idea; the generalisation was ours, not
theirs.

**C3 — §6's "permission-filtered ListTools" is true but the implication is not.** Filtering
happens in `ListTools` only — `CallTool` consults no permission map, so a hidden tool still
executes if named. Tools absent from the permission table are **allowed by default**, and
seven currently-routable tools have no entry. The table is maintained parallel to the
routing switch with **no completeness test**.

**C4 — §6's "lenient removes" is not implemented.** Per-item outcomes are real and good; a
removal of something missing is reported as a failure, not absorbed as a no-op. The
"16 tools collapsed to 4" history could not be verified at this commit.

**C5 — §4's action-consolidation claim is right about the engine and did not land
end-to-end.** Three built-in actions exist; the agent-facing MCP schema still advertises
**seven**, documenting the config shape of types the API now rejects. Nothing tests the two
lists against each other. Cite Paca for the consolidation *design*, never as evidence that
consolidating is cheap to finish.

**C6 — §7's "durable Streams for at-least-once work" is not true on the agent path.** The
Python consumer creates its group at `$` (anything published earlier is lost), reads only
new entries, never reclaims — there is **no `XAUTOCLAIM`/`XPENDING`/`XCLAIM` anywhere in
the repository** — and acks only on success, so a failed trigger is never retried and leaks
a pending entry forever. Their Go consumer does it correctly, so this is a regression
against their own house standard, not a design position.

**C7 — §5's "spins an OpenHands sandbox" understates the deployment cost.** The sandbox is a
sibling container spawned through the **host Docker socket**, bind-mounted into the agent
service — root-equivalent host access. That belongs in the record as a prerequisite, not an
implementation detail.

**C8 — §5's "permission-checked as its own project member" is true at the API and the
credential model is weaker than it sounds.** There is **one shared deployment-wide agent API
key**, and which agent is acting is asserted by an `X-Agent-ID` **header the caller
supplies**. Anything holding the key can act as any agent in any project. ⚠️ Direct
collision with **R11** (never take an identity from request input) — a refusal, not a model.
Their ACP bridge tokens are per-agent, hashed and rotatable, which is the shape to copy.

**C9 — §8's "backend plugins are WASM (wazero)" is false for the agent-facing half.** Plugin
MCP tools are arbitrary ES modules dynamically imported **into the MCP server process** and
handed the API key — same process, same credential, no sandbox. They are not
permission-filtered and are dispatched **before** core tools, so a plugin declaring
`update_task` shadows the real one.

**C10 — §5: `automation_message` is a fifth trigger type surviving only on a fallback
branch.** Latent misrouting for any future control type carrying a `trigger_type` field.

### What the re-read CONFIRMED
§5's dispatch-chain shape (event-driven, one payload shape for all sources, session marker
written on the task); the ACP watchdog; the DB-seeded event index; §7's "permission checked
once at join" and "realtime never persists raw tokens"; §4's `call_api` reader-visible-header
gap. Also confirmed, and useful to us: **their realtime service is an accelerant, not a hard
dependency** — the publish path's own docstring says clients "see new messages without
waiting for the next poll cycle", i.e. there is a poll fallback. That is the opposite of the
Plane finding about their collaboration server, and it means nothing in the agent surface
implies a sibling service.

### ⚠️ The biggest finding is an absence
**There is no approval or human-in-the-loop primitive anywhere in Paca's agent layer.** A
grep of the whole territory for approve/approval/confirm/consent returns prose only. An
agent's autonomy is exactly its project-role permission set, exercised unilaterally; the only
human levers are pause and stop, both after the fact. The "you MUST invoke a skill before
acting" rule is a paragraph in a prompt with nothing enforcing it.

For an **AI-driven company operating system**, that is the single most important gap, and the
correct conclusion is that **Paca is not prior art for it.** If we want "an agent may do X
unilaterally but needs a human for Y", we design it ourselves, and the natural seam is a
per-tool gate at the tool layer — not a sentence in a system prompt. Recorded as a decision
owed rather than a ticket, because it shapes the Action Broker and the agent-dispatch chain
together.

Two more absences worth the same treatment: **their e2e suite specifies nothing about the
agent layer at all** (21 Playwright specs and 20 Gherkin features covering auth, projects,
tasks, views, sprints and docs — not one line touching agents, automations or websockets), so
the mining instruction has an honest negative answer; and a **hard timeout that reports
success** — an agent exhausting its hour is torn down and recorded as `FINISHED`, with no
test covering the path. Whatever bound we set, exhausting it must be its own terminal state.


---

## 11. Second-pass corrections — the data model and API (2026-08-10)

*Same pinned commit. A full re-read of `services/api` re-derived §2, §3 and §9 from the code.
**Eighteen defects; six material.** With §10's ten, our Paca record carried ~28 errors — the
cost of a first pass that read for features rather than for verification. Corrected here, not
silently edited.*

**C11 · MATERIAL · §2.6 "activities are written asynchronously" — only SYSTEM ones are.**
Comments are written **synchronously straight to Postgres** and only realtime-published. Two
paths, not one.

**C12 · MATERIAL · §2.6 / §9 row 6 — "field-diff content powering diff & revert". There is no
revert in the API at all.** No endpoint, no service method. The affordance exists in their web
app as a **client-side reconstruction** that re-issues an ordinary PATCH. Three things break
the story we told: `custom_fields` diffs are recorded with **no old and no new value**, so
custom fields are structurally unrevertable; the diff is computed from the *request* against
the pre-image rather than from the resulting row, so service-side defaulting is mis-recorded;
and automation writes emit a **different content shape**, so automation changes are not
revertable by the same reader. ⚠️ The affordance is still worth building (§9.5) — but as our
design, on our schema, not as a lift.

**C13 · MATERIAL · §2.4 — the fractional-indexing arithmetic is NOT in the API.** `MoveTask`
accepts a **client-supplied** `position float64` and upserts it verbatim; the algorithm is a
frontend contract. Worse, and directly relevant to us: **neither move method validates that
`task_id` belongs to the view's project.** That is the same join-table authorisation class we
just fixed in our own `views.set_positions` after reading Plane's advisory — now sighted a
third time, in a second reference. It is a *category* of bug, not an incident.

**C14 · MATERIAL · §2.2 "exactly two hard rules" — there are three, and enforcement is
weaker.** `wouldCreateCycle` **fails open** past 50 ancestors or on any lookup error; `CreateTask`
runs no cycle check at all; and **nothing checks the parent lives in the same project**, even
though their task *links* have an explicit cross-project refusal. The asymmetry is in their
code.

**C15 · MATERIAL · §2.3 — `is_required` on custom fields is enforced nowhere**, and there is
**no write-time type validation of custom-field values at all**; the repository works around it
with regex-guarded casts so a stray value cannot abort a query. Our §2.3 presented `field_type`
as a constraint. It is not.

**C16 · MATERIAL · §2 preamble — "migrations re-run on every boot, every statement idempotent
… independently converged with our `02+` rule". Overstated in our favour and against theirs.**
Paca has **no migration ledger of any kind** and no numbering-collision check; every file
re-executes on every boot forever. And one migration performs an in-place `DROP COLUMN` in the
same file that backfills — the **opposite** of expand/contract. Our R1/R6/verify-by-ledger-line
discipline is strictly stronger. **Not a convergence**, and the claim should never have been
written as one.

**C17 · §2.7 — BDD and time tracking were never in core.** Checklists and GitHub are real
extractions; the other two appear only as *example* plugin identifiers in doc comments. Same
error class as the Plane "kill switch" we credited and they do not have.

**C18 · §2.1 — nothing in Paca is ever restorable.** `deleted_at` is set and never cleared;
no trash, no undelete, no archived state distinct from deleted. Soft-delete there is a
referential-integrity device, not an undo feature.

**C19 · §2.5 — agents are PER-PROJECT rows** with their own key reference. There is no
org-level agent registry: the same assistant used in ten projects is ten rows and ten secrets
to rotate. ⚠️ Our registry is platform-level and should stay there.

**C20 · MATERIAL · §3 — `config.filters` is not "id arrays", and this one we got wrong in the
direction of UNDERSELLING it.** It is a **recursive, dimension-agnostic selector**:
`{all: bool, items: {<uuid|virtual-group>: bool | nested}}` per dimension, plus per-custom-field
range/contains blocks. "Every status except Archived" survives a status added next month; an
ID snapshot silently starts hiding new work. **Materially better than what we recorded** — see
§9.5, where it is minted.

**C21 · MATERIAL · §3 — "one task-list endpoint serves every page; `view_id` enriches each
task" is half true, and the missing half matters.** `view_id` loads positions and switches the
sort. **It applies no filtering whatsoever.** The saved filter config is stored, handed back,
and never read by the server; all ~20 dimensions arrive as query parameters the client builds.
So the presentation-vs-query-constraints split is a **client-side convention, not a
server-enforced property** — which is exactly the property our own `146_projects.sql` comment
claims the split buys ("what stops a saved view from silently changing which rows a member may
see"). **We must not cite Paca as prior art for it.** We hold that property; they do not.

**C22 · MATERIAL · §9 row 5 "agents are ordinary members/actors" — true in the schema, false
in the authentication, and the inversion matters.** The agent identity is the client-supplied
header `X-Agent-ID`, trusted on presentation of a single shared static key that resolves to a
seeded **SUPER_ADMIN** bot. Narrowing to the agent's project role happens *only* when the
route's scope resolver yields a project — so on any global-scope route the request is evaluated
as SUPER_ADMIN regardless of the header. ✅ Our §5 parenthetical — that our
`EffectiveAccess.intersect()` narrows an agent by the acting member and Paca has nothing this
strong — is **verified correct**.

**C23 · §9 row 9 "idempotent everywhere" — true of the automation engine, false of the activity
spine.** The activity's primary key is minted by the producer and carried on an at-least-once
stream, and the consumer's insert has **no `ON CONFLICT`**; a redelivery raises a duplicate-key
error, is deliberately not acked, and is re-read on every restart — a permanently poisoned
pending list. Adopt the discipline from the engine, never from the spine.

**C24 · §9 row 6 "single activity spine" — there are two**, `task_activities` and
`doc_activities`, same shape, no common parent; the agent feed has to `UNION ALL` them. Our one
`pm_activities` is the better shape, and it is *why* the agent activity feed (§9.5) is cheap
for us and expensive for them.

### Where the re-read says WE are ahead
No delta/changed-since feed of any kind (we have tombstones + `/projects/delta/tasks`); no
per-user view state, so collapsing a lane collapses it for everyone (our `pm_view_user_state`);
no archive state distinct from delete; `ON DELETE SET NULL` on status so deleting a status
silently orphans tasks to NULL, against our `RESTRICT` + a 409 naming the count; no foreign key
on their position rows, so deleting a task leaves orphans forever; **66 `binding:"required"`
validation tags with no validator in the module at all** — R7 stated as a failure, since a rule
with no fence reads as protection to anyone skimming; and two parallel project-role
vocabularies, which is our own "do not invent a second way" rule demonstrated.
