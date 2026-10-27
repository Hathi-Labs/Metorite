# Personal vs Shared Agents

**Status:** Draft / RFC — **§6's roster is aspirational, not a work list** (see the
warning there) · **Date:** 2026-07-26, updated 2026-08-01 (doc-truth pass) ·
**Verified against code:** 2026-08-03 (WS-14 doc remediation — §6 only) ·
**Owner:** vjvarada
**Companion to:** [`README.md`](README.md) (rooms) · [`memory-clearance.md`](memory-clearance.md) (memory compartments)

> **Header correction 2026-08-03 (R4).** This file read `Draft / RFC · 2026-07-26`
> while carrying 2026-08-01 update blocks in the body. The date now reflects both.
> Also note its **path**: this document lives at `docs/multiplayer/agent-kinds.md`, not
> under `project-docs/specs/` — `work_plan.md` §4 cited it as if it were a spec and
> was corrected in the same change.

Which agents are one-per-person and which are one-brain-for-the-team — and what that
decides about memory, sharing, and rooms.

Mockup: [`mockup-agents.html`](mockup-agents.html)

---

## 1. It's two axes, not one

"Private agent vs public agent" sounds like one flag. It is two, and conflating them is what
produces the leak:

| Axis | Question | Values |
|---|---|---|
| **Visibility** | Who may *invoke* it? | `private` · `team` · `organization` |
| **Instancing** | Whose memory does it accumulate? | `personal` · `team` · `shared` |

The email agent is the proof they're orthogonal. **Everyone in the company should be able to
use it, and every person should get their own memory.** That is org visibility with personal
instancing — inexpressible as a single flag. Visibility is designed in the org research doc
(§6.1); instancing is new and is the one that governs data.

| | **personal** instancing | **team** instancing | **shared** instancing |
|---|---|---|---|
| **private** visibility | **Startup coach** — only you invoke it, only your memory | — | — |
| **team** visibility | A team-restricted tool where each member's history is their own | **Sales assistant** — the sales team invokes it, one shared brain | — |
| **org** visibility | **Email agent** — anyone can use it, everyone gets their own | — | **Reconciler** — one brain, org-wide facts |

Four cells are the real product. The rest are legal but rare.

### 1.1 A third value worth having

`memory: "none"` — a stateless agent that accumulates nothing at all. The right setting for
formatters, linters, and one-shot utilities, and it removes an entire class of question.

---

## 2. What the code does today

> **Update 2026-08-01 (doc-truth pass): no longer the live behaviour.**
> Instance-keying shipped — migration 136 added the instance key to
> `agent_blob` and the run path now resolves instance-keyed agent memory
> ([`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md)
> §6, step 2). This section stands as the rationale and the pre-change history.

Today **every agent has exactly one memory bucket, keyed by agent name, written by every user
and read by every user.**

- Read, on every run: `get_scoped_context(scope_key(agent=agent_name), …)` —
  `routes/agent.py:1302`, commented *"shared across every user of this agent"*.
- Write, whenever the model calls the tool: `add_scoped_memories(scope_key(agent=agent), …)` —
  `acb_skills/memory_tools.py:334`.
- `scope_key(agent="sales")` → `"agent:sales"` — one partition per agent name
  (`mem0_client.py:46-62`).

For genuinely shared agents that is correct and is the feature. For an agent people treat as
personal — a coach, an email assistant — it means **every user's facts pool into one
compartment that every other user's runs read back.**

### 2.1 The boundary is currently a docstring

`save_agent_memory`'s docstring (`memory_tools.py:306-310`) says:

> *"Save a fact into this AGENT'S shared memory (visible to all its users). … Do NOT put a
> single user's private preference here (use `save_memory` for that)."*

That instruction is addressed to the model. For a shared agent it's good guidance. For a
personal agent it is the *only* thing standing between two users' private context — and a
model's judgement about what counts as "a single user's private preference" is not an access
control. Instancing replaces the instruction with a key.

### 2.2 Graphiti: writes are partitioned, reads are not

Second, narrower finding in the same area. The knowledge-graph write passes a per-user group:

```python
# routes/agent.py:1360-1366
background_tasks.add_task(
    add_episode, name=f"agent:{agent_name}:{user_id[:20]}",
    content=user_msg[:500], group_id=user_id,          # ← partitioned by user
)
```

but the read never filters on it:

```python
# graphiti_client.py:144-165 — GraphitiClient.search()
results = await g.search(query=query, center_node_uuid=center_node_uuid,
                         num_results=num_results)      # ← no group filter
```

`search_entity_timeline` (`:253`) is called on every enriched run (`agent.py:1316-1321`), so
its results can span every user's episodes even though each was written into its own group.
Writes scoped, reads unscoped. The fix is to pass the run's clearance as the group filter —
subject to confirming `graphiti-core`'s exact search signature in the pinned version, which I
have not verified here.

### 2.3 Calibration: both are latent, not live-by-default

`.env.example` ships `MEM0_ENABLED=false` and `GRAPHITI_ENABLED=false` (`:52`, `:57`). So this
is not "you are leaking today" unless memory is enabled in the deployed environment — it is
"this is the behaviour the moment memory is on and more than one person uses an agent." Worth
checking the deployed `.env` before deciding urgency; either way it should be fixed before
personal agents ship.

---

## 3. The declaration

An agent's kind is a property of the agent, so it belongs in the agent's own `config.json`,
versioned in its repo — consistent with *"Git is the source of truth for all agent-editable
artefacts"* (`AGENTS.md`). The registry mirrors it; an org admin can override.

```jsonc
// config.json in agent-startup-coach
{
  "name": "startup-coach",
  "runtime": "github-copilot",
  "sharing": {
    "instancing":  "personal",     // personal | team | shared
    "visibility":  "private",      // private | team | organization
    "team":        null,           // required when either is "team"
    "memory":      "instance",     // instance | none
    "shareable":   false           // may one of its sessions become a room?
  }
}
```

```sql
-- agent_sharing migration — number assigned at build time (still unbuilt;
-- "119" predates migrations 136-139 landing)
ALTER TABLE dynamic_agents
    ADD COLUMN IF NOT EXISTS instancing TEXT NOT NULL DEFAULT 'personal'
        CHECK (instancing IN ('personal','team','shared')),
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'organization'
        CHECK (visibility IN ('private','team','organization')),
    ADD COLUMN IF NOT EXISTS team_ref   TEXT,
    ADD COLUMN IF NOT EXISTS memory_mode TEXT NOT NULL DEFAULT 'instance'
        CHECK (memory_mode IN ('instance','none')),
    ADD COLUMN IF NOT EXISTS shareable  BOOLEAN NOT NULL DEFAULT false;
```

> **Update 2026-08-01 (doc-truth pass):** the `dynamic_agents` sharing columns
> above are still unbuilt, and this work is now owned by
> `department_centers.md` Phase C (`work_plan.md` D3, WS-14).

### 3.1 The default must be `personal`

The failure modes are asymmetric: an agent wrongly marked shared **leaks**; an agent wrongly
marked personal **forgets**. One is a breach, the other is a recall miss you notice and fix.

So `instancing` defaults to `personal` and `shareable` to `false` — which is a **deliberate
change from today's behaviour**, where every agent's memory is implicitly org-shared. Every
existing agent therefore needs an explicit decision at migration time (§6), not a silent
carry-over.

---

## 4. How instancing keys memory

One extension to the same chokepoint function:

```python
scope_key(agent="startup-coach", instance="u:vijay@fracktal.in")
                                        → "agent:startup-coach#u:vijay@fracktal.in"
scope_key(agent="sales",         instance="t:sales")
                                        → "agent:sales#t:sales"
scope_key(agent="reconciler")           → "agent:reconciler"      # unchanged
```

`#` as the delimiter because emails contain `@` and agent names don't contain `#`. A bare
`agent:<name>` stays valid, so shared agents keep their existing partition and nothing
migrates by accident.

Resolution happens once, at run start, next to the clearance computation:

```python
def agent_scope(agent: AgentRecord, actor: str) -> str | None:
    if agent.memory_mode == "none":   return None
    if agent.instancing == "personal": return scope_key(agent=agent.name, instance=f"u:{actor}")
    if agent.instancing == "team":     return scope_key(agent=agent.name, instance=f"t:{agent.team_ref}")
    return scope_key(agent=agent.name)
```

Call sites: `routes/agent.py:1302` (read), `memory_tools.py:297,334` (the `recall_agent` /
`save_agent_memory` tools — these read the agent name from a contextvar and must read the
resolved *scope* instead), and `tasks/task_memory.py:47,90`.

---

## 5. The rules

**1. A personal agent's session can never become a room.** The Share button is disabled with
the reason stated, not hidden: *"Startup coach is a personal agent — its memory is yours
alone, so its conversations can't be shared live."*

**2. But you can still show someone.** The escape hatch is a **read-only transcript
snapshot** — a different object from a room. People can read what happened; nobody can stand
in your coach's context or make it answer them. Honest, and it covers the real want ("show my
co-founder what the coach said") without punching a hole in the model.

**3. Personal agents still read org memory.** Isolation is of *instances*, not of the company.
A personal-agent run reads: `prefs:<you>` · `user:<you>` · `agent:<name>#u:<you>` ·
`org:global` · any `subject:` compartment you're a member of. It never reads another
instance, and no other instance reads it.

**4. Team agents are shared brains with a team-sized audience.** `agent:<name>#t:<team>`,
readable by team members, and their sessions *can* become rooms — that is the sales-assistant
case the whole room model was built for.

**5. Instancing is immutable while memory exists.** Flipping a live agent from shared to
personal cannot un-commingle what's already pooled (§6). The change is allowed only through
the migration path, never as a quiet edit.

---

## 6. Migrating the agents we already have

The hazard: existing `agent:<name>` buckets hold facts from every user, and **they are not
reliably attributable**. `client.add(messages, user_id=scope, agent_id=agent_id)`
(`mem0_client.py:231`) records the scope and the agent, not the human who caused the write.

`agent_run` (migration 50) does carry `user_id`, `agent_name`, and `started_at`, so a
best-effort correlation by timestamp is *possible* — but concurrent users' runs interleave,
and a wrong attribution moves someone's fact into someone else's private compartment. That is
a worse outcome than forgetting.

**Recommendation: quarantine, then review.** On flipping an agent to `personal`, move its
bucket to `quarantine:agent:<name>` — readable by nobody, deleted by nobody — and let the
instances relearn. An admin screen lists the quarantined facts so anything genuinely
org-general can be promoted to `org:global` by hand. Bounded, reversible, and it never guesses.

> **Update 2026-08-01 (doc-truth pass):** this recommendation shipped —
> the quarantine of commingled agent data **landed as migration 137**.

Then the actual decision, per agent we have today:

> ⚠️ **This table is a DESIGN PROPOSAL, not a work list — verified 2026-08-03.**
> Two things about it are load-bearing and were being read the wrong way by
> `department_centers.md` Phase C:
>
> 1. **Seven of the twelve agents below do not exist.** `apps/agents/` holds exactly
>    six: `agent-apis-config`, `agent-app-builder`, `agent-email-assistant`,
>    `agent-orchestrator`, `agent-task-manager`, `agent-whatsapp-assistant`. `sales`,
>    `billing`, `delivery`, `startup-coach`, `triage`, `reconciler` and `strategy` are
>    all hypothetical here. A ticket cannot "make sales team-instanced"; it would first
>    have to build a sales agent. *(`reconciler` is a **service** at
>    `apps/services/reconciler/`, not a loadable agent — a third distinct thing.)*
> 2. **For three agents that DO exist, this table contradicts shipped config.** It
>    assigns `task-manager` (`:288`), `orchestrator` (`:295`) and `app-builder`
>    (`:296`) **personal** instancing. All three `config.json` files say
>    `"instancing": "shared"`. The measured state of the six is **four `shared`**
>    (apis-config, app-builder, orchestrator, task-manager) **+ two `personal`**
>    (email-assistant, whatsapp-assistant).
>
> **Why that matters more than a doc nit.** `instancing` is the state-partition key.
> Flipping any of the three to `personal` to "match the roster" makes
> `AgentManifest.instance_key()` (`packages/acb_skills/acb_skills/manifest.py:235-246`)
> return `u:<email>` instead of `''`, which moves `memory_scope()` from `agent:<slug>`
> to `agent:<slug>#u:<email>` and `blob_instance()` likewise — **every existing memory
> and blob silently becomes unreachable from the running agent.** That is a data
> migration (§6's own quarantine-then-review procedure, shipped as migration 137),
> not a config edit, and it is explicitly **not** in `department_centers.md` C3.
>
> Reconciling this table with the shipped configs — annotating it as aspirational, or
> writing the migration plan — is C3 step 2. Until then, **the `config.json` files are
> the truth and this table is the proposal.**

| Agent | Instancing | Visibility | Shareable | Why |
|---|---|---|---|---|
| `startup-coach` | personal | private | ✗ (snapshot only) | Everything it knows is about you |
| `email-assistant` | personal | organization | ✗ | Everyone should have one; nobody should see another's mailbox context |
| `whatsapp-assistant` | personal | organization | ✗ | Same |
| `task-manager` | personal | organization | ✓ | GTD data is already `user_id`-scoped; a shared planning room is useful |
| `sales` | **team** | team (sales) | ✓ | The canonical shared agent — one brain, the whole team talks to it |
| `billing` | team | team (finance) | ✓ | Same shape |
| `delivery` | team | team (delivery) | ✓ | Same shape |
| `triage` | personal | organization | ✗ | Triages *your* inbox and messages |
| `reconciler` | **shared** | organization | ✓ | Operates on company source-of-truth; no per-user state |
| `strategy` | shared | team (exec) | ✓ | Org-level digest; audience is the restriction, not the memory |
| `orchestrator` | personal | organization | ✓ | The general chat agent — memory is yours, sessions are shareable |
| `app-builder` | personal | organization | ✓ | Builds *your* apps; rooms are useful for co-building |

Note `task-manager`, `orchestrator`, `app-builder`: **personal instancing with shareable
sessions.** Those aren't contradictory — the agent's accumulated memory stays yours, and when
you share a session the room runs at room clearance (`memory-clearance.md` §3.3), which
excludes your instance compartment anyway. Personal instancing and a shared room compose
correctly; only agents where *every* conversation is inherently private (coach, email,
triage) get `shareable: false`.

---

## 7. How this composes with rooms and clearance

Nothing in the clearance rule changes. A run still reads at the clearance of its
least-cleared viewer; instancing only decides **which agent compartment is a candidate** in
the first place:

| Run | Agent compartment offered | Then filtered by clearance |
|---|---|---|
| You, solo, startup coach | `agent:startup-coach#u:you` | trivially, viewers = {you} |
| You, solo, sales assistant | `agent:sales#t:sales` | trivially |
| Sales team room, sales assistant | `agent:sales#t:sales` | intersection over the room excludes anyone's `user:` and any `subject:` not shared by all |
| Someone outside sales invited to that room | `agent:sales#t:sales` **drops out** — they aren't in the team instance | and the room says so in the banner |

That last row is the interesting one and worth building deliberately: inviting a non-member
into a team-agent room silently degrades the agent unless the UI says why. The banner should
read *"Arun isn't on the sales team — the assistant is running without its team memory in
this room."* Same principle as the private hint: make the boundary legible instead of letting
the agent just seem dumber.

---

## 8. Phasing

Slots ahead of the memory work in [`memory-clearance.md`](memory-clearance.md) §7, because
instancing decides what a compartment even is.

**Update 2026-08-01 (doc-truth pass):** team-instanced agent rollout is owned by
`work_plan.md` WS-14 (Centers C); this section is the design reference.

| Phase | Work |
|---|---|
| **0** | Graphiti read scoping (§2.2) — pass a group filter, or disable `search_entity_timeline` until it is scoped. Alongside the `routes/memory.py` authorization fix. |
| **3a′** *(before compartments)* | The agent-sharing migration (next free number at build time) · `config.json` `sharing` block + registry mirror · `scope_key(instance=…)` · `agent_scope()` resolution at the four call sites · `shareable=false` disables the Share button · quarantine + admin review for existing buckets |
| **3b** | Team instances, the non-member banner (§7), transcript snapshots as the personal-agent escape hatch |

**Acceptance for 3a′:** two users, one personal agent, memory enabled. Assert that no
`search()` call in user B's run is ever issued with user A's instance scope key, and that
`save_agent_memory` in A's session writes only to A's instance — verified at the Mem0 call
layer, not by reading the answers.

---

## 9. Open questions

1. **Does a personal agent ever need to learn across its instances?** "Most founders ask this
   at month 3" is real value, and it is exactly the thing that must not be built by pooling raw
   memory. Probably a separate, explicitly-derived aggregate compartment with no verbatim
   facts — worth designing before someone asks for it and reaches for the shared bucket.

   > **Prior art has a second answer worth weighing (2026-08-01).** `qm` offers an explicit
   > **environment attachment**: two scopes may be attached to one environment, at which point they
   > share one memory notebook, one disk and one sandbox. It is owner-mediated — a non-owner asking
   > to attach gets a refusal that names the owner to ask, the same shape as a credential grant.
   > That covers "these two instances really are one brain" deliberately and reversibly, without an
   > aggregate-derivation pipeline. It does **not** answer the cross-instance *learning* case above
   > (it merges, it does not generalise), so the two are complementary rather than alternatives.
   > `multiplayer_prior_art_qm_2026-08.md` §QM-7.
2. **Team membership source.** ~~`team_ref` needs a real team object; the org research doc's
   `module` is the natural home, but it isn't built yet.~~ **Answered** —
   [`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md) §1:
   `sharing.team` names an `org_group.slug` (migration 138), the single group primitive shared
   with access control Phase 2 and session sharing. `t:<team>` keys are `t:<org_group.slug>`.
3. **Per-user agent config.** If everyone has their own email agent, does everyone get their
   own `instructions.md` tweaks and model choice, or only their own memory? Memory-only is
   the smaller first step; per-user prompt overrides are a real want soon after.
4. **Cost attribution for personal instances** — N users × one agent is N instances of spend
   against one registration. The existing per-run `agent_run` accounting covers it, but the
   observability view is currently per-agent and would report a misleading total.
5. **Does `shareable: false` need an admin override?** A compliance or handover situation may
   require reading someone's coach transcripts. If yes, it must be an audited break-glass, not
   a quiet capability.
