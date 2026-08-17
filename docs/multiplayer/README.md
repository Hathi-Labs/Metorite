# Multiplayer Agents — Analysis & Implementation Plan

**Status:** Phases 0-1 built · Phase 2 built as *steer* (floor baton deferred to an owner re-decision) · Phase 3 partly built · **Date:** 2026-07-26 · **Verified against code on 2026-08-02, re-verified, then adversarially reviewed and repaired twice the same day** · **Owner:** vjvarada

> **What the review passes caught in *this* file** — recorded because the corrections are the
> reason to trust what is left, not decoration.
> **Pass 2:** the document overstated its own supersede guard (it claimed `mark_active` raises
> `SupersedeRefused`; it does not — see §5.2, §8 Phase 0, and the anchor grep in §12.3).
> **Pass 3:** §2's anchor table was **7 wrong out of 8**, three of them resolving to real but
> unrelated code, under a "verified" header with no caveat (fixed, with the caveat, above the
> table); and §5.2's update cited a test as demonstrating the `mark_active` bypass when that
> test cannot distinguish the two states (§5.2 now says plainly that no test demonstrates it,
> and describes the one that would).
> **Line numbers move; re-verify at dispatch** — `work_plan.md` §1 contract item 4.

> **What is real as of 2026-08-02** (each claim re-checked against the tree on
> that date; **§12 lists the exact verification commands and their real
> output**).
>
> **Built.** Membership, shared history, presence, live spectate, message
> authorship, the clearance filter, several agents per room, and the whole room
> UX (header + cap banner, share sheet, presence rail, attributed turns) — §8's
> Phase 0 and Phase 1, plus the parts of Phase 3 that could not wait (the
> authority rule, and personal memory being excluded from shared rooms in both
> directions). **Also built, and this is the change most of this document
> predates: mid-run steer and the two-layer supersede guard** (§5.2, §8 Phase 2,
> shipped in `15c8933f`). A second person's message now folds into the live run
> — `orchestrator/steer.py::route_turn` returns DROP / ENGAGE / ABORT / STEER,
> the steered caller answers `202 {"steered": true}` and stands down, signals
> are durable on `cc:steer:` and replayable into the next run, and a principal
> outside the run's floor is refused `409 steer_outside_run_floor` and told why
> (`routes/agent.py:274-369`). Underneath it, **`run_detached`
> (`stream_relay.py:883-895`) raises `SupersedeRefused`** for a caller who does
> not own the run — *before* it reaches the `mark_active(reset=True)` at `:909`
> that DELETEs the transcript — so §3.3's destruction path is closed for **every
> caller of `run_detached`**, not only at the route. Be exact about the residual:
> `mark_active` itself (`:343-405`) issues its `DELETE` at `:377` with **no
> ownership check of its own**, so the invariant holds over `run_detached`'s
> callers, not over the destructive statement. A future path that calls
> `mark_active(reset=True)` directly would bypass it.
>
> **Not built, and read §5.1 / §5.3 as a plan rather than a description:** the
> five `floor_mode`s and the floor baton, the turn queue, the observer lane,
> handoff-with-a-note, HITL floor-holder routing, per-participant cost
> attribution, `subject:` memory compartments, and the `prefs`/`user` backfill.
> `chat_session.floor_mode` exists and defaults to `'open'`; nothing enforces
> `'driver'`. Whether the baton still earns its place *on top of steer* is an
> **owner re-decision** (§8 Phase 2), not queued work. The authoritative build
> state for the access primitives is
> [`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md) §6;
> the dispatch state for what remains is
> [`work_plan.md`](../../project-docs/work_plan.md) §2, WS-10.
>
> **Shipped migrations:** **136** (`agent_blob` instance key), **137**
> (quarantine of commingled agent data), **138** (`org_group`,
> `org_group_member`, `chat_session.visibility`, `chat_session_participant`),
> **139** (`chat_message` author_email/author_kind, clearance tags, replay
> filter, `chat_session_agent`). Room memory compartments shipped 2026-07-30
> ([`memory-clearance.md`](memory-clearance.md) §7).

Turn Metorite agent sessions from a thousand private threads into **rooms** — shared,
live, durable places where several people and one agent work the same problem together.
Anyone on the team can drop into a running session to watch it work, redirect it, and hand
it off, the way they would with a human teammate.

The thesis this responds to: the best work tools of the last two decades won by going
multiplayer (Docs over Word, Figma over Photoshop). AI hasn't had that moment yet, because
a chat is a box only one person can see. Agents now run tasks that take hours or days —
work at that scale was never meant to be done alone.

Interactive mockups live alongside this doc:

| Mockup | Surface |
|---|---|
| [`mockup-room.html`](mockup-room.html) | The shared session room — presence rail, attributed turns, floor control, observer lane, live steer |
| [`mockup-room-settings.html`](mockup-room-settings.html) | Access & data-sharing panel — roles, capacity dials, context policy, integration bindings, what's private |
| [`mockup-share.html`](mockup-share.html) | Going shared mid-conversation — `@mention` in the composer, history waterline, memory disclosure |
| [`mockup-memory.html`](mockup-memory.html) | What the agent knows and who can see it — compartments, per-fact audience, "what would they see?" |
| [`mockup-agents.html`](mockup-agents.html) | The agent catalog — personal / team / shared instancing, and what each choice means |

**Companion docs:**

- [`../../project-docs/specs/agent_platform_hardening_2026-07.md`](../../project-docs/specs/agent_platform_hardening_2026-07.md)
  — **adversarial review of everything below.** 20 findings, the container-isolation
  decision, and the ordered fix list. Two of them change this document's design rather than
  its implementation: participant input must be **user-role** (steer is otherwise a
  prompt-injection channel), and messages must carry the **clearance set of the run that
  produced them** so replay can be filtered by label, not only by join cursor — otherwise the
  model launders restricted content into a transcript that later joiners can read.

- [`../../project-docs/specs/groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md)
  — **the binding decisions (2026-07-29).** Org access control Phase 1 shipped (resolved
  principals, `EffectiveAccess`, default-deny, agent-run gating) and handed the rest to this
  workstream. That spec fixes the three shared primitives: `org_group` (one group model for
  teams, agent instancing, and sharing), `chat_session_participant`, and the authority rule —
  a shared run acts at the **intersection** of all participants' access. Where this document
  disagrees with it, that spec wins (§4.3 notes the specifics).
- [`agent-kinds.md`](agent-kinds.md) — **personal vs shared agents.** Which agents are
  one-per-person (coach, email) and which are one-brain-for-a-team (sales assistant).
  Read this first: instancing decides what a memory compartment even is, and whether an
  agent's sessions may become rooms at all.
- [`../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md`](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md)
  — **prior art: `yc-software/qm`, read 2026-08-01.** An outside team reached this document's
  intersection rule independently and applied it to three resources. Three of its findings change
  what is written below rather than confirming it: **steer should be built before floor control**
  (§4.6 / §5.1 — QM-1, **built**), a shared room should have **no ambient credentials at all**
  (§6.4 — QM-3; note the correction there — `acting_identity` was a *proposal*, never a column,
  and QM-3 belongs to **WS-2 / WS-1**, not to this workstream), and participant **tenure windows
  should narrow the model's context**, not only each viewer's replay (§6.5 — QM-5, an undone
  design comparison). **Reference-only**: it owns no work item and no status
  ([`work_plan.md`](../../project-docs/work_plan.md) §4).
- [`memory-clearance.md`](memory-clearance.md) — how memory is partitioned across sessions
  *and* across people, and how the agent decides which parts it may use on a given call.
  Supersedes §6.3 below.
- [`../../project-docs/specs/memory_architecture.md`](../../project-docs/specs/memory_architecture.md)
  — the memory system as a whole: seven stores unified into six tiers, how a fact persists
  and gets corrected, and the always-on **file tier** (`agent-data/`), which is shared across
  all users of an agent today and matters more than the vector tier because it is injected
  rather than retrieved.

---

## 1. TL;DR / Recommendation

**We are closer than it looks.** The hard part of multiplayer agents is not the UI — it is a
durable, ordered, replayable event log per session; runs that survive the client that started
them; and control commands that reach the right worker. Metorite already has all three,
and all three are **keyed by `thread_id`, not by user**. Nothing in the transport is
single-player.

What is missing is three things, in this order:

1. **Membership** — a session has exactly one owner (`chat_session.user_id`) and every read
   path is `WHERE user_id = :uid`. There is no way to express "Sanjay can see this too."
2. **Floor control** — who is allowed to talk to the agent right now. Without it, a second
   participant's message *silently kills the first participant's in-flight run and erases
   its transcript* (§3.3). This is not a nicety; it is the thing that makes naive sharing
   destructive.
3. **A privacy boundary on the run context** — today the agent's context is stitched from
   the *caller's* private memory (§3.5). Share the room without fixing that and one person's
   private facts get rendered into a transcript the whole team reads.

**Recommendation: the thread is the room.** Do not introduce a new document abstraction.
A room is a `chat_session` row plus a member list, and every existing thread-keyed
primitive (`cc:stream:`, `cc:active:`, `cc:control:`, the reconnect/replay endpoint) becomes
multiplayer the moment the ownership check becomes a membership check.

Four phases, roughly six weeks, each independently shippable:

| Phase | Ships | Rough |
|---|---|---|
| **0 — Make the races explicit** | Concurrent-run 409, transcript no longer erasable by a second party, message authorship | ~3 days |
| **1 — Read-only multiplayer** | Membership, shared history, presence, live spectate ("watch it work") | ~1 week |
| **2 — Contribution** | Floor control, mid-run steer, turn queue, HITL routing ("redirect it", "hand it off") | ~1.5 weeks |
| **3 — The privacy boundary** | Room memory scope, context policy, room integration bindings, private lanes, since-join history | ~2 weeks |
| **4 — Scale & limits** | Fan-out multiplexer, capacity dials, per-participant cost attribution | ~1 week |

---

## 2. What already works

This is the plumbing referred to in the framing. It is genuinely most of the problem.

> **Anchors re-verified against the tree on 2026-08-02 (third pass).** Seven of the eight rows
> below were stale — they carried pre-`15c8933f` line numbers, and **three of them resolved to
> real but unrelated code**, which is worse than a dangling number because it reads as
> confirmation (`:659` is `publish_control`, not `run_detached`). The earlier "verified" claim
> in this file's header covered the *narrative* and not this table. **Line numbers move;
> re-verify at dispatch**, per `work_plan.md` §1 contract item 4 — the same caveat
> [`memory-clearance.md`](memory-clearance.md) §3.5 carries. Re-derive with:
> `git grep -nE "^async def |^def |^STREAM_PREFIX|^CONTROL_PREFIX" -- apps/services/orchestrator/orchestrator/stream_relay.py`

| Capability | Where | Why it matters for multiplayer |
|---|---|---|
| **Durable ordered event log per thread** | `apps/services/orchestrator/orchestrator/stream_relay.py:53` — `cc:stream:{thread_id}`, Redis Stream, `MAXLEN ~50 000`, 1h TTL | Redis `XREAD` is inherently fan-out: N independent readers can each hold their own cursor on the same stream. **Multiple simultaneous subscribers already work today** — nothing in the transport assumes one reader. |
| **Runs detached from the HTTP response** | `stream_relay.run_detached` (`:823`) | The agent keeps running when the browser that started it disappears. This is the "agents run for hours, days, weeks" premise, already satisfied. |
| **Join-and-catch-up** | `replay_events` (`:189`), `subscribe_events` (`:256`), `GET /agent/run/{thread_id}/reconnect?since=` (`apps/services/gateway/gateway/routes/agent.py:2139`) | Exactly the primitive a late joiner needs: replay everything since a cursor, then go live with no gap. Built for browser refresh; works unchanged for a second person. |
| **Cross-worker control bus** | `cc:control:{thread_id}` pub/sub + applied-ack (`stream_relay.py:557-780` — `CONTROL_PREFIX` at `:557` through `_stop_control_listener_wait` at `:770`) | A command issued by *any* participant on *any* worker reaches the worker that owns the run, and is confirmed applied. Already carries `cancel` and `respond_input`. `steer` shipped as a new applier on it, not as new infrastructure. |
| **Liveness / seed presence** | `cc:active:{thread_id}`; `GET /chat/active-sessions` (`routes/chat.py:677`, `list_active_sessions` `:680`) | Already scans `cc:active:*` to show which sessions are running. |
| **Authoritative run-end persistence** | `gateway/chat_fold.py:410` `persist_final_assistant_message` | The transcript is folded and written server-side at the run boundary, independent of any browser. A room's history does not depend on a participant staying connected. |
| **Identity at the edge** | `packages/acb_auth/acb_auth/deps.py` — `UserContext(email, role)`, internal-bearer-verified SSO headers | Every request already carries a verified actor. Membership checks have something to check against. |
| **Cost & activity feed** | `packages/acb_common` Redis activity/cost feed; live token tracking (Custom Apps) | Per-participant cost attribution in a room is a re-key, not a new system. |
| **Org/RBAC design already researched** | `project-docs/specs/multi_user_organization_research.md` | Orgs, memberships, permission vocabulary, agent visibility, memory scoping. **This RFC is the session-level layer on top of it**, and deliberately does not re-litigate the org model. |

**Relationship to the org research doc.** That doc answers *"who can access which agents and
data across the company"* — a static, org-shaped question. This doc answers *"how do several
people occupy one live agent run at the same time"* — a dynamic, session-shaped question.
They compose: org permissions set the ceiling, room roles narrow it (§5.4).

---

## 3. The five things that block multiplayer today

Found by reading the code, not by inspection of the feature list. Items 3 and 5 are the
non-obvious ones and they are the reason this needs a design rather than a patch.

### 3.1 Reads are single-owner

`routes/chat.py` gates every read and write on the owner's email:

*(The line numbers in this list are the **pre-membership** ones and no longer resolve — the
code they describe was replaced, as the update below records. They are left as written because
they cite a version of the file, not the current one.)*

- `_get_sessions` (`:62`) — `WHERE user_id = :uid`
- `_get_messages` (`:191`) — returns `[]` unless `SELECT 1 FROM chat_session WHERE id=:id AND user_id=:uid`
- `_patch_session` (`:151`), `_delete_session` (`:179`) — same predicate
- `list_active_sessions` (`:438`) — cross-references `AND user_id = :uid`

> **Update 2026-08-01 (doc-truth pass): no longer true.** The single-owner
> predicates above were replaced by membership checks —
> `resolve_room_access(session_id, email)` in
> `apps/services/gateway/gateway/rooms.py` is the one authority for who may
> read a session. This section stands as the pre-multiplayer record.

### 3.2 Control is single-owner

`_thread_owner_ok(thread_id, user_id)` (`routes/agent.py:1651`) gates **reconnect** (`:1544`)
and **cancel** (`:1674`). It is correct today and deliberately permissive (returns `True` for
ephemeral threads and on DB error), but it encodes "one email owns one thread".

> **Update 2026-08-01 (doc-truth pass): no longer true.** Reconnect and cancel
> are now gated by `resolve_room_access` (`gateway/rooms.py`) rather than
> `_thread_owner_ok`.

### 3.3 A second person's message destroys the first person's run — silently

This is the sharp edge. In `run_detached` (today `stream_relay.py:897-911`; the
`mark_active` call has since grown `actor`/`source`/`floor` arguments and the guard
described in §5.2 now sits immediately above it at `:883-895`):

```python
# One run per thread: cancel any stale run still attached to this thread.
prev = _DETACHED_TASKS.get(thread_id)
if prev is not None and not prev.done():
    prev.cancel()
    ...
# Fresh run boundary: clear previous events so replay-from-0 is exact.
await mark_active(thread_id, reset=True)   # ← DELETEs cc:stream:{thread_id}
```

Both behaviours are *right* for single-player (they implement steer/retry/Quick-action:
supersede my own run, and keep replay-from-0 exact). In a room they mean:

> Alice's agent is 40 minutes into a task. Bob types "also check the invoice" — Alice's run
> is cancelled mid-flight and the Redis transcript of those 40 minutes is deleted. The
> cancellation is deliberately silent (`stream_relay.py:930-942` suppresses `RUN_ERROR` on
> this path, because for single-player it is a supersede, not a failure — the suppression is
> the `except asyncio.CancelledError` arm **inside `run_detached`**, not a separate handler).

So: **floor control is a correctness requirement, not a UX preference.** Phase 0 turns this
silent destruction into an explicit 409 + product decision (§8).

### 3.4 Human turns never enter the shared stream, and messages have no author

Only agent events are pushed to `cc:stream:` (`push_sse_event` is called on the executor's
SSE lines). A participant's message reaches the *agent* but never reaches the other
*browsers*. And `chat_message` (`infra/postgres/02_chat_history.sql:23`) has
`role IN ('user','assistant','system')` (`:26`) and no author column — so in a room every human turn
renders as an anonymous "user". You cannot tell who asked what.

> **Update 2026-08-01 (doc-truth pass): fixed.** Migration 139 added
> `chat_message.author_email` / `author_kind`, plus per-message clearance tags
> and clearance-filtered replay redaction.

### 3.5 The run context is stitched from one person's private data

`routes/agent.py:1252-1300`:

```python
user_id: str = getattr(user, "email", "") or "anonymous"
_set_memory_user_id(user_id)                      # scopes remember/save_memory
...
mem_ctx = await get_memory_context(user_id, user_msg)   # this user's private Mem0 facts
parts.append("## Memory from past conversations\n" + mem_ctx)
```

and symmetrically on the write path (`add_memories_background`, `:1448`) the turn's content
is extracted into **the caller's personal memory store**.

In a shared room, unchanged, this means: (a) the driver's private facts are injected into a
context whose output everyone reads, and (b) everyone else's contributions get written into
the driver's personal memory. Both directions are wrong. §6.3 is the fix and it is the single
most important decision in this document.

---

## 4. Q1 — The best way to have multiple people use the same agents

### 4.1 Three sharing models — don't conflate them

| Model | Meaning | Status |
|---|---|---|
| **Shared definition** | Many people, many separate sessions, one agent *registration* | **Exists.** `dynamic_agents` is global; anyone with gateway access can run any registered agent. This is a shared *tool*, not multiplayer. |
| **Shared room** | Many people, **one live thread**, one agent context, simultaneous | **The ask.** Everything below. |
| **Shared long task** | One long-running agent; humans drop in and out asynchronously, hand off, pick up hours later | Falls out of the room model + durable log, provided history and floor survive everyone disconnecting. |

The distinction matters because "shared agent" is often sold as the first and delivers none
of the value. The value is one *context* several people can stand in.

### 4.2 Recommendation: the thread is the room

Do not build a separate "collaboration document" object. A room is:

```
chat_session  (already exists — becomes the room)
  + chat_session_member  (new — who's in it and in what capacity)
  + cc:room:{thread_id}  (new Redis stream — room events that outlive runs)
```

> **Update 2026-08-01 (doc-truth pass):** the proposed `chat_session_member`
> shipped as **`chat_session_participant`** (migration 138).

Why this and not a new abstraction:

- Every transport primitive is already thread-keyed. `cc:stream:`, `cc:active:`,
  `cc:control:`, `/reconnect?since=`, `chat_fold`, `workspace_path` — all of it becomes
  multiplayer by changing an ownership predicate to a membership predicate.
- The agent's context *is* the thread's message history. A room with a different identity
  than the thread would need context reconciliation; a room that **is** the thread needs none.
- It degrades gracefully: a session with one member behaves exactly as today, so nothing
  regresses for solo use.

### 4.3 Schema

> **Superseded in part (2026-07-29):** the participant table, visibility values, and
> authority model below were drafted before org access control Phase 1 shipped and handed
> off ([`org_access_control.md` §10](../../project-docs/specs/org_access_control.md)).
> The binding decisions now live in
> [`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md):
> the table is `chat_session_participant(subject, role ∈ owner|member|viewer)` with the
> `app_grants` subject vocabulary (email / `group:<slug>` / `org`), visibility is
> `private|people|org` mirroring `apps.visibility`, and — most importantly — there is **no
> `acting_identity` column**: a shared run acts with the **intersection** of every
> participant's `EffectiveAccess`, never one member's identity. The room-layer columns
> below (floor control, history waterline, budgets) remain this doc's scope and land with
> the room feature.

```sql
-- ── The room layer (this doc's scope; participants live in migration 138) ───
ALTER TABLE chat_session
    -- user_id keeps its meaning: the creator/owner. Membership is additive.
    ADD COLUMN IF NOT EXISTS floor_mode TEXT NOT NULL DEFAULT 'driver'
        CHECK (floor_mode IN ('solo', 'driver', 'queue', 'open', 'moderated')),
    ADD COLUMN IF NOT EXISTS history_visibility TEXT NOT NULL DEFAULT 'full'
        CHECK (history_visibility IN ('full', 'since_join')),
    ADD COLUMN IF NOT EXISTS context_policy TEXT NOT NULL DEFAULT 'room'
        CHECK (context_policy IN ('room', 'driver', 'none')),
    ADD COLUMN IF NOT EXISTS max_contributors INT NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS token_budget BIGINT;           -- NULL = unlimited

-- ── Attribution + private lanes ─────────────────────────────────────────────
ALTER TABLE chat_message
    ADD COLUMN IF NOT EXISTS author_email TEXT,             -- NULL = agent/system
    ADD COLUMN IF NOT EXISTS author_kind  TEXT NOT NULL DEFAULT 'human'
        CHECK (author_kind IN ('human','agent','system')),
    ADD COLUMN IF NOT EXISTS visibility   TEXT NOT NULL DEFAULT 'room'
        CHECK (visibility IN ('room','private')),
    ADD COLUMN IF NOT EXISTS private_to   TEXT;             -- email, when visibility='private'
```

> **Update 2026-08-01 (doc-truth pass):** as shipped, the participant table is
> `chat_session_participant` (migration 138), and `floor_mode` landed with
> `DEFAULT 'open'` — not the `'driver'` default drafted above; nothing enforces
> `'driver'` yet. The `chat_message` authorship columns landed in migration 139.

**Room roles are not org roles.** `owner` / `contributor` / `observer` describe a capacity
*in this room*. `UserRole.EXECUTIVE` / `EMPLOYEE` describes authority *in the company*. §5.4
defines how they compose, and the rule is one-directional: room membership never escalates
org permissions.

### 4.4 The room event channel — a second stream, deliberately

Add `cc:room:{thread_id}`, a Redis Stream alongside `cc:stream:{thread_id}`.

**Why not reuse `cc:stream:`:** because `mark_active(thread_id, reset=True)` deletes it at
every run boundary (the `r.delete(_stream_key(...))` at `stream_relay.py:377`, reached from
`run_detached`'s call at `:909-911`), by design, so that replay-from-0 exactly covers
the current run. Room events — who joined, who holds the floor, who said what between runs —
must survive run boundaries. Two streams with different lifecycles is the honest model:

| Stream | Lifecycle | Carries |
|---|---|---|
| `cc:stream:{tid}` | **Reset per run**, 1h TTL | AG-UI run events: `RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `TOOL_CALL_*`, `RUN_FINISHED` |
| `cc:room:{tid}` | **Never reset**, TTL refreshed on write, ~24h | `PARTICIPANT_JOINED` / `_LEFT` / `_PRESENCE`, `USER_MESSAGE`, `FLOOR_*`, `STEER_INJECTED`, `NOTE_ADDED`, `ROOM_SETTINGS_CHANGED`, `HANDOFF` |

Clients subscribe to both through one merged SSE endpoint (§4.5) with two cursors. The
frontend translator (`workbench/control_plane/src/lib/chatStream.ts`) gains new event cases;
the existing run-event handling is untouched.

Room event shape (mirrors AG-UI conventions so the translator stays uniform):

```jsonc
{ "type": "USER_MESSAGE", "threadId": "…", "messageId": "…",
  "author": { "email": "sanjay@fracktal.in", "name": "Sanjay", "avatarUrl": "…" },
  "content": "also check the invoice", "ts": 1753500000000 }

{ "type": "FLOOR_GRANTED", "threadId": "…", "holder": "sanjay@fracktal.in",
  "grantedBy": "vijay@fracktal.in", "expiresAt": 1753500120000 }

{ "type": "STEER_INJECTED", "threadId": "…", "author": "sanjay@fracktal.in",
  "text": "skip the staging deploy", "appliedAt": "tool_boundary" }
```

### 4.5 API surface

```
# Membership
POST   /chat/sessions/{id}/members              invite {email, room_role}
DELETE /chat/sessions/{id}/members/{email}      remove
GET    /chat/sessions/{id}/members              list + presence + floor holder
POST   /chat/sessions/{id}/join                 self-join (if visibility allows)
POST   /chat/sessions/{id}/leave

# Presence + live
POST   /chat/sessions/{id}/presence             heartbeat (10s)
GET    /chat/sessions/{id}/room-stream?since=…&roomSince=…
                                                merged run + room SSE

# Floor & steering
POST   /agent/run/{tid}/floor                   {action: acquire|release|request|grant, to?}
POST   /agent/run/{tid}/steer                   {text}  — non-destructive mid-run guidance
POST   /chat/sessions/{id}/handoff              {to, note?} — transfer owner/driver

# Room config
PATCH  /chat/sessions/{id}/room                 visibility, floor_mode, context_policy,
                                                history_visibility, max_contributors, budget
```

> **Update 2026-08-01 (doc-truth pass):** the shipped surface is
> `gateway/routes/rooms.py`, not the `/members` `/join` `/leave` draft above.
> Real endpoint groups: `GET/PATCH /sessions/{id}/room` (room state +
> settings), `POST/PATCH /sessions/{id}/participants[...]`,
> `POST/DELETE /sessions/{id}/agents[...]`, `POST /sessions/{id}/presence`,
> `GET /sessions/{id}/room-stream`, and `GET /directory` (share directory).
> **Steer shipped, but not as `POST /agent/run/{tid}/steer`:** an ordinary turn
> to a thread with a live run is *routed* to a steer by
> `orchestrator/steer.py::route_turn` inside `POST /agent/run/stream`, which is
> why the second caller gets `202 {"steered": true}` instead of a stream
> (§5.2). The floor and handoff endpoints are still unbuilt and are pending the
> owner re-decision in §8 Phase 2.
>
> **The `/members` half of this section is not this workstream's to build.**
> Group membership — the `group:<slug>` subjects a room shares to — is owned by
> **WS-13 / Centers B** ([`work_plan.md`](../../project-docs/work_plan.md)
> §4): `routes/admin/groups.py` + `/settings/groups`, shipped 2026-08-01.
> Rooms consume groups (`gateway/rooms.py` expands `group:<slug>` at read
> time); they do not administer them.

One helper replaces `_thread_owner_ok` everywhere:

```python
@dataclass(frozen=True, slots=True)
class RoomAccess:
    role: str | None          # 'owner' | 'contributor' | 'observer' | None
    can_read: bool
    can_send: bool            # may take the floor / enqueue a turn
    can_steer: bool           # may inject mid-run guidance
    can_cancel: bool
    can_invite: bool
    since_stream_id: str      # history_visibility='since_join' → join cursor, else "0-0"
    since_message_ts: int     # ditto for the Postgres history read

def resolve_room_access(thread_id: str, user: UserContext) -> RoomAccess: ...
```

Keep `_thread_owner_ok`'s permissive contract for the cases it was built for: a thread with
no `chat_session` row (ephemeral/legacy) and a DB error both resolve to full access, so
legitimate solo operations are never blocked by an infra hiccup.

### 4.6 Steering a run in flight — the "redirect it" verb

The framing's core verb. It must **not** be "send a message", because that cancels the run
(§3.3). Reuse the control bus:

1. `POST /agent/run/{tid}/steer` → `dispatch_control(tid, {"cmd": "steer", "text": …, "author": …})`.
2. The executor registers a `steer` applier (exactly as it registers `respond_input` today)
   that appends to a per-run pending-guidance queue.
3. The queue is drained **at the next tool boundary** and injected as a system-role note:
   `"[steer from Sanjay] skip the staging deploy"`. Tool boundaries are the natural seam —
   the model is between decisions, and injection there does not corrupt a streaming turn.
4. `STEER_INJECTED` is pushed to `cc:room:` so everyone sees who redirected it and when.

Non-destructive, attributed, works cross-worker on day one because `dispatch_control` already
handles the local-hit / publish / applied-ack path with a retry for the subscribe race.

---

## 5. Q2 — Managing how many people can work with the agents

Two distinct questions live under this heading and they need different mechanisms:
**floor control** (who may act *right now*) and **capacity** (how many may be involved *at all*).

### 5.1 Floor control modes

Per-room `chat_session.floor_mode`:

| Mode | Who may drive the agent | What happens when a non-holder sends | Good for |
|---|---|---|---|
| `solo` | Owner only | Rejected (403) | Today's behaviour; the default for private sessions |
| **`driver`** *(default for shared)* | One holder of the floor baton | Offered: *request the floor* / *steer* / *note to room* | Pair-working, incident response, demo-to-a-room |
| `queue` | Anyone; turns run serially | Enqueued; UI shows the queue and lets you reorder or drop yours | Async collaboration, long tasks with several stakeholders |
| `open` | Anyone, immediately | Runs immediately — **only legal because a new run no longer cancels the old one silently** (Phase 0 makes this a 409-or-queue decision) | Small trusted rooms, fast brainstorms |
| `moderated` | Owner/driver only | Lands in an **observer lane**; the driver promotes a suggestion into the agent's context | Large rooms, customer-facing sessions, training |

**Implementation** — one Redis key, no new subsystem:

```python
# Acquire: atomic, self-expiring, so a dead browser can't hold the floor forever.
ok = await r.set(f"cc:floor:{tid}", email, nx=True, ex=FLOOR_TTL)   # 120s
# Hold:    refresh on the presence heartbeat while the holder is connected.
# Release: explicit POST, or automatically on RUN_FINISHED, or by TTL lapse.
# Grant:   owner may force-transfer (Lua CAS) — always emits FLOOR_GRANTED.
```

Every transition emits a `FLOOR_*` room event and an `audit_event` (`acb_audit`) with the
actor, so "who told it to do that" is answerable after the fact.

**Recommended default `driver`**, because of §3.3: a baton is the smallest mechanism that
makes the destructive race impossible rather than merely unlikely.

> **⚠️ Challenged by prior art (2026-08-01) — build §4.6 steer first, then re-decide this.**
>
> `qm` ships multiplayer rooms with **no floor control at all**. A second person's message during a
> live run is folded into that run as a mid-turn steer, the second surface stands down so the reply
> isn't posted twice, and steer signals are durable so one whose target run died is replayed into a
> fresh run. The baton was never needed because the destructive race never happens: there is no
> second run to supersede the first.
>
> That inverts this document's Phase 2 ordering (§8 sequences *floor control, then steer*). The
> evidence says **steer, then measure whether five modes are still load-bearing** — and §4.6 already
> notes steer is "a new applier, not new infrastructure" on the existing control bus.
>
> Two things survive regardless: §5.2's correctness fix (a party who isn't legitimately superseding
> must not be able to reach `mark_active(reset=True)` and delete a transcript they don't own), and
> the carve-out that a human message arriving during an *automation* turn should start its own run
> rather than steer into a cron. Detail:
> [`multiplayer_prior_art_qm_2026-08.md` §QM-1](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md).

### 5.2 The Phase 0 correctness fix that unlocks all of this

`POST /agent/run/stream` must stop silently superseding an in-flight run for the same thread:

- If the thread is `cc:active:` and the caller **holds the floor** and passes
  `intent: "supersede"` → current behaviour (cancel + reset). This is the existing
  steer/retry/Quick-action path and must keep working byte-for-byte.
- If the thread is active and the caller **does not hold the floor** → `409 Conflict` with
  `{ "activeRun": {...}, "holder": "vijay@fracktal.in", "options": ["steer","queue","request_floor"] }`.
- `mark_active(reset=True)` may only be issued by the run that legitimately supersedes.
  Otherwise a second party can delete a transcript they do not own.

This one change converts a silent data-loss bug into an explicit product choice, and it is
worth shipping on its own even if multiplayer stops here.

> **Update 2026-08-01 (built).** Shipped, and with the sequence inverted per
> [`multiplayer_prior_art_qm_2026-08.md`](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md)
> §QM-1: **steer first, floor control re-decided afterwards.** What landed:
>
> - **The supersede rule, stated once: *you may supersede a run you own, and no
>   other.*** "Own" is `cc:runactor:{thread_id}`, stamped at run start. It is
>   enforced at **two** layers, not one. The route still answers `409`
>   (`_refuse_if_another_run_is_active`); underneath it, **`run_detached`
>   (`stream_relay.py:883-895`) raises `stream_relay.SupersedeRefused`** — and it
>   raises *before* the destructive statement it guards, the
>   `mark_active(reset=True)` at `:909` that DELETEs `cc:stream:{thread_id}`. A
>   guard at the route is a policy each new *route* can forget; a guard in
>   `run_detached` is one that every caller of `run_detached` inherits whether or
>   not it remembers. That mattered immediately: `/copilot/chat` reached
>   `run_detached` with **no actor at all**, so it was still a door onto this bug
>   after the route-level 409 shipped. It now stamps its actor.
> - **What the inner layer does not buy — stated so nobody has to rediscover it.**
>   `mark_active` (`stream_relay.py:343-405`) performs its
>   `r.delete(_stream_key(thread_id))` at `:377` with **no ownership check of any
>   kind**. The only `raise SupersedeRefused` in the tree is at `:895`, inside
>   `run_detached`. So the property is *"unreachable by a non-owner through
>   `run_detached`"*, **not** *"unreachable at the destructive site"*: a future
>   code path that calls `mark_active(reset=True)` directly bypasses the guard
>   entirely. Today `stream_relay.py:909-911` is the **only** production call
>   passing `reset=True` (every other hit of `git grep -n "reset=True" -- apps/
>   packages/` is a comment about it), so the gap is latent rather than open —
>   but it is a gap in the *shape* of the guarantee. Pushing the check down into
>   `mark_active` would make it an invariant over the statement; that is a
>   follow-up, and this update did not do it.
>
>   > **No test in the tree demonstrates this bypass, and an earlier version of
>   > this note wrongly claimed one did.** It cited
>   > `tests/unit/test_concurrent_run_guard.py:118-125`
>   > (`test_reset_wipes_the_event_log`). That test **cannot distinguish the two
>   > states**: it seeds only `fake_redis.store[_stream_key("t1")]`, never
>   > `cc:runactor:t1` or `cc:active:t1`, and `_FakeRedis` is fresh per test
>   > (`:54-62`) — so `get_run_actor("t1")` is `None` and `is_active("t1")` is
>   > `False`, which is precisely the state `run_detached` (`:883-895`) **allows
>   > by documented design** (*"Fails OPEN … no recorded owner"*). No refusal is
>   > owed there, so none being raised proves nothing about ownership. Its own
>   > docstring says what it is for — *"Asserting it here so the destructiveness
>   > stays visible"* — which is true and useful, and is not this claim.
>   >
>   > **The test that would demonstrate it** (not written, deliberately — writing
>   > it belongs with the fix, not with the doc): seed `cc:runactor:t1` with
>   > `alice@` **and** `cc:active:t1`, then call
>   > `await stream_relay.mark_active("t1", reset=True, actor="bob@")` **directly**
>   > and assert the transcript is gone and no `SupersedeRefused` was raised —
>   > while the same `(owner, actor)` pair through `run_detached` raises. Two
>   > assertions over one pair of inputs is what makes the asymmetry visible; one
>   > assertion over an unowned thread is not evidence of anything.
> - **Fail-open is unchanged and deliberate**, in exactly the two cases
>   `get_run_actor` documents: an unattributable run (legacy, Redis hiccup) and
>   an anonymous caller (cron, service-to-service). A false refusal blocks real
>   work; a false allow only restores the behaviour we already had.
> - **Steer is the new default path** for the case the 409 was invented for. A
>   message arriving for a thread with a live run is routed by a pure function
>   (`orchestrator/steer.py::route_turn`) to one of four outcomes — drop the
>   agent's own message, engage when nothing is running, abort on a *bare* stop,
>   otherwise steer. "stop the staging deploy" steers; "stop" aborts.
> - **The second caller stands down.** A steered turn answers `202` with
>   `{"steered": true}` and **no stream**, and the surface suppresses its own
>   assistant bubble. Delivering from both sides is how one answer gets posted
>   twice — qm hit exactly that, and it is a test here.
> - **Signals are durable and replayable.** A steer is written to
>   `cc:steer:{thread_id}` *before* it is dispatched, so one aimed at a run that
>   died mid-send survives and is replayed into the next run's message rather
>   than lost. Delivery rides the existing `cc:control:` bus as a new `steer`
>   applier — no new infrastructure, per §4.6.
> - **Carve-out:** a human message during an *automation* turn engages its own
>   run rather than steering into a cron. "Automation" reads the run's existing
>   correlation `source` (`schedule` / `webhook` / `event` / `workflow`); unknown
>   and empty both resolve to *human*, because mistaking a conversation for a
>   cron would spawn the second concurrent run this whole section exists to stop.
> - **Authority (`groups_sessions_authority.md` §3): a steer may not move the
>   floor under a running turn.** The run records the participant roster its
>   `intersect()` fold was taken over; a steer is admitted only from a principal
>   inside it. Someone added to the room *after* the run started is refused with
>   `409 steer_outside_run_floor` **and told why** — the turn has already
>   resolved credentials and read memory at the wider floor, and neither
>   un-reading that nor letting it keep acting above its viewers' clearance is
>   acceptable. They send normally once the turn ends. Rooms shared by
>   `group:<slug>` or `org` cannot be checked this precisely and fall back to the
>   room's own send capability; naming participants individually is what buys the
>   stricter guarantee.
> - **Where the model actually reads it.** A native MAF run's input is built once
>   by `_compose_maf_run_input` and consumed at run start — there is no message
>   store to append to mid-run. The one channel that reliably carries text into a
>   running model is a **tool's return value** (it is how `ask_user`'s answer
>   arrives today), so a steer rides the next tool result. That is also the right
>   moment on its own terms: at a tool boundary the model is between decisions,
>   so injection cannot corrupt a half-streamed sentence.
> - **`STEER_INJECTED` is a room event, not a run event** — `cc:room:`, per §4.4.
>   Run events are folded into the transcript by *both* `gateway/chat_fold.py`
>   and `lib/chatStream.ts`, so a new run-event type is a two-sided change; a
>   steer is a fact about the room, not about the assistant message.
>
> **Still unbuilt and now genuinely open:** the five `floor_mode`s, the turn
> queue, the observer lane. §QM-1's recommendation was to build steer and then
> measure whether a baton still earns its place — that measurement is the next
> decision, not a foregone one.

### 5.3 Capacity dials

The instinct is to cap "people in the room". That is the wrong axis. **Observers are cheap;
contributors are expensive** — not in compute but in *context window*, which is the genuinely
scarce resource. Cap accordingly:

| Dial | Default | Enforced where | Why |
|---|---|---|---|
| `max_contributors` | 5 | Membership write + floor acquire | Each contributor adds turns to the thread history that every subsequent run pays for. |
| Observers | unbounded (soft-warn at 25) | — | One extra Redis cursor each. See §5.5 for the real cost. |
| Concurrent active runs per user | 3 | Run start; scan `cc:active:*` | Prevents one person parallel-farming the fleet. |
| Concurrent active rooms per org | tiered | Run start | The SaaS metering hook (see the org research doc, §17.7). |
| Steer rate | 1 per participant per 30s per run | `/steer` | Protects the context window and the model's coherence. |
| `token_budget` per room | NULL (off) | Cost feed at run boundary | When exceeded, the room degrades to read-only until the owner raises it. |

Per-participant cost attribution rides the existing activity/cost feed: stamp
`participant_email` alongside `thread_id` on each run's token record, and the room header can
show "1.2M tokens · Vijay 61% · Sanjay 39%".

> **Not this workstream's to build** ([`work_plan.md`](../../project-docs/work_plan.md)
> §4 single-owner registry):
>
> - **Cost attribution** — the `(run_id, member_email, agent, instance)` stamp at the
>   gateway choke points is **WS-6** (decision D1). The per-room view here is a rollup of
>   that one record, not a second system. Do not stamp a room-only key.
> - **`token_budget` per room / degrade-to-read-only** — **WS-16** (decision D2):
>   per-member monthly caps ship first and per-room budgets build on the same records.
>   §4.3's `token_budget` column and §8 Phase 4's budget bullet are both WS-16's.
>
> This section stays as the room-shaped requirement those workstreams must satisfy; it
> owns neither.

### 5.4 The permission matrix

> **Update 2026-08-01 (doc-truth pass):** the shipped role vocabulary is
> **`owner` | `member` | `viewer`**
> ([`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md) §2),
> not the `observer` / `contributor` / `owner` triple used below. Read
> "observer" as `viewer` and "contributor" as `member`.

| Action | Observer | Contributor | Owner | Also requires |
|---|---|---|---|---|
| Read history (subject to `history_visibility`) | ✅ | ✅ | ✅ | — |
| Watch a live run | ✅ | ✅ | ✅ | — |
| Add a room note / reaction | ✅ | ✅ | ✅ | — |
| Send a turn to the agent | ❌ | ✅ | ✅ | Holds the floor (mode-dependent) |
| Steer an in-flight run | ❌ | ✅ | ✅ | Rate limit |
| Request the floor | ✅ | ✅ | ✅ | — |
| Grant / revoke the floor | ❌ | ❌ | ✅ | — |
| Answer an `ask_user` (HITL) | ❌ | ✅ | ✅ | Floor holder first; falls back to any contributor after 60s |
| **Approve an outward write** (Action Broker / `approval_queue`) | ❌ | ❌ | ❌ | **Org permission only** — see below |
| Cancel the run | ❌ | ✅ | ✅ | — |
| Invite / remove members | ❌ | ❌ | ✅ | — |
| Change room settings | ❌ | ❌ | ✅ | — |
| Delete the room | ❌ | ❌ | ✅ | — |

> **Rule: room membership never escalates org permissions; it can only narrow them.**
>
> Being a contributor in a room does not grant the authority to approve an email send, a CRM
> write, or a `pending_commits` push. Those stay gated on the org-level permission
> (`require_role`, and later the permission vocabulary in the org research doc §4.3). The
> effective permission for any action is `org_permission AND room_role_permission`. Without
> this rule, "invite them to the room" becomes a privilege-escalation primitive.

### 5.5 What the fan-out actually costs

Being honest about the scaling shape, because it determines when Phase 4 is needed.

Today each subscriber is its own `XREAD ... BLOCK 30000` loop (`subscribe_events`, `:256`).
A 20-person room watching one run is 20 blocked Redis connections *per worker* plus 20 SSE
connections. Redis handles that trivially; the constraint is uvicorn workers and file
descriptors, and it is linear in viewers.

The fix, when it is needed (Phase 4, not before): a **per-process fan-out multiplexer** — one
`XREAD` per `thread_id` per worker, broadcasting to an in-process `asyncio.Queue` per local
subscriber. That turns `O(viewers)` Redis cursors into `O(threads × workers)`. It is a
contained change inside `stream_relay` with no API surface, which is exactly why it should be
deferred until room sizes justify it.

---

## 6. Q3 — What is private and what is shared

### 6.1 The principle: three concentric scopes

- **Participant-private** — never leaves the individual, regardless of room role.
- **Room-shared** — visible to members, subject to `history_visibility`.
- **Org-shared** — visible beyond the room.

Everything below is a decision about which ring a given surface sits in. The default when
uncertain is the *inner* ring: it is easy to promote something into the room later and
impossible to un-share it.

### 6.2 Classification of every data surface

| Surface | Where it lives | Today | In a room |
|---|---|---|---|
| Message content | `chat_message.content` | Owner-only | **Room-shared** — the point of the feature. Attributed via `author_email`. |
| Tool calls & results | `chat_message.tool_events` | Owner-only | **Room-shared.** Seeing *what the agent did* is most of "watch it work". |
| Reasoning / chain-of-thought | `chat_message.reasoning` | Owner-only | **Room-shared, but per-room toggle.** Some rooms (customer-facing, exec review) should not expose raw CoT. Default on for internal rooms. |
| Generative-UI cards | `chat_message.custom_events` | Owner-only | **Room-shared.** Note: interactive cards need one authoritative responder — route interaction through the floor holder. |
| Agent workspace files | `chat_session.workspace_path` → `routes/workspace.py` | Per-session | **Room-shared.** Files are the deliverable; sharing the room without the artifacts is pointless. Writes remain agent-only. |
| **Personal episodic memory** | Mem0 via `get_memory_context(user_id, …)` (`agent.py:1822`) | Per-user | **PRIVATE — excluded from shared rooms by default.** See §6.3. |
| Agent memory | `AGENT_SCOPE_PREFIX` (`acb_memory`) | Cross-user already | **Room-shared.** Unchanged. |
| Org memory | `ORG_SCOPE_KEY` | Global | **Org-shared.** Unchanged. |
| **Room memory** *(new)* | `scope_key("room", thread_id)` | — | **Room-shared.** The new scope facts learned in a room are written to. §6.3. |
| Entity timeline (Graphiti) | `search_entity_timeline` | Global | **Org-shared**, subject to the entity ACLs in the org research doc §9. Not a room concern. |
| Provider / LLM keys | `provider_keys` (encrypted) | Server-side | **Never exposed.** No role, in any room, can read them. |
| Integration credentials & OAuth tokens | `integration_credentials` | Server-side, per-user | **Never exposed.** But *whose* credentials the agent acts with is a room-visible fact. §6.4. |
| Data pulled by tools (inbox, GTD, CRM) | email/task tables, user-scoped | Per-user | **Leak surface.** A tool that reads the driver's inbox renders its contents into a transcript the room reads. Governed by §6.4 + an explicit banner. |
| HITL questions (`ask_user`) | Executor futures + control bus | Owner answers | **Room-shared, one authoritative answer.** Floor holder first, any contributor after 60s. |
| Outward-write approvals | `approval_queue`, `pending_actions`, `pending_commits` | Role-gated | **Visible to the room, actionable only by org permission.** §5.4. |
| Cost / tokens | Redis activity+cost feed | Per-run | **Room-shared, attributed per participant.** |
| Audit events | `audit_event` | actor = email | **Extended**: every room action records `thread_id`, actor, and room role. |
| Copilot server-side session | `chat_session.service_session_id` | Per-session | **Room-shared implicitly** — it *is* the shared agent state. Reinforces "one context, not one per person". |
| Private lane messages | `chat_message.visibility='private'` *(new)* | — | **Participant-private** until explicitly promoted. §6.5. |

### 6.3 The context policy — the most important decision here

`chat_session.context_policy`:

| Value | Memory injected at run start | Memory written at run end | Use |
|---|---|---|---|
| **`room`** *(default for any session with >1 member)* | room scope + agent scope + org scope | **room scope** | Shared work. No participant's private facts enter the room; nothing from the room pollutes anyone's private store. |
| `driver` | the floor holder's personal Mem0 **+** the above | the floor holder's personal store + room scope | "Help me with *my* inbox, while the team watches." Requires an explicit, persistent banner: *"Vijay's personal context is in play"*, and per-session consent from the floor holder. |
| `none` | nothing | room scope only | Clean-room work: audits, customer demos, anything where reproducibility matters more than recall. |

Two rules make this coherent:

> **Memory follows the room, not the person.** Facts learned in a shared room are written to
> the room's scope, and are promoted to org scope only by an explicit action — never
> silently into a participant's personal store.

> **Promotion is one-way and explicit.** `room → org` is a button with an actor and an audit
> record. There is no `personal → room` automatic path at all.

Concretely, this is a branch at two call sites in `routes/agent.py`: the memory-block builder
(`get_memory_context`, `:1822`) selects scopes by policy, and `add_memories_background`
(`:2006`) selects its
write scope by policy. `acb_memory` gains a `room` scope alongside the existing
`AGENT_SCOPE_PREFIX` / `ORG_SCOPE_KEY`, which is a key-prefix addition, not a new store.

Ship the room memory scope in the **same** phase that opens sharing (Phase 3 gates Phase 1's
default). Until then, shared rooms run `context_policy='none'` — no memory rather than the
wrong memory.

> **⚠️ Superseded — `context_policy` is on the wrong axis.**
>
> A per-room switch cannot express *"this deal is confidential and that one is
> collaborative"* when both belong to the same person and the same agent: `driver` leaks the
> restricted subject through semantic retrieval, and `room` forgets the collaborative one.
> Confidentiality is a property of the **subject**, not of the person or the room.
>
> **[`memory-clearance.md`](memory-clearance.md) replaces this section** with memory
> *compartments* (`subject:` / `room:` / `prefs:` / `user:` / `agent:` / `org:`) and a
> per-run *clearance* — **a run reads at the clearance of its least-cleared viewer**. The
> two rules above survive intact; what changes is that the unit of scoping becomes the
> compartment rather than the room. `context_policy` remains only as a coarse room-level
> override (`none` forces a clean room regardless of clearance).
>
> That doc also flags a prerequisite: `routes/memory.py` currently accepts any scope key by
> path parameter without checking the caller, so any signed-in user can read or delete
> another user's memory today. It moves to Phase 0.

### 6.4 Which identity do the tools act as?

When the agent sends an email or writes to ClickUp from a room, whose credentials does it use?

| Option | Behaviour | Verdict |
|---|---|---|
| **Driver** | Whoever holds the floor | ❌ Non-deterministic and leak-prone. The same prompt does different things depending on who typed it, and a tool call silently exposes the driver's mailbox to the room. |
| **Owner** | Always the room creator | ⚠️ Predictable and auditable, but surprising: a contributor's request quietly acts as someone else. |
| **Room binding** *(recommended)* | The room declares its bindings explicitly; `acting_identity` is fixed at room creation and shown permanently in the header | ✅ Explicit, visible, auditable. Falls back to owner when unset. |

Rules:

- **Identity is fixed at run start** and stamped into every `audit_event` and tool result.
  It never changes mid-run, even if the floor changes hands.
- The room header permanently shows what the agent can act as:
  *"Acting as vijay@fracktal.in · Gmail, ClickUp (team), Zoho (read-only)"*.
- Changing `acting_identity` requires owner + the consent of the identity's owner, and emits
  a `ROOM_SETTINGS_CHANGED` event so nobody's mailbox is quietly enrolled.

> **⚠️ Superseded twice. Do not build anything in this section.**
>
> **First, the factual correction (verified 2026-08-02): `acting_identity` never existed in
> code, and the table above is a *rejected* design, not a description.**
> `infra/postgres/138_groups_and_session_participants.sql:26` says so in the migration
> itself — *"There is NO acting_identity column, deliberately: a shared run acts at the
> INTERSECTION of all participants' access (spec §3), never as one member."* §4.3 was
> superseded on 2026-07-29 by
> [`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md).
> Any doc (including the prior-art map and the work-plan board) that reads *"rather than one
> `acting_identity`"* is describing a road not taken.
>
> **Second, the direction (2026-08-01 prior art) — the answer is "none of the three".**
>
> `qm` makes **nothing ambient in a shared scope** — not even the speaker's own credentials. Only
> grants bound to *this exact room* materialize; connector OAuth tokens are DM-only; org service
> credentials require an all-internal audience. A grant carries
> `(credentialId, audienceScopeId, once|standing, purpose)` and is re-checked on every use (wrong
> scope → 403; revoked / already-used / expired → 410), with a consent protocol whose load-bearing
> rule is worth copying verbatim: **"only the owner's own reply is approval — a relayed 'they said
> it's fine' is not"**, verified against the speaker of the turn. Grants cannot be minted on a
> triggered turn; a secret-drop link means a fresh key is never pasted into chat; and broker
> delivery lets the agent call a target by proxy without the secret ever entering the sandbox.
>
> That is the fail-closed form of this section. A single room-level `acting_identity` answers
> "whose mailbox" once for the whole room; per-credential grants answer it per credential, with
> consent, revocation and an audit record — and compose with the Action Broker (WS-1) and secrets
> (WS-2) instead of sitting beside them. Their documented residual applies to us too: a credential
> materialized into a sandbox is plaintext to any process there, and a stated *purpose* is an audit
> field, not enforced authorization. Detail:
> [`multiplayer_prior_art_qm_2026-08.md` §QM-3](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md).
>
> **Gate: not this workstream's.** Per-credential room grants are net-new work with no
> acceptance criteria in any doc, and the prior-art map routes QM-3 to **WS-2** (OWNER-GATE
> end to end — rotation, force-push) and **WS-1** (Action Broker). WS-10 must not pick it
> up; it appears here so this section is not mistaken for buildable scope.

### 6.5 Private lanes inside a shared room

Total transparency is not the goal; *shared context with private edges* is.

- **Whisper / private ask.** "Ask privately" opens a child thread
  (`parent_session_id = room`) with the room transcript as **read-only** context. The
  question and answer are `visibility='private'`, `private_to=<email>`, and appear only to
  the author — with a **Promote to room** action that re-emits them as room-visible with
  attribution. Costs are still attributed to the asker.
- **Private notes.** Annotations on any message, author-only. Never enter the agent's context.
- **Since-join history.** `history_visibility='since_join'` is nearly free: store
  `join_stream_id` / `join_message_ts` on the membership row at join time and feed them into
  the *existing* `?since=` replay cursor (`agent.py:2139`) and the `before`/`limit` window in
  `_get_messages` (`chat.py:260`). Both mechanisms already exist for pagination and reconnect.

  > **Prior art (2026-08-01) — the cursor should narrow the *model*, not only the viewer.** `qm`
  > stores a tenure window per participant (`valid_from_seq` / `valid_to_seq`) and composes the
  > turn's replay as the **intersection of every current participant's window**, so a late joiner
  > narrows what the agent itself may re-read, not just what that person sees. That is the same
  > outcome [`memory-clearance.md`](memory-clearance.md) §5.4 reaches by queueing joins to run
  > boundaries, by a simpler mechanism — worth comparing before building either.
  > [`multiplayer_prior_art_qm_2026-08.md` §QM-5](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md).
  >
  > **The gap is real; the design is not done. NOT DISPATCHABLE.** Verified 2026-08-02:
  > the viewer half is built and correct (mig 138 `:97-98` → `gateway/rooms.py:277-282`,
  > `:291-292` → the `timestamp_ms >= :waterline` predicate at `routes/chat.py:314-316`).
  > The **model** half is not: a run's history comes from
  > `_get_messages(thread_id, _hist_uid, limit=50)` (`routes/agent.py:1947-1956`), narrowed
  > by the *acting caller's* window only — so an owner with full history, asking in a room
  > with a late joiner, puts pre-waterline content through the model and into an answer that
  > joiner reads. The sentence above ("worth comparing before building either") is an
  > **undone design comparison, not acceptance**: an implementer would have to choose
  > between qm's per-participant `valid_from_seq` intersection and §5.4's run-boundary join
  > queue with no recorded decision. Write that decision before this becomes a slice.

### 6.6 Never shared, regardless of role

Enforced server-side at context assembly and in the serializer — never merely hidden in the UI:

- Decrypted provider keys and integration credentials; raw OAuth tokens.
- Another participant's personal memory scope.
- Another participant's private lane (`visibility='private'` rows they don't own).
- The internal gateway bearer token (`GATEWAY_INTERNAL_TOKEN`).
- Any `pending_commits` diff the viewer's org role doesn't already permit.

---

## 7. UX

See the mockups. The load-bearing ideas:

1. **Presence rail** — faces of who is here, who is watching, who holds the floor. Live agent
   state ("running · 4m 12s · 1.2M tokens") is a room-level fact, not a per-browser fact.
2. **Attributed turns** — every human turn carries a face and a name. The agent's turns carry
   the agent's avatar (`64_agent_avatars.sql` already exists).
3. **The floor is visible and requestable** — a single clear affordance: *"Vijay is driving ·
   Request the floor"*. Handing off is one click and lands as a room event.
4. **Steer without stopping** — a distinct input affordance from "send a turn", with a
   distinct rendering (inline, italic, attributed) so the transcript shows the redirection in
   its true position within the run.
5. **The observer lane** — in `moderated` rooms, observer suggestions sit beside the
   transcript and the driver promotes them. This is how a room stays useful above ~6 people.
6. **A permanent, unmissable data banner** — what identity the agent acts as, which context
   policy is in force, and whether anyone's personal context is in play. Privacy that is only
   in a settings page is not privacy.

### 7.1 Going shared mid-conversation

The common case is not "start a shared session" — it is realising, forty turns deep, that
this shouldn't be yours alone. Five entry points, one sheet
([`mockup-share.html`](mockup-share.html)):

- **`@mention` in the composer.** Type `@sanjay` mid-message; a chip appears inline; sending
  converts the thread to a room and invites him. The Docs move, and the path most sharing
  should take.
- **Share button in the thread header** — always present, quiet while solo. Plus `⌘⇧S` and
  the sidebar row menu.
- **The agent asks.** When a run hits an approval it can't make or a domain it can't act in:
  *"This needs Finance sign-off — bring someone in?"* Agent-initiated multiplayer costs
  nothing once rooms exist.

The sheet makes three entangled decisions together, which is why it is one sheet: **who**,
**the history waterline** (*from here on* by default, rendered as a visible divider in the
transcript afterwards), and **what memory the room will and won't have** — computed exactly,
because the clearance of every past run is known. That last block is what makes the
confidential-deal case safe, and it is detailed in
[`memory-clearance.md`](memory-clearance.md) §6.

---

## 8. Phased plan

> **Gate labels** (`work_plan.md` §1 contract item 7, §6 registry). Every
> unbuilt item below carries one:
>
> - **AGENT-SAFE** — an independent agent may build it end to end once its
>   done-when is testable.
> - **OWNER-GATE** — an agent must **refuse** it and say which gate. Two items
>   in this document are owner gates and are registered by name in
>   [`work_plan.md`](../../project-docs/work_plan.md) §6: the **floor-control
>   re-decision** (Phase 2) and the **`prefs`/`user` backfill *apply*** (Phase 3,
>   [`memory-clearance.md`](memory-clearance.md) §8 Q1).
> - **✅ built** — no label needed; the item is history.

### Phase 0 — Make the races explicit (~3 days) — ✅ built

*Ships value even if multiplayer stops here: it is a real data-loss bug.*

- `POST /agent/run/stream` returns **409** when the thread is active and the caller isn't
  legitimately superseding (§5.2). Existing single-player supersede path unchanged.
- `mark_active(reset=True)` is not reachable **through `run_detached`** except from a
  legitimate supersede. Read that scope literally: the refusal lives in `run_detached`
  (`stream_relay.py:883-895`), not inside `mark_active` — see the update below.
- `chat_message.author_email` / `author_kind` added and populated on every write path
  (`chat_fold`, `save_messages`, the Next translator's checkpoints).
- ✅ **Authorize `routes/memory.py`** (done 2026-07-30) — the path parameter is a *scope key*
  and was never compared to the caller, so any member could read, search or delete a
  colleague's memory scope by email in a URL. Worse than first written up: the
  `/api/chat/memories?userId=` path needed no session at all, and `lib/memory.ts` forwarded
  no identity, so the gateway saw a service principal. Fixed at all three layers, plus
  `delete` now checks the memory is in the scope
  ([`memory-clearance.md`](memory-clearance.md) §2.1).
- **Scope Graphiti reads** — knowledge-graph episodes are written with a per-user `group_id`
  but `GraphitiClient.search()` passes no group filter, so retrieval spans every user's
  episodes ([`agent-kinds.md`](agent-kinds.md) §2.2). Both Mem0/Graphiti findings are latent
  while `MEM0_ENABLED` / `GRAPHITI_ENABLED` are false — check the deployed `.env` for urgency.
  The **file tier** finding is not gated by either flag: `agent-data/` is shared across all
  users of an agent whenever an agent uses `save_note` / `recall_notes`
  ([`memory_architecture.md`](../../project-docs/specs/memory_architecture.md) §5.3).
  Its instance key lands with the compartment work in Phase 3a, not Phase 0 — but it is the
  finding to weigh first, because it is the tier that is injected rather than retrieved.
- **Acceptance:** two clients on one thread; the second cannot cancel or erase the first's
  run; every stored message resolves to an author; a caller cannot read a memory scope they
  don't own.

> **Update 2026-08-01 (built).** The two bullets above are now enforced inside
> `run_detached` (`stream_relay.py:883-895` raises `SupersedeRefused` before the
> `mark_active(reset=True)` at `:909`), not only at the route, and
> `/copilot/chat` — which reached `run_detached` with no actor and was therefore
> still an open door — now stamps one. The guarantee covers `run_detached`'s
> callers, **not** `mark_active` itself, which still deletes without an ownership
> check of its own; §5.2's update states that residual. See §5.2 for the
> supersede rule as implemented. Regression cover:
> `tests/unit/test_supersede_guard.py`.

### Phase 1 — Read-only multiplayer (~1 week) — ✅ built

- Membership + room columns — **landed as migration 138** (drafted here as 117) + backfill.
- `resolve_room_access` replaces `_thread_owner_ok` at both call sites; membership predicate
  replaces `WHERE user_id = :uid` in the five `chat.py` helpers.
- `cc:room:{tid}` stream; presence heartbeat; `PARTICIPANT_*` events.
- Merged `/room-stream` SSE; frontend translator cases; presence rail.
- Invite / join / leave; sessions sidebar shows shared rooms.
- Shared rooms are pinned to `context_policy='none'` until Phase 3.
  **Update 2026-08-01 (doc-truth pass):** superseded per §6.3 — clearance
  compartments shipped 2026-07-30, so shared rooms run at room clearance;
  `context_policy` survives only as the coarse `'none'` clean-room override.
- **Acceptance:** Sanjay opens Vijay's running session, sees the last hour replay and then
  live tool calls, and cannot send, steer, or cancel.

### Phase 2 — Contribution (~1.5 weeks) — steer ✅ built · floor 🔴 owner re-decision

| Item | State | Gate |
|---|---|---|
| `steer` applier on `cc:control:`, draining at tool boundaries | ✅ built | — |
| Durable replayable signals (`cc:steer:`) + `202 {"steered": true}` stand-down | ✅ built | — |
| Automation carve-out; run-floor authority check; §5.2 supersede fix | ✅ built | — |
| Floor baton (`cc:floor:`), all five `floor_mode`s, `FLOOR_*` events, audit | not built | **OWNER-GATE** — *floor-control re-decision* |
| Turn queue (`queue`), observer lane (`moderated`) | not built | **OWNER-GATE** — same decision (both are `floor_mode` behaviours) |
| Handoff with a note; HITL floor-holder routing | not built | **OWNER-GATE** — same decision (both presuppose a floor holder) |

- **Acceptance (steer half, met):** a second participant redirects a run mid-flight without
  cancelling it, the room sees who did it and when, and no second assistant turn is posted.
  Asserted in `tests/unit/test_steer_routing.py` + `tests/unit/test_supersede_guard.py`.
- **Acceptance (floor half, not met and deliberately unwritten):** the floor request/grant
  criterion is *not* restated here, because writing a done-when for a mechanism whose
  existence is still in question would make an owner decision look like queued work.
  If the owner decides the baton earns its place, the acceptance is written *then*.

> **Update 2026-08-01 (re-scoped and partly built).** The order of this phase is
> inverted per
> [`multiplayer_prior_art_qm_2026-08.md`](../../project-docs/specs/multiplayer_prior_art_qm_2026-08.md)
> §QM-1: **steer was built first**, and whether the five floor modes still earn
> their place is now a measurement rather than a plan. Steering dissolves most of
> the problem the baton was invented for — a second person's message lands in the
> running turn instead of being refused — so the remaining question is what a
> baton adds *on top of* that, not what it prevents.
>
> **Built:** the `steer` applier on `cc:control:`, draining at tool boundaries;
> durable replayable signals (`cc:steer:`); the `202 {"steered": true}`
> stand-down; the automation carve-out; the run-floor authority check; and the
> §5.2 correctness fix underneath all of it. Details in §5.2's update.
> **Not built and deliberately unscheduled:** the floor baton and all five
> `floor_mode`s, the turn queue, the observer lane, handoff-with-a-note, and
> HITL floor-holder routing. `chat_session.floor_mode` still exists and still
> defaults to `'open'`; nothing enforces `'driver'`.
>
> The acceptance line above is therefore split: *"Sanjay redirects the run
> mid-flight without cancelling it, and the room sees who did what when"* is met;
> the floor request/grant half is not, and is pending the owner's re-decision.

### Phase 3 — The privacy boundary (~3 weeks) — partly built

**Owned by [`memory-clearance.md`](memory-clearance.md) §7**, which splits it into 3a/3b/3c
and holds the acceptance. This list is the room-side index; where the two disagree,
memory-clearance wins.

| Item | State | Gate |
|---|---|---|
| **3a** — `scope_key()` kinds, `prefs`/`user` vocabulary, clearance resolution at run start, read/write rules at both `routes/agent.py` memory call sites, the memory tools' write scope, clearance-keyed session cache | ✅ built 2026-07-30 | — |
| **3a remainder** — the compartment registry (migration: next free number at build time) and **`subject:` compartments** | not built | **AGENT-SAFE** — the dispatchable slice, once [`memory-clearance.md`](memory-clearance.md) §7.1's surface spec is accepted. This is WS-10's one green item. |
| **3a remainder** — the `prefs`/`user` **backfill classifier**, dry-run report only | not built | **AGENT-SAFE** |
| **3a remainder** — *applying* that backfill to live memories | not built | **OWNER-GATE** — mutates live Mem0 data (`work_plan.md` §6, "live-DB one-offs"); [`memory-clearance.md`](memory-clearance.md) §8 Q1 ends *"it should be a deliberate, communicated choice."* |
| **3b** — subject binding (bound rooms, inline declaration), entity-linked inference that may only narrow, the per-viewer private hint, extraction classification | not built | **AGENT-SAFE** after the 3a remainder; §8 Q2 (auto-create on binding) is an open product call inside it |
| **3c** — share sheet with the memory disclosure, history waterline, memory inspector | not built | **AGENT-SAFE** |
| Room integration bindings + `acting_identity` fixed at run start | **superseded, do not build** | §6.4's update: `acting_identity` never existed in code (migration 138 line 26 rejects it explicitly) and the direction is now per-credential grants — **WS-2 / WS-1**, not this row |
| Private lanes: whisper child threads, private notes, promote-to-room | not built | **AGENT-SAFE** |
| `history_visibility='since_join'` via the join cursors | ✅ viewer half built (mig 138 `:97-98` `join_stream_id`/`join_message_ts` → `gateway/rooms.py:277-282` read, `:291-292` applied → `routes/chat.py:314-316` SQL predicate) | model half **not built** — see §6.5's QM-5 note; it is an **undone design comparison**, not acceptance |
| The permanent data banner | not built | **AGENT-SAFE** |

- **Acceptance:** held verbatim by [`memory-clearance.md`](memory-clearance.md) §7 ("Acceptance
  for 3a"), not restated here — the load-bearing test is at the query layer, not the answer
  layer: in a room whose viewers aren't all cleared for a restricted subject, assert that
  `search()` is **never called** with that scope key. The **room** half is met
  (`tests/unit/test_memory_compartments.py`); the **subject** half waits on the slice above.

### Phase 4 — Scale & limits (~1 week) — not built

| Item | State | Gate |
|---|---|---|
| Per-process fan-out multiplexer in `stream_relay` | not built | **AGENT-SAFE** |
| Capacity dials (§5.3) | not built | **AGENT-SAFE** |
| Room `token_budget` + degrade-to-read-only | not built | **not this row's** — **WS-16** (D2) |
| Per-participant cost attribution in the header and the observability view | not built | **not this row's** — **WS-6** (D1) |

- **Acceptance:** a 25-viewer room holds one `XREAD` per worker. (The budget half of the
  original acceptance line moved with the work to WS-16.)

---

## 9. Rejected alternatives

- **CRDT / Yjs document model (the literal Figma analogy).** An agent session is an
  append-only, causally-ordered event log with a single writer (the agent) and serialized
  human turns — not a shared mutable document. A CRDT adds a large dependency and solves a
  merge problem we do not have. *Revisit only* for collaborative editing of a prompt draft or
  a workspace file, which is a genuinely different surface.
- **WebSocket rewrite.** SSE + Redis Streams already gives ordered delivery, replay from a
  cursor, and reconnect — the properties a socket would have to re-earn. The only gap is
  client→server, already covered by POST + the control bus. A rewrite would be pure cost.
- **Per-user forked contexts ("everyone gets their own copy").** Destroys the premise. The
  value is one context several people stand in; forking makes it N private threads with
  extra steps.
- **Room as a new top-level object separate from the thread.** Requires reconciling two
  identities and rewriting every thread-keyed primitive. §4.2.
- **Broadcasting personal memory into rooms and relying on UI hiding.** Privacy enforced in
  the renderer is not privacy — the model has already seen it and will paraphrase it into the
  transcript.

---

## 10. Open questions

1. **Room lifetime.** `cc:stream:` is 1h TTL; a room that spans days needs its live log
   rehydrated from Postgres on demand. Do we materialize a room event table, or accept
   "live events are ephemeral, transcript is durable"? (Leaning: the latter — `chat_message`
   is already the durable truth, and `cc:room:` is the live layer.)
2. **Guests / external participants.** The org research doc has no external-user path.
   Sharing a room with a customer is a real want and a real risk surface.
3. **Notifications.** When an agent parks on `ask_user` at 2am and nobody holds the floor,
   who gets pinged? Ties into the existing WhatsApp / email surfaces.
4. **Room templates.** "Incident room", "deal room", "review room" — preset floor mode,
   context policy, agent, and bindings. Probably Phase 5.
5. **Does `driver` context policy survive contact with reality**, or is the leak risk high
   enough that we only ever ship `room` and `none`?
6. **Interactive gen-UI cards with multiple viewers** — one authoritative responder is the
   right rule, but the card components need to render a disabled state for non-holders.

---

## 11. Summary

The framing is right: agents are the one powerful new tool people still use alone. For
Metorite the gap between here and multiplayer is smaller than it looks, because the
substrate — a durable per-thread event log, detached runs, cross-worker control, replay from
a cursor — was already built for reconnection and is user-agnostic.

Three things stood between us and it: **membership** (a predicate change), **the destructive
race** (fixed — see §5.2: steer, plus a supersede guard inside `run_detached`), and **a
privacy boundary on the run context** (memory compartments; the room half is built, the
`subject:` half is the one dispatchable slice left). Membership and the race took weeks. The
privacy boundary is the one that determines whether people trust the room enough to use it,
and it is the one to get right rather than fast.

*(The original text here named "floor control (a Redis baton)" as the second thing. Steer
replaced it — see §5.2 and §8 Phase 2. Whether a baton is still wanted on top is an owner
re-decision, not a remaining blocker.)*

---

## 12. Verification

Every claim in this document's status header and §8 is checkable by one of the commands
below. Run them from the repo root. Real output as of **2026-08-02**, on Windows with the
tree at `b5a218bd` + this doc pass.

### 12.1 Tests

```bash
uv run python -m pytest tests/unit/test_steer_routing.py tests/unit/test_supersede_guard.py \
                        tests/unit/test_memory_compartments.py tests/unit/test_rooms.py -q
# → 61 passed, 14 skipped in 21.39s
```

> **⚠️ The skip is the trap, and it is silent.** All 14 skips are the whole of
> `tests/unit/test_rooms.py`, which needs a reachable Postgres carrying **migrations 138 +
> 139** (`_db_ready()` probes `chat_session_participant.subject`, `chat_session_agent`,
> `chat_message.author_kind`). Without a database the room membership suite does not fail —
> it **disappears**, and a green run proves nothing about `resolve_room_access`. A change to
> `gateway/rooms.py` is not verified until that file reports **14 passed**, not 14 skipped.
> The other three files are pure-function suites and run anywhere.

> **⚠️ Never run `tests/unit/` as a directory on a developer box with a live `.env`.** It
> hangs against the live database (`test_memory_integration.py` measured exit 124), and
> `test_owner_bootstrap.py` must never reach prod (`work_plan.md` §6). Name test files.

### 12.2 Lint

```bash
# The BLOCKING gate — this is what pr-check.yml:51 runs and what must be green.
uv run ruff check . --select F821,F601,F602,F502,F7,B006
# → All checks passed!

# The full check is a REPORT, not a gate: 1,970 pre-existing style findings on main
# as of 2026-08-02 (pr-check.yml:53-60 and deploy.yml:58 both swallow its exit code).
uv run ruff check .
# → Found 1970 errors.   ← identical on main; do NOT read a green here as the bar
```

### 12.3 Anchors — is this document still true?

```bash
# Steer shipped, and is an ancestor of main
git merge-base --is-ancestor 15c8933f main && echo "steer is on main"
# → steer is on main

# The four routing outcomes and the durable-signal knobs exist
git grep -nE "class Route|STEER_TTL_SECONDS|MAX_PENDING_STEERS" -- \
    apps/services/orchestrator/orchestrator/steer.py
# → :60 STEER_TTL_SECONDS = 1800 · :65 MAX_PENDING_STEERS = 20 · :72 class Route(str, Enum)

# The 202 stand-down and the run-floor 409, in the route
git grep -nE "steered|steer_outside_run_floor" -- \
    apps/services/gateway/gateway/routes/agent.py
# → the STEER / DROP / ABORT responses at :274-369

# The supersede guard's REAL location (§5.2, §8 Phase 0). The raise is inside
# run_detached and comes BEFORE the destructive call; mark_active itself has no
# ownership check, so read the line ordering, not just the presence of a raise:
git grep -nE "raise SupersedeRefused|^async def run_detached|^async def mark_active|await mark_active" \
    -- apps/services/orchestrator/orchestrator/stream_relay.py
# → :32  await mark_active(thread_id)        (the module docstring's usage sketch)
#   :343 async def mark_active   ·   :823 async def run_detached
#   :895 raise SupersedeRefused  ·   :909 await mark_active(   ← guard precedes it
#   The ONLY raise is at :895, inside run_detached (:823) — not inside mark_active
#   (:343-405), whose body deletes the stream at :377 with no ownership check.
#   That asymmetry IS the documented residual; a green grep here is not a
#   claim that the destructive statement itself is guarded.

# There is still no acting_identity COLUMN anywhere (§6.4) — and the claim is about
# the whole tree, so the grep has to be about the whole tree. The one hit is the
# migration comment that says so; a single matching comment is the CORRECT result:
git grep -n "acting_identity" -- apps/ packages/ infra/ workbench/ tests/
# → 138_groups_and_session_participants.sql:26: "There is NO acting_identity column,
#   deliberately"    (exactly one hit, a comment; zero DDL, zero code)

# `subject:` is genuinely absent — the scope kind, the registry, the session column
git grep -n "subject" -- packages/acb_memory/acb_memory/compartments.py
# → exactly one hit, line 23, in the module docstring saying subject compartments
#   "are not built". Zero occurrences in scope_key(), scope_kind(), resolve_clearance().
git grep -nE "memory_compartment|subject_ref" -- infra/postgres/ \
    || echo "registry unbuilt — expected"
# → registry unbuilt — expected
```

(`git grep` rather than `rg`: ripgrep is not on every box's PATH, and `git grep` respects
the index so it never wanders into `node_modules/` or a stale worktree.)
