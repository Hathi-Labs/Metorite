# Memory Compartments & Run Clearance

**Status:** Phase 0 + the room half of 3a **built**; `subject:` compartments and the
`prefs`/`user` backfill **not built** ([§7](#7-phasing)) ·
**Date:** 2026-07-26 · **Verified against code on 2026-08-02, re-verified the same day, then
adversarially reviewed and repaired twice** — line numbers move; re-verify at dispatch, per
`work_plan.md` §1 contract item 4. ·
**Verification commands:** [§9](#9-verification) · **Owner:** vjvarada
**Companion to:** [`README.md`](README.md) (the multiplayer room model) ·
[`agent-kinds.md`](agent-kinds.md) (personal vs shared agents) ·
`../../project-docs/specs/multi_user_organization_research.md` (org/RBAC)

> **What the three passes corrected, so the anchors below can be trusted for the reason they
> should be — that they were wrong before and were caught.**
>
> - **Pass 2** fixed six things: a wrong permission line (`permissions.py:68`, not `:70`), an
>   acceptance criterion that said "member" where §7.1.5 allows members
>   ([§7.1.3](#713-create-compartment-and-add-member-endpoints) dw1), a precedent cited for a
>   `409` that actually returns `400` (dw5), a grammar claim that named `_clean_slug`
>   ([§7.1.1](#711-the-scope-key-must-be-one-url-path-segment)), two surviving readings of
>   `subject_ref` ([§3.2](#32-the-compartment-registry)), and three done-whens that were
>   already green before any work started (§7.1.1 dw2/dw3, §7.1.5's row count).
> - **Pass 3 (2026-08-02) fixed the one that mattered.** §7.1.4 specified the clearance cap by
>   pointing at `_capability_cap` — a **display** cap that drops `group:` and `org` subjects by
>   design — so the "intersection" it described became a **union** for exactly the rooms where
>   a leak is widest, and its done-when passed against two email addresses. §7.1.4 now names
>   the site, the helper, and the expansion, and requires the expansion to **fail closed**.
>   Also in pass 3: done-when 6 asserted a `422` that shipped code does not produce
>   (`RoomPatch` is not a closed model), §7.1.6 left two of §3.2's five `audience` values with
>   no derivation rule, and §7's Gate cell read an unqualified **AGENT-SAFE** while the two
>   other docs qualified it.

> **Read [`agent-kinds.md`](agent-kinds.md) first.** An agent's *instancing* (one brain per
> person / per team / for everyone) decides how the `agent:` compartment below is keyed, and
> whether the agent's sessions may become rooms at all. This doc assumes that choice is made.

How one agent holds memory across sessions **and** across people, and decides which
parts of it are usable on any given call.

Mockups: [`mockup-share.html`](mockup-share.html) (going shared mid-conversation) ·
[`mockup-memory.html`](mockup-memory.html) (what it knows, and who can see it)

---

## 1. The case that breaks the simple model

From the room RFC, `chat_session.context_policy` was `room | driver | none` — a per-room
switch for whether personal memory participates. That is too coarse to survive a real
situation:

> The CEO discusses **Project Falcon** with `agent-sales` — an acquisition nobody outside
> two people should know exists. The same CEO, with the *same agent*, also works the **Acme
> renewal**, which is deliberately collaborative: three people in a deal room, all of whom
> should share the agent's context on it.

Under a per-room switch:

- `context_policy='driver'` in the Acme room loads the CEO's personal memory — and Mem0
  retrieval is **semantic**, so a question about "the enterprise pipeline" can surface
  Falcon on similarity alone. Nobody made a mistake; the retriever did its job.
- `context_policy='room'` excludes personal memory entirely — so the agent forgets
  everything the CEO ever told it, including the Acme context that *should* be shared.

Both are wrong because the switch is on the wrong axis. **Confidentiality is not a property
of a person, and it is not a property of a room. It is a property of a subject.** Falcon is
restricted; Acme is not; both belong to the same person and the same agent.

So memory has to be partitioned by **compartment**, and each call has to run at a
**clearance**.

---

## 2. What the memory layer already gives us

This design is smaller than it sounds because the partitioning primitive already exists and
is in the right place.

| Fact | Where | Why it matters |
|---|---|---|
| One function builds every memory partition key | `scope_key(user=…, agent=…, org=…)` — `packages/acb_memory/acb_memory/mem0_client.py:50` | Adding compartment kinds is an extension of one function plus its constants, not a new store. |
| The scope rides Mem0's `user_id` field | `mem0_client.py:184-198`, `:231-239` — `client.search(query, filters={"user_id": scope})` | **The partition is applied inside the vector query.** A compartment we don't pass is never searched — not searched-then-filtered. This is the difference between a boundary and a suggestion. The module docstring (`:12-15`) says they chose `user_id` over `agent_id` precisely because it is the field Mem0 reliably filters on. |
| Three scopes already coexist in one collection | `AGENT_SCOPE_PREFIX = "agent:"`, `ORG_SCOPE_KEY = "org:global"` (`:46-47`) | The namespacing convention for new compartment kinds is established. |
| Per-scope read/write/enumerate helpers | `get_scoped_context`, `add_scoped_memories`, `get_scoped_all` (`:313-349`) | A compartment read is a call we already have; the inspector UI gets `get_scoped_all` for free. |
| Graphiti episodes are grouped | `add_episode(..., group_id=…)` — `graphiti_client.py:187` | Subject compartments map onto `group_id`, so the knowledge graph partitions the same way. |
| The assembled block is cached per thread | `get_session_memory` / `invalidate_session_memory` | Multi-compartment reads cost N searches; the existing cache absorbs that across turns (and it is why the block must stay byte-stable for prompt caching). |

### 2.1 Prerequisite: the memory API was open by path parameter — ✅ fixed 2026-07-30

`apps/services/gateway/gateway/routes/memory.py` resolves `UserContext` on every route and
then **never compares it to the `{user_id}` in the path**:

```python
@router.get("/{user_id}", summary="List all memories for a user")
async def list_memories(user_id: str, user: UserContext = Depends(get_current_user)):
    return await client.get_all(user_id)      # user is resolved, then ignored
```

Any authenticated user could list (`:59`), semantically search (`:71`), delete (`:84`), and
write (`:97`) **any** other user's memory scope by putting their email in the URL.

**It was worse than "needs a signed-in session".** Two of the three paths in did not need
one at all:

- `/api/chat/memories?userId=<colleague>` (Next BFF) took the scope from a query parameter,
  never called `auth()`, and `/api/chat/` sits in the proxy's public list.
- `lib/memory.ts` forwarded only the internal Bearer token and no identity headers, and the
  gateway reads a bearer-without-identity call as **the platform acting as itself** — full
  service access (`acb_auth/deps.py` §1b). So the scope check had nobody to compare against
  even once it existed.

The fix therefore lands in three places, and the middle one is the reason the first alone
would not have been enough:

1. `_authorize_scope` in `routes/memory.py` — one rule per scope shape (own email / agent /
   org), unknown shapes refused, and `delete` additionally confirms the memory is *in* the
   scope (naming your own scope and a colleague's memory id was the same IDOR one level
   down).
2. `lib/memory.ts` forwards the acting member, and the two Next routes require a session and
   derive the scope from it rather than from caller input.
3. A service principal may reach shared scopes but must **assert an identity** to touch a
   person's. `deps.py` §1b argues a narrower service grant is theatre since the token holder
   could assert any email anyway — true, and beside the point: the distinction that matters
   is between asserting an identity and *omitting* one, and omission is exactly how this bug
   happened.

This had to be fixed **before** compartments mean anything: a compartment model whose read
API is addressable by guessing a scope key is decorative. The shape of the fix, as built:

```python
compartment = resolve_compartment(scope_key)          # 404 if unknown
if not can_read(compartment, user):                   # membership / audience check
    raise HTTPException(403, "Not your memory")
# delete additionally requires ownership or can_promote
```

---

## 3. The model

### 3.1 Compartments

A **compartment** is the unit of memory scoping. Every fact lives in exactly one. A
compartment's identity is its scope key; its **audience** is who may read it.

```python
# scope_key() extended — same function, new kinds
scope_key(user="vijay@fracktal.in")          → "vijay@fracktal.in"        # existing
scope_key(user="vijay@fracktal.in", prefs=True)
                                             → "prefs:vijay@fracktal.in"  # NEW
scope_key(room="thread-abc123")              → "room:thread-abc123"       # BUILT 2026-07-30
scope_key(subject="zoho-4471")               → "subject:zoho-4471"        # NOT BUILT (§7.1)
scope_key(agent="sales")                     → "agent:sales"              # existing
scope_key(org=True)                          → "org:global"               # existing
```

> **Corrected 2026-08-02.** This line originally read
> `scope_key(subject="deal/zoho-4471") → "subject:deal/zoho-4471"`. A scope key with a `/`
> in it cannot be addressed by `GET /memory/{scope}` — the router misses on the extra path
> segment and 404s **before** `_authorize_scope` ever runs. The key is a slug; the graph
> entity reference (`deal:zoho-4471`) lives in `memory_compartment.entity_ref`. See
> **§7.1.1**, which carries the grammar and the test.

| Kind | Holds | Default audience | Travels into a shared room? |
|---|---|---|---|
| `prefs:<email>` | **How this person works** — "prefers terse answers", "wants the number first, reasoning after" | The person, wherever they are | **Yes**, for the person currently being answered |
| `<email>` (user) | **What this person told it** — episodic facts from their private sessions | Private, always | **Never** |
| `subject:<entity>` | Everything about a deal / customer / project | Explicit member list | Only if every viewer is a member |
| `room:<thread_id>` | Facts established in this room | Room members | It *is* the room |
| `agent:<name>[#instance]` | Tradecraft the agent learns — "Odoo invoice lines lag Zoho ~2 days" | Depends on the agent's **instancing** — see [`agent-kinds.md`](agent-kinds.md) | Yes, if the viewers share the instance |
| `file:agent:<name>#<instance>` | The **always-on file tier** — curated `agent-data/` knowledge injected on every run | Same as the agent instance | Yes, if the viewers share the instance — and this is the tier to get right first (see below) |
| `org:global` | Company facts — "standard terms are Net 30" | Org | Yes |

**Splitting personal memory into `prefs` and `user` is what makes shared rooms usable.** In a
shared room nobody's `user:` compartment loads — not even the driver's — but the floor
holder's `prefs:` does. The agent still answers in your register and respects how you like to
work, without exposing a single thing you told it in private. Preferences are self-descriptive
and you are in the room; episodic facts are not and may not be.

### 3.2 The compartment registry

```sql
-- memory_compartments migration — number assigned at build time (still unbuilt;
-- "118" predates migrations 136-139 landing)
CREATE TABLE IF NOT EXISTS memory_compartment (
    scope_key    TEXT PRIMARY KEY,          -- the Mem0 user_id partition value
    kind         TEXT NOT NULL CHECK (kind IN
                   ('prefs','user','subject','room','agent','org')),
    label        TEXT,                      -- "Project Falcon"
    entity_ref   TEXT,                      -- 'deal:zoho-4471' → acb_graph entity
    audience     TEXT NOT NULL DEFAULT 'private'
                   CHECK (audience IN ('private','explicit','room','team','org')),
    sensitivity  TEXT NOT NULL DEFAULT 'normal'
                   CHECK (sensitivity IN ('normal','restricted')),
    created_by   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_compartment_member (
    scope_key    TEXT NOT NULL REFERENCES memory_compartment(scope_key) ON DELETE CASCADE,
    user_email   TEXT NOT NULL,
    can_promote  BOOLEAN NOT NULL DEFAULT false,   -- may widen this compartment's audience
    granted_by   TEXT,
    granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_key, user_email)
);
CREATE INDEX IF NOT EXISTS memory_compartment_member_user_idx
    ON memory_compartment_member (user_email);

-- A session or room may be BOUND to a subject. Binding is consent (see §4.2).
ALTER TABLE chat_session
    ADD COLUMN IF NOT EXISTS subject_ref TEXT;   -- a COMPARTMENT SCOPE KEY,
                                                 -- e.g. 'subject:falcon' — NOT an
                                                 -- entity ref.  See §7.1.1/§7.1.4.
```

> **Amended 2026-08-02 — two readings of this DDL, one of them wrong.** The comment on
> `subject_ref` originally read `'deal:zoho-4471'`, which reads as a graph entity reference.
> It is not one. §7.1.4 requires the value to name **a compartment the caller is a member
> of** — so it holds a scope key (`subject:falcon`), and the graph entity reference lives in
> `memory_compartment.entity_ref` above (§7.1.1). Left as it was, an implementer had to
> guess which, and the two guesses build different authorization.
>
> The same DDL is amended in one other place: `memory_compartment_member.user_email` is
> superseded by **`subject`** with the `app_grants` vocabulary (`email` | `group:<slug>` |
> `org`) per **§7.1.6**, which also makes `audience` a derived label rather than a stored
> access input. Both amendments — and the slug-shaped `scope_key` — are collected in
> **§7.1.8**; that section, not this block, is the shape to build.

`sensitivity='restricted'` is not an extra permission — membership already governs that. It
drives *behaviour*: restricted compartments never appear in autocomplete for
non-members, never surface in a cross-compartment search, and block mid-run joins (§5.4).

### 3.3 The clearance rule

> **A run reads at the clearance of its least-cleared viewer.**

```python
def clearance(run) -> set[str]:
    viewers = room_members_who_can_see_output(run.thread_id)   # solo thread → {you}
    personal = intersection(compartments_readable_by(v) for v in viewers)
    return personal | {
        scope_key(agent=run.agent_name),
        scope_key(org=True),
        scope_key(room=run.thread_id),
        scope_key(user=run.floor_holder, prefs=True),
    }
```

Intersection, never union. Everything else follows from it:

- **Solo session** — the intersection over `{you}` is your whole clearance. You get Falcon,
  Acme, your private facts, your prefs. Nothing changes from today.
- **Falcon deal room (CEO + CFO)** — both are members of `subject:deal/falcon`, so it
  survives the intersection. Neither person's `user:` compartment does, because the other
  isn't cleared for it.
- **Acme team room** — the sales team is not in Falcon, so `subject:deal/falcon` is not in
  the intersection. The agent does not *decline* to use it; `search()` is **never called
  with that scope key**. There is no retrieval to leak.

The last point is the reason this is worth building rather than prompting for. "Don't mention
Falcon" in a system prompt is a request. Not passing the scope key is a boundary.

> **Now grounded in shipped machinery (2026-07-29):** this same rule was independently
> chosen for *permissions* — a shared run's authority is
> `EffectiveAccess.intersect()` folded over all participants
> ([`groups_sessions_authority.md`](../../project-docs/specs/groups_sessions_authority.md) §3,
> primitive in `packages/acb_auth/acb_auth/permissions.py`). Memory clearance and run
> authority are the same shape applied to two resources: what may be *read into* the run,
> and what the run may *do*. One rule, two enforcement points — credentials via
> `executor._integration_authorizer`, compartments via the scope keys passed to `search()`.

> **Independently reproduced outside the building (2026-08-01).** `yc-software/qm` — a multiplayer
> agent harness released 2026-07-29 — reached the same rule with no contact with this design, and
> applied it to **three** resources: transcript replay into the model (an entry is replayed only if
> *every* audience member is entitled to its origin label), shared file handles, and **network
> egress** (allowed hosts = the intersection across the audience, denied hosts = the union). All
> three fail closed on an empty audience. Two things follow. First, the rule is not an
> over-engineering of ours — it is what the problem forces. Second, egress is a third enforcement
> point we do not use: we intersect what a run may *read* and what it may *do*, not where it may
> *connect*. Noted against WS-1/WS-3 in
> `multiplayer_prior_art_qm_2026-08.md` §QM-0.

### 3.4 The write rule

> **A fact may be written only to a compartment whose audience is no wider than the session
> where it was learned — unless a human widened it deliberately.**

Three cases:

1. **Default** — write to the narrowest compartment covering the session's audience. A solo
   CEO session writes to `user:vijay`. A room writes to `room:{tid}`.
2. **Bound session** — if the session declares `subject_ref`, facts file into that subject's
   compartment. **Binding a session to a subject is the consent to write there**, which is
   why binding is an explicit act with a confirmation, not an inference (§4.2).
3. **Promote** — widening beyond the session audience is a human action by someone with
   `can_promote`, and the confirmation names every person who gains access.

And the rule that stops the classifier from becoming the leak:

> **Inference may choose *which subject* a fact belongs to. It may never choose an audience
> wider than the session it was learned in.**

Without this, an auto-classifier that files a private CEO remark under `subject:acme`
(audience: sales team) has just published it. With it, the worst an over-eager classifier can
do is file a fact somewhere too narrow — recoverable, and visible in the inspector.

### 3.5 Where this lands in code

> **Anchors re-verified against the tree on 2026-08-02.** Five of the seven rows below were
> stale (they cited the pre-136 line numbers, and two named work as pending that had already
> shipped). Line numbers move; re-verify at dispatch, per `work_plan.md` §1 contract item 4.

| Change | State | File — verified 2026-08-02 |
|---|---|---|
| New scope kinds | ✅ built (`room:`, `prefs:`; **no `subject:`**) | `packages/acb_memory/acb_memory/compartments.py:72` `scope_key()` + the namespace constants at `:35-38`. **Not** `mem0_client.py`, which no longer defines any of it — that file now only *re-exports* the vocabulary (`:40-47`, `__all__` at `:59-71`) so existing `from acb_memory.mem0_client import scope_key` imports keep working. One definition, deliberately. |
| Compartment resolution + ACL | ✅ built | `packages/acb_memory/acb_memory/compartments.py` — `Clearance` (`:41`), `scope_key` (`:72`), `scope_kind` (`:108`), `resolve_clearance` (`:130`). Deliberately dependency-free: it knows nothing about rooms or permissions, and the caller resolves `shared`. Any `subject:` extension must preserve that (§7.1). |
| Read: loop the clearance set instead of one `user_id` | ✅ built | `apps/services/gateway/gateway/routes/agent.py:1815` — the `_build_memory_block` closure, consumed at `:1878`. **Not `:1291`.** Clearance is resolved just above at `:1768-1774`. |
| Write: resolve the target compartment instead of the caller | ✅ built | The decision is `_extract_user = _clearance.write if _room_is_shared else _mem_user` at `routes/agent.py:1968-1974`; the call is `add_memories_background(...)` at `:2006-2008` inside `_persist_on_complete` (`:1983`). **Not `:1448`.** The function itself is `packages/acb_memory/acb_memory/mem0_client.py:300`. |
| Memory *tools* write scope | ✅ built — **this row is done** | `routes/agent.py:1785` already calls `_set_memory_user_id(_clearance.write)`, so `remember` / `save_memory` file into the room's compartment in a room and the caller's when solo. The rename to `_set_memory_write_scope` was cosmetic and was not done; the behaviour it described is in place. |
| Authorize the memory API | ✅ built | The gate is `_authorize_scope` at `routes/memory.py:128-167`, with one helper per shape (`_authorize_org` `:73`, `_authorize_room` `:82`, `_authorize_prefs` `:95`, `_authorize_agent` `:103`, `_authorize_person` `:112`). It is applied at **five** routes, not four: `:187` list, `:200` search, `:214` delete, `:244` add, `:266` status. **Not `:59,71,84,97`.** An unrecognised shape raises 404 (`:167`) — which is why `subject:` is a 404 today. |
| Graphiti grouping | not built | `add_episode(group_id=<scope_key>)` — `packages/acb_memory/acb_memory/graphiti_client.py:187`. Latent while `GRAPHITI_ENABLED` is false. |
| **Instance-key the file tier** | ✅ built (migration **136**) | `agent_blob` PK `(agent_name, path)` → `(agent_name, instance, path)`, with migration 137 quarantining commingled rows — [`memory_architecture.md`](../../project-docs/specs/memory_architecture.md) §6.1. |

The read path is the only one with a cost change: N compartment searches instead of one.
Bounded by clearance size (typically 4–6), parallelisable, and absorbed across turns by the
existing `get_session_memory` cache — **whose key must gain the clearance set**, or a room
whose membership changes will serve a block assembled at a wider clearance.

### 3.6 The file tier is the one to get right first

Compartments above are the *vector* tier. There is a second durable tier that is more
dangerous, because it is **injected rather than retrieved**: the curated `agent-data/`
knowledge the framework doc calls *"prompt that grows over time."*

`agent_blob`'s primary key is `(agent_name, path)` — `agent_name` is documented as the only
tenant key — so today there is **one `agent-data/NOTES.md` per agent, shared by every user of
it**, and `recall_notes(path)` with no query returns the whole file. A vector fact leaks only
on a semantic match; the file tier is simply *there*, in full, on every run that loads it.
*[2026-08-09: under D15 this is a live multi-tenancy gap, not a latent one — the fix rides
WS-29 (MT-1b/MT-1g for keys; the `slug='default'` resolution is implementation-spec trap 5).
Do not copy this pattern into new code — R5.]*

It needs the same instance key and the same write rule as the compartments, and the two must
land in the same phase — partitioning the vector tier while leaving the file tier shared fixes
the smaller half of the problem. Full design:
[`memory_architecture.md`](../../project-docs/specs/memory_architecture.md).

---

## 4. "Which deal am I on?" — subject resolution

The clearance rule needs to know the subject. Three ways it learns, in descending authority:

### 4.1 Bound room (strongest)

The room declares `subject_ref = 'subject:falcon'` — a **compartment scope key**, per §3.2 as
amended and §7.1.4. That compartment may in turn carry `entity_ref = 'deal:zoho-4471'`,
which is where the `acb_graph` entity (`deal`, `customer`, `project`, `person`) is named;
deal rooms, incident rooms and account rooms are a natural product concept and map onto
entities that already exist there. Everything is unambiguous: reads include that subject if
the viewers are cleared, writes file there, and the room header names it.

### 4.2 Declared inline

"This is about Falcon" in the composer binds the thread. Because binding changes *write*
targets, it takes a confirmation that names the consequence: *"Facts from this conversation
will be filed under Project Falcon, readable by Vijay and Meera."*

### 4.3 Inferred (weakest, and deliberately one-directional)

Entity linking on the turn against the graph. Subject to one hard constraint:

> **Inference may only narrow reads. It may never add a compartment to the clearance.**

If the agent infers "this is about Falcon" inside a room not cleared for Falcon, it does not
load Falcon memory. What it does instead is the interesting part.

### 4.4 The private hint

When a cleared viewer is in a room that isn't cleared for relevant context, the agent must
not announce that. Saying *"I have information about Project Falcon that I can't use here"*
leaks the existence of Falcon to the room — the breach the compartment was built to prevent.

So the notice is computed per-viewer and delivered on **that viewer's private lane**,
rendered client-side for them alone:

> 🔒 *You have 3 memories about **Project Falcon** that this room can't use.*
> **Ask privately** · **Add this room to Falcon** · **Dismiss**

Everyone else sees nothing — not a redaction marker, not a placeholder. This reuses the
private-lane mechanism from the room RFC (§6.5) and turns the boundary from a silent
degradation into a legible, actionable moment for exactly the person entitled to see it.

---

## 5. The permutations

Every row is the same agent (`agent-sales`) and the same underlying memory. Only the call
changes.

| # | Situation | Viewers | Reads | Writes |
|---|---|---|---|---|
| 1 | CEO solo, Falcon | vijay | `prefs:vijay` · `user:vijay` · `subject:falcon` · `agent:sales` · `org` | `user:vijay` — or `subject:falcon` if the session is bound |
| 2 | Falcon deal room, CEO + CFO | vijay, meera | `prefs:<holder>` · `subject:falcon` · `room` · `agent` · `org` — **neither person's `user:`** | `room` + `subject:falcon` (bound) |
| 3 | CEO joins the Acme team room | sales team + vijay | `prefs:<holder>` · `subject:acme` · `room` · `agent` · `org`. **Falcon is not queried** | `room` + `subject:acme` |
| 4 | Sales rep solo | rep | `prefs:rep` · `user:rep` · subjects they're a member of · `agent` · `org` | `user:rep` |
| 5 | Org-wide room | everyone | `room` · `agent` · `org` only | `room` |
| 6 | CEO's private lane *inside* the Acme room | vijay | Full CEO clearance, **including Falcon** | `user:vijay` — never the room |
| 7 | CEO hands the Falcon room to a rep | — | Blocked. Adding a member to a subject-bound room grants memory access, so it requires `can_promote` and a confirmation naming the count | — |
| 8 | Agent learns tradecraft in the Falcon room | vijay, meera | — | A generalisation with no restricted entity → `agent:sales`. Anything naming Falcon stays in `subject:falcon` (§5.2) |

### 5.1 Why row 2 is the one to check your intuition against

Two people, both fully cleared for the subject, and yet **neither person's private compartment
loads**. That is correct: Meera being cleared for *Falcon* does not make her cleared for
*everything Vijay ever told the agent*. Subject clearance and personal clearance are different
axes, and the intersection rule keeps them separate without anyone having to think about it.

### 5.2 The extraction leak, and the fix

`agent:` and `org:` compartments are org-readable, so extraction into them is the one place a
restricted fact could escape sideways: "Falcon is at $4.2M" is a fact about a deal, but a
naive extractor might file it as general agent knowledge.

The rule: **extraction runs, then every extracted fact is classified. Any fact that references
an entity belonging to a restricted compartment inherits that compartment** and cannot land in
`agent:`/`org:`. Only entity-free generalisations reach the shared compartments. When
classification is uncertain, it files to the narrowest candidate — a fact in the wrong narrow
compartment is a recall miss; a fact in the wrong wide compartment is a breach.

### 5.3 Revocation is forward-only, and we should say so

Removing someone from a compartment stops future reads. It does not un-read what they already
saw, and it does not scrub transcripts they legitimately had access to. The UI must state this
plainly rather than implying a clawback: *"Meera will no longer be able to use these memories.
Conversations she has already seen are unchanged."*

### 5.4 Clearance is fixed at run start

Mirroring acting identity in the room RFC. Two consequences worth building for:

- **You cannot un-see.** If a run has already loaded Falcon into context, someone joining
  mid-run cannot narrow it. So joins are admitted at **run boundaries**; if a run is holding a
  restricted compartment the join queues, and the room shows *"admitting Priya after this
  turn"*. The alternative — letting them in and hoping the model doesn't reference what it
  loaded — is not a boundary.
- **Sharing a conversation that already used restricted memory** cannot retroactively unshare
  it: those facts may be sitting in the transcript above. This is precisely why the history
  waterline defaults to *from here on*, and why the share sheet has to disclose it (§6.2).

---

## 6. UI/UX — going shared, mid-conversation

The moment matters: you are already deep in a chat and you realise this shouldn't be yours
alone.

### 6.1 Five ways in, one sheet

1. **`@mention` in the composer.** Type `@sanjay` mid-message; a chip appears inline; sending
   converts the thread to a room and invites him. The Google Docs move, and the lowest-friction
   path — most sharing should happen this way.
2. **Share button in the thread header** — always present, quiet while solo.
3. **`⌘⇧S`.**
4. **Sidebar row → Share.**
5. **The agent asks.** When a run hits an approval it can't make or a domain it can't act in,
   it can offer: *"This needs Finance sign-off — bring someone in?"* Agent-initiated
   multiplayer is a real moment and costs nothing to support once rooms exist.

All five open the same sheet, because the decisions below are entangled and must be made
together.

### 6.2 The share sheet

Four blocks, in this order:

**People** — who, and at what room role (observer / contributor). Plus the link setting:
invite-only, or anyone at Fracktal with the link.

**History waterline** — *from the beginning* / **from here on** (default) / *pick a point*,
with a live preview of where the divider lands. Implemented by the join cursors already in the
room RFC (`join_stream_id` / `join_message_ts` feeding the existing `?since=` replay and the
`before` window in `_get_messages`).

**What the agent will and won't be able to use** — the block that makes the Falcon case safe,
and the most important disclosure in the feature:

> **This conversation has used memory from:**
> 🔒 **Project Falcon** — restricted · 3 memories · Sanjay is not a member
> ● Acme Corp — 7 memories · Sanjay is a member
> ● Sales agent memory, Company memory
>
> After sharing, the agent **will not** use Project Falcon here.
> ⚠️ **9 messages above your waterline reference Project Falcon.** Sharing from the beginning
> would expose them. → *Keep the waterline here* (recommended) · *Share everything anyway*

This is the disclosure the whole model exists to make possible: it can be computed exactly,
because the clearance set for every past run is known.

**Acting identity** — one line: *the agent will act as vijay@fracktal.in (Zoho, ClickUp)*.

### 6.3 After sharing

- A persistent **waterline divider** in the transcript: *"Sanjay and Priya can see from here ↓
  — shared by you, 10:24."* Everything above is simply never sent to them.
- The header swaps the quiet solo chip for the presence rail and the data banner.
- The composer grows the mode switch: **Turn · Steer · Note · Ask privately**.
- A **restricted-context pill** appears while a run holds a restricted compartment,
  explaining why joins are queued right now.

### 6.4 The memory inspector

The `/memory` page today lists one flat scope. It becomes *"what does this agent know, and who
can see it"*: compartments down the left with kind, label, audience, fact count and a
restricted marker; facts on the right with their source session, date, and audience; and
actions that are honest about their blast radius — **promote** (widen; confirmation names
everyone who gains access and how many facts), **restrict**, **move to another compartment**,
**forget here**, **delete everywhere**.

One view earns its place beyond browsing: **"what would <person> see?"** — pick a teammate and
the inspector greys out everything outside their clearance. That is the check a CEO actually
wants before adding someone to a room.

---

## 7. Phasing

Slots into the room plan in [`README.md`](README.md) §8. Dispatch state lives in
[`work_plan.md`](../../project-docs/work_plan.md) §2, **WS-10**.

**Gate labels** (`work_plan.md` §1 contract item 7): **AGENT-SAFE** = an independent agent
may build it; **OWNER-GATE** = an agent must refuse it and name the gate.

| Phase | Work | Gate |
|---|---|---|
| **0** *(with the room Phase 0)* | ✅ **Authorize `routes/memory.py`** (§2.1) — done 2026-07-30. | — |
| **3a** *(built half)* | ✅ **Built 2026-07-30:** `scope_key()` kinds (`room:`, `prefs:`), `acb_memory/compartments.py` with `resolve_clearance`, clearance resolution at run start, read/write rules at both `routes/agent.py` call sites, the memory tools' write scope, the room compartment end-to-end, and the session cache keyed by clearance. | — |
| **3a** *(remainder — `subject:`)* | **Not built:** the compartment registry (migration: **next free number at build time**) and `subject:` compartments. A room's audience is its participant list, which already exists; a subject's needs the registry. **This is WS-10's one dispatchable slice** — its surface is specified in **§7.1**, and its acceptance is the "Acceptance for 3a" paragraph below, unchanged. | **AGENT-SAFE once §7.1 is accepted** — every decision in §7.1 is marked `DECISION (agent-proposed, owner may overrule)`, so dispatching before the owner has read it would build five endpoints, a migration and a column on a live table against defaults nobody signed. The qualifier is written here because the board's own Authority rule (`work_plan.md` §1) says the owning spec wins for *what to build and how*, so an unqualified cell here would out-rank `README.md` §8's and `work_plan.md`'s. All three now say the same thing. |
| **3a** *(remainder — `prefs`/`user` backfill)* | The `prefs`/`user` split has the vocabulary and the read path but **no backfill classifier**. Verified 2026-08-02: **nothing anywhere writes a `prefs:` key** — `resolve_clearance` writes the bare actor when solo and `room:<tid>` when shared, so `prefs:` is read-only and permanently empty. Splits in two: a **classifier + dry-run report** over existing personal memories, and **applying** its output. | **classifier + dry-run: AGENT-SAFE** · **apply: OWNER-GATE** — it mutates live Mem0 data (`work_plan.md` §6, "live-DB one-offs"), and §8 Q1 ends *"it should be a deliberate, communicated choice."* An agent must produce the report and stop. |
| **3b** | Subject binding (bound rooms, inline declaration) · entity linking for inference · the private hint on the personal lane · extraction classification (§5.2) | **AGENT-SAFE** after the 3a remainder — **except** §8 Q2 (auto-create on binding), which is an unresolved product call inside it |
| **3c** | Share sheet with the memory disclosure · waterline · memory inspector · "what would they see?" | **AGENT-SAFE** |

> **Phase-ID note (R2).** These 3a/3b/3c IDs are *this document's* and are the ones that
> bind. `memory_architecture.md` §9 uses a different 3a′/3b/3c/4 ladder for the **file-tier**
> work (owned by WS-9), and the two are not the same phases. Anything that says
> "`subject:` compartments are WS-10 **3b**" is wrong: they are the **3a remainder** above.
> On the dispatch board they are named **WS-10 S1** so no cross-doc phase ID is needed at
> all.

**Acceptance for 3a** — the load-bearing test: two threads, one agent, one person. Thread A is
bound to a restricted subject; Thread B is a shared room with someone not cleared for it. Assert
that no `search()` call in Thread B's run is ever issued with the restricted scope key — not
that the answer avoids mentioning it. Assert the same for the room after a mid-conversation
share, and that facts from Thread A never appear in any compartment readable by the room.

> **Met for the room case** (`tests/unit/test_memory_compartments.py`): the search calls are
> recorded and asserted to be exactly `room:` / `prefs:` / `agent:` / `org:` — no bare-email
> compartment is ever passed to `search()`. The *subject* half of this acceptance waits on
> `subject:` compartments, which are not built.
>
> **A second mechanism of the same class, not yet a live risk (checked 2026-08-01).** `qm` labels
> every compacted summary conservatively and refuses to label one that spans two personal scopes —
> because a summarizer is otherwise a laundering path: it reads at one clearance and writes an
> artifact stored at another. **We do not have that exposure today**, because
> `packages/acb_llm/acb_llm/context.py::fit_messages_to_context` *truncates* the longest message
> rather than summarizing it. If WS-9 introduces summarizing compaction, the summary needs a
> clearance label chosen by the same conservative rule — and the safe answer when the inputs span
> two private compartments is *no label*, i.e. don't persist it.
>
> One thing this exercise surfaced that the design did not: **the session memory cache would
> have undone the rule.** The assembled block is cached per thread for 10 minutes, so a thread
> cached while solo kept serving the owner's private block after somebody was invited in —
> the read path refusing to fetch what the cache was already holding. §3.5 flagged that the
> key "must gain the clearance set"; it now does, and `invalidate_session_memory` clears every
> per-clearance variant rather than the bare key.

### 7.1 The surface `subject:` presumes — design, 2026-08-02

The acceptance above is good and is **unchanged**. What it lacked was a surface: an audit on
2026-08-02 found that to make it *runnable* an implementer would have had to invent, with no
spec text, the endpoint that creates a compartment, the endpoint that adds members, the
writer for `subject_ref`, the `_authorize_scope` rule for the new shape, the meaning of
`audience='team'`, and what `sensitivity='restricted'` gates beyond prose. This section
supplies all six.

**How to read this.** Every design choice below is marked
`DECISION (agent-proposed, owner may overrule)`. Two calls are deliberately **not** made and
are named in §7.1.9 — an agent-proposed default is a starting point, not a product decision
taken on the owner's behalf. Every *done-when* is a command or an assertion, never a
judgement.

#### 7.1.0 What exists today

| Thing | State on 2026-08-02 |
|---|---|
| `scope_key()` kinds | five shapes, no `subject` — `compartments.py:97-105` |
| `scope_kind()` | returns `"unknown"` for `subject:` — `compartments.py:108-127` |
| `resolve_clearance()` | `(actor, agent_name, thread_id, shared)`; no subject parameter — `compartments.py:130` |
| `memory_compartment` / `memory_compartment_member` | **absent** — `git grep memory_compartment -- infra/postgres/` is empty |
| `chat_session.subject_ref` | **absent** — same grep |
| `_authorize_scope` | refuses unknown shapes with 404 — `routes/memory.py:167`. **So `subject:` is a 404 today, from the gate, not from a missing row.** |
| Room settings writer | `PATCH /chat/sessions/{id}/room` — `routes/rooms.py:553`, model `:81-85`, gated `access.can_manage`, SQL `_update_room` `:589` |
| Groups | `org_group` / `org_group_member` shipped (migration 138 `:40-66`); admin CRUD `routes/admin/groups.py`; membership expanded at read time by `gateway/rooms.py:163-179` |
| Permission vocabulary | closed tuples in `packages/acb_auth/acb_auth/permissions.py` — `FEATURES` `:73-101` (includes `memory`; the six `center.*` slugs were appended 2026-08-03, which is what moved both tuples down), `CAPABILITIES` `:105-133` (includes `memory:read_org` / `memory:write_org`; **no compartment permission**) |

#### 7.1.1 The scope key must be one URL path segment

> `DECISION (agent-proposed, owner may overrule)` — the subject scope key is
> **`subject:<slug>`** where `slug` matches `^[a-z0-9][a-z0-9._-]{0,63}$`. The graph entity
> reference (`deal:zoho-4471`) lives in `memory_compartment.entity_ref`, **not in the key**.

§3.1's example `scope_key(subject="deal/zoho-4471") → "subject:deal/zoho-4471"` cannot be
addressed by the API that must authorize it: `GET /memory/{scope}` is a single path segment,
so a `/` in the key produces a route miss — a 404 raised by the router **before**
`_authorize_scope` runs. Every other shape in use today (`<email>`, `prefs:<email>`,
`room:<thread_id>`, `agent:<name>`, `org:global`) is slash-free by accident; this one would
not have been. The slug grammar is deliberately **the permission-segment one**:
`permissions.py:_SEGMENT_RE` (`:138`) is `^[a-z0-9][a-z0-9._-]*$` — identical character
classes, with a `{0,63}` length bound added here so a scope key cannot grow unbounded.

> **Not `_clean_slug`, and the difference is not cosmetic.** An earlier draft claimed group
> slugs use the same grammar. They do not: `routes/admin/groups.py:_clean_slug` (`:98-109`)
> tests `c.isalnum() or c in "_-"`, so it **forbids `.`**, **permits a leading `-`/`_`**,
> **accepts unicode alphanumerics** (`isalnum()` is not ASCII-only), and caps at
> `_SLUG_MAX = 48` (`:46`) rather than 64. Anyone implementing §7.1.1 by reusing
> `_clean_slug` would get a different accepted set than the grammar above — reuse
> `_SEGMENT_RE`'s shape, not that function.

**Done when**
1. `pytest -k scope_key_is_one_path_segment` — a parametrized test over every
   `scope_key(**kwargs)` shape asserts `"/" not in key` and `key == quote(key, safe=":@.-_")`.
2. A compartment created as `subject:falcon` returns **200** to its creator on
   `GET /memory/subject:falcon`, and each of `subject:Falcon`, `subject:deal/zoho-4471`,
   `subject:-falcon`, `subject:` and a 65-character slug is **rejected at create with 400**
   rather than silently normalised.
   > **Do not use the old form of this criterion.** It read *"`GET /memory/subject:falcon`
   > reaches `_authorize_scope`; assert the 404 body is `Unknown memory scope`"* — which is
   > **already true on `main` with zero work done**: `_authorize_scope` falls through to
   > `raise _deny("Unknown memory scope", 404)` at `routes/memory.py:167` for every
   > unrecognised shape. Keep that as a *regression pin* for an unknown slug if you like; it
   > is not evidence this slice was built.
3. The slug grammar has **one** definition — a module constant beside `scope_key` in
   `packages/acb_memory/acb_memory/compartments.py` — and every surface that validates a
   subject slug either imports it or, where it must mirror it, is pinned by a **drift test**
   asserting the two are equal. The mirror case is real and deliberate: `routes/memory.py`
   duplicates the scope constants at `:53-56` precisely so *"authorization still works when
   Mem0 is not installed — a router that fails open because an optional dependency is missing
   is not a gate"* (its own comment, `:50-52`). Copy that pattern, not a silent third copy;
   retyping the pattern in the test file does not count as the assertion.
   > §3.1's table was corrected to the slug form on 2026-08-02. That is **done**, not
   > acceptance — a criterion satisfied before the work starts cannot gate the work.

#### 7.1.2 `scope_key` / `scope_kind` / `resolve_clearance`

> `DECISION (agent-proposed, owner may overrule)` — `resolve_clearance` gains
> **`subjects: tuple[str, ...] = ()`**, and the caller passes **already-intersected**
> compartments: only those every viewer may read. The intersection is computed in the
> gateway, not here.

`compartments.py`'s docstring (`:14-18`) states the constraint this respects: the module is
*"deliberately dependency-free… the caller resolves `shared`… Keeping the direction that way
is what lets `acb_memory` stay below `acb_auth` and the gateway in the import graph."* A
`subjects` parameter honours that; a membership lookup inside `acb_memory` would invert it.

**Done when**
1. `scope_key(subject="falcon") == "subject:falcon"` and `scope_kind("subject:falcon") == "subject"`;
   `scope_kind("subject:")` is `"unknown"` (matching the existing empty-suffix rule at
   `compartments.py:119-124`).
2. `resolve_clearance(actor="a@b.c", agent_name="sales", thread_id="t", shared=True,
   subjects=("subject:falcon",)).read` == `("room:t", "subject:falcon", "prefs:a@b.c",
   "agent:sales", "org:global")` — order asserted exactly, because `Clearance.read` is
   documented as byte-stable for prompt caching (`compartments.py:44-48`).
3. `Clearance.fingerprint` differs between the same room with and without the subject —
   otherwise the session memory cache serves a block assembled at the wider clearance, the
   failure §3.5 already caught once.
4. The existing `tests/unit/test_memory_compartments.py` still passes **unchanged**: solo
   and subject-free shared runs must be byte-identical.

#### 7.1.3 Create-compartment and add-member endpoints

> `DECISION (agent-proposed, owner may overrule)` — subject compartments are **room-shaped,
> not admin-shaped**. They introduce **no new permission string**. Reaching the endpoints
> needs `feature:memory` (which already exists); everything else is decided by a
> compartment-scoped membership row, exactly as a room's capacity comes from
> `chat_session_participant` and not from a permission.

The two precedents in the tree point in opposite directions, and choosing between them is
the decision: `routes/admin/groups.py` gates on `admin:members:manage` because a group is an
org-administered object; `routes/rooms.py` gates on `resolve_room_access(...).can_manage`
because a room is a working object its participants own. A subject compartment — "everything
about the Falcon deal", created by the person working the deal — is the second kind. Gating
it on an admin permission would mean a CEO cannot compartmentalise their own deal without an
administrator, which inverts the confidentiality story the whole document is built on.

**Placement.** These routes extend the **existing** `gateway/routes/memory.py` router
(prefix `/memory`). Not a new router: `_authorize_scope` is the one place the scope
vocabulary fails closed (`:128-167`), and a second router would be a second place for a new
shape to be forgotten. Root `AGENTS.md` "Place before building" — extend the owning seam.

```
POST   /memory/compartments                        create   {kind:'subject', slug, label,
                                                             entity_ref?, sensitivity}
GET    /memory/compartments                        list — only what you may read (§7.1.7)
PATCH  /memory/compartments/{scope}                label / sensitivity
POST   /memory/compartments/{scope}/members        add      {subject, can_promote}
DELETE /memory/compartments/{scope}/members/{subject}       remove, or leave (subject == you)
```

| Route | Gate |
|---|---|
| all five | `require_feature("memory")` — `acb_auth.deps:383`. **Deliberately per-route, not `require_feature_router`**: the five shipped memory routes have no feature gate today (`memory.py:45-48` carries only `require_internal_auth`), and retro-fitting one is a behaviour change to shipped endpoints that does not belong in this slice. It is listed as deferred work in §7.1.9. |
| `POST /compartments` | `feature:memory` only. The creator is inserted as a member with `can_promote = true` — the same shape as migration 138's backfill giving every session's creator the `owner` row. |
| `PATCH`, `POST /members`, `DELETE /members/{other}` | member row with `can_promote = true` — §3.4's "widening beyond the session audience is a human action by someone with `can_promote`". |
| `DELETE /members/{self}` | any member may leave, mirroring `remove_participant` (`rooms.py:497-499`). |

> **Permission-name honesty.** `feature:memory` is real (`permissions.py:68`, inside the
> `FEATURES` tuple). `memory:read_org`
> / `memory:write_org` are real (`:110-111`). **There is no compartment permission in the
> product and this design proposes none.** If the owner wants an org-level lever on *who may
> create compartments at all*, it is a new entry in the `CAPABILITIES` tuple (suggested name:
> `memory:compartments:create`) — and it must actually be added to that tuple, because
> `CAPABILITIES` is what the admin UI offers and what makes a typo catchable
> (`permissions.py:84-85`). A permission string invented in a route and never added there is
> grantable only by hand-editing a role, which is how a gate becomes decorative.

**Done when**
1. Creator can read and write their compartment; a second signed-in **non-member** gets 403
   (or 404 if restricted, §7.1.7) on every one of the five `/memory/{scope}` routes. *Read
   "non-member" literally — §7.1.5's table allows a **member** both read and write, and a
   gate built to this criterion as "member → 403" would lock people out of the compartment
   they were added to.*
2. `POST /compartments` twice with the same slug → 409, message names the slug (mirrors
   `create_group`, `groups.py:217-220`).
3. A member without `can_promote` calling `POST /members` → 403.
4. A caller lacking `feature:memory` → 403 on all five routes, and the 403 body names the
   permission (`require_permission`'s documented contract, `deps.py:360-363`).
5. `DELETE /members/{self}` succeeds for an ordinary member; the last `can_promote` member
   cannot leave — a compartment nobody may administer is the same defect
   `_remove_participant` refuses for rooms. **Match that precedent's status exactly: it is
   `400`, not `409`** (`routes/rooms.py:533-538` — *"The last owner cannot leave a room"*).
   An earlier draft of this criterion said 409; the point of citing a precedent is to copy
   it, so the precedent wins. (Done-when 2's 409 is a different precedent —
   `create_group`, `groups.py:217-220` — and is correct as written.)

#### 7.1.4 The writer for `subject_ref`

The recommendation put to this pass was to fold the writer into the existing
`PATCH /sessions/{id}/room` rather than add an endpoint. **Evaluated against the actual
route, and adopted.**

> `DECISION (agent-proposed, owner may overrule)` — `RoomPatch` gains
> `subject_ref: str | None`, validated in `patch_room` and written by `_update_room`. **No
> new endpoint.** `subject_ref = ""` clears the binding.

Why it fits, checked against `routes/rooms.py`:

- **Authority is already right.** `patch_room` gates on `access.can_manage` (`:563`), which
  is owner-only (`gateway/rooms.py:_capabilities` `:111-112`). §4.2 says binding is a consent
  act because it changes *write* targets; owner-only is exactly that authority.
- **The disclosure already exists.** `patch_room` publishes `ROOM_SETTINGS_CHANGED` with the
  changed keys (`:583-585`), which is the room-visible confirmation §4.2 asks for.
- **The SQL writer needs no new shape.** `_update_room` (`:589-599`) builds
  `UPDATE chat_session SET {sets}` from column names drawn from closed vocabularies and
  binds the values as parameters. `subject_ref` joins that closed set; only the *value*
  needs validating, and it validates in `patch_room` beside the other three
  (`:567-578`) — the same place, the same shape.
- **§4.2 of the room RFC forbids the alternative.** "Do not introduce a new document
  abstraction… the thread is the room." A `POST /sessions/{id}/subject` would duplicate the
  access resolution, the 404/403 branches and the event publish for one column.

**One constraint the recommendation did not name, and it is load-bearing.** The value must
be a compartment **the caller is a member of** (else 403; 404 if unknown or restricted).
Otherwise an owner could bind their room to a compartment they cannot read, and the very
next turn's extraction would write the room's facts into it — publishing into a compartment
by naming it. And, symmetrically: **binding governs writes only.** Reads stay governed by
the intersection of §3.3, so binding a room some of whose participants are not members does
*not* leak the subject into the transcript — it just means the agent will not use it there.
That is confusing unless it is said, so:

> `DECISION (agent-proposed, owner may overrule)` — `PATCH /sessions/{id}/room` returns a
> **clearance cap** alongside `changed`: which participants are not members of the bound
> subject, and therefore why the agent will not read it here. The room already tells you what
> it lost when you shared it; now it tells you the same about memory. Same *idea* as the
> credential cap — **deliberately not the same code.**

> **Do not reuse `_capability_cap` (`routes/rooms.py:191-244`), and this is the one line to
> read twice.** It is a **display cap that under-reports by design**: it keeps only email
> subjects (`emails = [m for m in members if "@" in m]`, `:207`) and short-circuits to an
> empty cap when fewer than two survive (`:208-212`) — its own comment says group and `org`
> subjects are skipped *"because the authority fold expands them elsewhere"* (`:209-211`).
> Under-reporting a lost Zoho credential is a cosmetic miss. Under-reporting a **participant**
> here turns the intersection into a **union**: a room whose participants are
> `[owner@example.com (a compartment member), group:sales]` returns an empty cap, which reads
> as "no non-member participants", which admits `subject:falcon` to `Clearance.read` and
> renders a restricted compartment's facts into a transcript forty people can read.

**The intersection, and where it is computed.** Every other surface in §7.1 names a file and a
line; the security-critical computation gets the same treatment. One helper, one file, beside
the cap it must not be confused with:

```python
# apps/services/gateway/gateway/routes/rooms.py — beside _capability_cap (:191)
async def _subject_clearance_cap(session_id: str, subject_scope: str) -> dict[str, Any]:
    """Which of this room's people are NOT cleared for *subject_scope*.

    → {"subject": str, "readable": bool, "degraded": bool,
       "capped": list[str],   # the subjects AS THE OWNER NAMED THEM
       "cappedPeople": int}   # how many real people that expands to

    ``readable`` is True only when EVERY EXPANDED participant is a compartment
    member. ``degraded`` means the expansion did not complete, and then
    ``readable`` is False — never the other way round.
    """
```

> **Decide over expanded people; *report* the subject the owner named.** `capped` carries
> `"group:sales"`, never the forty emails behind it — `_capability_cap`'s own comment
> (`routes/rooms.py:209-211`) makes the point that naming a cause inside a group *"would mean
> naming a person the room's owner never added"*, and that reasoning is right even though its
> conclusion (skip the subject entirely) is not. `cappedPeople` is the expanded **count**, which
> is what makes the expansion observable in a test without turning the cap into a group-roster
> disclosure. The bug being fixed is that `_capability_cap` skips the *decision*; keeping its
> discretion about the *display* costs nothing.

Three steps, each naming the code it reuses:

1. **Expand the room's participants — before the intersection, never after.**
   `chat_session_participant.subject` holds **raw, unexpanded** subjects by design:
   `gateway/rooms.py::_expand_members` (`:201-218`) says so in its own docstring and
   deliberately does not expand, because *"a group subject counts as one other party, which is
   exactly what it is from the transcript's point of view."* That is correct for the
   shared/solo decision and **wrong here**. §7.1.6's read-time group join
   (`gateway/rooms.py:163-179`) expands compartment **member** rows — it says nothing about
   room **participants**, and conflating the two is what produced this defect.
   The expansion that is right already exists, and it is the authority fold's:
   `packages/acb_auth/acb_auth/access.py::resolve_session_access` (`:343-434`) turns
   `group:<slug>` into the group's active members (`_GROUP_MEMBER_SQL`, `:330-336`) and `org`
   into every active member (`_ORG_MEMBER_SQL`, `:338-340`), bounded by
   `_MAX_PARTICIPANT_EXPANSION = 200` (`:324`, tripped at `:412-416`).
   **Factor that loop out** as `acb_auth.access.expand_session_subjects(session_id) ->
   list[str] | None` and have `resolve_session_access` call it, so there is exactly one
   expansion in the tree. Do not re-implement it in `routes/rooms.py`: a second expansion is a
   second place for `org` to be forgotten.
2. **Intersect.** The subject survives iff **every** email from step 1 is a member of the
   compartment. Compartment membership is itself subject-shaped (§7.1.6:
   `email` | `group:<slug>` | `org`), so the member side expands too — `gateway/rooms.py:163-179`
   for the group half, and an `org` member row means every active member is a member. Two
   expanded sets, one subset test.
3. **Fail closed.** If step 1 returns `None`, the subject is **dropped** from `Clearance.read`
   and the cap returns `{"readable": false, "degraded": true}`. Say this out loud, because the
   shipped code it reuses does the **opposite on purpose**: `resolve_session_access`
   **fails open to actor-only** on exactly these errors (`:417-426`) — *"the sharing feature
   must not become a new way for a solo run to lose its authority."* That is right for a
   *capability*, where a false deny blocks real work and a false allow only restores yesterday.
   It is wrong for a *confidentiality* decision, where a false allow **is** the disclosure. One
   expansion, two callers, two deliberately opposite postures. `expand_session_subjects`
   returning `None` rather than a partial list is what makes that expressible at all — a
   partial list is indistinguishable from a complete one at the call site.

**Where the intersection is consumed.** `apps/services/gateway/gateway/routes/agent.py:1768-1774`
is the **only** `resolve_clearance` call site in the tree; it gains `subjects=` — the tuple
from step 2, empty whenever step 3 fired. The cap block on `PATCH /sessions/{id}/room` is a
*rendering* of that same computation, not a second one: both go through
`_subject_clearance_cap`. Two implementations of one confidentiality rule is how they drift.

> **A latent defect in the join being reused — one line, before it is copied into a
> confidentiality gate.** `gateway/rooms.py:163-179` and `_GROUP_MEMBER_SQL`
> (`access.py:330-336`) both match `org_group` **by slug alone**, while `org_group` is
> `UNIQUE (organization_id, slug)` (migration 138 `:40-66`). It is latent only because
> `get_org_id` resolves a single deployment org (`routes/admin/_common.py:96-112`, keyed on
> `DEFAULT_ORG_SLUG`). Provision a second organization and an identically-slugged group in it
> expands into this one's clearance. Filter on `organization_id` in the copy.
> *[2026-08-09: under D15 this is a live multi-tenancy gap, not a latent one — the fix rides
> WS-29 (MT-1b/MT-1g for keys; the `slug='default'` resolution is implementation-spec trap 5).
> Do not copy this pattern into new code — R5.]*

**Done when**
1. Owner binds to a compartment they belong to → 200; `chat_session.subject_ref` is set;
   a `ROOM_SETTINGS_CHANGED` event carries `subject_ref` in `settings`.
2. Owner binds to a compartment they do **not** belong to → 403 (404 if restricted), and
   `chat_session.subject_ref` is unchanged.
3. A `member` (not owner) attempting the bind → 403 with `access.denied(...)`'s wording.
4. **The cap is correct for `group:` and `org` participants.** Five assertions, and **an
   implementer may not satisfy this criterion with email participants alone** — that is the
   defect it exists to catch. In every part, assert on `resolve_clearance`'s output, never on
   the answer text.
   - **a — group, capped.** Participants `[owner@example.com, group:sales]` with three members
     in `group:sales`, owner is a compartment member, no `group:sales` member is. Bind → 200;
     the response carries `readable: false`, `capped == ["group:sales"]` and
     **`cappedPeople == 3`**; the next run's `Clearance.read` **does not** contain the subject
     scope. *The count is the assertion that fails against an unexpanded implementation — a cap
     that echoes the subject without expanding cannot produce it.*
   - **b — group, cleared.** Same room after `POST /members {subject: "group:sales"}` → the cap
     is **empty**, `cappedPeople == 0`, and `Clearance.read` **does** contain the subject scope.
     *(a) and (b) are one criterion: (a) alone still passes against an implementation whose cap
     is always non-empty, and (b) alone against one whose cap is always empty — which is
     precisely the `_capability_cap` shortcut.*
   - **c — group, partially cleared.** Two of the three `group:sales` members added to the
     compartment individually → **still capped**, `cappedPeople == 1`, subject absent from
     `Clearance.read`. Intersection, not "the group is mostly in". This part is unsatisfiable
     without a real expansion on **both** sides.
   - **d — `org`.** Participant `org` with one active `app_user` who is not a compartment
     member → capped, subject absent from `Clearance.read`.
   - **e — expansion fails closed.** Force `expand_session_subjects` to return `None` (patch it,
     or point it at a session past `_MAX_PARTICIPANT_EXPANSION`) → the response carries
     `degraded: true`, `readable: false`, and the subject scope is **absent** from
     `Clearance.read`. A degraded expansion that still admits the subject is a failing test,
     not a warning.
   > **Why this criterion is shaped this way.** It previously read *"Binding a room with a
   > non-member participant → 200, and the response's clearance cap names that participant"* —
   > which any implementer tests with two email addresses, and which therefore passes green
   > against a cap that silently returns `[]` for every `group:`/`org` room. Those are exactly
   > the rooms where the leak is forty people wide. A criterion that cannot fail on the case it
   > is protecting is not a criterion.
5. `subject_ref = ""` clears it; the next run writes to `room:<tid>` again.
6. `PATCH` with an unknown key is **accepted and ignored**, and the test says so. `RoomPatch`
   (`routes/rooms.py:81-84`) is a plain `BaseModel` with no `model_config`, and there is no
   `extra="forbid"` anywhere under `apps/services/gateway`; pydantic's default is
   `extra='ignore'`. Verified against this repo's interpreter (pydantic 2.13.4):
   `RoomPatch(visibility='org', bogus_key=1)` constructs, and `patch_room` (`:566-586`) reads
   only its three known fields, so the unknown key is inert. Pin *that*, so the next reader
   does not "fix" a 422 that was never there.
   > **Closing the model is its own ticket, not this slice.** `model_config =
   > ConfigDict(extra="forbid")` on a live endpoint can break an existing client that sends a
   > harmless extra field, which is the class of change §7.1.9 rules out here. It is filed
   > there with the other deferred item. An earlier draft of this criterion read *"`PATCH` with
   > an unknown key **still** 422s — `RoomPatch` stays a closed model"*; the word "still"
   > asserted current behaviour that does not exist, and would have invited exactly the
   > smuggled behaviour change.

#### 7.1.5 The `_authorize_scope` rule for a `subject:` shape

> `DECISION (agent-proposed, owner may overrule)` — three edits to
> `apps/services/gateway/gateway/routes/memory.py`, mirroring `_authorize_room` exactly.

1. `SUBJECT_SCOPE_PREFIX = "subject:"` beside the other three mirrored constants (`:53-56`).
2. **Add `subject:` to the `personal` tuple at `:143-147`.** A subject compartment names a
   membership, so a service principal holding `*` must assert an identity to reach it. That
   line is the fix for the original hole (`lib/memory.ts` omitted the header and got god
   mode); a new membership-shaped scope that is not listed there inherits the hole.
3. `_authorize_subject(scope, user, *, write)` dispatched before the terminal
   `raise _deny("Unknown memory scope", 404)` at `:167`.

| Case | Result |
|---|---|
| No such compartment | **404** `"Unknown memory scope"` |
| Member, read | allow |
| Member, write (add / delete a memory) | allow |
| Non-member, `sensitivity='normal'` | **403** `"Forbidden: that is another subject's memory."` |
| Non-member, `sensitivity='restricted'` | **404**, byte-identical to "no such compartment" (§7.1.7) |
| Service principal with `*` and no asserted identity | **403**, logged `memory.service_call_without_identity` |

> **Widening is deliberately not a row here.** *Add a member* and *`PATCH` sensitivity* are
> gated by a member row with `can_promote`, but they are **§7.1.3's endpoints, not
> `_authorize_scope` outcomes** — `_authorize_scope` never sees them. An earlier draft
> listed widening in this table, which made its done-when uncountable. Its assertions live
> in §7.1.3 done-when 3.

`delete` keeps its second check unchanged (`memory.py:232-241`): authorize the scope, then
confirm the memory is *in* it, 404 rather than 403 on a miss. Nothing about subjects relaxes
that.

**Done when** — **six assertions, one per row of the table above** (they are now all and
only `_authorize_scope` outcomes, so the count and the table cannot drift apart), plus: the
existing five shapes' behaviour is unchanged, evidenced by
`uv run python -m pytest tests/unit/test_memory_authorization.py -q` staying green
**unmodified** (17 passed in 0.41s on 2026-08-02). Naming the file is the point: a green run
on those is part of this slice's evidence, not an afterthought.

#### 7.1.6 `audience='team'` maps onto the shipped `org_group`

> `DECISION (agent-proposed, owner may overrule)` — `memory_compartment_member` holds a
> **`subject`**, not a `user_email`, and its vocabulary is the `app_grants` one already used
> everywhere else: `email` | `group:<slug>` | `org`. **`audience` is derived from the member
> rows, not stored as a second source of truth.** `team` means *at least one `group:` subject
> and no `org` subject*.

**The full derivation, so no value in §3.2's `CHECK` is left without a rule.** Read the member
rows and pick the first that matches, top down: any `org` subject → **`org`**; else any
`group:` subject → **`team`**; else more than one email subject → **`explicit`**; else exactly
one email subject → **`private`**. **`room` is not derivable for a subject compartment and is
never assigned to one** — it belongs to the `room:<thread_id>` kind, whose audience is the
participant list rather than a member table. Say it rather than leaving a `CHECK` value with no
producer: an unreachable enum value reads as an unimplemented branch to whoever comes next.
This is a **label only**. None of the five is ever an access input — access is the member rows,
per the rejected alternative below — so a wrong label is a display bug, not a disclosure.

`audience='team'` was written into §3.2 before groups existed. They exist now: `org_group` /
`org_group_member` shipped in migration 138 (`:40-66`), the same migration comment records
that group subjects *"expand at READ time, so leaving a group removes you from its rooms
with no fan-out write"* (`:23-25`), and `gateway/rooms.py:163-179` is the exact join to
reuse. `routes/rooms.py:_valid_subject` (`:100-111`) is the exact validator to reuse — its
own docstring says one vocabulary across sharing surfaces is the point.

**Rejected alternative:** keep `audience` as a stored `CHECK`ed column and add an
`audience_ref` naming the group. Two places that can disagree about who may read a
restricted compartment — and the disagreement is a disclosure, not a display bug. §3.2's
`audience` column survives only as a denormalised label for the inspector, recomputed on
write, never consulted for an access decision.

**Done when**
1. `POST /members {subject: "group:sales"}` → a member of `group:sales` reads
   `subject:acme` (200); a non-member gets 403.
2. Remove that person from the group via `DELETE /admin/groups/sales/members/{email}` →
   their **next** read is 403, and `SELECT count(*) FROM memory_compartment_member` is
   unchanged. Expansion at read time, no fan-out write.
3. `GET /memory/compartments` reports `audience: "team"` for that compartment and
   `"explicit"` once the group subject is replaced by two email subjects — derived, asserted
   against the member rows rather than a stored column.
4. `POST /members {subject: "org"}` → `audience: "org"`; `_valid_subject` rejects
   `"group:"` and `"notanemail"` with 400.

#### 7.1.7 One testable consequence for `sensitivity='restricted'`

§3.2 gives `restricted` three behaviours in prose — no autocomplete for non-members, no
cross-compartment search, blocked mid-run joins. Prose is what failed the audit. Exactly one
of the three has a surface that exists today, so exactly one becomes a done-when:

> `DECISION (agent-proposed, owner may overrule)` — **`restricted` means the compartment's
> existence is confidential.** A refusal to a non-member must be indistinguishable from
> "there is no such compartment".

Concretely:

- `GET /memory/compartments` **omits** restricted compartments the caller is not a member
  of. Not greyed out, not returned with a null label — absent.
- Every `/memory/subject:<slug>` route returns **404** to a non-member when the compartment
  is restricted, and **403** when it is normal. This is the difference that matters: a 403
  confirms the compartment exists, and §4.4 exists precisely because *"I have information
  about Project Falcon I can't use here"* leaks Falcon. A 403 in the network tab says the
  same sentence.
- The 404 body is byte-identical to the one for a slug that was never created.

**Done when**
1. A creates `subject:falcon` with `sensitivity='restricted'`. As B: `GET /memory/compartments`
   does not contain `subject:falcon`.
2. As B: `GET /memory/subject:falcon` → 404, and
   `resp.json() == client.get("/memory/subject:never-existed").json()`.
3. Same compartment set to `sensitivity='normal'` → as B the same call is **403**, and the
   compartment now appears in the listing (with its label, without its facts).
4. As A (a member) both calls are 200 in both sensitivities — `restricted` changes what
   *others* can learn, never what members can do.

**Deliberately not in this slice, and why.** *"Never surface in a cross-compartment search"*
has no surface to change: there is no cross-compartment search endpoint — every route takes
one scope. *"Block mid-run joins"* depends on §5.4's run-boundary join queue, which is
unbuilt; inventing it here would be the scope creep this design is meant to prevent. Both
stay prose, marked as prose, until they have a surface — and neither is an acceptance
criterion for this slice.

#### 7.1.8 The migration

Two tables and one column, per §3.2, amended in **three** places — build this list, not the
raw §3.2 block:

1. **§7.1.1** — `memory_compartment.scope_key` is slug-shaped (`subject:<slug>`,
   `^[a-z0-9][a-z0-9._-]{0,63}$`); the graph entity reference stays in `entity_ref`.
2. **§7.1.6** — `memory_compartment_member.user_email` becomes **`subject`** carrying the
   `app_grants` vocabulary (`email` | `group:<slug>` | `org`); the primary key and the index
   follow it; `memory_compartment.audience` survives only as a denormalised label recomputed
   on write and **never** consulted for an access decision.
3. **§7.1.4** — `chat_session.subject_ref` holds a **compartment scope key**
   (`subject:falcon`), not an entity reference. §3.2's original `-- 'deal:zoho-4471'` comment
   said otherwise and is corrected there; this is the reading to build.

> **R1 — the number is assigned at build time.** Do not write an absolute future migration
> number into this or any document. Find the next free one when you build:
> ```bash
> ls infra/postgres/*.sql | sed 's#.*/##' | sort -n | tail -1
> ```
> and use the next integer. §3.2 already carries this phrasing; keep it.

#### 7.1.9 What this slice does **not** decide

Three things are named rather than answered, because answering them is a product call this
design has no standing to make:

1. **Compartment sprawl / auto-create on binding** (§8 Q2). Whether *"this is about Falcon"*
   in the composer auto-creates a compartment or requires an explicit create is a product
   decision about how much ceremony confidentiality should carry. It is 3b work either way
   and does not block this slice — **the owner should answer it before 3b is dispatched.**
2. **An audit trail on every restricted read** (§8 Q5). Cheap via `acb_audit`, and the
   document itself says *"worth deciding early"*. It is not decided; this slice does not add
   one, and does not foreclose one.
3. **Whether creating a compartment should need an org permission at all.** §7.1.3 chose
   "any member with `feature:memory`", mirroring rooms. If the owner wants a lever, it is a
   new `CAPABILITIES` entry and a one-line dependency — but it is their call, not a default
   to be adopted quietly.

**Deferred, adjacent, and deliberately not done here** — three items, each a behaviour change
to something already live, each owed its own change and its own test:

1. The five shipped `/memory` routes carry no `feature:memory` gate (`memory.py:45-48`).
2. **`RoomPatch` is not a closed model** (`routes/rooms.py:81-84`: plain `BaseModel`, no
   `model_config`; no `extra="forbid"` anywhere under `apps/services/gateway`), so
   `PATCH /sessions/{id}/room` silently ignores unknown keys instead of 422-ing. Closing it is
   worth doing — a typo'd `floor_mode` currently succeeds with no effect — but it can break a
   client that sends an extra field, so it is a ticket of its own, not a line in this slice.
   §7.1.4 done-when 6 pins the *current* behaviour so the gap stays visible meanwhile.
3. **A compartment is immortal, and this slice keeps it that way.** §7.1.3 ships no
   `DELETE /memory/compartments/{scope}`, and its done-when 5 forbids the last `can_promote`
   member from leaving — so a compartment can be emptied of ordinary members but never removed,
   and always retains one administrator. That is the right trade for a confidentiality object
   whose facts outlive any one membership, and it is stated here rather than left implicit so
   nobody reads the missing endpoint as an oversight. Deletion needs an answer to "what happens
   to the Mem0 rows partitioned under this scope key" — which is a data-lifecycle decision, not
   a route.

#### 7.1.10 Verification for this slice

```bash
uv run python -m pytest tests/unit/test_memory_compartments.py -q      # must stay green, unchanged
uv run python -m pytest tests/unit/test_memory_subject_compartments.py -q   # the new file
uv run python -m pytest tests/unit/test_rooms.py -q                    # needs Postgres — see §9
uv run ruff check . --select F821,F601,F602,F502,F7,B006               # the blocking gate (§9.2)
```

**Before writing the cap, re-derive the trap §7.1.4 exists to close** — it is two greps:

```bash
# 1. _capability_cap drops every non-email subject and short-circuits. THIS is why it
#    must not be reused for a confidentiality decision.
git grep -n -A6 'emails = \[m for m in members' -- \
    apps/services/gateway/gateway/routes/rooms.py
# → :207 the filter · :208-212 the empty-cap short-circuit · :209-211 the comment
#   saying group/`org` subjects are skipped "because the authority fold expands them
#   elsewhere" — true for a display cap, fatal for an access decision.

# 2. The expansion that must be reused instead, and its deliberate fail-OPEN, which
#    this slice's caller must invert.
git grep -nE '_GROUP_MEMBER_SQL|_ORG_MEMBER_SQL|_MAX_PARTICIPANT_EXPANSION|return actor_access, sorted' \
    -- packages/acb_auth/acb_auth/access.py
# → nine lines: :324 the 200-subject bound · :330/:338 the two SQL constants and
#   :401/:406 their uses · :412/:415 the bound tripping · :426 the fail-OPEN return
#   inside `except` · :429 the unrelated single-member early return.  It is :426
#   that matters: your caller must fail CLOSED on that same error (§7.1.4 step 3).
```

The load-bearing assertion is the one §7 already states and this slice does not restate:
`search()` is **never called** with a scope key outside the clearance. Assert it the way
`tests/unit/test_memory_compartments.py:181`
(`test_a_room_never_issues_a_search_for_a_private_compartment`) does — record the calls to a fake client and
compare the list exactly, not as a subset. *"An extra scope here would be an extra
disclosure."*

---

## 8. Open questions

**These are questions, not work.** None carries acceptance; an agent that finds itself
implementing one has left its slice. Each is labelled with who must answer it.

1. **Backfilling the `prefs` / `user` split.** — **OWNER-GATE (the apply half).** Existing
   personal memories are one undifferentiated bucket. A classifier pass can split them, but
   the safe default on uncertainty is `user:` (private) — which means the agent will seem to
   forget some preferences until they are re-learned. Acceptable, but it should be a
   deliberate, communicated choice.
   > **Split for dispatch (2026-08-02).** A **classifier that produces a dry-run report** —
   > "N memories, M would move to `prefs:`, here they are" — writes nothing and is
   > **AGENT-SAFE**. **Applying** it mutates live Mem0 data and is **OWNER-GATE**
   > (`work_plan.md` §6, "live-DB one-offs"), registered there by name. An agent must stop at
   > the report. Note the current state that makes this a real backlog rather than a
   > nice-to-have: verified 2026-08-02, **nothing writes a `prefs:` key anywhere** —
   > `resolve_clearance` writes the bare actor when solo and `room:<tid>` when shared — so
   > `prefs:` is read-only and permanently empty until either this backfill runs or a write
   > path is added.
2. **Compartment sprawl.** — **OWNER (product call).** One per deal is a lot of compartments.
   Auto-create on binding, and let unbound subjects share a per-agent default until someone
   restricts them? Blocks **3b**, not the §7.1 slice; see §7.1.9 item 1.
3. **Cross-compartment questions.** — **OWNER (product call).** "Which of my deals are at
   risk?" legitimately spans compartments the *asker* is cleared for but no single room is.
   Probably answerable only in a solo session or a private lane — worth confirming that is
   acceptable rather than surprising.
4. **Graphiti's bi-temporal edges** cross entities by design; `group_id` partitions episodes,
   but a traversal could still bridge two compartments. Needs its own pass before Graphiti
   memory is trusted with restricted subjects. — **AGENT-SAFE to investigate, but latent**:
   `GRAPHITI_ENABLED` is false, and flipping it is itself an **OWNER-GATE** (`work_plan.md`
   §6). Do not flip it to test.
5. **Does `sensitivity='restricted'` need to imply an audit trail on every read?** For a
   Falcon-class compartment, "who asked the agent about this, and when" is probably a thing
   the owner wants. Cheap to add via `acb_audit`; worth deciding early. — **OWNER (product
   call).** §7.1 neither adds one nor forecloses one; see §7.1.9 item 2.

---

## 9. Verification

Exact commands for everything this document claims. Real output as of **2026-08-02**, tree
at `b5a218bd` plus this doc pass.

### 9.1 Tests

```bash
uv run python -m pytest tests/unit/test_memory_compartments.py -q
# → 24 passed in 0.35s    (the clearance rule, asserted against recorded search() calls)

uv run python -m pytest tests/unit/test_memory_compartments.py tests/unit/test_rooms.py \
                        tests/unit/test_steer_routing.py tests/unit/test_supersede_guard.py -q
# → 61 passed, 14 skipped in 21.39s
```

> **⚠️ The 14 skips are the whole of `tests/unit/test_rooms.py`, and they are silent.**
> That file needs a reachable Postgres carrying **migrations 138 + 139** — `_db_ready()`
> probes `chat_session_participant.subject`, `chat_session_agent`, and
> `chat_message.author_kind`. Without a database the room membership suite does not fail, it
> **disappears**, and a green run proves nothing about `resolve_room_access` or
> `_authorize_room`. Since `_authorize_room` is the precedent §7.1.5 copies, a change to
> `_authorize_subject` is not verified until that file reports **14 passed**.
>
> `tests/unit/test_memory_compartments.py` is a pure-function + fake-client suite and runs
> anywhere, including with `MEM0_ENABLED=false`.

> **⚠️ Never run `tests/unit/` as a directory on a box with a live `.env`.** It hangs against
> the live database (`tests/unit/test_memory_integration.py` measured exit 124); assume
> `test_memory_e2e.py` does too. `test_owner_bootstrap.py` must never reach prod
> (`work_plan.md` §6). Name test files.

### 9.2 Lint

```bash
# The BLOCKING gate (pr-check.yml:51). This is the bar.
uv run ruff check . --select F821,F601,F602,F502,F7,B006
# → All checks passed!

# The full check is a non-blocking REPORT — 1,970 pre-existing style findings on main
# as of 2026-08-02. A change is "clean" when the count does not grow, not when it is zero.
uv run ruff check .
# → Found 1970 errors.
```

### 9.3 Anchors — is this document still true?

```bash
# §3.5: one definition of the vocabulary; mem0_client re-exports it
git grep -n "scope_key\|scope_kind" -- packages/acb_memory/acb_memory/compartments.py \
                                       packages/acb_memory/acb_memory/mem0_client.py

# §2.1 + §7.1.5: the gate, and the five routes it guards
git grep -n "_authorize_scope\|@router\." -- apps/services/gateway/gateway/routes/memory.py
# → gate at :128; routes at :187 :200 :214 :244 :266

# §7 remainder: subject compartments and the registry are genuinely absent
git grep -n "subject" -- packages/acb_memory/acb_memory/compartments.py
# → exactly one hit, the docstring at :23 saying they are not built
git grep -nE "memory_compartment|subject_ref" -- infra/postgres/ \
    || echo "registry unbuilt — expected"
# → registry unbuilt — expected

# §8 Q1: nothing writes a prefs: key
git grep -n "PREFS_SCOPE_PREFIX\|prefs=True" -- packages apps
# → definition, scope_key(), scope_kind(), resolve_clearance()'s READ set, and the API
#   authorizer. No write path.

# R1: the next free migration number, at build time only
ls infra/postgres/*.sql | sed 's#.*/##' | sort -n | tail -1
```

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-10 — **Multiplayer remainder** — S1 `subject:` compartments · floor-control re-decision · `prefs`/`user` backfill

**State cell (as of the move):** 🟡 Docs → S1

**Narrative (verbatim):** **Steer is SHIPPED — struck from this row's title** (`15c8933f`, ancestor of `main`: `orchestrator/steer.py::route_turn` → DROP/ENGAGE/ABORT/STEER, durable `cc:steer:` signals, `202 {"steered": true}` stand-down, `409 steer_outside_run_floor`, plus the two-layer supersede guard; `tests/unit/test_steer_routing.py` + `test_supersede_guard.py` green). **Audited 2026-08-01 → NO-GO on 5 of 7 contract points; §5-style remediation applied 2026-08-02** (both docs re-headered "verified against code on 2026-08-02", §3.5's 5 stale anchors fixed, gate labels added, verification blocks added). **That remediation was then independently verified and returned FAIL; repair round 1 landed the same day.** The P0 was the remediation's own new claim that `mark_active(reset=True)` raises `SupersedeRefused` — **it does not**: `mark_active` (`stream_relay.py:343-405`) deletes the stream at `:377` with no ownership check, and the only `raise` is at `:895` inside **`run_detached`** (`:823`), before it calls `mark_active` at `:909`. So the guard covers `run_detached`'s callers, **not** the destructive statement; both docs now say so, and README §12.3 carries an anchor grep that shows the line ordering. Six smaller defects fixed with it: `feature:memory` is `permissions.py:68` (not `:70`); §7.1.3 dw1 said "member" where §7.1.5 allows members (now **non-member**); dw5's `409` now matches its own precedent's **400** (`routes/rooms.py:533-538`); the slug grammar no longer claims `_clean_slug` (which forbids `.`, allows a leading `-`/`_` and unicode alnum) — it is `_SEGMENT_RE`'s shape plus a 64-char bound; `subject_ref` now reads as a **compartment scope key** everywhere (§3.2/§3.4/§4.1/§7.1.8), not an entity ref; and three already-green done-whens (§7.1.1 dw2/dw3, §7.1.5's miscounted row) were **replaced with criteria that require the work**, not merely labelled. Residual recorded, not built: moving the ownership check into `mark_active` would make it an invariant over the statement — no ticket minted for it here. **The row is now three things, and only one is work:** ① **`subject:` compartments = WS-10 S1, the dispatchable slice.** It is the one item with real query-layer acceptance (`memory-clearance.md` §7, kept verbatim) — it was NO-GO only because the surface it presumes was unspecified. **`memory-clearance.md` §7.1 now specifies it** (create/add-member endpoints and their gating, the `subject_ref` writer folded into the existing `PATCH /sessions/{id}/room`, the `_authorize_scope` rule, `audience='team'` → the shipped `org_group`, and a testable `sensitivity='restricted'` = *existence is confidential*, 404-not-403). Every decision there is marked `DECISION (agent-proposed, owner may overrule)` — **AGENT-SAFE once §7.1 is accepted**: dispatch after the owner reads it, or overrule and re-dispatch. (The owning spec's own Gate cell now carries that qualifier too — it read an unqualified "AGENT-SAFE" until 2026-08-02, and by this board's Authority rule the owning spec out-ranks this row for *what to build and how*, so the weakest of the three preconditions was the one that would have won.) **Repair round 2 (2026-08-02) — adversarial review returned REQUEST-CHANGES with no P0 and five P1s; all repaired in the same change.** The one that mattered: §7.1.4 specified the clearance cap as *"computed the way `_capability_cap` (`rooms.py:191`) already computes the credential cap"* — but `_capability_cap` **drops `group:` and `org` subjects by design** (`:207`, and short-circuits empty at `:208-212`, its own comment at `:209-211` saying so), so an implementer following that pointer would have turned the intersection into a **union** for exactly the rooms where a leak is widest: `[owner@x, group:sales]` bound to a restricted subject would come back with an empty cap, read as "no non-member participants", and admit the compartment to `Clearance.read` for forty people — while done-when 4 ("a non-member participant") passed green against two email addresses. §7.1.4 now names the site (`_subject_clearance_cap` beside `_capability_cap` in `routes/rooms.py`, consumed at the tree's only `resolve_clearance` call site, `routes/agent.py:1768-1774`), requires participants to be **expanded before** the intersection through one factored-out helper (`acb_auth.access.expand_session_subjects`, lifted out of `resolve_session_access` `:343-434` which already does the `group:`/`org` expansion at `:330-340`), and requires that expansion to **fail closed** — the opposite posture to `resolve_session_access`'s deliberate fail-open at `:417-426`, stated as such so nobody "fixes" it back. Done-when 4 is now four parts that cannot be satisfied without the `group:` and `org` cases. The other four P1s: the prior-art doc's QM-1 state cell still read "designed, unbuilt" for shipped steer (and QM-2 "✖" for built-but-off S4) while two other files in the same change said built; README §2's anchor table was **7 wrong of 8** under a "verified" header (fixed + caveat added, plus four stale repeats outside the table); §5.2 cited `test_reset_wipes_the_event_log` as demonstrating the `mark_active` bypass when that test seeds no `cc:runactor:` and so **cannot distinguish the two states** (README now says no test demonstrates it and describes the one that would); and §7.1.4 done-when 6 asserted a `422` on unknown `PATCH` keys that shipped code does not produce — `RoomPatch` (`routes/rooms.py:81-84`) is a plain `BaseModel`, no `extra="forbid"` anywhere in the gateway, verified against the repo interpreter (pydantic 2.13.4) — so it now pins the *real* behaviour and closing the model is filed as its own ticket in §7.1.9 rather than smuggled into this slice. ② **Floor control = OWNER-GATE, registered in §6 by name.** Per QM-1 steer dissolved most of the problem the baton was invented for; README §8 Phase 2 says whether the five modes still earn their place is *"pending the owner's re-decision"*. No acceptance is written for it on purpose — writing one would make an owner call look like queued work. ③ **`prefs`/`user` backfill** — classifier + **dry-run report** is AGENT-SAFE; **applying it is OWNER-GATE**, registered in §6 (mutates live Mem0). Verified: nothing writes a `prefs:` key anywhere today, so `prefs:` is permanently empty until this runs. **Two prior-art corrections (2026-08-02):** QM-3's *"rather than one `acting_identity`"* was factually wrong — there is no such column and never was (mig 138 `:26` rejects it explicitly), so QM-3 is net-new work with zero acceptance and maps to **WS-2 / WS-1, not here**; and the R2 phase-ID collision is resolved — the prior-art doc called `subject:` compartments "3b" while the owning spec puts them in the **3a remainder**, so the owning spec's ID wins and the board calls the slice **S1**. QM-5 (tenure narrows the model, not just the viewer) is a **real gap with an undone design**: viewer half built (mig 138 `:97-98` → `rooms.py:277-292` → `chat.py:314-316`), model half not (`_get_messages(thread_id, _hist_uid, …)` at `routes/agent.py:1947-1956` narrows by the acting caller only) — but README §6.5 says the two mechanisms are *"worth comparing before building either"*, which is a decision to record, not acceptance.

**Corrections applied 2026-08-09:**
- `org:global` scope must become tenant-scoped under D15 — coordinate S1 with WS-29 MT-1c and D17 (Mem0 binds tenant via connection options); do not mint a sixth scope shape (`saas_multitenancy.md` §1.9).
