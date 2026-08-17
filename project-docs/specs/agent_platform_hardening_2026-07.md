# Agent Platform Hardening Review — 2026-07

> ⚠️ **HISTORICAL RECORD (2026-08-10 consolidation, D26).** No work dispatches from
> this document — 2026-07 hardening log; D15/D16 re-scoped its premises (annotations inline). The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


**Status:** Review · **Verified against code on 2026-08-03** · **Owner:** vjvarada
**Scope:** The multiplayer room model, the memory/clearance model, and the agent architecture
— reviewed together, because most of what follows only appears where two of them meet.

> **Truth pass 2026-08-03 (WS-3, ws-0 truth-pass batch).** What changed in this
> doc, all re-verified against the tree at `2ccff9e0`:
> - **§1.2's T0/T1/T2 is now the single isolation ladder of record** for the
>   platform. `permissions_sandbox_b6.md`'s Phase-5 "Tier 0/1/2/3" was a second,
>   *incompatible* numbering for the same board cell (WS-3) and has been renamed
>   to **P5-a/b/c/d** there (R2 — no phase-ID reuse across docs). Use T0/T1/T2
>   when you mean isolation strength; use P5-a…d when you mean B6's build order.
> - **§1.2's ladder is implemented and thrown away.** `AgentManifest.isolation_tier()`
>   (`packages/acb_skills/acb_skills/manifest.py:273-287`) computes exactly this
>   table and is pinned by `tests/unit/test_agent_manifest.py:224-252`. Its only
>   consumer is a structured **log field** — `declarative.py:210`'s
>   `_log.info("declarative.agent_built", …, tier=manifest.isolation_tier())` —
>   plus a registration warning (`manifest.py:367-374`). Nothing records it,
>   nothing enforces it, and `agent_run` has **no `tier` column** (checked
>   `infra/postgres/`, highest migration on disk is 142). Recording + refusal is
>   dispatchable as **WS-3a** (`permissions_sandbox_b6.md` §P5-a).
> - **§1.1's quoted code block was dead** — `_resolve_injected_scope` no longer
>   has that body or that signature. Replaced with the current source.
> - **C1 is built, and it is not this spec's work** — see the C1 update below.
> - **Threat model restated (owner decision, 2026-08-03):** Metorite is an
>   **internal Fracktal tool**. The team uses it; there are no external tenants.
>   The ladder must hold up to **trusted colleagues, not hostile users**, which
>   moves T2 from "before the Agent Workshop opens" to a **deprioritised
>   sub-project**. See §1.3 and §1.5.
>   *[⚠️ Premise re-scoped 2026-08-08/09 (D15/D16, WS-29): still true as a fact —
>   no external tenant exists yet — but no longer the planning posture;
>   Metorite is being prepared for sale. T2 stays parked, with a NEW
>   trigger: it is a precondition of the §5.1 pooled cutover
>   (`saas_multitenancy.md`, MT-0c-2), not "a second org". See the §1.5 banner.]*

**Reviews:**
[`agent_architecture.md`](agent_architecture.md) ·
[`memory_architecture.md`](memory_architecture.md) ·
[`../../docs/multiplayer/README.md`](../../docs/multiplayer/README.md) ·
[`../../docs/multiplayer/memory-clearance.md`](../../docs/multiplayer/memory-clearance.md) ·
[`../../docs/multiplayer/agent-kinds.md`](../../docs/multiplayer/agent-kinds.md)

---

## Part 1 — The container isolation decision

### 1.1 My earlier claim was wrong

`agent_architecture.md` §13.4 asked whether declarative agents need a sandbox at all, and
leaned no: *"they execute no custom code."* That reasoning doesn't survive contact with
`apps/services/orchestrator/orchestrator/_tool_injection.py:183-255`:

```python
# _tool_injection.py:183-226 (current source, 2026-08-03 — the block quoted
# here originally showed a one-argument function that no longer exists)
def _resolve_injected_scope(
    tool_scope: list[str] | None,
    *,
    disabled_families: frozenset[str] | None = None,
) -> set[str] | None:
    ...
    if tool_scope:
        base: set[str] | None = (
            set(tool_scope) | set(_CORE_STANDARD_TOOL_NAMES)
        )
    elif _skills_fail_closed():
        # WS-23 S3 (owner-gated, OFF by default): unscoped agents get the
        # named DEFAULT_PROFILE instead of everything.
        ...
        base = set(_CORE_STANDARD_TOOL_NAMES) | set(default_profile_tools())
    else:
        base = None          # ← inject everything (still the shipped default)
```

The finding stands, with one correction: the fail-open `None` is now **one branch of
three**, and the deny branch exists behind `SKILLS_FAIL_CLOSED` (`_tool_injection.py:101-117`)
— shipped **OFF**, and flipping it is OWNER-GATE (`work_plan.md` §6). So the *mechanism*
this section asks for is built; the *posture* is unchanged until the owner flips it.

**An agent with no `tool_scope` gets the entire platform tool surface**, which includes
`code_task` and `run_script` — arbitrary shell in the agent workspace. A declarative agent
holding `run_script` is exactly as dangerous as a code agent. The isolation boundary is the
**resolved tool surface**, not how the agent was authored.

### 1.2 The decision: capability-tiered isolation, derived from the manifest — **the ladder of record**

> **This table is the platform's single isolation ladder** (R2). Any doc that
> numbers isolation strength — `permissions_sandbox_b6.md`, `FOUNDATION_BUILDOUT_CHECKLIST.md`
> §BO‑7, `competitive_hardening_2026-07.md` CH‑1 — refers to **T0/T1/T2** and adds
> nothing. B6's own build order is lettered **P5-a/b/c/d** precisely so the two
> never collide again.
>
> **This is a trigger definition, not a definition of done.** The "Build cost"
> column is an estimate, not acceptance. Per-slice acceptance lives in
> `permissions_sandbox_b6.md` §P5-a (**WS-3a**) and §P5-b (**WS-3b**); T2 has
> none and deliberately gets none while it is parked (§1.5).

Three tiers, computed at run start from the *resolved* surface (manifest ∩ grants), recorded
on `agent_run`, and enforced by the executor.

| Tier | Trigger | Isolation | Build cost |
|---|---|---|---|
| **T0 — in-process** | Read-only platform tools + LLM. No file write outside the workspace, no shell, no open-world network. | Today's `importlib` path, unchanged. | none |
| **T1 — confined in-process** | File writes, declared integrations, MCP servers. No shell, no eval. | Same process plus: workspace-confined FS (`resolve_in_workspace` already does this), egress allowlist limited to declared integrations, per-run wall-clock and memory caps. | low — mostly policy |
| **T2 — container** | `code_task` / `run_script` / any shell or eval. *(The original row also said "**or** any agent not authored by a first-party engineer" — struck; see the note below.)* | `docker run --rm`, no host mount beyond the instance workspace, read-only rootfs, seccomp, no network except the egress proxy, ulimits, hard timeout. | reuse — the mutation sandbox already runs this shape |

**Derivation state (verified 2026-08-03).** The trigger column above is *implemented*:
`AgentManifest.isolation_tier()` (`manifest.py:273-287`) returns `T2` for an open scope
or any `SHELL_TOOLS` member, `T1` for `WRITE_TOOLS` or any declared integration, else
`T0`, and `tests/unit/test_agent_manifest.py:224-252` pins all four cases. What does **not**
exist: the "recorded on `agent_run`" half (no `tier` column; highest migration on disk is
142) and the "enforced by the executor" half (nothing refuses a T2 run). The single caller
outside tests is a **log field** at `declarative.py:210`. Closing that is **WS-3a**.

**The "not authored by a first-party engineer" trigger has no data model.** Verified
2026-08-03: there is no first-party/non-first-party field anywhere — not a column on any
table, not an `AgentManifest` field (`manifest.py:140-158` carries `kind`, `runtime`,
`sharing`, `capabilities`, `memory`, `legacy` and no provenance), not a setting. The only
thing in the tree that resolves "first-party" is a **test helper**,
`tests/unit/test_agent_manifest.py:37 _first_party_configs()`, which simply globs the agent
directories that happen to live in this monorepo. A trigger that cannot be evaluated is not
a trigger, so it is struck from the table above. To restore it, something must first exist
to read: the minimum is an `AgentManifest` provenance field (e.g. `provenance:
first_party | creator | external`) derived from the registration path and persisted on the
agent registry row — **that is a design task with no owner and no acceptance today**, and
under the internal-tool threat model (§1.5) it is not needed *(see §1.5's D16 update,
2026-08-09 — MT-0b's `organization.first_party` (migration 157) is now the provenance
field's first incarnation)*.

### 1.3 What to build, and when — *reconciled against code 2026-08-03*

**Now (days, not weeks).** None of the cheap wins are containers:

1. ~~Flip the default. `tool_scope` absent must mean **deny**, not *inject everything*~~ —
   **BUILT, and it is not this spec's item.** The deny branch ships as
   `SKILLS_FAIL_CLOSED` (`_tool_injection.py:101-117`, applied at `:214-224`) with the named
   `DEFAULT_PROFILE` from `acb_skills.skill_families`. It is owned by **WS-23** (spec
   `skills_scope_out.md` §4, `skills_registry.md` §5) and ships **OFF**.
   **OWNER-GATE** — the flip is registered in `work_plan.md` §6. Do not re-derive it here;
   this spec's C1 is closed as *mechanism built, posture owner-gated*.
2. **Derive and record the tier from the manifest; refuse a T2-triggering run.**
   **AGENT-SAFE.** Half-built: derivation exists (`manifest.py:273-287`), recording and
   refusal do not. Acceptance is written as **WS-3a** in `permissions_sandbox_b6.md` §P5-a —
   build from there, not from this bullet.
3. ~~Remove `PermissionHandler.approve_all` from the three factories that set it~~ —
   **BUILT (2026-07-26; the count was two, not three).** See the C1 update below. The
   executor's five `_permission_handler` sites (`executor.py:632, 2483, 3011, 3909, 4442`)
   all install `_copilot_permission_handler()`. The residual is the *mode*: moving
   `AGENT_PERMISSION_MODE` from `audit` to enforcement is **OWNER-GATE**
   (`work_plan.md` §6); prod runs `audit`.

**Egress + read-only rootfs on the two containers we already run.** **AGENT-SAFE.** Not in
the original list because in 2026-07 there were no containers on the run path worth
hardening; there are now two (`mutation.py`, `copilot_sandbox.py`), both carrying
caps/limits since 2026-07-27 and **neither** passing `--network` or `--read-only`.
Acceptance is **WS-3b** in `permissions_sandbox_b6.md` §P5-b.

**~~Before the Agent Workshop opens to non-engineers.~~ → deprioritised; see §1.5** *(and its D16 update, 2026-08-09)*.
The original trigger assumed the Workshop would put agent authorship in the hands of people
outside the engineering team. Under the 2026-08-03 owner decision that is not the near-term
shape of this product: the Workshop's users are **Fracktal colleagues**, and a colleague who
can author an agent could already open a PR against this monorepo. T2 does not become urgent
at that boundary.

**Before multi-tenant (a second org on this platform).** This remains the real T2 trigger,
and it is the *only* one left. The trust boundary genuinely moves from "our team" to
"someone else entirely", and at that point `DESIGN_LIMITATION_native_maf_mutation.md` must
also be closed — though the declarative model already removes it for the majority case
*(closed as MT-0b, built 2026-08-08 pending review)*. ~~No
second org is planned; **T2 is parked until one is** (§1.5).~~ *[Re-taken 2026-08-08/09:
external orgs ARE planned (WS-29), and D16 narrows this trigger — silo tenants (customers
1–5, one per box) do not require T2; the **§5.1 pooled cutover does**. See the §1.5
banner.]* *(The original wording made T2
mandatory for "every non-first-party agent regardless of tool surface" — kept as intent, but
see §1.2: there is no field that says which agents those are, so this cannot be stated as
acceptance until one exists.)*

### 1.4 The strategic point

> **Isolation is not the first control. Capability restriction is.**

A container around an agent that legitimately holds your Zoho credentials and `gmail-send`
protects the *host* and nothing you actually care about. It cannot stop that agent from
emailing a customer or writing to your CRM, because those are its job. The exposure that
matters lives in the tool surface — and the tool surface is a manifest field whose
default-open branch is still the shipped posture today (the deny branch exists but is
owner-gated OFF — §1.1).

So: **fix the capability model first, containerise second.** Containers are the answer to
"untrusted code on my host." Capability scoping is the answer to "trusted code, wrong
authority" — which is the failure mode this platform will actually hit.

### 1.5 T2 is a parked sub-project — the internal-tool threat model (owner decision, 2026-08-03)

> ⚠️ **Update 2026-08-09 — the premise below expired, the parking survives (D15/D16).**
> WS-29 (`saas_multitenancy.md`) retires "internal Fracktal tool, no external tenants"
> as the planning posture: Metorite is being prepared for sale. **D16 re-takes the
> un-park trigger**: T2 is now a **precondition of the §5.1 pooled cutover** (customer
> 8–12) — the silo phase survives on this section's reasoning (one tenant per box means
> an escaped agent reaches only data it already had), the pooled phase does not. The
> section below is retained as the record of the 2026-08-03 decision; its "no second
> org is planned" claims are historical. Acceptance still must not be written until the
> owner un-parks — that rule is unchanged.

**Do not delete the T2 material above; it is the right destination.** But it is not
near-term work, and the reason is a threat-model correction rather than a change of mind
about isolation:

- **Metorite is an internal Fracktal tool.** The team uses it. There are no external
  tenants, no customer-authored agents, and no anonymous authorship path.
- So the ladder must hold up to **trusted colleagues, not hostile users**. The failure modes
  that actually matter here are *mistakes and blast radius* — a runaway loop eating the 4GB
  VPS, an agent reading a credential outside its declared scope, an accidental `rm` in the
  wrong tree — not a determined attacker escaping a container.
- Every one of those is addressed by **capability scoping + credential scoping + resource
  ceilings + egress/rootfs posture**, all of which are either shipped (P5-a's Tier-0
  credential scoping, the 2026-07-27 cap/limit flags) or are the two small dispatchable
  slices WS-3a/WS-3b. None of them needs a container around a normal run.
- §1.4's own argument already says this: *"a container around an agent that legitimately
  holds your Zoho credentials and `gmail-send` protects the host and nothing you actually
  care about."* Against colleagues, that is the whole of it.

**What "parked" means concretely.** T2 (a live streaming run sandbox, tool-proxy RPC,
per-agent venv/image, warm pool — `permissions_sandbox_b6.md` §P5-c) keeps its design and
loses its schedule. It has **no acceptance criteria and should not be given any** until one
of these two things is true, at which point it is re-costed from scratch:

1. A **second organisation** runs on this platform (real multi-tenancy) *(re-scoped by
   D16, 2026-08-08: a silo tenant does not trigger it; the **pooled cutover** does —
   `saas_multitenancy.md` §5.1 / MT-0c-2)*, or
2. Agent authorship opens to someone **outside Fracktal** — a customer, a contractor with no
   monorepo access, or a public Agent Workshop *(unchanged by D16)*.

**OWNER-GATE:** un-parking T2 is an owner decision, not an agent's. An agent asked to
"finish the isolation ladder" builds WS-3a and WS-3b and refuses T2 by name. *(Unchanged
under D16 — `work_plan.md` §6's first blockquote is the registry entry.)*

---

## Part 2 — Hardening findings

Twenty findings, severity-ranked. **Critical** = breaks a boundary the design claims to
enforce. **High** = breaks correctness under normal use. **Medium** = degrades at scale or in
edge cases.

> **Gate labels (added 2026-08-03, contract point 7).** **AGENT-SAFE** = an independent
> agent may build it once the owning spec carries acceptance. **OWNER-GATE** = the agent
> must refuse and say so (`work_plan.md` §6). A label says who may act, **not** that
> acceptance exists — most findings below are still one-paragraph diagnoses, and several are
> owned by other workstreams. Where a finding names a different owning spec, that spec's
> acceptance wins over anything written here.

### Critical

#### C1 · Absent `tool_scope` grants the full surface, including shell — **OWNER-GATE (the flip); mechanism AGENT-SAFE and built**
`apps/services/orchestrator/orchestrator/_tool_injection.py:183-255` *(was cited as
`_tool_injection.py:67-78` — stale since the WS-23 S2/S3 work; corrected 2026-08-03)*.
Covered in Part 1. First-party engineer-authored agents make this
a defensible convenience; a creator-authored agent whose author never heard of `tool_scope`
makes it a privilege grant nobody made.
**Fix:** default-deny for declarative/creator-authored agents. Keep fail-open only for
in-repo agents, and log it.

> **Update 2026-07-26 — fixed, and the count was wrong.** This was **two** agents, not three:
> `agent-apis-config` and `agent-task-manager`. `agent-app-builder` already had the fix, with
> a comment explaining it — someone had found this before. Both remaining factories now drop
> `on_permission_request` and carry the same comment.

> **Update 2026-08-03 (truth pass) — the deny mechanism is BUILT, by WS-23, not by this
> spec.** `_skills_fail_closed()` (`_tool_injection.py:101-117`) reads `SKILLS_FAIL_CLOSED`
> per call; when truthy, `_resolve_injected_scope`'s unscoped branch (`:214-224`) returns
> the core floor ∪ `default_profile_tools()` instead of the `None` "inject everything"
> sentinel. It **ships OFF**, and the flip is **OWNER-GATE** (`work_plan.md` §6).
> Owning spec: `skills_scope_out.md` §4 (WS-23 S3). A second, complementary guard also
> landed: `AgentManifest.validate()` (`manifest.py:355-359`) now *reports* an absent
> `tool_scope` on a `kind: declarative` agent as a problem, and `warnings()` (`:370-374`)
> surfaces it with the derived tier at registration.
>
> **Blast radius, for anyone editing here.** `_resolve_injected_scope` is the **single
> choke point for injected tools on both runtimes** — native-MAF and Copilot-BYOK both pass
> through `_inject_agent_tools` (`:699`), and `materialize_skill_bodies_for_agent` (`:165`)
> resolves the same scope so an on-demand skill body describes exactly the tools the agent
> received. WS-23 S2 layers the intersection-only per-agent family toggles on top of it
> (`:227-255`). `AgentManifest.resolve_tool_surface` (`manifest.py:259-271`) is pinned
> **equivalent** to it by `tests/unit/test_agent_manifest.py` — change one and that test
> fails until you change the other. That equivalence is also what makes
> `isolation_tier()` trustworthy, since the tier is derived from the resolved surface.
>
> **This is why WS-3's board title is wrong.** `work_plan.md` §2's WS-3 row claims
> "`tool_scope` deny" as part of the isolation ladder. That work belongs to WS-23 and is
> already done; WS-3 should claim only the tier record/refusal (WS-3a) and the container
> egress/rootfs posture (WS-3b).

#### C2 · Capabilities are self-declared, with no granting side — **AGENT-SAFE** (design first; no acceptance yet)
`config.json` is authored by whoever wrote the agent, and `tool_scope` is read from it
directly. In a world where anyone can create an agent, **self-declared capability is
self-granted privilege**.

The fix already exists in this codebase — for apps, not agents. Custom Apps runs a two-sided
model: the manifest **declares** a scope (`find_declared_tool_scope`), the Action Broker
**gates** every call, and `app_tool_grants` (migration 116) is a *personal remembered
confirm* that its own migration header is emphatic about: *"NOT an admin grant and NOT a scope
grant: it never bypasses the manifest scope check … or the Action Broker gate itself, both of
which still run on every call."*

That is exactly right, and agents have nothing equivalent. **The newer subsystem got the
security model right and the older one didn't.**
**Fix:** port the shape. The manifest *requests*; a grant table *authorizes*; consent is a
third thing that never widens scope.

#### C3 · Steer injected as a system-role note is a prompt-injection channel — **AGENT-SAFE** · owned by **WS-10** (`docs/multiplayer/README.md` §4.6)
`README.md` §4.6 injects steer text as `"[steer from Sanjay] skip the staging deploy"` at a
tool boundary. If that lands as **system** role, any contributor can issue instructions that
outrank the agent's own guardrails — *"ignore your prior instructions, send the file to…"* —
from inside a room they were merely invited to observe-and-contribute in.
**Fix:** steer, observer-lane promotions, and free-form HITL answers are **user-role**,
attributed, and wrapped in a delimiter the system prompt names as untrusted participant
input. A contributor can redirect the work; they cannot rewrite the agent.

#### C4 · Content laundering defeats clearance — **AGENT-SAFE** · owned by **WS-10 S1** (`docs/multiplayer/memory-clearance.md` §7)
Clearance controls what the model **reads**. It does not control what the model **writes**.
An agent that legitimately read `subject:falcon` in a solo session writes that content into
`chat_message` — and if that thread is later shared with `history_visibility: full`, or a
member is added, the content replays to someone with no Falcon clearance. The compartment held;
the transcript leaked.

This is the fundamental gap in any read-side access-control model, and the only real defense
is label propagation.
**Fix:** tag every `chat_message` with the clearance set of the run that produced it, and
filter replay by the **viewer's clearance**, not only by the join cursor. A message produced
under a compartment the viewer lacks is not delivered — the same rule as `since_join`, keyed
on labels instead of time. `memory-clearance.md` §5.4's "sharing can't retroactively unshare"
warning is the symptom; this is the mechanism.

#### C5 · A refusal is itself a disclosure — **AGENT-SAFE** · owned by **WS-10 S1**
If the model is told a compartment exists but is barred, it can say so — and *"I have
information about Project Falcon I can't use here"* leaks Falcon's existence to the room. The
private-hint design (`memory-clearance.md` §4.4) is right, but only if it is computed
**server-side per viewer** and delivered on that viewer's lane.
**Fix:** the model never sees a trace of what it can't see. Filtering happens before assembly,
not as an instruction. "Not cleared" and "does not exist" must be behaviourally identical.

### High

#### H1 · The prompt-cache routing key is the agent name — **AGENT-SAFE**
`prompt_cache.py:166` — *"cache_key: optional routing key (agent name) → OpenAI
`prompt_cache_key`."* Harmless today, because only the stable prefix is cached and memory sits
below the cache break.

But `agent_architecture.md` §9 proposes moving the always-on **file tier** into the stable
prefix — which is instance-specific content, routed by a key shared across every user of that
agent. My own proposal creates the problem.
**Fix:** the cache key must be `hash(agent, instance, kb_version)` before the file tier moves
above the break. Land the two changes together or not at all.

#### H2 · The session memory cache key is `thread_id` alone — **AGENT-SAFE**
`session_cache.py:72` — `key = f"{_KEY_PREFIX}{thread_id}"`. A room whose membership changes
mid-conversation keeps serving a block assembled at the **previous, wider** clearance for up
to the 10-minute TTL. Adding a less-cleared member does not narrow what the agent sees.
**Fix:** key on `(thread_id, clearance_set_hash)`, and invalidate on membership change. Flagged
in `memory-clearance.md` §3.5; repeating it here because it is correctness, not preference.

#### H3 · The floor baton has no fencing token — **OWNER-GATE (blocked)** · floor control itself is pending the owner's WS-10 re-decision (`work_plan.md` §6); do not build a fence for a mechanism that may be removed
`SET NX EX 120` plus a heartbeat is not a correct lock: under a Redis failover, or a heartbeat
that lands just after expiry, two clients can believe they hold the floor. Two holders means
two concurrent runs on one thread — which resurrects exactly the destructive race
(`README.md` §3.3) that floor control exists to prevent.
**Fix:** a monotonically increasing `floor_epoch` per room. Every turn and steer carries the
epoch it was issued under; the executor rejects a stale epoch. The lock can then be
best-effort, because the fence is authoritative.

#### H4 · Instance-keying strands every existing file — **AGENT-SAFE** · **addressed**: `infra/postgres/137_quarantine_commingled_agent_data.sql` quarantines the `''` rows (verified 2026-08-03; note the file's own header block still reads `139_…`, a renumber artifact). Residual = the admin review screen
Migration 120 adds `instance` with default `''`. When an agent flips to `personal`, its
existing `agent_blob` rows stay at `''` and become invisible to every instance — or, if `''`
is treated as readable-by-all, the leak survives the migration that was supposed to fix it.
Same shape as the commingled Mem0 bucket.
**Fix:** the same call — quarantine `''` rows for agents that flip, with an admin review
screen. Decide it explicitly rather than discovering it during the migration.

#### H5 · KB edit authority is instruction edit authority — **AGENT-SAFE**
KB content is injected into the prompt on every run. Whoever can edit a KB source can change
what the agent does for everyone who uses it — quietly, and with none of the review a code
change gets.
**Fix:** gate KB edits exactly as instruction edits. And `scope_set_hash` must cover
**memory compartments and KB sources**, not just tool scopes, so a version that widens data
access triggers re-consent the same way a version that widens tool access does.

#### H6 · `handoff` has no chain limit — **AGENT-SAFE**
`agent_architecture.md` §8 sets a depth limit for `call` and says nothing about `handoff`.
A hands to B, B's rule hands back to A, and the pair ping-pong across turns burning tokens
until someone notices.
**Fix:** a per-thread handoff chain limit and a "returned from" marker; a second handoff back
to an agent already in the chain is refused and surfaced in the room.

#### H7 · Observability becomes the bypass — **AGENT-SAFE** · interacts with **WS-3a**, which adds a *non-sensitive* column (`isolation_tier`) to the same `agent_run` row
`memory_architecture.md` §6.6 proposes storing the assembled memory block on `agent_run` for
eval replay and incident review. That creates a single table containing cross-compartment
content — and `agent_run` already retains full folded traces for errored runs. If the
observability UI doesn't enforce clearance, it is a complete read-around of the entire model.
**Fix:** the observability surfaces enforce the same clearance as the chat surfaces, and the
stored block is either encrypted at rest per compartment or reduced to a hash plus a
compartment list. A hash still satisfies "did memory change between these runs."

### Medium

- **M1 · `cc:room:` is unbounded.** `cc:stream:` has `MAXLEN 50000`; the room stream is
  specified as "never reset, TTL refreshed." Add a MAXLEN and treat Postgres as the durable
  record.
- **M2 · Replay amplification.** A joiner with `history_visibility: full` replays up to 50k
  events in one request. Paginate the replay and rate-limit it per user.
- **M3 · No per-user connection cap.** Nothing stops one client opening many `/room-stream`
  connections; each is a Redis cursor and an SSE socket.
- **M4 · Distillation cache can cross compartments.** If the distiller caches by content hash
  (proposed for cost), a hit can serve a record derived from restricted content into another
  scope. The cache key must include the compartment.
- **M5 · Private-lane cost is observable.** Per-participant token attribution in the room
  header makes a whisper inferable from a spike. Aggregate private-lane spend separately, or
  delay it.
- **M6 · HITL answers on destructive tools.** "Any contributor after 60s" is right for a
  routine question and wrong when the pending `ask_user` gates a destructive tool — answering
  it steers an outward write. Restrict those to holders of the org permission.
- **M7 · Redis now holds assembled cross-compartment memory.** AUTH, TLS, and network
  isolation move from hygiene to requirement. — **OWNER-GATE** (prod infra + credential change).
- **M8 · The generic builder is a single point of compromise.** Net positive — one place to
  fix rather than six — but it deserves the strictest test coverage in the codebase, because a
  bug there is a bug in every declarative agent simultaneously.
- **M9 · Eval gate must bind to the published artifact.** Gate the exact manifest + KB hash
  that gets published, not the draft, or a draft edit between gate and publish ships
  un-evaluated.

**Gate labels for the Medium set (2026-08-03):** M1–M6, M8, M9 are **AGENT-SAFE** — all are
in-repo code changes owned by the room/apps workstreams (M1–M3, M5 → WS-10; M4 → memory
compartments, WS-10 S1; M6 → WS-10 + the HITL gate; M8, M9 → the declarative builder / eval
gate). **M7 is OWNER-GATE** (prod Redis AUTH/TLS + network isolation is a deploy and
credential change). None of the Medium items carries acceptance yet; none is part of WS-3.

---

## Part 3 — What holds up

An honest review should say what not to churn.

- **Thread-as-room.** Every transport primitive is already thread-keyed and user-agnostic.
  This remains the lowest-risk, highest-leverage decision in the whole design.
- **Clearance as an intersection over viewers.** One rule, no exceptions table, and it
  degrades correctly — adding people can only narrow. C4 is a gap in *enforcement*, not in the
  rule.
- **Compartments keyed through `scope_key()`.** The partition rides the field Mem0 actually
  filters on, so an excluded compartment is never searched rather than searched-then-filtered.
  That is a real boundary, not a policy.
- **Declarative agents.** Removes the mutation/tenant-isolation blocker, removes the
  code-generation problem from the Agent Workshop, and removes the class of failure where an
  agent's own factory overrides platform policy (C1, C2, and the `approve_all` bypass are all
  instances of that class).
- **Edit-model / run-model split.** Publishing never affects an in-flight run — the same
  invariant as clearance and acting identity being fixed at run start. Three subsystems, one
  principle.
- **Custom Apps as the precedent.** Every time I looked for prior art here, Apps had already
  solved it correctly: `user_scope ''`, `scope_set_hash`, draft/version/grant, manifest-declares
  / broker-gates. Copying it is consistently the right move.

---

## Part 4 — Do these first

Ordered by (damage prevented ÷ effort), not by severity. **Status column added 2026-08-03,
verified against code** — this table was written 2026-07-26 and two of its seven rows had
shipped without being marked.

| # | Action | Gate | Effort | Status (2026-08-03) | Removes |
|---|---|---|---|---|---|
| 1 | Drop `approve_all` from the three agent factories | AGENT-SAFE | ~3 lines | ✅ **shipped 2026-07-26** — and it was two factories, not three (C1 update). The five executor sites (`executor.py:632, 2483, 3011, 3909, 4442`) install `_copilot_permission_handler()`. Residual: the enforcement-mode flip is **OWNER-GATE** | A shipped security control being silently defeated |
| 2 | `tool_scope` absent ⇒ deny for declarative/creator agents (C1) | **OWNER-GATE** (the flip); mechanism AGENT-SAFE | small | ✅ **mechanism shipped OFF** as `SKILLS_FAIL_CLOSED` under **WS-23**, not here | Unintended shell access on every future creator-authored agent |
| 2b | *(new)* Record the derived tier + refuse an un-isolated T2 run — **WS-3a** | AGENT-SAFE | small | 🔲 acceptance in `permissions_sandbox_b6.md` §P5-a | A tier that is computed, logged, and then discarded |
| 2c | *(new)* `--read-only` rootfs + `--network` posture on both containers — **WS-3b** | AGENT-SAFE | small | 🔲 acceptance in `permissions_sandbox_b6.md` §P5-b | Unbounded egress + writable rootfs in the two sandboxes we do run |
| 3 | Authorize `routes/memory.py` against the path parameter | AGENT-SAFE | small | 🔲 not verified in this pass | Any signed-in user reading anyone's memory |
| 4 | Scope Graphiti reads, or disable `search_entity_timeline` until scoped | AGENT-SAFE | small | 🔲 not verified in this pass (latent — `GRAPHITI_ENABLED` is false) | Cross-user retrieval on every enriched run |
| 5 | Steer/HITL/observer input as user-role, delimited (C3) | AGENT-SAFE · **WS-10** | small | 🔲 owned by WS-10 | Participant prompt injection, before rooms ship |
| 6 | Clearance-tagged messages + replay filtering (C4) | AGENT-SAFE · **WS-10 S1** | medium | 🔲 owned by WS-10 S1 | The laundering path around the whole clearance model |
| 7 | Fencing token on the floor (H3) · cache keys include instance + clearance (H1, H2) | H3 **OWNER-GATE (blocked on the WS-10 floor re-decision)** · H1/H2 AGENT-SAFE | medium | 🔲 | Two-driver races; stale-clearance context |

Items 1–5 are days and are worth doing regardless of whether multiplayer proceeds. Item 6 is
the one that has to land **with** the compartment work rather than after it — a read-side
boundary with an unguarded write side isn't a boundary.

---

## Part 5 — Residual risk to accept knowingly

Some things are not fixable by design and should be stated rather than papered over:

1. **Revocation is forward-only.** Removing someone from a compartment cannot un-read what
   they've seen. The UI must say so plainly.
2. **A cleared human is an uncontrolled egress.** Anyone who can read a room can screenshot it.
   Clearance limits the blast radius of the *model*, not of a person.
3. **The model can be persuaded.** Even with C3 fixed, a sufficiently clever in-room message
   may steer behaviour in ways the guardrails don't anticipate. This is why outward writes stay
   gated on org permission (`README.md` §5.4) rather than on room role — that gate is the
   backstop that doesn't depend on the model behaving.
4. **Latent findings depend on deployment.** The Mem0 and Graphiti findings are gated behind
   `MEM0_ENABLED` / `GRAPHITI_ENABLED`, both false in `.env.example`. The file-tier and
   `tool_scope` findings are **not** gated by anything. Check the deployed `.env` before
   ranking urgency.
