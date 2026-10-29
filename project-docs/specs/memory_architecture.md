# Memory Architecture — one model for seven stores

> **⚠️ RE-SCOPED 2026-10-29 by D132 (and D128 item 7). Mem0 is NOT a tier. Graphiti is NOT a tier.**
>
> Mira remembers through three stores:
> 1. **The member profile** holds what Mira knows about one member. See
>    [`personalization.md`](personalization.md) and D110 (`user_settings.assistant_profile`).
> 2. **Org knowledge** is Knowledge plus the pointer graph. See
>    [`knowledge_base.md`](knowledge_base.md), [`knowledge_curation.md`](knowledge_curation.md)
>    (D-KC-8) and D128. The `NOTES.md` of a shared agent moves here (H-237).
> 3. **Chat history** has full text search (MEM-2, D132.3).
>
> Mem0 and Graphiti retire after KG-6 (KG-11). Until then, `MEM0_ENABLED` and
> `GRAPHITI_ENABLED` stay off (D128.7).
>
> **These parts no longer hold:**
> - §1 rows 1 and 2, and §2 (the vector tier).
> - §3 rows "Durable-recalled" and "Relational".
> - §4 (the Mem0 bullet and the vector tier in the fact life).
> - §5.2 and §5.3 where they name Mem0 or Graphiti.
> - §6.2, §6.4 and §6.5 where they name Mem0 or Graphiti.
> - §8 (the Mem0 comparison) and the Mem0 and Graphiti phasing in §9.
> - The companion link to `llm_caching_memory.md` for Mem0 scopes.
>
> **These parts still hold:** the blob-store file tier (§6.1 and §6.2), provenance (§6.3),
> the correction experience (§6.7), compartments and clearance (§7), and run capture (§6.6).
> Each affected section carries its own RETIRED marker. For the WS-9 board row, see `work_plan.md` §2.

**Status:** Draft / RFC · **Date:** 2026-07-26 · **Owner:** vjvarada

How every agent remembers: what tier a fact belongs in, where it physically lives, how it
survives a run, how it gets corrected, and how all of that holds up when several people share
one agent.

**Companions:**
[`agent_architecture.md`](agent_architecture.md) (**the scope lattice, the manifest, and the
authored-Knowledge layer these tiers sit inside — read it first: it decides whether an agent
is declarative or code, which is what keys every compartment below**) ·
[`agent_file_and_memory_framework.md`](agent_file_and_memory_framework.md) (the durable-state
contract — the three folders) ·
[`agent_persistence_implementation.md`](agent_persistence_implementation.md) (the blob store,
function by function) ·
[`llm_caching_memory.md`](llm_caching_memory.md) (prompt caching. Its Mem0 scopes are RETIRED in part by D132) ·
[`../../docs/multiplayer/memory-clearance.md`](../../docs/multiplayer/memory-clearance.md)
(compartments and per-run clearance) ·
[`../../docs/multiplayer/agent-kinds.md`](../../docs/multiplayer/agent-kinds.md) (personal
vs shared instancing)

> **Note on the `agent-startup-guru` reference.** This repo's framework doc cites it as the
> pattern to copy (§4, *"a self-contained memory bank under `outputs/_memory/` +
> `agent-data/` managed by a `memory-management` skill — JSON/MD working memory + SQLite FTS
> long-term"*). I could not read that repo directly here — it is private and this session
> can't add a repo from another owner — so §8 below reasons from that description. Two things
> are worth confirming from the source, because they change §6.4: **does it keep an index or
> manifest file**, and **does its memory-management skill do its own compaction** when the
> bank grows? If it has a compaction protocol, we should port that rather than invent one.

---

## 1. We don't have a memory system. We have seven.

> **RETIRED in part (2026-10-29, D132):** rows 1 (Mem0) and 2 (Graphiti) are RETIRED (D132.4). The other rows hold.

Each of these was the right answer to the problem in front of it. None of them share a
vocabulary for scope, lifetime, retrieval, or budget — which is why "how does memory work"
currently has no single answer.

| # | Store | Holds | Physical home | Retrieval | Scope key |
|---|---|---|---|---|---|
| 1 | **Mem0** | Extracted episodic facts | Postgres + pgvector (`mem0_memories`) | Semantic top-k | `user_id` field, repurposed: `<email>` · `agent:<name>` · `org:global` |
| 2 | **Graphiti** | Entities + bi-temporal edges | Neo4j | Graph + semantic | `group_id` = user (**on write only** — §5.2) |
| 3 | **Blob store** | The three folders — `agent-data/`, `inputs/`, `outputs/` | Postgres (`agent_blob`, `agent_file_history`) | Whole-file read by tool call | **`agent_name` only** |
| 4 | **Chat history** | The literal transcript | Postgres (`chat_session`, `chat_message`) | Windowed by recency; `query_history` SQL | `user_id` = owner email |
| 5 | **Session cache** | The assembled memory block | Redis, 10-min TTL | Direct | `thread_id` |
| 6 | **Run trace** | What happened in a run | Postgres (`agent_run`) | SQL | `run_id` |
| 7 | **App stores** | GTD, email patterns, WhatsApp, reply memory, task clarifications | Postgres, per-app tables | SQL / purpose-built vectors | Varies per app |

Three of these are *authoritative* durable state (1, 3, 4 — plus 2 when enabled). The rest are
caches, logs, or domain records. The confusion is that they all get called "memory."

---

## 2. Two philosophies, and the rule for choosing

> **RETIRED in part (2026-10-29, D132):** the vector tier (Mem0) is RETIRED (D132.4). Org knowledge (D128) and the member profile (D110) take its place. The file-tier rule holds.

The `agent-startup-guru` pattern (a curated memory bank in files) and our Mem0 layer (semantic
recall) look like alternatives. They aren't — they have opposite properties, and a good agent
needs both.

| | **File tier** — `agent-data/NOTES.md` | **Vector tier** — Mem0 |
|---|---|---|
| Retrieval | Whole file, deterministic | Top-k by similarity |
| Visible to a human | Yes — readable, editable, diffable | No — a row in a vector table |
| Scales | Poorly (it's prompt) | Well (it's an index) |
| Reliable | Exactly what you put there | Whatever the query surfaces |
| Failure mode | Grows until it eats the context window | Silently doesn't return the thing you needed |

> **The rule.** Ask: *if the agent didn't have this in front of it, would it do the wrong
> thing?* → **file tier**. *Would it merely miss a useful detail?* → **vector tier**.

"Never send pricing to a customer without Meera's sign-off" is the first kind — an agent that
misses it does damage. "Acme's CFO used to work at Harrow" is the second — nice to surface,
harmless to miss. Today both land wherever the model happens to put them.

The graph tier is a third thing again: it answers *what changed and when*, which neither of
the others can. That's what makes it the right home for supersession (§6.5).

---

## 3. The six tiers

| Tier | What | Store | Lifetime | Budget | Retrieval |
|---|---|---|---|---|---|
| **Working** | This run's scratch | In-context | The run | context window | always present |
| **Session** | This thread's turns | `chat_message` + Redis block | The thread | last N turns | always present, windowed |
| **Durable-explicit** | Curated knowledge the agent must always have | `agent_blob` → `agent-data/` | Forever, until compacted | **hard cap, ~2k tokens** | deterministic injection (§6.2) |
| ~~**Durable-recalled**~~ | ~~The long tail of learned facts~~ | ~~Mem0 / pgvector~~ | **RETIRED (D132.4, KG-11).** The member profile (D110) and org knowledge (D128, D-KC-8) hold these facts now | — | — |
| ~~**Relational**~~ | ~~Entities, edges, validity intervals~~ | ~~Graphiti / Neo4j~~ | **RETIRED (D128.6, D132.4, KG-11).** The pointer graph in Postgres (`kb_entities`, `kb_links`) replaces it | — | — |
| **Structured** | Domain records — tasks, emails, deals | App tables | Per the domain | — | SQL |

Everything above the line (working, session) is bounded by construction. Everything below it
needs an explicit budget, and only one tier currently has one at all.

---

## 4. How a fact actually persists

> **RETIRED in part (2026-10-29, D132):** the Mem0 bullet and the vector-tier steps in the fact life are RETIRED (D132.4). The Postgres-is-truth rule holds.

**Postgres is authoritative for every durable tier. Disk and Redis are caches, always.**

- **Blob store** — `agent_blob` holds current content; `agent_file_history` is an append-only
  log of every unique version by sha256, with action, actor, and run/session provenance. Disk
  at `{agents_clone_dir}/repos/{agent}` is a cache; `rehydrate_workspace` restores it before
  every run, and gateway reads fault-in individual files on demand. A wiped volume loses
  nothing.
- **Mem0** — pgvector in the same Postgres. Same model: the DB is truth.
- **Redis** — only ever a cache. `cc:stream:` (1h TTL), the assembled memory block (10-min
  TTL). Nothing durable lives only here.
- **Graceful degradation is the contract everywhere** — DB down means memory functions no-op
  and return empty, and agents keep working. That's why memory can be added anywhere without
  becoming a new failure mode.

### The life of one fact

```
   said in a conversation
        │  extraction  (should be gated — §6.4)
        ▼
   vector tier  ──────────────► retrieved when relevant
        │  promotion (used repeatedly, or "always remember this")
        ▼
   file tier    ──────────────► present on every run
        │  compaction (over budget, stale, or superseded)
        ▼
   vector tier
        │  decay (unused and superseded)
        ▼
   archived — never hard-deleted, always auditable
```

Today only the first arrow exists. Everything below it is manual or absent, which is why
`NOTES.md` can only grow.

---

## 5. What's broken

Two of these were already flagged in the multiplayer docs. The third and fourth are new, and
they're in the file tier — the exact mechanism the startup-guru pattern is built on.

### 5.1 The memory API is open by path parameter

`routes/memory.py:59,71,84,97` — every route resolves `UserContext` and never compares it to
the `{user_id}` in the URL. Any signed-in user can list, search, and delete any other user's
memory scope.

> **Update 2026-08-01 (doc-truth pass):** fixed 2026-07-30 — authorization shipped; see
> `docs/multiplayer/memory-clearance.md` §2.1.

### 5.2 Graphiti writes are partitioned, reads are not

> **RETIRED in part (2026-10-29, D132):** RETIRED (D132.4). Graphiti is off and retires at KG-11, so this fault will not ship. Org scoping is KG-9.

`agent.py:1366` writes episodes with `group_id=user_id`; `graphiti_client.py:144` searches
with no group filter at all. Retrieval spans every user's episodes.

### 5.3 The file tier is shared across all users of an agent — and it's whole-file

> **RETIRED in part (2026-10-29, D132):** the Mem0 `agent:` comparison below is RETIRED (D132.4). The file-tier fault itself holds, and §6.1 fixes it.

`agent_blob`'s primary key is `(agent_name, path)`, and the module docstring is explicit:
*"agent_name is the only tenant key."* So there is **one `agent-data/NOTES.md` per agent, not
per user.** Two people using the email assistant append to the same file.

This is worse than the Mem0 `agent:` bucket, for a specific reason: **Mem0 leaks only what is
semantically similar to the query; the file tier leaks the entire file.** `recall_notes(path)`
with no query returns the whole thing (`note_tools.py:150`). One call, everything anyone ever
wrote.

> **Update 2026-08-01 (doc-truth pass):** superseded by migrations 136
> (`agent_blob_instance` — the PK is now `(agent_name, instance, path)`) and 137
> (`quarantine_commingled_agent_data`). The prose above stands as history of why.

### 5.4 …and it's model-directed, so it's also unreliable

The same tier fails in the opposite direction. Nothing injects `NOTES.md`. The tool-injection
prompt *asks* the model to fetch it (`_tool_injection.py:488-493`):

> *"Maintain `agent-data/NOTES.md` as your cross-session working memory. At the START of each
> session: read this file if it exists to restore context."*

An agent that doesn't call `recall_notes` runs with **no durable memory at all**, and nothing
detects it. So the tier is simultaneously leaky when it fires and absent when it doesn't.

Both failures have the same root cause and the same fix: the file tier should be a
deterministic, instance-keyed, budgeted injection — not an instruction to the model to go and
read a shared blob.

### 5.5 No budget, no provenance, no supersession, no correction

- **No budget.** `NOTES.md` grows forever. It is prompt, so it competes with the context
  window and eventually degrades every run — and it undercuts the prompt-caching work in
  `llm_caching_memory.md`, whose whole point is a stable, bounded prefix.
- **No provenance.** The assembled block is flat text. The agent can't say where a fact came
  from, and neither can we when a run goes wrong.
- **No supersession.** "Terms are Net 30" and "we moved to Net 45" can both be injected. The
  model picks one, silently.
- **No correction.** There is a delete-by-id API and no way for a user to say "that's wrong"
  in the place where they see the mistake. This is the biggest UX gap in the system.

---

## 6. The design

### 6.1 Instance-key the file tier, exactly like memory compartments

The blob store gains the same instance key the memory compartments get in
[`agent-kinds.md`](../../docs/multiplayer/agent-kinds.md) §4, so the two tiers partition
identically and one mental model covers both.

```sql
-- migration 136_agent_blob_instance.sql (NEVER mutate 71 in place — deployed DBs ran it)
ALTER TABLE agent_blob
    ADD COLUMN IF NOT EXISTS instance TEXT NOT NULL DEFAULT '';   -- '' = shared, u:<email>, t:<team>
ALTER TABLE agent_file_history
    ADD COLUMN IF NOT EXISTS instance TEXT NOT NULL DEFAULT '';
-- Repoint the PK: (agent_name, path) → (agent_name, instance, path)
```

| Agent instancing | Memory compartment | Blob-store instance |
|---|---|---|
| `personal` | `agent:<name>#u:<email>` | `u:<email>` |
| `team` | `agent:<name>#t:<team>` | `t:<team>` |
| `shared` | `agent:<name>` | `''` |

`''` as the default keeps every existing row valid and every shared agent working unchanged.
`rehydrate_workspace(agent, root)` gains the instance and restores the caller's workspace;
`folder_of` and the write-through seams pass it through. This touches
`blob_store.py`, the two write-through seams in `write_artifact.py` / `note_tools.py`, the
gateway mirror helpers, and the executor's rehydrate call — all listed in
`agent_persistence_implementation.md` §3–4. Instancing rollout is owned by `work_plan.md` WS-14.

**`outputs/` is the exception worth thinking about.** Deliverables are shared in a room even
when the agent is personally instanced. Simplest correct rule: `agent-data/` and `inputs/` are
instance-keyed; `outputs/` is keyed by instance too but *readable* through the room's file
view, because the room already gates who can see it.

### 6.2 Make the file tier a deterministic, bounded injection

> **RETIRED in part (2026-10-29, D132):** the contrast with the Mem0 block is RETIRED (D132.4). The member profile block (D110) now sits beside the file header. The injection design holds.

Stop asking the model to remember to read its own memory.

At run start, alongside the member profile block (D110), assemble a **memory header** from the instance's
`agent-data/` — `NOTES.md` plus any file the agent marks always-on — capped at a hard token
budget (default ~2 000). Inject it as a labelled block. Keep `recall_notes` for *targeted*
re-reads with a query filter and for everything outside the always-on set.

This is strictly better on three axes at once:

- **Reliable** — the agent can't forget to load its own memory.
- **Bounded** — the budget is enforced at assembly, and overflow triggers compaction (§6.4)
  rather than silent context bloat.
- **Cache-friendly** — a curated file is *byte-stable across turns*, unlike Mem0's semantic
  block. It belongs in the stable prefix. The session cache in `llm_caching_memory.md` Phase 4
  exists to paper over Mem0's instability; the file tier needs no papering over, so this
  *increases* the cacheable prefix rather than eating into it.

### 6.3 Provenance on every injected fact

Every line in the assembled block carries where it came from and when:

```
- Pricing over ₹40L needs Meera's sign-off            ⟦file · team:sales · 2026-06-02⟧
- Acme's renewal is ₹42L, up from ₹38L                ⟦vector · subject:deal/acme · 2026-07-19⟧
- Standard payment terms are Net 45 (was Net 30)      ⟦graph · org · valid from 2026-04-01⟧
```

The UI strips the marker; the model keeps it. This one change unlocks three things that are
impossible without it: the agent can answer *"why did you say that"* by citing rather than
inventing, the user can correct a specific fact in place (§6.7), and a run can be replayed
with the exact context it had (§6.6).

### 6.4 Promotion, compaction, decay

> **RETIRED in part (2026-10-29, D132):** promotion into and demotion to the Mem0 vector tier are RETIRED (D132.4). The file-tier budget and decay rules hold.

Concrete rules for the ladder in §4:

- **Extraction is gated.** Save decisions and durable facts, not chatter. Generalise the rule
  already proven in `agent_file_and_memory_framework.md` §8 — *save the committed outcome,
  never the proposal* — and reuse the shape of Graphiti's `_is_episode_worthy` gate for the
  Mem0 path, which currently extracts from every turn indiscriminately.
- **Promotion to the file tier is earned or explicit.** Either the user says "always remember
  this", or a fact is retrieved and used N times (default 3) across distinct sessions. Never
  on first sight — that's how a memory bank fills with noise.
- **Compaction runs when the budget is exceeded**, not on a timer. Least-recently-used facts
  are demoted back to the vector tier, and near-duplicates are merged. Demotion never deletes;
  it moves a fact out of the always-on budget.
- **Decay archives** facts that are both unused and superseded. Archived, never hard-deleted —
  `agent_file_history` is already append-only and the audit trail matters more than the bytes.

### 6.5 Supersession

> **RETIRED in part (2026-10-29, D132):** the Graphiti path is RETIRED (D132.4). Supersession of a fact now belongs to the pointer graph (D128).

Two contradictory facts must never both inject. Give every durable fact `valid_from` and
`superseded_by`, and filter at assembly: group by subject, keep the newest, and mention the
supersession only when the model asks for history.

Graphiti's bi-temporal edges are *designed* for exactly this and would do it properly — but it
is disabled (`GRAPHITI_ENABLED=false`) and has the read-scoping bug in §5.2. So there are two
honest paths: fix and enable Graphiti and let it own supersession, or implement a
`superseded_by` field at the Mem0 layer as an interim. The interim is cheap; enabling Graphiti
is the better end state. Don't do both.

### 6.6 Make runs reproducible

Memory makes a run non-deterministic, which breaks golden trajectories and makes incidents
hard to reconstruct. Store the assembled memory block — content plus hash — on the `agent_run`
row (migration 50 already has the table and the run/user/model columns). Then:

- an eval can pin the block and replay a run with identical context;
- an incident review can answer *"what did it know when it said that"*;
- a diff between two runs shows whether memory or the model changed the outcome.

The block is already assembled and already cached in Redis, so this is a write, not a system.

### 6.7 The experience

**Correction is the missing primitive.** Everything else in this doc is plumbing; this is what
users actually feel.

- **A memory chip on the turn** — *"used 3 memories"* — expands to the provenance list from
  §6.3. Per fact: **that's wrong** · **forget this** · **always remember this** (promote to
  the file tier).
- **Correcting in place, where the mistake is visible**, not in a settings screen. A wrong
  fact gets superseded with the correction, attributed to the person who caught it.
- **The inspector** ([`mockup-memory.html`](../../docs/multiplayer/mockup-memory.html)) stays
  the browse-and-audit view: compartments, per-fact audience, *what would this person see*.
- **The agent can describe its own memory** when asked, from the injected provenance instead
  of confabulating a plausible answer.

---

## 7. How this composes with multiplayer

The file tier needs the same two rules the compartments get, and it needs them more.

**Read.** The always-on file tier joins the clearance set as a compartment
(`file:agent:<name>#<instance>`). Because it is injected rather than retrieved, it is the
single most dangerous tier to get wrong in a room: a vector fact leaks only on a semantic
match, but the file tier is simply *there* on every run.

**Write.** The same rule as memory — *write audience ⊆ session audience*. A room may append to
`agent-data/` only when the instance's audience is no wider than the room. A team agent in a
room of team members: fine. The same agent in a room containing one outsider: the write goes
to room scope instead, because `agent-data/` outlives the room and would carry the fact to
everyone using that instance forever after.

| Folder | In a room |
|---|---|
| `agent-data/` | Instance-keyed. Written only when the room's audience covers the instance. |
| `inputs/` | Follows the uploader; promotable to `agent-data/` under the same write rule. |
| `outputs/` | Room-readable — files are the deliverable (`docs/multiplayer/README.md` §6.2). |

---

## 8. What this means for the startup-guru pattern

> **RETIRED in part (2026-10-29, D132):** the Mem0 comparison is RETIRED (D132.4). Chat history uses full text search (D132.3), and org knowledge uses Knowledge (D128).

The framework doc's recommendation — *copy `agent-startup-guru`* — is right about the shape.
A curated memory bank with an explicit management protocol is a better mental model than
"facts go into a vector store somewhere," and it's what makes an agent feel like it's actually
learning.

But it is a **single-user agent's design**. Two things it never needed, which a multi-user
platform cannot skip:

1. **An instance key** (§6.1). Without it, adopting a richer always-loaded memory bank
   *amplifies* §5.3 — the more the pattern succeeds, the more each user's bank contains, and
   all of it is shared.
2. **A budget with compaction** (§6.2, §6.4). A memory bank that only grows is fine for one
   person over months and fatal for an agent serving a company for years.

So: adopt the structure, add the key and the budget. And confirm the two questions in the note
at the top — if its memory-management skill already has a compaction protocol, port that
instead of inventing §6.4.

One deliberate divergence: the framework doc notes both reference repos use **SQLite FTS** for
long-term recall. We should not copy that. Our Mem0 + pgvector partition already beats lexical
FTS on semantic recall and is one store rather than two. The thing worth copying is the
*protocol* — what gets written, when, and how it's curated — which is exactly what §8 of the
framework doc already concluded.

---

## 9. Phasing (re-scoped 2026-10-29 by D132)

Mem0 and Graphiti are not tiers (see the banner). This section lists what is left of WS-9.
It interleaves with the multiplayer plan, and the numbering matches
[`../../docs/multiplayer/README.md`](../../docs/multiplayer/README.md) §8.

### 9.1 The tiers that stay

| Tier | Store | Owner of the fact |
|---|---|---|
| Working and session | Context window, `chat_message` | This spec §3 |
| Durable-explicit | `agent_blob` file tier (`agent-data/`) | This spec §6.1 and §6.2. The `NOTES.md` of a shared agent moves to org knowledge (H-237) |
| Member profile | `user_settings.assistant_profile` | [`personalization.md`](personalization.md), D110 |
| Org knowledge | Knowledge pages and the pointer graph | [`knowledge_base.md`](knowledge_base.md), [`knowledge_curation.md`](knowledge_curation.md), D128 |
| Chat history | `chat_session`, `chat_message` | Full text search, MEM-2 (D132.3) |
| Structured | App tables | The app that owns them |

Compartments, clearance and `subject:` grants (§7) still apply to the file tier and to chat history.
D132 does not change them.

### 9.2 The tiers that retire

| Tier | Status |
|---|---|
| Durable-recalled (Mem0) | **RETIRED by D132.4.** The code and `MEM0_ENABLED` stay off until KG-11 removes them |
| Relational (Graphiti) | **RETIRED by D128.6 and D132.4.** KG-11 removes it with the empty `acb_graph` tables |

### 9.3 The phases

| Phase | Work | State |
|---|---|---|
| **0** | Authorize `routes/memory.py` (§5.1). Scope Graphiti reads (§5.2) | ✅ done 2026-07-30. Graphiti half is moot (RETIRED) |
| **3a′** | Blob instance-keying and run-path wiring, migration **136**. The compartment remainder is WS-10's: [`docs/multiplayer/memory-clearance.md` §7](../../docs/multiplayer/memory-clearance.md). Do not restate it here | ✅ done |
| **3b** | Deterministic budgeted file-tier injection (§6.2). Provenance markers (§6.3). Extraction gate (§6.4) | Open. The file tier part holds. The Mem0 extraction gate is **RETIRED** |
| **3c** | Memory chip and in-place correction (§6.7). Run-block capture (§6.6) | Open. Re-aim §6.7 at the member profile (D110) and org knowledge, not Mem0. `PATCH /memory/{scope}/{memory_id}` on Mem0 is **RETIRED** |
| **4** | Compaction and decay (§6.4). Supersession (§6.5) | Open for the file tier. The Graphiti-or-interim-field choice of §6.5 is **closed by D132**: neither is built. Supersession of a fact belongs to the pointer graph (KG) |

**Acceptance for 3a′** (unchanged). Two users of one personal agent both call `save_note`. Assert that
their `agent_blob` rows differ by `instance`. Assert that neither user's `recall_notes` or injected
header returns the other's content. Assert that `rehydrate_workspace` restores only the caller's instance.

**What is left of WS-9.** Only the file-tier work of 3b and 4 and the correction UX of 3c on the
profile and org knowledge. Those slices need a new acceptance section before dispatch (the 2026-08-02
audit found none). The owner of that rewrite is the next WS-9 task. If no slice survives, WS-9 closes
into WS-KG at KG-11.

---

## 10. Open questions

1. **Where does the always-on budget actually sit?** 2 000 tokens is a guess. It should be
   derived from the model's context and what the caching work wants in the stable prefix —
   worth measuring against a real agent's `NOTES.md` before fixing a number.
   **Partly answered:** `agent-startup-guru` keeps an `agent_memory_index.json` beside its
   long-term store — *always-load a small index, load entries on demand*. That is the shape
   the budget should take, so the question narrows to how big the index may be rather than how
   big the memory may be. See [`agent_architecture.md`](agent_architecture.md) §7.
2. **Who curates the file tier — the agent or a human?** Agent-curated is the point of the
   pattern; human-reviewable is what makes it trustworthy. Probably: agent proposes, the
   promotion affordance in §6.7 is the human's veto.
3. **Should promotion be per-instance or per-agent-definition?** If every founder's coach
   independently learns the same general lesson, that's waste — but pooling it is exactly the
   leak in `agent-kinds.md` §9.1. A derived-aggregate tier with no verbatim facts is the
   likely answer, and it needs its own design.
4. **Do the app stores (7) fold into this model or stay separate?** GTD items and email
   patterns are structured domain records, not recalled facts. They probably stay separate —
   but their *derived* facts ("Vijay reschedules deep work on Fridays") belong in the tiers
   above, and nothing currently promotes them there.
5. **Does the session cache survive instance-keying?** `session_mem:{thread_id}` is keyed by
   thread, which is already per-room, so it should be fine — but it must include the clearance
   set in the key, or a room whose membership changes serves a stale block computed at a wider
   clearance.
   **Answered/built 2026-07-30:** the cache key now includes the clearance set, and
   `invalidate_session_memory` clears every per-clearance variant rather than the bare key
   (`acb_memory/session_cache.py`; `docs/multiplayer/memory-clearance.md` §7).

Question 5 is a correctness issue, not a design preference — it was resolved during 3a′, as required.

## Board record (2026-08-09) — moved from work_plan.md §2

> **RETIRED in part (2026-10-29, D132):** this record is history. Its Mem0 facts are RETIRED (D132.4) and do not guide new work.

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-9 — **Memory tiers 3b/3c/4** (budgeted file-tier header, provenance markers, correction UX, supersession)
**State cell (as of the move):** 🟡 Docs
**Narrative (verbatim):** 3a′ substrate shipped (migs 136–139). §6.7 correction UX is the highest-leverage UX item in the corpus. **Audited 2026-08-02 → NO-GO**: §9 gives acceptance for **3a′ only** (which is WS-10's, already shipped) — 3b/3c/4, the whole of WS-9, have none; §6.7 is experience prose with no endpoint, model or assertion; §6.5 ends "there are two honest paths… don't do both", an owner call presented as acceptance. Header still says `Draft / RFC · 2026-07-26` over a body stamped 2026-08-01 (R4). Paths are bare filenames whose line numbers have moved (`routes/memory.py` gate is now `_authorize_scope` :128-167; `_tool_injection.py:488-493` moved to `acb_skills/addendum.py` in WS-23 S3). **Verified substrate:** `MemoryClient` has search/add/get_all/delete and **no `update`**; the API has no PUT/PATCH; `/memory` already does list + semantic search + delete + clear-all (§5.5 understates it) but has **no edit, no provenance**, and hardcodes one of the **five** scope shapes (`<email>` · `prefs:` · `room:` · `agent:` · `org:global`). No provenance/supersession fields exist anywhere. **NOT owner-gated** — the gate logic is testable against a fake with Mem0 disabled (41 tests, 0.58s); the real trap is inverted: **this box's `.env` already has Mem0 enabled, and `tests/unit/test_memory_integration.py` HANGS (measured exit 124); assume `test_memory_e2e.py` does too — name test files, never `tests/unit/`.** **D4 constraint:** `orchestrator/agents.py:520-534` reads only the user scope, so correcting an `org:global` fact would show fixed in the UI and change nothing on that path — PR-1 must restrict to `<email>`/`prefs:`/`agent:` or say so. **Slice when specced (3c-0, AGENT-SAFE):** `PATCH /memory/{scope}/{memory_id}` reusing `_authorize_scope(write=True)` + the 404-not-403 membership probe at `memory.py:237-240`; `MemoryClient.update`; provenance in **Mem0's own metadata** (`corrected_by`/`corrected_at`/`supersedes`) — no new table; PATCH in the Next proxy; inline edit + compartment selector on `/memory`. **Scope creep to cut:** §6.1 instance-keying is WS-14's and the 3a′ remainder is WS-10's — this row should stop claiming both.

**Corrections applied 2026-08-09:**
- ownership settled 2026-08-09 — the 3a′ remainder (subject: compartments) is WS-10's S1
- this spec owns 3b/3c/4 only.
