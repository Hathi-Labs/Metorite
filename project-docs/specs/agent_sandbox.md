# Agent sandbox — build the tool that does not exist yet

<!-- ste-tier: strict -->

- **Status:** ACTIVE, 2026-10-27. **D130** answers every owner call of §14,
  and takes each recommendation. **D187** amends the spec on 2026-11-07
  (§0a): a user tool is an AI Studio package. **D224** sets the order of
  work on 2026-11-15 (§0b): chains of existing actions first, code last.
  **D227** (2026-11-19, §0c) opens SBX-9 to the beta orgs, and rung 6a′ runs compute first.
  SBX-8, SBX-15a and SBX-18 are built dark (2026-11-19, §12). Nothing else is built.
- **Verified against code on 2026-10-19** at `main` `daad004f9`. The anchors
  of D187 were read on 2026-11-07 at `main` `f2671c706`. Re-verify every
  anchor at dispatch.
- **Board row:** **WS-TO**. Metorite task 810 is the umbrella of the SBX
  slices. Epic 3393 (WS-TOF, D224) orders the work with the Studio skill
  slices (§0b). The work extends the
  one sandbox broker of D83, so `maf_coding_engine.md` (WS-43) still owns the
  broker. Metorite task 1962 is SBX-0b, the D187 amendment.
- **Asked by:** the owner, 2026-10-18 and 2026-10-19, in chat. The owner
  asked whether the AI may write code for a function that no tool covers. The
  owner chose **"A full capable sandbox"**, and asked us to check
  `perplexityai/SPACE` for it. On 2026-11-06 the owner asked for repair,
  reuse and platform adoption, and accepted each recommendation on
  2026-11-07 (D187).
- **Owner tasks:** #234 to #242 in the Metorite project. §1 maps each one to
  a section.
- **Reads:** D12, D15, D16, D56, D58, D64, D83, D85, D86, D90, D93, D97,
  D99, D100, D126, D127, D139, D146, D147, D180, D187, D227, R5 to R8.
- **Owns:**
  - tool-gap detection, and the tool lifecycle on the one Studio library
    (D187),
  - the run capability and the sandbox API door,
  - the build, smoke, run and repair steps of a user tool,
  - the review controls of a stored tool,
  - the choice of sandbox backend.
- **Does not own:**
  - the broker, the image, the egress proxy and the host rules
    (`maf_coding_engine.md`),
  - the package store and its versions (`ai_studio.md` A3 and A4, D187),
  - installs, scopes and promotion (`package_host.md`, D187),
  - the tool failure log (Metorite task 1793, `tool_failure_log.md`, D187),
  - the confirmation card (`projects_ai_chat.md` §5),
  - branch 0 and the route allowlists (`external_agent_access.md` §10.1),
  - credit prices (`credit_pricing.md`),
  - the box (`box_hardening.md`).

**Ids in this spec.** Every id carries the `SBX-` prefix (R2). The slices
are `SBX-0`, `SBX-0b` and `SBX-1` to `SBX-17` (D187). The fences are
`SBX-F1` and up, the owner gates `SBX-G1` and up, and the owner calls
`SBX-Q1` and up. The owner's approval records each accepted call as a
numbered entry in `work_plan.md` §3.

---

## 0a. Amendment, 2026-11-07 (D187)

**The owner's request, 2026-11-06, in chat.** The assistant builds a tool
from the chat when no tool exists. It repairs a tool that fails, and uses the
new version. Each created or repaired tool stays reusable in the
organization.

A useful tool can serve all of Metorite. This matters most when a built-in
tool fails, and the fix of one member could fix it for everyone. The owner
accepted each recommendation below on 2026-11-07: "go ahead with your
recommendation". `work_plan.md` §3 records it as D187.

### The five decisions

1. **A sandbox per chat thread, and short-lived (§6.2).** No member gets a
   sandbox of their own. The broker key stays (organization, `user-tool`,
   thread).
   - A tool version is an immutable stored copy. A container never holds a
     tool between runs.
   - Why: a sandbox that lives long gathers data, and an injected prompt
     could leave something behind in it. Idle containers cost CPU. Sharing
     works by a version, not by a machine.
2. **One library: a user tool is an AI Studio package (§7, §8).** Its code
   is a member-authored logic module (`ai_studio.md` B5), and the catalog
   serves it as an action.
   - So a tool gets the scopes of the package host and the promotion of the
     same install (`package_host.md` §6).
   - The tables `user_tools` and `user_tool_versions` go. A version is a row
     of `app_versions`, and its five files live in a new column of that row
     (§7.2).
   - Before any build, `find_tool` asks whether catalog actions can do the
     need with no code (§6.1).
   - A holder of `studio:publish` reads the code and approves one version
     before another member can run it (§8.1).
3. **Repair at three levels (§6.7).** Three signals start a repair, and one
   failed run gets at most 1 repair build.
   - (a) The member's own tool fails. The assistant builds the next version,
     runs it, and keeps it only if that run succeeds.
   - (b) An installed tool of another member fails. The same steps, and the
     new version runs for this member only.
   - (c) A built-in tool fails. The assistant never changes platform code at
     run time. It may build a private workaround, and it files the error to
     the tool failure log (task 1793).
4. **Platform adoption only through reviewed repo code (§6.8).**
   - A tool serves all of Metorite only as first-party code in the
     repository, after a PR and a review. A release then ships it as a
     `system` install.
   - Nobody copies a running tool from one organization into another. The
     source organization must consent, because the code is theirs.
   - Only counts cross tenants: runs, the success rate and the number of
     organizations with the same gap.
5. **Production on a dedicated sandbox box (§4.6).**
   - A separate small VPS runs the Docker backend of the broker under gVisor
     (`runsc`). Only a private network reaches it. The backend value is
     `docker-remote`.
   - Member data stays with the hosting provider that processes it today. No
     new processor joins.
   - The `e2b` backend stays the alternative, after a data processing
     agreement (D130.7). This amends D130.1 for production only. The dev
     stack keeps the local Docker backend.

### Building blocks, speed and order

- **Building blocks (§6.9).** The image holds 24 pinned Python packages, and
  nothing installs at run time. SBX-13 adds one door client to the image.
  The build runs on the one coding harness (D139, CA-14), and the model is a
  setting.
- **Speed (§6.10).** Reuse first, no install at run time, and one warm
  container for each thread.
- **The order (§12).** Phase 1 runs no code: SBX-1 as reshaped, and the
  spec of the tool failure log. Phase 2 builds the door, the build and the
  run on the dev stack. Phase 3 is production on the dedicated box, after
  the owner gates.

### What changes in this spec

The header, §0, §2, §3.1, §3.2, §4.4, §6.1 to §6.5, §7, §8, §9.2, §11, §12,
§13, §14 and §16 change. §4.6 and §6.7 to §6.10 are new. Each changed
paragraph carries "(D187)". Text with no mark stands as D130 left it.

---

## 0b. The order of work: tools on the fly (D224, 2026-11-15)

**The ask.** On 2026-11-15 the owner asked that the chat fix tools, and
build the tools that do not exist from the apps and integrations that it
has. The owner asked that these tools persist for a member and for the org,
and fit the tool loading that exists. Then the owner wrote "Go in the order
that you recommend". D224 records the order. Epic 3393 (WS-TOF) holds the
slices.

**The rule.** A chain of existing actions comes before code. Most missing
tools are a chain of actions that exist: read the CRM, find the contact,
draft the mail. D187 already tries a chain before it writes code (§6.1).
Code adds logic, not reach, because no provider token enters the sandbox
(§5.4). A chain needs no sandbox and no owner hardware gate.

### The ladder

Each rung ends in a demo on production (D211). The column "On main" was
read at `539221a29` on 2026-11-15. Re-verify it at dispatch.

| Rung | The demo (task) | On main | Slices (task) | Owner gate |
|---|---|---|---|---|
| 1. Save and reuse | A member does a three-step job and saves it. A new chat finds it from a plain sentence and runs it (3414) | F3 loads a skill pack. F6a (3160) built the save offer behind a build-time flag. Only Knowledge reaches the catalog: CRM, Projects, Email and Calendar give 0 rows. A skill matches on its exact name only. A skill pack adds rules, not tools | TOF-1a catalog coverage (3404). TOF-1b a skill brings its tools (3411). TOF-1c a match on the member's words (3412). TOF-1d the offer for each org (3413) | The offer flag, the HathiLabs org first |
| 2. The tool failure log | A tool error becomes one task with counts. A fix merges, and the next release closes it (3417) | Nothing. Task 1793 has no spec | TOF-2a the spec (3416), then the build (1793) | None |
| 3. Repair a saved skill | Mira shows why a skill broke, proposes version 2 and tries it with writes held. The member approves, and undo works (3420) | F7 undo exists. F4 tests and §7 step 6 "Improve" of `ai_studio.md` are not built | TOF-3b the F4 tests (3419). TOF-3a the repair (3418) | None |
| 4. Share with the org | A member shares a skill, an admin approves it, and the chat of another member uses it (3422) | `list_loadable` loads an `org` skill. No route sets `visibility`. The J2b slices approve app versions, not skills | TOF-4a share and approve (3421): 🔨 BUILT, dark (2026-11-18), `ai_studio.md` §14 J2c. TOF-4b the chat tool, the Needs row and the purge rule (3765): 🔨 BUILT, dark (2026-11-19), `ai_studio.md` §14 J2d | None |
| 5. Connected systems | A skill uses an approved MCP tool, then a Zoho action (3426) | MCP rows reach the catalog (B1b-2). `action_tool` gives None for an `mcp:` or `integration:` id. B1b-3 and B1b-4 are in flight | TOF-5a MCP actions (3423). TOF-5b integration actions (3424), after IN-3 (3176). TOF-5c a direct provider request (3425) | 3176 is OWNER-GATE. 3425 is a decision. `api_connections.md` D-API-2 asks to replace 3425 with a closed operation list, with no wait for IN-3 |
| 6. Sandbox code tools | The chat builds, runs and keeps a tool that no chain can do | No SBX code | SBX-1 to SBX-11 (umbrella 810). SBX-12 to SBX-17 (3427 to 3432) | SBX-G9 to SBX-G11 for production |

§12 named tasks 1980 to 1985 for SBX-12 to SBX-17. Those tasks never
existed on the board. Tasks 3427 to 3432 replace them.

The spec of the failure store of rung 2 is `tool_failure_log.md` (TOF-2a,
task 3416).

### Persistence and loading

- **Where a tool lives.** A saved skill is a `studio_skills` row with
  immutable `studio_skill_versions` rows, under FORCE RLS. A code tool is an
  `app_versions` row (§7.1). This adds no store.
- **Who can use it.** First the member who saved it. Then the whole org,
  after a holder of `studio:publish` approves one version (TOF-4a, SBX-Q3).
  Every org gets a tool only as first-party code, through a reviewed pull
  request (§6.8). Nothing copies from one org to another.
- **How it loads.** The one chat path: `_apply_tool_packs`, the F3 skill
  packs, then `find_tools` (D222). The chat loads a skill as it loads a
  code pack. This adds no loader.

### Two merges, DECIDED (D225)

The owner approved both on task 3394 on 2026-11-15. `work_plan.md` D225
records them.

1. **One finder.** D187 planned `find_tool` (§6.1). D222 plans `find_tools`
   (`one_assistant_context.md` §4.2.9). The chat has one tool, `find_tools`.
   On no match, it does the rest of §6.1. It checks the library and the
   catalog actions, and asks the one decide question. Then it writes the
   `tool_gaps` row. It says if a chain can do the job, or if a new tool is
   necessary. SBX-1 builds no second finder. The guards SBX-F7, SBX-F18 and
   SBX-F23 bind the no-match path. The SBX-1 audit decides how the path keeps
   the `tool_scope` gate.
2. **One failure store.** Task 1793 (the tool failure log) and IR-4 (issue
   reports, epic 475) each planned a store. Each grouped errors by
   fingerprint into one task. They have one store and one fingerprint now,
   and both write to it.
   TOF-2a (task 3416) writes its spec with IR-0 (task 555). D126 and SBX-Q8
   bind its consent rules.

### Out of scope

- How the chat sends a turn to an attached MCP server, the description of
  the server, and its `mcp.call` log line. WS-CSY owns them (epic 3390,
  `agent_architecture.md` §12.2).
- The tool search pilot (task 3190). On 2026-11-15 the model called
  `find_tools` on 0 of 3 Studio chats, because it took the pack loader. So
  TOF-1c matches a skill on the server, and rung 1 does not wait for the
  model to search.

---

## 0c. Amendment, 2026-11-19 (D227)

**The ask.** On 2026-11-19 (IST) the owner answered the question "Should I plan the
sandbox build by milestones, including the HathiLabs-only pilot on our own
machine?". The owner wrote:

```text
"Go ahead with your recommendations. Start with all of the accounts since all of our customers are pilots. All of them can test out. I hope you've taken proper consideration of the architecture to be the most robust possible when it comes down to this."
```

The recommendations were three. First, do not lift D85 on the production
gateway box. Second, build the real sandbox of this spec. Third, run an
optional pilot for the HathiLabs org on the home server. The agent reads the
owner's choice as every org in place of the pilot. `work_plan.md` §3 records
it as D227.

**The second ask.** Later on 2026-11-19 (IST), in the same chat, the owner wrote:

```text
"with the orchestrator for the entire CI/CD session running separately, you should prioritize getting the sandbox plan done because it will greatly improve the functionality of the chat, right? Ensure that this is also prioritized in today's build and is finished by morning, and do whatever is needed to get it live and working."
```

The agent reads the second quote as reordering D224 rung 6 ahead of rungs 1
to 5. The agent asks the owner to confirm this on task 3786. The owner's
answer on the gates and on a narrower first version is pending on task 3786.
This spec records no answer to either.

### What production shows

The agent read two facts on production at about 21:50 UTC on 2026-11-18.
`SELF_SERVE_SIGNUP_ENABLED` is `true`. `TOOL_RESULT_SPILL` is `shadow`.
Anyone can sign up, so an org that nobody chose can exist. Because of this,
the entry `*` is not allowed now, for `tools` and for the compute run.

### What changes

1. **SBX-9 and gate SBX-G1 cover the beta orgs.** The owner wrote "all of our
   customers are pilots". The agent reads this as every beta org. The entry
   is an explicit list of today's beta orgs, in place of `tools:<Fracktal>`.
   A new org joins only by an owner act. The entry `*` is allowed only while
   self-serve signup is off. D179 holds the same stance for Studio. The
   owner's words replace ring 1 of `engineering_practice.md` §2 for this
   target (§4.3).
2. **The home-server pilot is withdrawn.** The agent reads the choice of the
   owner as this withdrawal. The reason is D187 decision 5: member data stays
   with the hosting provider that processes it today. The home server is a
   residential PC. It also runs CI and the agent lanes. So no customer code
   and no customer data runs there. The first production run is on the
   dedicated gVisor box of §4.6. The agent asks the owner to confirm this on
   task 3786.
3. **D85 stands.** The gateway box still runs no shared-agent code on the
   host. D227 lifts nothing and opens no owner gate. The owner still does
   SBX-G1, SBX-G6, SBX-G9, SBX-G10, SBX-G11 and SBX-G13. The owner also does
   WS43-G1 to WS43-G5.
4. **What SBX-9 must add for a list.** The parser takes a list and `*`.
   `parse_maf_coding_scope` in `sandbox_broker.py` accepts `<target>:<org>`
   and `<target>:*`. But `tools` is not in `MAF_CODING_TARGETS`
   (`sandbox_broker.py:104`). The parser raises `ValueError` on an unknown
   target, and the check then turns **every** target off. So an early
   `tools:` entry would stop `code_task`, `app_builder` and `projects` on that
   box. SBX-4 adds `tools` to the set. SBX-18 adds `compute` to it.
   Fence SBX-F24 pins the parser. The SBX-9 checklist pins the deployed
   build.
5. **Other per-org flags that SBX-5 and SBX-6 need.** A stored tool is a
   Studio package. So `PACKAGE_HOST_ORGS` and `STUDIO_ORGS` must hold the
   org. `PACKAGE_HOST_ORGS` takes `*` (`package_host/flags.py`). D179 item 2
   names `STUDIO_ORGS=*`, and this slice did not verify its parser. Each flip
   is a separate `enforcement-flip` act. `tool_scope` is a key of an agent
   config file and not a flag.

**The SBX-9 checklist.** SBX-9 does each step, in this order.

1. Read `/version` on the gateway box. Confirm that the deployed SHA holds
   SBX-4, so `tools` is in `MAF_CODING_TARGETS`. Do this before each flip.
2. Confirm the flags of item 5 hold each org of the list.
3. Run the HathiLabs smoke of rung 6f, and read its pass test.
4. Flip `tools:<org>` for each beta org of the list.
5. Use `tools:*` only while self-serve signup is off
   (`SELF_SERVE_SIGNUP_ENABLED`, `routes/signup.py`).
   While signup is on, a new org joins the list only by an owner act.
   Or SBX-6 excludes a trial org. A stranger who signs up gets no code sandbox.
6. Before the SBX-G13 flip, read `/version` too. Confirm that the deployed
   SHA holds SBX-18. An early `compute:` entry turns every target off, as an
   early `tools:` entry does.

### How the design holds when code runs for many orgs

Each layer below exists in this spec. D227 adds none, except the control of
residual risk 1 and the two gaps that follow the list.

- **A separate box, with gVisor `runsc` only.** The box needs no KVM, and the
  broker refuses `runc` in production mode (§4.6, SBX-F21).
- **A private network only.** The daemon of the box has no public address
  (§4.6, SBX-G10).
- **No secret enters the sandbox** (§5.4, SBX-F4).
- **The code acts as the member.** It uses one door, and the member's own
  checks apply (§5.1, §5.2, SBX-F1, SBX-F6).
- **A read runs, and a write waits for a card** (§5.3, SBX-F3).
- **The network stays shut, except a profile** (§5.5, SBX-F5).
- **Prompt injection** gets the rules of §5.7. The same limits bind a
  tool that an injected prompt builds.
- **Limits for each step, and caps** for each org and each box (§5.6). The
  hard cap of D19.3 stops a step for an org with no credits (§10).
- **A short-lived sandbox for each thread, and immutable versions** (D187
  decision 1, §7.2, SBX-F10).
- **Review before another member runs a tool** (§8.1, SBX-F13).
- **Kill switches** (§8.3) and **audit** (§8.4, §9.1, §10).

**Residual risk 1: a gVisor escape across orgs.** No slice closes the escape
itself. The box holds no long-lived secret and no tenant data at rest. Only
a private network reaches it. The egress goes through the proxy of WS-43g.

But one box serves more than one org. Code of org A escapes gVisor while a
step of org B runs on the same box. The code then takes the run capability
of org B (`METORITE_API_TOKEN`, §5.1, valid up to 15 minutes) or the input
data of that step. With that capability, it acts as a member of org B
through the door.

Two controls are possible. Control (a): the egress proxy stamps the source
container, and the door refuses a capability from another container. The
proxy runs on the same box, so a host-level escape can reach it. Control (b):
the box runs one org at a time. The agent recommends (b), because it holds
even when the proxy falls.

**What control (b) is.** SBX-15a builds it as a lock of one org for each
remote box, across every target. The broker counts the org label of the
containers on the remote daemon. A start for a second org waits or answers
"the sandbox is busy". SBX-18 starts compute containers through the same
start path, and `compute_max_total` defaults to 1.

The cost is waiting. An org waits behind the run of another org, for 900 s
at most (§5.6). One box then serves fewer orgs in an hour. More boxes buy
the capacity back, and that is an owner act (SBX-G9).

**What control (b) does not stop.** Control (b) stops an escape that runs at
the same time as the step of another org. It does not stop an escape that
stays on the box after its step ends. Such an escape can wait for the next
org.

The real controls for it are a guest kernel for each sandbox (§4.4, a
microVM) or a clean box between orgs. **This is an OPEN RISK for the
owner.** The owner decides on task 3786. No slice builds either control yet.

**Residual risk 2: the price.** SBX-7 meters each step before SBX-9, so a
runaway org is metered. D130 item 4 takes the recommendation of SBX-Q4. The
credit price itself is the owner's act (SBX-G6). SBX-7 refuses a step when
the `sandbox` price row is missing (SBX-F16). SBX-G6 is a gate of SBX-9.

**Two new gaps for "many orgs at once".** Both come from the text of §6.2
and §8.3.

- **Gap A. No switch turns off one org.** `MAF_CODING_SCOPE` without
  `tools:` stops all orgs at once. A problem in one org needs a flip of the
  whole target. SBX-6 takes it. It adds a setting `user_tool_disabled_orgs`.
  The list wins over the scope, and it takes `*`. One hot switch then stops
  one org or all orgs. The name ends in `_ORGS`, so a SIGHUP reloads it
  (`docs/flag_reload.md`). `MAF_CODING_SCOPE` and `user_tool_runs_enabled`
  need a restart. A restart ends the chat runs in flight.
- **Gap B. The box is first come.** `user_tool_max_total` defaults to 1, and
  at the cap a step answers "the sandbox is busy" (§6.2). With many orgs on,
  one slow run blocks every other org. SBX-15b sets `user_tool_max_total` from
  the measured size of the box, after SBX-8 measures the cost of gVisor. It
  keeps `user_tool_max_per_org` below it. There is one per-org cap, and its
  name is `user_tool_max_per_org` (§5.6, §6.2). The per-org cap that the
  broker adds for the generic path is this cap. No slice adds a second name.

### Rung 6a′: the compute run (owner priority, 2026-11-19)

The owner asked that the chat run code soon. The owner wrote "finished by
morning" and "do whatever is needed to get it live and working". Rung 6a′ is
the smallest path that does this. It sits between 6a and 6b, and the names 6a
to 6f stay as they are. SBX-18 builds the `compute` broker target. SBX-19
builds the `compute_run` chat tool.

**What 6a′ does not do.** It has no door. The code reads and writes nothing in
Metorite. It has no network and holds no secret. It saves no tool. Those stay
in rungs 6b to 6f.

**Robustness.** 6a′ is a strict subset of §5. Rule 4 (no secret) and rule 5
(network closed) hold with no exception. Rules 1 to 3 do not arise, because
the code has no door.

The inputs are `tr_` refs of tool results that the turn already holds. The
model never retypes rows. The output is framed as untrusted text (§5.7).

**The scope seam.** `compute` goes in `MAF_CODING_TARGETS`. The flip is
`compute:<org>` in `MAF_CODING_SCOPE`, the one scope seam. The setting
`COMPUTE_RUN_ORGS` is the hot kill switch only.

`COMPUTE_RUN_ORGS` reloads on SIGHUP, and it stops one org or all orgs. It
opens nothing alone. The flip is gate SBX-G13.

SBX-G13 has the gate id `ws43-sandbox-flip`, which the dev-phase window does not
open (`maf_coding_engine.md` §12). `compute:` and `COMPUTE_RUN_ORGS` take the
explicit list of today's beta orgs. They take `*` only while self-serve
signup is off.

**Residual risk.** Residual risk 1 applies to the in-flight input data of
another org. Control (b) carries it, through SBX-15a. SBX-18 starts compute
containers through the same lock, and `compute_max_total` defaults to 1. The
open risk of an escape that stays on the box applies here too. The owner
decides it on task 3786.

**Production gates.** The flip needs SBX-G9, SBX-G10, SBX-G11, SBX-G13,
WS43-G2 and WS43-G5. It also needs WS43-G1 (BH-3, the broker's own unit,
D127.2, §4.6) and a price answer (SBX-G6). The owner decides on task 3786
between two choices. Choice one: build BH-3 before the 6a′ flip.

Choice two: waive BH-3 for rung 6a′ only. This amends §4.6 ("BH-3 still
binds") for that rung. The key to the box then sits in the gateway unit.
The cost is this: a gateway compromise gets root on the sandbox box. 

The box
holds no long-lived secret and no data at rest. The waiver never reaches
past 6a′. BH-3 is unconditional for 6e and SBX-9. The agent does not decide
this.

**The price.** SBX-19 writes a `usage_event` at price 0 through the one meter,
if that is one call. If it is not, SBX-7 does it. Whether beta use is free is
the owner's call on task 3786 (D130 item 4). The recommendation is free,
with the daily cap.

**The input rule.** SBX-19 holds a `KeepForCompute` step. While compute is on
for the org, it keeps a ref for every plain-text tool result of the turn.
The size runs from 1 character to 2 MB. It does not depend on
`TOOL_RESULT_SPILL` or on the threshold of 24,000 characters. Eviction works
for each owner, so one org cannot evict the refs of another.

**The pass test.** The HathiLabs smoke has eight checks. A pandas run with a
CSV and a PNG shows a card. A short tool result (under 24,000 characters)
gives a ref, and the run reads it. A socket probe fails.

`os.environ` shows no secret. A foreign ref answers "not found". A `while
True` loop stops at 30 s. A second org does not see the tool. Emptying the
flag `COMPUTE_RUN_ORGS` stops the next call.

### The ladder: sub-rungs of rung 6 (D211)

The sub-rungs 6a to 6f split rung 6 of D224. They are not the company
milestones M0 to M4 (D211 item 8). The plan runs backward from SBX-9, as
`orchestration_playbook.md` §6d says. Each rung ends in a demo.

A rung that runs on the dev stack ends in a dev-stack preview for the owner
(D145 step 1). The production rungs are 6a, 6a′ and 6f. SBX-5 depends on
SBX-3 (§12), so SBX-3 sits in 6c. SBX-8 and SBX-15a depend on the broker
only. So 6a′ can reach production before 6b and 6e.

| Rung | The demo | Slices | Gate |
|---|---|---|---|
| **6a. The gap log on production** | The owner opens the gap view on production and sees real counts. No code runs | SBX-1 | `enforcement-flip` of `user_tool_find_enabled`, under a dated grant |
| **6a′. The compute run** | In the chat, a member asks for a calculation on rows that a tool returned. The chat runs pandas code in a sealed container and shows a CSV or PNG card. This is the first rung that runs code in production | SBX-8, SBX-15a, SBX-18, SBX-19 | SBX-G9 to SBX-G11, SBX-G13, WS43-G1 (BH-3, or the waiver), WS43-G2, WS43-G5, SBX-G6 |
| **6b. The door and a tool (dev-stack preview)** | The chat builds a read-only tool. It reads two pages through the door, and passes its smoke test | SBX-2, SBX-13, SBX-4, WS-43g (egress proxy, not built) | None |
| **6c. Keep and reuse, and a write behind a card (dev-stack preview)** | The chat saves the tool, and a new thread reuses it. A tool that writes shows a card first | SBX-3, SBX-5 | None |
| **6d. Repair, review and metering (dev-stack preview)** | A failed tool gets one repair. A holder of `studio:publish` approves a version. The owner sees credits fall. A step with no `sandbox` price row is refused | SBX-12, SBX-6, SBX-7 | None |
| **6e. The tools half of the remote backend (dev-stack preview)** | A build, a smoke test and a run of a tool go to a second local daemon under `runsc`. A second org waits while the first holds the box | SBX-15b, BH-3 (unconditional) | The install of BH-3 on the box is WS43-G1 |
| **6f. Production on the dedicated box, the beta orgs** | The HathiLabs org runs a tool on the real box. The same day, `tools:<org>` opens it to each beta org of the list | SBX-9 | SBX-G1, SBX-G6, SBX-G9 to SBX-G11, WS43-G1 to WS43-G5 |

- **The pass test of 6f, before the second flip.** One `tools` run returns
  the right output. The run log shows `runsc`. A run of a second org waits
  or answers "busy" while the HathiLabs run holds the box. Then the owner flips the
  list.
- **The HathiLabs run is verification.** It proves the real box. It is not
  a narrower pilot. The agent reads "the same day" as this order. Both flips
  are owner acts.
- **Rung 6 runs first (the second ask).** Rung 6a does not wait for rungs 1
  to 5. The agent reads the second ask as putting rung 6 ahead of them.
  SBX-1 needs no owner act except the flag flip of 6a.

### The critical path to SBX-9

Statuses were read from `origin/main` at `fe470c832` on 2026-11-19. A gate
that lives on a box is "not verified", because this slice reads no box.

| Needs | Status | Evidence |
|---|---|---|
| SBX-1 | Not built | `git grep tool_gaps` finds no code or SQL |
| SBX-2 | Not built | `git grep SANDBOX_ROUTES` finds nothing |
| SBX-3 | Not built | Needs SBX-2. `git log --grep SBX-3` finds no build commit |
| SBX-4 | Not built | `MAF_CODING_TARGETS` holds no `tools` (`sandbox_broker.py:104`) |
| SBX-5 | Not built | `git log --grep SBX-5` finds the spec commit only. Its input B7 is built |
| SBX-6 | Not built | Its inputs are partly built, see PKG-11 and J2 below |
| SBX-7 | Not built | No `sandbox` row in `task_catalog` (`git grep` of the SQL) |
| SBX-8 | Not built | `git grep runsc` finds no code |
| SBX-12 | Not built | Needs SBX-5 |
| SBX-13 | Not built | `git grep metorite_door` finds nothing |
| SBX-15a, the remote backend and the org lock | Not built | `git grep docker-remote` finds nothing |
| SBX-15b, the tools half | Not built | Needs SBX-4 and WS-43g |
| SBX-18, SBX-19, the compute run | Not built | `git grep compute_run` finds nothing |
| BH-3, the broker unit (WS43-G1) | Not built. The install on the box is an owner gate, not verified | `git ls-files` lists no `acb-sandbox-broker` unit, and `tests/unit/test_sandbox_broker_socket.py` is absent. `box_hardening.md` §5 BH-3 specifies both |
| WS43-G2 | Owner gate. Image code built, box build not verified | WS-43b and WS-43y1a are on `main` |
| WS43-G4 | Owner gate. Not verified | `maf_coding_engine.md` line 827 |
| WS43-G5 | Owner gate. Task 3912 installed `runsc` on `aroh-sandbox-1` (2026-11-23). Production use is not verified | `maf_coding_engine.md` line 1677. `docs/gcp_aroh.md` §4a |
| WS-43g, the egress proxy | Not built | `maf_coding_engine.md` line 2162 says `grant_egress()` is not built. `git grep grant_egress` finds no code |
| `ai_studio.md` B7 | Built, dark (2026-11-13, task 1414) | `ai_studio.md` line 977 |
| `package_host.md` PKG-11 | Partly built | 11a and 11b-2 built dark. 11b-1 is "not built for now" (path A) |
| `ai_studio.md` J2 | Partly built | J2a, J2b-1, J2b-2, J2b-4a, J2b-5 built dark. J2, J2b, J2b-3, J2b-4, J2b-4b open (`ai_studio.md` lines 1277 to 1288) |
| SBX-G9, buy the box | **Done (D231, 2026-11-23).** The owner approved `aroh-sandbox-1` on GCP on task 3912 | §4.6. `docs/gcp_aroh.md` §4a |
| SBX-G10, the private network | Owner only. Not done | §13 |
| SBX-G11, `sandbox_backend=docker-remote` | Owner only. Not done | §13 |
| SBX-G13, the `compute:` flip | Owner only. Not done | §13. Gate id `ws43-sandbox-flip` |
| SBX-G6, the credit price | Owner only. Not done | §13. No `sandbox` row in `task_catalog` |
| SBX-Q4, the credit price call | Decided: D130 item 4 takes the recommendation | The price itself is SBX-G6 |

### New items

- **SBX-6 adds Gap A.** The setting `user_tool_disabled_orgs`, which takes
  `*`, and a test for it in SBX-F24.
- **SBX-15a adds control (b).** `docker-remote` and one org for each remote
  box, across every target.
- **SBX-15b adds Gap B.** `user_tool_max_total` from the measured box, and
  `user_tool_max_per_org` below it.
- **SBX-7 refuses a step** when the `sandbox` price row is missing. SBX-F16
  holds the test.
- **SBX-9 adds** the checklist above. **SBX-F24**
  (`tests/unit/test_sandbox_scope_all_orgs.py`, §11) fails when `tools:*`
  parses to an error, when an org in `user_tool_disabled_orgs` still runs, or
  when `tools` is absent from `MAF_CODING_TARGETS`. It is advisory until
  SBX-6 and SBX-9 build it.

---

## 0. The answer

**Build the full capable sandbox as a new target of the WS-43 sandbox
broker, not as a second sandbox.** The broker, the image, the safe opener
and the scope setting are built and dark. The egress proxy is specified
(WS-43g). A second sandbox would be a second seam, which CLAUDE.md §4 calls
a defect.

**The code acts as the member who asked, and never as more.** It reaches
Metorite through one door, with a short-lived run capability. The door applies
the member's own permission checks and FORCE RLS (R5). A read runs at once. A
write waits for the member's card. No provider token and no platform key
enters the sandbox.

**A tool is stored only after it worked.** The assistant first checks its
own tools and the registry. It builds a tool only when both come back empty.
A tool that passed its smoke test and its first real run becomes a private
registry row. Another member can use it only after a person approves it.

**(D187) The registry is the Studio library.** A stored tool is a private
AI Studio package, and a version is a row of `app_versions` (§7). Before a
build, the assistant also checks whether Studio can do the need with no
code. A tool that fails gets one repair (§6.7).

**Runtime.** Slice 1 runs on the broker we have, under gVisor where the box
has it, with no spend and no third party. SPACE cannot run on our boxes, and
it is 18 days old. Scale moves user code off the gateway box to hosted
microVMs with an E2B-compatible API. SPACE's E2B wrapper is the exit to a
self-hosted microVM host later, with the same SDK. §4 gives the comparison,
and §14 lists the calls that cost money.

**(D187) Production runs on a dedicated sandbox box first.** A separate
small VPS runs the broker's Docker backend under gVisor (§4.6). Hosted
microVMs stay the alternative.

---

## 1. The owner tasks, mapped

| Task | Title | Where this spec answers it |
|---|---|---|
| #234 | Spec tool-gap detection — decide nothing exists before a sandbox spins up | §6.1, fence SBX-F8 |
| #235 | Design the user-tool registry schema — where and how tools are stored | §7, fences SBX-F9 and SBX-F10 |
| #236 | Provision an on-demand sandbox — isolated execution with limits | §4, §5.6, §6.2, fence SBX-F11 |
| #237 | Build the missing tool in the sandbox — write code, install deps, smoke-test | §6.3, fence SBX-F12 |
| #238 | Run the built tool to finish the user's work | §6.4, §5, fences SBX-F1 to SBX-F6 |
| #239 | Persist the tool on success — store only tools whose run succeeded | §6.5, fence SBX-F9 |
| #240 | Reuse stored tools before rebuilding — look up the registry first | §6.1, §6.6, fence SBX-F8 |
| #241 | Add safety and review controls — permissions, approval gate, versioning, disable | §5, §8, fences SBX-F13 and SBX-F14 |
| #242 | Instrument and document the flow | §9, fence SBX-F15 |

---

## 2. What exists today (measured 2026-10-19)

Read at `main` `daad004f9`. Each row is a seam that this spec reuses. No
row is rebuilt.

| Piece | Where | What it does today | State |
|---|---|---|---|
| The four shell tools | `packages/acb_skills/acb_skills/manifest.py:72`, `SHELL_TOOLS` | `code_task`, `run_script`, `install_dependency` and `run_command`. An agent that holds one is tier T2 (`manifest.py:289`) | Live |
| The D85 withhold | `orchestrator/_tool_injection.py:235`, `_withheld_shell_tools` | A shared agent loses all four tools unless the broker covers it. The log line is `executor.shell_tools_withheld` (`:2128`). Since D129 a personal agent loses them too. Only the root `metorite` agent keeps them | Live (D85, D129) |
| The no-egress rule | `_tool_injection.py:2619`, `_apply_no_egress`, and `acb_skills/egress.py` | A covered run, and each run under it, holds only tools that say `open_world=False`. The log line is `executor.egress_tools_withheld`. `egress.mark_untrusted_session` marks a session that read untrusted content | Live (H-236) |
| `run_script` on the host | `acb_skills/code_tools.py:204` | `subprocess.run` in the gateway's process tree, with the network and the agent's declared credentials. Its docstring says "process-level hygiene, not a container sandbox" | Live for the root `metorite` agent only (D129) |
| The sandbox broker | `apps/services/orchestrator/orchestrator/sandbox_broker.py` (2330 lines) | The one module that runs `docker`. One container per organization, agent and thread. `--network none`, a read-only root, no capabilities, a non-root uid, CPU, memory and process limits, an image pinned by digest. The registry is in memory, in the gateway process | Built, dark (WS-43c) |
| The scope setting | `acb_common/settings.py:1040`, `maf_coding_scope` | A comma list of `<target>:<org>`. Targets in code: `code_task`, `app_builder` and `projects`. Empty turns every target off | Built. The default is empty |
| `run_command` | `acb_skills/sandbox_tools.py:405` | Runs one command in the broker container. Only `ProjectsSandboxProvider` (`:644`) attaches it, for one run | Built, dark (WS-43d) |
| User-written skills | `agent-data/skills/` through `acb_skills/tenant_file_store.py` | MAF `SkillsProvider` loads them in a covered run. A skill loads only for the member who wrote it (`tests/unit/test_sandbox_skill_privacy.py`) | Built, dark |
| The egress proxy and the grant | `maf_coding_engine.md` §7.3, slice WS-43g | An approved per-host grant, an organization network and one proxy. `request_network_access` is a stub that answers "off". No `egress_proxy.py` exists | **Spec only** |
| The sandbox image and its CI | `apps/services/orchestrator/Dockerfile.coding-sandbox`, `.github/workflows/sandbox-docker.yml` | A pinned image with pandas, PDF and Office libraries and no secret. The workflow runs `pytest -m sandbox_docker` on each pull request and fails on a skip | Built (WS-43b). Not on any box (WS43-G2) |
| BH-2, the gateway unit sandbox | `deploy/hostinger/acb-gateway.service.d/50-hardening.conf:45` | systemd hardening of the gateway. It makes `/run/docker.sock` inaccessible to the gateway, so the broker cannot be healthy on production | Live (WS-49, #758) |
| BH-3, the broker's own unit | `box_hardening.md` §5 BH-3 | A separate `acb-sandbox-broker.service` with its own user and a local socket | **Spec only**. Gate WS43-G1 |
| The app sandbox | `workbench/control_plane/src/components/SandboxedHtml.tsx`, `src/lib/theme/sandbox-frame.ts` | A browser iframe with `sandbox="allow-scripts"`, an opaque origin and `connect-src 'none'`. Custom Apps call the API through a parent bridge as the viewer. No server code runs | Live. Not reused here: it runs no server code |
| Self-mutation | `orchestrator/mutation.py:345` | Platform code changes in their own container, first-party org only, with a person's approval before a push | Live for the first-party org. Not reused here: it changes platform code, not tenant tools |
| The confirmation card | `acb_skills/ask_tools.py:845`, `request_confirmation` | One card, many rows if needed, `non_interactive_default="deny"`. It fails closed with no channel. "Not now" is a decline | Live |
| The `via` stamp | `routes/projects/core.py:66`, and `record_activity` at `:3142` | `X-Actor-Via` lands in `meta.via`, and `created_by` stays the member (D-PM-36) | Live |
| The audit seam | `packages/acb_audit/acb_audit/log.py`, table `audit_event` | `AuditEvent(actor, action, target, payload, organization_id)` and `record()` | Live |
| Member identity for outside agents | `acb_auth/deps.py:443` (branch 0), `acb_auth/agent_tokens.py`, `infra/postgres/248_agent_access_tokens.sql` | Opaque tokens stored as SHA-256 in the RLS-exempt `agent_token` table. A signed request names a connection row, and the gateway reads the member from that row. `MCP_ROUTES` and `MCP_WRITE_ROUTES` (`agent_oauth.py:548`, `:567`) are the route allowlists | Live (WS-55, D97) |
| Member identity for in-process skills | `skill-projects/client.py:100` | A skill sends `GATEWAY_INTERNAL_TOKEN` and `X-User-Email` (branch 1a). That bearer can act as any member, so it must never enter a sandbox | Live |
| Credits | `apps/services/customer_console/customer_console/store.py:1112`, `record_usage`, and `credits.py:606`, `decide_spend` | `usage_event` has `task`, `quantity` and `unit`. `task_catalog` is an allowlist with no sandbox task. `POST /usage/record` passes token fields only | Live for AI calls. Sandbox time has no meter |
| The SSRF guard | `apps/services/gateway/gateway/outbound_guard.py` | One `is_global` check with a pinned address, for the gateway's own outbound calls | Live |
| A user-tool registry | — | No `user_tool`, `custom_tool` or per-member tool table exists | **Absent** |
| The Studio package store (D187, read 2026-11-07) | `gateway/studio/packages_store.py`, tables `apps` and `app_versions` | A package is a row of `apps` with `format = 'manifest'`. `publish_version` (`:709`) and `rollback` (`:752`) append `app_versions` rows and never change one | Built, dark (`ai_studio.md` A3, A4). The library of D187 |
| Installs and scopes (D187, read 2026-11-07) | `gateway/package_host/store.py`, table `package_install` (`324_package_registry.sql`) | One install of one package at `private`, `group` or `org`. A `system` package is a file and has no row | Built, dark (PKG-4, PKG-5b) |
| The share permission (D187, read 2026-11-07) | `acb_auth/permissions.py:198` | `studio:publish` is a capability. No code names `tools:approve` | Built. No route reads it yet (J1-1) |

**What the table shows.** The sandbox is built and dark, and production
cannot run it yet. Three pieces block a production run: the Docker socket
that BH-2 hides, the egress proxy of WS-43g, and the image on the box
(WS43-G2). This spec adds the lifecycle, the door and the registry. It
waits on those three for production.

---

## 3. Scope and non-goals

### 3.1 In scope

1. Tool-gap detection: the checks that must come back empty before a build
   starts (#234).
2. The user-tool registry: the Studio package store, two tenant tables and
   one lookup table (#235, D187).
3. A sandbox for one build or one run, from the existing broker, with limits
   (#236).
4. The build: code, pinned packages and a smoke test, in the sandbox (#237).
5. The run: the tool does the member's work, as the member (#238).
6. Persist on success, and only on success (#239).
7. Reuse before rebuild (#240).
8. The review controls: approval, versions, pins, disable and revoke (#241).
9. Logs, a per-run row, the tool-gap counter and the member guide (#242).
10. The run capability and the sandbox API door (§5).
11. A backend seam inside the broker, so a run can go to a hosted microVM
    service later (§4.4).
12. Repair of a tool that fails, at three levels (§6.7, D187).
13. The one path from a tool of a tenant to a platform tool (§6.8, D187).
14. The dedicated sandbox box for production (§4.6, D187).

### 3.2 Non-goals

- **A second sandbox, a second proxy or a second confirmation card.** Each
  one exists. This spec extends them.
- **A provider token in the sandbox.** The code reaches Zoho, Gmail and
  Microsoft Graph only through the Metorite routes that front them (§5.4).
- **Code that runs with no person to ask.** A workflow or a background job
  cannot build a tool, and cannot pass a write card. A stored tool may run
  there only for reads, after SBX-Q5.
- **A tool marketplace across organizations (D187).** A running tool never
  leaves its organization, and nobody copies it into another one. A tool
  reaches every organization only as reviewed first-party code in the
  repository, with the consent of the source organization (§6.8). That is a
  `system` install at a release, not a marketplace.
- **A sandbox for each member (D187).** A sandbox serves the steps of one
  chat thread and then stops (§6.2).
- **A change to platform code at run time (D187).** A failed built-in tool
  gets a reviewed PR, never a live patch (§6.7, level c).
- **P5-c, P5-d, P5-b.3 and the pooled cutover.** D83 keeps them parked. A
  user-tool run is a T2 container that runs one command set. It is not an
  agent run in a container.
- **A UI to edit tool code by hand.** A member asks the assistant for a new
  version.
- **Delete and grant writes.** D-PM-35 and D-PM-40 stand. The door refuses
  them (§5.3).
- **Self-hosting SPACE now.** §4.3 gives the reason, and §4.5 the trigger to
  look again.

---

## 4. The runtime

### 4.1 The measured fact that decides most of it

Production `srv1914284` and staging `srv1747539` are KVM guests
(`systemd-detect-virt` answers `kvm`). Each has 2 vCPU, about 7 GB of RAM, no
`/dev/kvm`, and no `vmx` or `svm` CPU flag. Measured 2026-10-19.

So no microVM runs on either box. Firecracker needs `/dev/kvm`, and SPACE's
only implemented runtime is Firecracker (SPACE `docs/execution-environments.md`).
A microVM needs a new KVM-capable host, and a new host costs money. Money is
an owner decision (CLAUDE.md §3a rule 3).

### 4.2 The four options

| | (a) SPACE, self-hosted | (b) Hosted, E2B-compatible | (c) gVisor on our boxes | (d) The WS-43 broker as built |
|---|---|---|---|---|
| **What it is** | Firecracker microVMs on a KVM host, run by SPACE on Kubernetes | E2B cloud microVMs (Firecracker), driven by the E2B Python SDK | The same broker, with Docker's `--runtime=runsc` (gVisor, systrap platform) | Docker `runc` containers from `sandbox_broker.py` |
| **Isolation** | Strong. One guest kernel per sandbox | Strong. One guest kernel per sandbox, on someone else's hardware | Medium. gVisor serves the syscalls in user space, so the host kernel sees a small surface | Weakest. One shared host kernel (WS43-S1) |
| **Fits a box with no KVM** | No. It needs a new bare-metal or nested-virt host | Yes. Nothing runs on our box but the SDK call | Yes. systrap needs no KVM | Yes |
| **Where member data goes** | Our host | A third party (E2B) | Our box | Our box |
| **Ops load** | High. Kind, PostgreSQL, S3, about 8 DaemonSets, btrfs, 50 GiB disk floors, a Rust build. Version 0.8.0, no tag, about 100 commits in 4 days | Low. An account and a key | Low. Install `runsc` and register the runtime (WS43-G5) | None more. Built and dark |
| **Shares the gateway's 2 vCPU** | No | No | Yes | Yes |
| **Cash cost (estimate)** | A bare-metal host. A small dedicated server is about EUR 40 to 70 a month. The SPACE EKS reference uses `m5d.metal`, about USD 4,000 a month on demand | Pro is USD 150 a month plus use. Use is USD 0.000014 per vCPU-second and USD 0.0000045 per GiB-second. A 60-second run at 1 vCPU and 1 GiB is about USD 0.0011 | USD 0 | USD 0 |
| **Built here** | Nothing | Nothing | Named as later hardening (WS43-S1, gate WS43-G5) | WS-43b, WS-43c, WS-43d built. WS-43g spec only |

The SPACE facts come from the repo on 2026-10-19: Apache-2.0, created
2026-10-01, no release, no tag, 10 open issues and 88 open pull requests.
`docs/installation.md` asks for "an apt-based Linux x86_64 host with KVM".
The E2B prices come from `e2b.dev/pricing` on 2026-10-19. Every cash figure
in this table is an estimate. The spec opens no account and spends nothing.

### 4.3 Recommendation for slice 1: the broker we have, under gVisor

**Use option (d) now, and add (c) where the box has `runsc`.** Reasons:

1. **No spend and no third party.** Member data from the CRM and Projects
   stays on our box. A hosted sandbox makes its vendor a processor of that
   data. That is a DPDP question (WS-37), and nobody has answered it.
2. **Everything it needs is built or specified.** The broker, the image, the
   limits, the reaper, the safe opener and the egress proxy design are in
   `maf_coding_engine.md` §7. That spec names their fences, WS43-F1 to
   WS43-F30, and each built slice carries its own.
3. **gVisor closes most of WS43-S1 at no cost.** `sandbox_runtime=runsc`
   is the setting that WS43-S1 already names. The broker keeps `runc` as the
   default, and a box opts in.
4. **It keeps the slice narrow.** The new work is the lifecycle and the door,
   not a platform.

**What it costs.** User code shares 2 vCPU with the gateway. So in slice 1
the `tools` target holds 1 container per organization, and 1 per box
(§6.2). gVisor slows syscall-heavy work such as `pip install`. Nobody has
measured by how much on our box. SBX-8 measures it.

**What blocks production today.** BH-2 makes the Docker socket inaccessible
to the gateway, so the broker cannot start a container on production. The
broker needs its own unit first (BH-3, gate WS43-G1). The egress proxy
(WS-43g) and the image on the box (WS43-G2) also come first.

**Where it runs first.** (D227) The dev stack, then production on the
dedicated box. A run for the HathiLabs org proves the box. Then the owner
flips `tools:<org>` for each beta org (§0c). The owner's words replace ring 1 of
`engineering_practice.md` §2 for this target. Each step is an owner act
(§13).

### 4.4 Recommendation for scale: hosted microVMs through the broker

**At scale, the broker sends a user-tool run to a hosted microVM service with
an E2B-compatible API (option b).** The member, the assistant and the door do
not change. Only the backend changes. Reasons:

1. **A user's code leaves the gateway box.** A runaway build cannot starve
   the gateway. An escape lands in a microVM with the data of one step. It
   does not land on the box that holds every tenant.
2. **A guest kernel per sandbox.** That closes WS43-S1 for this target.
3. **The cost is small next to the floor.** Take 10,000 runs a month, each
   of 60 seconds at 1 vCPU and 1 GiB. At the E2B prices above, that use costs
   about USD 11, plus the USD 150 Pro fee (estimate).
4. **One SDK, two exits.** SPACE ships an E2B compatibility wrapper (ECW,
   `docs/ecw.md`). It is tested with `e2b==2.21.1`. So the same backend code
   can point at a self-hosted SPACE later, with a change of `domain` and key.

**The backend seam.** One new setting, `sandbox_backend`, with the values
`docker` (default) and `e2b`. Only `sandbox_broker.py` imports the `e2b`
package, and WS43-F1 grows to say so (SBX-F11). The broker's interface of
`maf_coding_engine.md` §7.1 stays the same: `acquire`, `exec`, `host_files`,
`grant_egress`, `revoke_egress`, `release` and `sweep`.

**(D187) A third value, `docker-remote`.** It drives the Docker daemon of
the dedicated sandbox box of §4.6, and it is the first choice for
production. `e2b` stays the alternative. The interface above does not
change.

**What the hosted backend changes.**

- The workspace is no longer a bind mount. The broker uploads the inputs and
  downloads `outputs/` through the SDK's file calls. The safe opener still
  guards every host write.
- The door must be reachable from the vendor's network. It gets a public
  name, `sandbox-api.metorite.com`, with TLS. The capability is the only
  credential, and the door trusts no source address (§5.2).
- Egress control moves to the vendor's network policy. ECW maps `allowOut`
  to default-deny. The broker sets the allow list per sandbox from the
  profile of §5.5.

### 4.5 When to look at SPACE again

Look again when all three hold:

1. SPACE has a tagged release at 1.0 or later.
2. A customer contract, or the DPDP work of WS-37, needs member data to stay
   on hardware we control.
3. The owner accepts a KVM host and the ops load of row (a).

Then SBX-11 runs our escape probes and the egress probes of WS43-F5 against
a SPACE host through ECW, before any tenant run goes there.

### 4.6 Production: a dedicated sandbox box (D187)

**The recommendation.** Production runs user tools on a separate small VPS.
That box runs the Docker backend of the broker under gVisor (`runsc`). Only
a private network reaches it. The gateway box runs no member code, which is
the aim of D127.2.

**This amends D130.1 for production only.** The dev stack and the `e2b`
alternative keep D130.1 as it is.

**Why this box comes before E2B.**

1. Member data stays with the hosting provider that already processes it.
   No new processor joins, so no new data processing agreement is due
   (D130.7).
2. The systrap platform of gVisor needs no KVM, so a plain KVM guest runs it
   (§4.1).
3. User code no longer shares the 2 vCPU of the gateway (§4.3).
4. The broker is still one module. Only the place of the daemon changes.

**How the broker drives the box.** One broker, and no second sandbox seam.

- `sandbox_backend=docker-remote` points the `docker` CLI child at the
  daemon of the box. `DOCKER_HOST` is already one of the names that a
  `docker` child gets (`acb_common/child_env.py:169`, `DOCKER_NAMES`).
- The daemon listens on the private network only, through `ssh://` or TLS
  with a client certificate. The key lives with the broker and goes to the
  `docker` child only.
- A remote daemon cannot see a bind mount of the gateway box. The broker
  binds the run-data dir today (`sandbox_broker.py:403`). On this backend it
  moves the inputs in and the outputs out through the daemon, as §4.4 does
  for `e2b`. SBX-15 picks the mechanism, for example `docker cp` into a
  volume. The safe opener still guards each host write.
- The egress proxy of WS-43g runs on the sandbox box. It forwards the door
  name `metorite-api.sandbox` to the gateway over the private network. So
  the door needs no public name.
- The box keeps no tenant data at rest. A run-data dir lives for one run, as
  on the gateway box.
- Production allows `runsc` only on this box (§13). The caps of §5.6 take
  the size of the box in SBX-15.

**BH-3 still binds.** The key that reaches the box lives in the broker's
own unit (BH-3, gate WS43-G1), not in the gateway process. So SBX-9 still
waits on WS43-G1.

**The dev stack** keeps the local Docker backend. SBX-15 tests
`docker-remote` against a second local daemon.

**The alternative.** The `e2b` backend of §4.4 (SBX-10) stays. It runs
tenant data only after a data processing agreement and a region check
(D130.7).

**What it costs.** The owner picked the plan and the place (SBX-G9, D231). This
spec quotes no price.

**Where the box is (task 3912, 2026-11-23).** The owner approved a GCP box
in place of the Hostinger KVM 2 of task 3786. `aroh-sandbox-1` is built in
`aroh-vpc`, with no external IP and no service account. This amends D187
decision 5 and D130.7 for the sandbox box only.

`docs/gcp_aroh.md` §4a holds its settings. SBX-G9 is done. SBX-G10, SBX-G11 and WS43-G1 are still open.

---

## 5. "Full capable" — what the code can reach

**The owner's question:** "Would that code have access to the CRM, the app,
and all endpoints to use as input data?"

**The answer.** Yes, as the member who asked, and through the same checks as
that member's own browser. It reads what the member can read. It writes only
what the member approves on a card. It never sees a provider token or a
platform key. Five rules make that true.

### 5.1 Rule 1 — the code acts as the member

- At the start of each build, smoke or run step, the broker mints one **run
  capability**. It is 32 random bytes. The gateway stores only its SHA-256.
- The capability maps to these values:
  - one organization, one member, one run and one thread,
  - one tool version and one phase,
  - a set of read scopes and a set of write scopes,
  - one expiry.
- The organization and the member come from the run binding
  (`executor._current_run_org()` and the run's member), never from input
  (R5e).
- The expiry is the end of the step or 15 minutes, whichever comes first. The
  end of the step revokes it.
- It is not a session cookie, not `GATEWAY_INTERNAL_TOKEN`, not an MCP OAuth
  token and not a provider token. Outside the door it opens nothing.
- It reaches the container in one environment variable, `METORITE_API_TOKEN`.
  The egress grant token of `maf_coding_engine.md` §7.3 uses the same path
  in its design. WS-43g is not built, so no such token exists in code yet.
- A thief who copies it out of the container gets little. It opens only the
  door, as that member, for the step's scopes, until it expires. Each write
  still waits for that member's card.

**This amends one sentence of D83.** D83 says "the broker gives it no key".
For the `tools` target only, the broker gives the container this one
capability. It is the owner's call (SBX-Q6), and the slices that need it
wait for that call.

### 5.2 Rule 2 — one door, and the member's own checks

- **The door** is a set of gateway routes under `/sandbox/v1/`. The code
  calls it at one fixed name. On the Docker backend, that name is
  `metorite-api.sandbox`, and the egress proxy forwards it to the gateway.
  The proxy adds no header and holds no key. On the hosted backend, the name
  is `sandbox-api.metorite.com`.
- **The identity step** is a sibling of branch 0 of `get_current_user`
  (`external_agent_access.md` §10.1). It reuses branch 0's steps 3 to 7:
  - the route must be on an allowlist,
  - the row must be live,
  - the member comes from the row, never from a header,
  - the resolved organization must equal the row's organization.
- **Its review rule is branch 0's.** Each change needs an adversarial review
  and an independent verification before it merges.
- **The allowlist** is `SANDBOX_ROUTES`, a set of (method, route template)
  pairs. It starts from `MCP_ROUTES` and adds the CRM and email read routes.
  A route that is not on the list answers 404. So "all endpoints" means every
  route that the list names, and a fence makes each new route a choice
  (SBX-F2).
- **The checks** are the route's own. The door calls the same handlers as
  the member's browser. So each permission check, each 404 for a row the
  member cannot see, and FORCE RLS at the `get_db()` seam apply unchanged.
  The door adds no SQL and no second permission model.
- **The stamp.** Each request carries `X-Actor-Via: sandbox:<tool slug>@v<n>`,
  set by the door from the capability row. `record_activity` copies it into
  `meta.via` (D-PM-36). The timeline then reads "by Priya, through the tool
  `quote-pdf` v3".
- **Limits.** 600 requests per step and 4 MB per response, both new. A page
  holds at most 100 rows, as on the MCP door. Past a limit, the door answers
  429.

### 5.3 Rule 3 — reads run, writes wait for the member

| Route class (`projects_ai_chat.md` §5.2) | What the door does |
|---|---|
| **A**, a read | Runs at once |
| **B**, a write the app can undo | Holds the request. The member's chat shows the confirmation card. On approval, the gateway runs the request as the member. On a refusal or a timeout, the code gets 403 |
| **C**, a write that is hard to undo | As B, one card for each request, with the counts first |
| **X**, excluded | 403 always. Deletes, grants, member and role writes, billing, sign-in, connector connect and disconnect, and admin routes |

- **The card shows the wire, not the model's words.** The door builds the
  card from the request it holds: the method, the route, the target row and
  each changed field. A model cannot describe a write as something else
  ("the card stopped matching the wire", WS-26d-write).
- **A batch.** The code may send many writes to `POST /sandbox/v1/batch`.
  The member sees one card with one row each, through
  `request_confirmation(rows=…)`, as `create_tasks` does.
- **No person, no write.** The card passes `non_interactive_default="deny"`.
  A run with no chat gets 403 for every write.
- **The smoke step writes nothing.** Its capability holds no write scope. A
  write there answers 403 with `smoke_is_read_only`, and the smoke report
  counts the writes the code would have made.
- **The card times out** after `sandbox_approval_timeout_seconds` (default
  300). WS-43g specifies that setting, and SBX-3 adds it if WS-43g has not.
  The wait counts against the step's time budget.

### 5.4 Rule 4 — no secret enters the sandbox

- No provider token. Zoho, Gmail, Microsoft Graph and every connector of the
  integrations registry stay in the gateway. The code reads the CRM through
  the Metorite CRM routes, and the gateway calls Zoho with the organization's
  own credential. The same holds for mail and calendar.
- No platform key: no `GATEWAY_INTERNAL_TOKEN`, no Router key, no session
  secret, no signing key and no database URL.
- The broker's existing rule stands: no `--env` that holds a secret, and no
  mount that can hold a key (`maf_coding_engine.md` §7.1 rule 5).
- The token probe of WS43-F12 extends to every provider token of the
  organization and to the five platform secrets above (SBX-F4).

### 5.5 Rule 5 — the network is closed, except what a profile opens

**Egress profiles.** Each step gets one profile. The broker sets it, and the
code cannot change it.

| Profile | Allowed hosts | Used by |
|---|---|---|
| `build` | `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, and the door | The build step |
| `run` | The door, and the package hosts for a reinstall from the tool's own lock with hashes | The smoke and run steps |
| `web:<hosts>` | Each host the member approved on a card, by exact name | A step that needs a third-party host |

- **The default is `run`** for a stored tool, and `build` for a build.
  Nothing else is open. A host off the profile answers 403 at the proxy.
- **The proxy rules of WS-43g stand:**
  - no private address and no DNS in the container,
  - one DNS answer for each request,
  - ports 80 and 443 only,
  - one log line for each request.
- **Data or web, not both, by default.** A step whose capability holds a
  read scope gets no `web:` profile. A web host can receive whatever the code
  read, and the proxy cannot see inside TLS. To change this default is an
  owner call (SBX-Q2). If the owner allows both, the card must say "this
  code can read your Metorite data and send it to `<host>`".
- **A marked session gets no web profile.** When `egress.session_is_marked`
  is true (the session read untrusted mail or another untrusted answer), the
  broker refuses a `web:` profile for that run.

**The residual we accept.** A package host can see the names a build asks
for. An attacker cannot read PyPI's or npm's logs, so this is not a channel
back to the attacker. `upload.pypi.org` and every publish route stay off the
list.

### 5.6 Limits

The broker's settings of `maf_coding_engine.md` §7.1 rules 6 to 12 apply.
This spec adds one row for each step and no new mechanism.

| Limit | Build | Smoke | Run | Setting |
|---|---|---|---|---|
| CPU | 1 | 1 | 1 | `sandbox_cpus` |
| Memory | 1 GiB | 1 GiB | 1 GiB | `sandbox_memory` |
| One command | 300 s | 120 s | 300 s | `sandbox_exec_max_timeout_seconds` |
| The whole step | 600 s | 180 s | 900 s | `user_tool_step_seconds_<phase>`, new |
| Workspace disk | 2 GiB | 2 GiB | 2 GiB | `sandbox_workspace_quota_mb` |
| Package cache | 512 MiB | 512 MiB | 512 MiB | `sandbox_local_quota_mb` |
| Processes | 256 | 256 | 256 | `sandbox_pids_limit` |
| Containers of this target | 1 per org, 1 per box | same | same | `user_tool_max_per_org`, `user_tool_max_total`, new |

A step past its time budget is killed. Its run row reads `timeout`.

### 5.7 Prompt injection

Text that an attacker can write reaches this flow in two places. It reaches
the model that writes the code, from an email, a web page, a CRM note, a task
or an attachment. It also reaches the code at run time, as input data.

| Threat | Control |
|---|---|
| Injected text makes the model write hostile code | The code runs only in the sandbox, with the member's access as the ceiling. Every write waits for a card that shows the wire (§5.3). No secret is inside (§5.4) |
| The code sends member data out | No open host while the step can read (§5.5). No DNS. The proxy logs each request. The door limits the volume |
| Injected text tricks the member's card | The door builds the card from the held request, never from the model's text |
| A stored tool is a trojan for the next member | Org sharing needs a person who reads the code and the manifest (§8). A new version is not shared until that person approves it. Each member's run is still that member's access |
| Data the code returns carries new instructions | The tool's output goes back to the model inside the one untrusted frame (`acb_skills/untrusted.py`). The run then counts as marked (`egress.mark_untrusted_session`) |
| A build in a marked session | The build card says that the request came after untrusted content. A marked session gets no web profile |
| The code calls the door as another member or org | The capability row holds the member and the org. The door never reads either from the request. RLS answers zero rows for another org |
| A sandbox escape | gVisor in slice 1, a microVM at scale. With `runc`, the WS43-S1 residual stands, and §13 keeps production on `runsc` only |

**The control that matters most is the card.** The others shrink the blast
radius. Only the card stops a write the member did not mean.

---

## 6. The tool lifecycle

> The build, smoke test and run steps run on the coding harness of `maf_coding_engine.md` §18 (CA-14).

### 6.1 Gap detection and reuse (#234, #240)

> **D225 (2026-11-15).** The finder is the no-match path of `find_tools`
> (D222), not a second tool. Where this section, §7.4 and §12.1 say
> `find_tool`, read that path. SBX-1 rewrites the text when it is
> dispatched. §0b holds the decision.

**The rule.** The assistant may build a tool only when two things hold. One
`find_tool` call in the same run answered `no_match`. The member approved
the build card.

**`find_tool(need)`.** A new platform tool on the general agent
(`orchestrator`, D99). (D187) It runs five checks in this order:

1. **The library (D187).** A lexical search over the name and the
   description of the Studio packages that the member can see. The member
   owns some of them. The others come from the installs that reach the
   member (`package_host.md` §5.2), or are `system` packages. A paused or
   archived install does not count. The search uses Postgres full text at
   query time, with at most 5 candidates, and a `system` package first.
2. **The run's own tools.** The names and descriptions of the tools in the
   run's scope after injection, and the skills catalog of WS-23.
3. **The compose check (D187).** It reads at most 20 rows of the Studio
   action catalog that the member can call. A lexical match on the title
   picks them (`catalog_rows` at `routes/studio/actions.py:149`, and
   `package_rows` at `:203`). Can a Studio skill or workflow of these
   actions do the need, with no code? Studio builds those with no sandbox
   (`ai_studio.md` §19.3, kind `none`).
4. **One typed question** on `tier-decide` (D93, D100). (D187) It asks:
   "Can one of these tools do this need? Or can these catalog actions do it
   with no code? Answer with a tool name, `compose`, `none` or `unsure`."
   One question serves checks 1 to 3, so the lookup costs one call to
   `tier-decide`.
5. **The record.** One `tool_gaps` row with the candidates, the answer and
   the outcome.

| Answer | What `find_tool` returns | What the assistant does |
|---|---|---|
| A platform tool | `existing`, with its name | Uses that tool |
| A stored tool | `reuse`, with its package id and version (D187) | Calls `run_tool` (§6.4) |
| `compose` (D187) | `compose`, with the catalog action ids | Offers a Studio skill or workflow. No sandbox starts |
| `unsure` | The candidates, and no gap id | Tries a candidate first, or asks the member |
| `none` | `no_match`, with a gap id | May offer a build |

**A failed built-in tool (D187).** When a platform tool failed in this run,
the assistant passes its name as `failed_tool`. The checks skip that tool,
and the gap row names it (§6.7, level c).

**Who the library search can see (D187).** The library search reads one
organization only. It keeps a package in three cases only: the viewer owns
it live, an install reaches the viewer, or it is a `system` package. It never
runs a bare full-text query over `apps`. Such a query would leak the private
package of another member into the candidates and the decide prompt
(SBX-F23).

**The build card.** Before a build, the member sees one card:

- what the tool will do, in one sentence,
- the read scopes and write scopes it asks for,
- the packages it may install,
- the estimated credits (§10),
- and, in a marked session, the line "This request came after content from
  outside your organization."

On a refusal, the gap row reads `declined`, and nothing starts.

**Why a fence and not a prompt.** `build_tool` takes the gap id. It refuses a
gap id that is not `no_match`, not from this run, or older than 30 minutes
(SBX-F8). So a model that skips the lookup gets a refusal, not a build.

### 6.2 Provision (#236)

- `build_tool` and `run_tool` call `broker.acquire()` with the new target
  `tools`. `MAF_CODING_SCOPE` takes `tools:<org>` or `tools:*`. An empty
  scope turns the target off. This scope entry is the feature flag.
- The container key is (organization, `user-tool`, thread), as the broker's
  rule 1 says. One thread keeps one container for its steps.
- The broker mounts the run-data dir at `/workspace/.run/` and the thread's
  output folder at `/workspace/outputs/`, as `maf_coding_engine.md` §16.3
  does. It mounts no tenant dir. A tool sees only the inputs of its own step.
- The broker counts this target against `user_tool_max_per_org` and
  `user_tool_max_total` (default 1 and 1) and against the broker's own caps.
  At a cap, the tool answers "the sandbox is busy, try again in a minute".
- **No fallback to the host,** as the broker's rule 14 says.

**Short-lived, one for each thread (D187).** No member has a sandbox of
their own. A container serves the steps of one thread: the build, the smoke
test, the run and a repair. The reaper of the broker stops it after
`sandbox_idle_ttl_seconds` (600 s, `settings.py:1678`) with no step. It never
lives past `sandbox_max_lifetime_seconds` (7200 s, `:1679`). The container
never holds a tool between runs, because each run writes the stored version
into a fresh run-data dir (§6.4).

### 6.3 Build and smoke test (#237)

**The build** is a MAF harness session, the same as `code_task` on MAF
(`maf_coding_engine.md` §7.6). It gets `run_command`, the file tools over the
run-data dir, and the `build` profile. It must write one package in the MAF
skill format, so the existing `SkillsProvider` can load it.

(D187) The session runs on the one coding harness of CA-14 (D139), with the
profile `tool_builder`. The model is a setting (§6.9). The five files become
the files of one `app_versions` row (§7.2):

| File | What it holds |
|---|---|
| `SKILL.md` | `name` and `description` front matter, and how to call the tool |
| `tool.json` | The input schema, the output schema, the read scopes and the write scopes |
| `main.py` | The entry point. It reads `/workspace/.run/input.json` and writes to `/workspace/outputs/` |
| `requirements.lock` | Each package pinned, with a hash |
| `smoke.json` | One sample input and the checks on its output |

**The smoke test** runs `main.py` with the sample input, under a read-only
capability (§5.3). It passes when all four hold:

1. The exit code is 0.
2. The output matches the output schema.
3. The step stayed inside its limits.
4. No call hit a route outside the tool's declared scopes.

On a failure, the session may fix the code and run the smoke test again. It
has at most 3 rounds, inside the build budget of 600 seconds. Then the build
fails, and the gap row reads `build_failed`. The gateway stores nothing.

### 6.4 Run (#238)

- `run_tool(tool_id, inputs)` writes `inputs` to `/workspace/.run/input.json`,
  mints a capability with the tool's declared scopes, and runs `main.py`
  under the `run` profile.
- A first run after a build uses the package from the run-data dir. A later
  run materialises the stored version into the run-data dir.
- (D187) A `tool_id` is the id of the package, `apps.id`. A later run reads
  the files of the version that the caller runs (§7.2, §8.2). It writes them
  into a fresh run-data dir.
- (D187) The writes that §5.3 holds for a card are the proposed writes of
  the B4 and B5 contract (`ai_studio.md` §3.7). The platform applies each
  one only after the member approves it.
- Writes go through cards (§5.3).
- The result is the files in `outputs/` and a JSON summary. The chat shows
  the files as artifact cards, as S8 does (`projects_ai_chat.md` §14). The
  summary goes back to the model inside the untrusted frame.
- Each step writes one `user_tool_runs` row (§7).

### 6.5 Persist on success, and only then (#239)

**A tool becomes a registry row only when its first real run succeeds.**
Success means all three:

1. The smoke test passed.
2. The first real run exited 0, and its output matched the schema.
3. No door call answered 5xx, and no write that the member approved failed.

(D187) Then, at the end of that run, the gateway writes the tool into the
one library, in one transaction, through `packages_store.py`:

- a private package (`create_package`), whose manifest names the logic
  module (§7.1),
- version 1 with its five files (`publish_version`, and the files of §7.2).

The owner runs it with no install, as the author of a package does. A share
is an install (§8).

**Before that, nothing is kept.** Until then, the package lives only in the
run-data dir, which the host deletes at the end of the run
(`maf_coding_engine.md` §16.3). So a failed build leaves no row, no file and
no blob. Only its `tool_gaps` row and its `user_tool_runs` rows stay.

**The member has the last word.** The result card shows "This is wrong" for
24 hours. If the member presses it, the gateway deletes the tool, while it
still has one version and no run by another member. The gap row then reads
`build_failed`. (D187) The delete is the delete path of the package store
(`packages_store.py`, A5c-1).

A new version follows the same rule. The member asks for a change, the build
makes a new package, and the new version row lands only after its first real
run succeeds.

### 6.6 Reuse

- `find_tool` puts stored tools first (§6.1 step 1).
- The assistant's system text lists at most 20 of the member's visible tools,
  by last use, with one line each. It does not inject each tool as a
  function, because each function costs context on every turn (WS-23).
- `run_tool` reads the version to run from the pin rule of §8.2.

### 6.7 Repair (D187)

**The rule.** When a tool fails, the assistant reads the error, builds the
next version, runs it, and keeps it only if that run succeeds. The rules of
§6.5 bind each new version. A repair never widens a tool.

**The signals.** Three signals start a repair, and nothing else does:

1. The run exits with a code that is not 0.
2. The output does not match the output schema of `tool.json`.
3. A door call answers 5xx.

A refusal is not a signal. A 403 for a declined card, a 404 and a 429 are
answers. A timeout and a limit kill are not signals in this version, and the
assistant reports them to the member. A door 5xx can be a fault of the
route, so the assistant also files it to the failure log (level c).

**The cap.** One failed run gets at most **1** repair build. That build has
the 3 smoke rounds of §6.3, inside the same build budget of 600 seconds. If
the new version fails too, the assistant stops and tells the member what
failed. The setting is `user_tool_repairs_per_run`, default 1 (SBX-F17). It
matches `MAX_MUTATION_ATTEMPTS = 1` of self-mutation
(`orchestrator/mutation.py:45`).

**What the repair reads.**

- The error class, the exit code and the last 4 KB of standard error.
- The `user_tool_runs` row of the failed run (§7.3), and the files of the
  version.
- The error text reaches the model inside the untrusted frame
  (`acb_skills/untrusted.py`), because input data can shape it.
- The smoke test of the repair also runs the input of the failed run, with a
  read-only capability (§5.3).

**A repair keeps the contract.** The new version keeps the input schema, the
output schema, the read scopes and the write scopes of the version that it
repairs. A change to any of them is a new version that the owner asks for.
So a repair never needs a new review of its scopes.

**Writes that landed.** A run can write before it fails. The run of the new
version is a new run, and each write waits for a new card (§5.3). That card
names the writes of the failed run that landed. So the member does not
approve one write twice.

**The three levels.**

| Level | What failed | What the assistant does | Who runs the new version |
|---|---|---|---|
| **(a)** | The member's own tool | Builds version N+1 in the sandbox, runs it, and keeps it only on success | The owner. `apps.live_version` moves to N+1 |
| **(b)** | An installed tool of another member | The same steps. Version N+1 lands in the same package, as a repair of this member | This member only, through a private pin (§8.2). Each other member keeps the approved version until a holder of `studio:publish` moves the pin |
| **(c)** | A built-in platform tool | Changes no platform code at run time. May build a private workaround now, through `find_tool` with `failed_tool` (§6.1). Files the error to the tool failure log | This member, until the fix ships |

**Level (b) in the store.** Today `packages_store.py` writes only the
packages of their owner. SBX-12 adds one path: a member who runs an
installed package may add a repair version, for their own use. The
version row names that member in `published_by`. The `live_version` of the
owner and each install keep their version. This path is the one exception to
D180.2, and one function of the store holds it.

**Level (c), the report.** Metorite task 1793 owns the tool failure log.
The spec of the failure store is `tool_failure_log.md`. A report holds:

- The name of the built-in tool, the error class and the run id.
- The gap category, and whether a workaround worked.
- The error text and the workaround code, only with the consent of the
  member. One consent covers one report (SBX-Q8, as D126 does for a chat).

**Level (c), the fix.** A coding agent turns a report into a PR on the
built-in tool, under the D145 loop and its review. The fix reaches every
organization at the next daily release. Then the gateway archives each
workaround install that names the fixed report, through the disposal path
(`package_host.md` §5.3). The member gets a note in the bell.

**Not self-mutation.** `attempt_self_mutation` (`orchestrator/mutation.py:345`)
serves the first-party organization only (`mutation.py:259`). It changes
platform code, and a person approves its push. A tenant run never calls it.
Level (c) uses the failure log and the PR loop.

### 6.8 Platform adoption (D187)

**The rule.** A tool serves all of Metorite only as first-party code in the
repository: a Studio B4 module (`ai_studio.md` §3.7) or a platform tool. It
goes through a PR and the review of its risk tier. A release then ships it
as a `system` install, which is a file of `package_host.md` §3.3.

**What never happens.**

- Nobody copies a running tool from one organization into another.
- No code of a tenant enters the repository without the consent of that
  organization. The code is theirs, and it can hold their business rules.
- No text, code or data of a tenant reaches another tenant or the counts.

**The signals.** Only counts cross tenants. They rank the candidates:

- the runs and the success rate, by gap category,
- the number of organizations with a gap in the same category,
- for a built-in tool, the failures by tool name and error class.

A category is a slug from the fixed list of §7.4, so it carries no tenant
text. The counts sum across organizations and name none.

**Who sees the counts.** A platform admin, in one view of the Operator
Console. The view shows counts only, as §9.2 says (D64.5). SBX-16 builds it
after SBX-5, so it is a later slice.

**The path.**

1. The counts name a candidate category.
2. A Metorite engineer writes the first-party module from the need alone. To
   start from the tool of a tenant, the engineer needs consent first.
3. An owner of the source organization approves one card, for one tool and
   one version (SBX-Q9, SBX-17). The PR names that consent record.
4. The PR follows the D145 loop. The daily release ships the `system`
   install.
5. The tool of the source organization runs until its owner archives it.
   `find_tool` ranks the `system` package first (§6.1).

### 6.9 Building blocks for the model (D187)

**The image.** `apps/services/orchestrator/sandbox/requirements.in` names 24
packages. `requirements.txt` beside it pins each one with a hash.

| Set | Packages |
|---|---|
| Data, spreadsheets, charts and simple PDFs | pandas, numpy, openpyxl, matplotlib, pypdf, fpdf2, markdown, tabulate, pyyaml, python-dateutil, requests |
| Business documents | python-docx, python-pptx, xlsxwriter, reportlab, pdfplumber, pillow |
| Analysis and charts | scipy, seaborn, networkx |
| HTML, XML and templates | beautifulsoup4, lxml, jinja2 |
| The data toolkit | duckdb. Only `sandbox/data_engine.py` imports it |

**Nothing installs at run time** (`requirements.in:16`). A build may install
an allowlisted package through the egress proxy, under the `build` profile
(§5.5). That proxy is WS-43g, and it is spec only. `ai_studio.md` §19.5 holds
the rules of such an install (B8).

**The door client (new).** One small Python module in the image,
`metorite_door`. A tool imports it to call Metorite as the member.

- It reads `METORITE_API_TOKEN` and calls the fixed door name of §5.2.
- It reads a list page by page, at most 100 rows each.
- It handles the answers of §5.3: a held write, a 403 and a 429.
- It uses `requests`, which the image holds, so it adds no package.

So a tool never writes HTTP by hand, and the model has less code to write.
SBX-13 adds it beside `sandbox/data_engine.py`, with the same read-only copy
(`Dockerfile.coding-sandbox:100`).

**The harness and the model.** The build runs on the one coding harness
(D139, CA-14), with the profile `tool_builder`. The model is a setting,
`user_tool_build_model`. It holds a Router tier name, as `mutation_model`
does (`settings.py:1579`). Its default is an Opus or Sonnet class model.

### 6.10 What makes it fast (D187)

1. **Reuse first.** `find_tool` returns a stored tool or a path with no
   code before any build. One decide question serves the checks (§6.1).
2. **No install at run time.** The image holds the 24 packages (§6.9).
3. **One warm container for each thread.** The build, the smoke test, the
   run and a repair use the same container (§6.2).
4. **The door client.** The model has less code to write, so a build needs
   fewer smoke rounds (§6.9).
5. **A repair keeps the contract.** No scope review waits (§6.7).
6. **The image is on the box first.** SBX-15 pulls it by digest when the box
   starts.

---

## 7. The registry schema (#235)

**(D187) One library.** A user tool is a Studio package, so the package
store of `ai_studio.md` A3 and A4 is its registry. This spec drops its own
tables `user_tools` and `user_tool_versions`, and nobody built either. It
keeps three new tables: `user_tool_runs`, `tool_gaps` and
`sandbox_capabilities`. It adds two nullable columns and one trigger to
`app_versions` (§7.2).

Each tenant table follows R5a: `organization_id`, the generated RLS
migration, and `tests/unit/test_tenant_coverage.py`. (D187) Each migration is
expand-only (R6): new tables, nullable columns or columns with defaults, and
no change to an existing row. Its number is the next free number at build
time (R1). Every query runs against a real Postgres as the NOBYPASSRLS app
role before anyone trusts it (R8).

### 7.1 A tool is a Studio package (D187)

A tool is one row of `apps` with `format = 'manifest'`
(`infra/postgres/322_apps_format.sql`). `gateway/studio/packages_store.py` is
its one writer. The manifest names one logic module with `runtime.kind`
`sandbox`, or `packages` for an install at build time (`ai_studio.md` §19.3,
B7). The module entry holds the input schema, the output schema and the
scopes. So the catalog, the validator and the reviewer read them with no
code.

Each column of the dropped `user_tools` table has a home:

| Was in `user_tools` | Now |
|---|---|
| `slug`, `name`, `description`, `owner_email` | The same columns of `apps` |
| `visibility` | The scope of each install: `private`, `group:<slug>`, `org` or `system` (`package_host.md` §6.1) |
| `status` | `apps.status`, and the `state` of each install: `active`, `paused` or `archived` |
| `latest_version` | `apps.live_version`, the pointer of the owner |
| `org_version` | `package_install.version` of the group or org install |
| `approved_by`, `approved_at` | The approval record of `ai_studio.md` J2 and `package_host.md` PKG-11 |
| `disabled_by`, `disabled_at`, `disabled_reason` | An install in `paused`, and its audit row with the reason |
| `last_run_at` | The newest `user_tool_runs` row of the package |
| `search` | Postgres full text at query time, over the packages that the member can see. No index until a measurement asks for one |

### 7.2 A version and its files (D187)

**A version is a row of `app_versions`** (`infra/postgres/114_custom_apps.sql:42`).
`publish_version` (`packages_store.py:709`) and `rollback` (`:752`) write it.
The store never updates or deletes a version row (`:49`).

**The five files live in a new column of that row.** No column of
`app_versions` can hold them today. Its columns are `id`, `app_id`,
`version`, `manifest`, `bundle_html`, `bundle_sha256`, `release_notes`,
`scope_set_hash`, `published_by`, `published_at`, `review_status` and the
generated `organization_id`. A package writes `bundle_html = ''`
(`packages_store.py:182`).

| Column | Type | Notes |
|---|---|---|
| `files` | `jsonb` | Null. Path to text: `main.py`, `tool.json`, `requirements.lock`, `smoke.json` and `SKILL.md`. At most 262144 bytes of JSON text |
| `files_sha256` | `text` | Null. The SHA-256 of that JSON text |

**Why a column, and no other place.**

- `app_versions.manifest` holds validated data, and the resolve and the
  catalog read it (`package_host.md` §5.2). Code in it would grow each of
  those reads.
- `bundle_html` holds the HTML entry of an App Workshop app. Two meanings for
  one column is a defect.
- `app_files` (`115_app_files.sql`) mirrors a draft. It has no version, and
  its rows change. A tool keeps no draft: until its first good run, its files
  live only in the run-data dir (§6.5).
- One row is one version. So the files, the manifest and the hash commit
  together.

**Expand only (R6).** `ADD COLUMN IF NOT EXISTS`, nullable and with no
default, so the ALTER rewrites no row. A CHECK holds the size and the shape
of a JSON object. Each old row passes it, because it holds NULL. A trigger
refuses each change of `files` after the INSERT, as `apps_format_fixed` does
for `apps.format` (`322_apps_format.sql:100`). Old code names no new column.

**SBX-5 adds it.** It adds both columns, the CHECK and the trigger. It also
makes `publish_version` write the files, and `rollback` copy them with the
manifest.

**`tool.json`** also holds the image digest of the build. For a workaround
(§6.7, level c), it names the built-in tool and the id of the report. The
store refuses a version whose `tool.json` and manifest entry differ
(SBX-F12).

**Who reads the files.** `run_tool` reads the files of the version that the
caller runs (§8.2), and writes them into a fresh run-data dir (§6.4). An
approver reads them on the approval card (§8.1).

### 7.3 `user_tool_runs` — one row for each step

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS |
| `app_id`, `version` | `uuid`, `int` | (D187) The package and its version. Null for a build that did not persist. No foreign key, so the row stays after a delete |
| `gap_id` | `uuid` | Null |
| `repair_of` | `uuid` | (D187) Null. The failed run that this repair answers (§6.7) |
| `signal` | `text` | (D187) Null. `'exit_code'`, `'schema'` or `'door_5xx'`, the signal of a failed run (§6.7) |
| `member_email`, `thread_id`, `run_id` | `text` | The member, the chat thread, the agent run |
| `phase` | `text` | `'build'`, `'smoke'` or `'run'` |
| `backend` | `text` | `'docker-runc'`, `'docker-runsc'`, `'remote-runsc'` (D187) or `'e2b'` |
| `outcome` | `text` | `'succeeded'`, `'failed'`, `'timeout'`, `'refused'` or `'cancelled'` |
| `exit_code` | `int` | Null |
| `wall_ms`, `cpu_ms`, `peak_mem_mb` | `int` | Measured by the broker |
| `api_reads`, `writes_held`, `writes_approved`, `writes_refused` | `int` | Counted by the door |
| `egress_bytes` | `bigint` | From the proxy log |
| `credits` | `numeric` | Null until SBX-7 |
| `error_class` | `text` | Null. A fixed list, never free text |
| `started_at`, `ended_at` | `timestamptz` | |

### 7.4 `tool_gaps` — one row for each `find_tool` call

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS |
| `member_email`, `thread_id`, `run_id` | `text` | |
| `need` | `text` | At most 280 characters, written by the model. It stays in the tenant |
| `category` | `text` | One slug from a fixed list of about 30, chosen by the decide question. It carries no tenant text |
| `candidates` | `text[]` | The tool names that the checks found |
| `answer` | `text` | `'tool'`, `'compose'` (D187), `'none'` or `'unsure'` |
| `outcome` | `text` | `'existing'`, `'reuse'`, `'compose'` (D187), `'no_match'`, `'declined'`, `'built'` or `'build_failed'` |
| `app_id` | `uuid` | (D187) Null. The package that answered or that the build made |
| `failed_tool` | `text` | (D187) Null. The name of a failed platform tool (§6.7, level c). A platform name, never tenant text |
| `created_at` | `timestamptz` | |

### 7.5 `sandbox_capabilities` — the capability lookup

The door reads a capability before it knows the tenant. A RLS table cannot
serve that read. So this table goes in `gen_tenant_migration.EXEMPT`, with the
same reason as the token table of `external_agent_access.md` §10.3.

| Column | Type | Notes |
|---|---|---|
| `token_sha256` | `text` | Primary key |
| `organization_id` | `uuid` | Not null |
| `member_email`, `run_id`, `thread_id` | `text` | Not null |
| `app_id`, `version` | `uuid`, `int` | (D187) The package and its version. Null during a build |
| `phase` | `text` | Not null |
| `read_scopes`, `write_scopes` | `text[]` | Not null |
| `expires_at` | `timestamptz` | Not null |
| `revoked_at` | `timestamptz` | Null |

A sweep deletes rows 1 day after `expires_at`. The table never holds the
token itself.

---

## 8. Review controls (#241)

**(D187) Sharing goes through the package host.** A tool is a package, so a
share is an install at a scope of `package_host.md` §6.1. Promotion moves the
same install to a wider scope, and it never copies the package (PKG-11,
`ai_studio.md` J2). This section adds the rules that hold because a tool
runs code.

### 8.1 Who can do what

| Act | Who | Card or check |
|---|---|---|
| Build a tool | (D187) Any member with `feature:studio`, when the scope names the org | The build card (§6.1) |
| Run a private tool | Its owner | Write cards (§5.3). (D187) No approval: D180.2 lets an owner publish for their own use |
| Share a tool with named people, a group or the org (D187) | Its owner asks, and a holder of `studio:publish` approves one version | The approver reads the five files, the scopes, the package list and the last 5 runs |
| Run an installed tool (D187) | Each member that the install reaches | That member's own access and cards. A shared tool shares code, never authority |
| Add a repair version (D187) | A member whose run of an installed tool failed (§6.7, level b) | The rules of §6.7. The version runs for that member only |
| Move the pin of an install (D187) | A holder of `studio:publish` | The approver reads the diff of the files |
| Disable a tool | Its owner, or a holder of `studio:publish` (D187) | Each install goes to `paused`. Running steps finish, and no new step starts |
| Revoke a tool | A holder of `studio:publish` (D187) | Each install goes to `archived`, and `find_tool` never returns it again |

**(D187) A code package needs the approval at each scope past private.**
`package_host.md` §6.1 lets a member with `feature:studio` install a package
for a group. That fits a declarative package. A package with a `sandbox` or
`packages` module runs code for each viewer. So an install of it for named
people, a group or the org waits for the approval of one version.

**(D187) The approver holds `studio:publish`.** D130.3 named a new
permission, `tools:approve`, and no code holds it. `studio:publish` exists
(`acb_auth/permissions.py:198`), and D180.3 makes it the gate of a share of a
package to others. A tool is now a package, so one object gets one gate. A
second permission for the same act on the same object would be a second
seam. The owner holds it through `*`, and an admin can grant it to one
person (D180.3, D180.4).

### 8.2 Versions and pins (D187)

- A version is immutable (§7.2).
- The owner runs `apps.live_version`.
- Each other member runs the `version` of the widest install that reaches
  them, as the resolve picks it (`package_host.md` §5.2, `resolve.py:126`).
- A repair version of level (b) runs only for its author. The author gets a
  private install of the package at that version. `run_tool` reads the
  private pin of the caller first, and the menus still show the widest
  install.
- A new version never moves the pin of an install. A holder of
  `studio:publish` must move it.
- A holder of `studio:publish` may move a pin back to any earlier version.
- `run_tool` refuses a version that the caller may not run, and an install
  that is not `active`.

### 8.3 Kill switches

- `MAF_CODING_SCOPE` without `tools:` stops every build and every run.
- `user_tool_runs_enabled` is an incident switch, default true. Set to
  false, it stops runs of stored tools and keeps builds. The scope entry
  keeps the whole feature dark, so this default changes nothing on its own.
- (D187) A holder of `studio:publish` pauses one install, and the disposal
  path removes its contributions at once (`package_host.md` §5.3).
- (D187) `user_tool_repairs_per_run` set to 0 stops each repair and keeps
  the runs.

### 8.4 Audit (D187)

Each act writes one audit row in the transaction of the act, through the
seam that owns the act. A version and a rollback write `app_audit` through
`packages_store.py`. A share, an approval, a pin, a pause and an archive
write the record of J2 and PKG-11. The row holds the actor, the package, the
version and the reason. Each write that a tool makes carries the stamp
`sandbox:<slug>@v<n>` in `meta.via` (§5.2).

---

## 9. Instrumentation and documentation (#242)

### 9.1 Log lines

One JSON line for each event, through `acb_common.get_logger`:

| Event | Fields |
|---|---|
| `sandbox.tool.gap` | org, member, run, category, answer, outcome |
| `sandbox.tool.build` | org, run, gap id, outcome, wall ms, smoke rounds |
| `sandbox.tool.run` | org, run, tool id, version, backend, outcome, wall ms |
| `sandbox.door.call` | org, run, method, route template, status |
| `sandbox.door.write_held` | org, run, route template, class, decision |
| `sandbox.capability` | org, run, phase, minted or revoked |

No line holds input data, output data, a need text or a token.

### 9.2 The tool-gap counter

- **For the organization.** An approver sees the gaps of their own
  organization: the need, the category and the outcome.
- **For our team.** The Operator Console sees counts by `category` and
  outcome, across organizations, and nothing else. D64.5 lets an operator
  reach the commercial record only, so no `need` text leaves the tenant.
- **What it is for.** A category with many `built` or `build_failed`
  outcomes is a tool we should build properly. The weekly list goes to the
  board as a candidate, never as an automatic build.
- **(D187) The adoption counts.** SBX-16 adds the counts of §6.8 to this
  view. They are the runs and the success rate by category, and the
  organizations for each category. They also count the failures of each
  built-in tool by error class. The view names no organization. A candidate
  becomes a platform tool only through §6.8.

### 9.3 Documentation

- `docs/agent_sandbox.md`, the member guide: what a tool can do, what it can
  never do, and what each card means. It says only what is built.
- A runbook section in the same file. It tells an operator how to disable
  one tool, how to stop every run, and how to read the log lines of §9.1.

---

## 10. Metering

Sandbox time is AI work, so it uses AI credits through the one entitlement
seam and the one usage ledger. It adds no second meter.

- Each step writes one `usage_event` row with the measured vCPU-seconds and
  GiB-seconds, against the organization and the member.
- That needs two changes in the customer console, and no new table. A new
  row `sandbox` in `task_catalog`, and `POST /usage/record` must pass
  `task`, `quantity` and `unit` (today it passes token fields only).
- The gateway's spend gate (`credits.decide_spend`) answers before a step
  starts, as it does for a chat call.
- The model tokens of the build session and of `find_tool` meter as they do
  today, through the Router.
- A hard cap stops a build or a run before it starts when the organization
  has no credits (D19.3).
- **The price is the owner's call** (SBX-Q4). The proposal: cost plus the
  per-tier margin of `credit_pricing.md`, priced from the backend's real
  vCPU-second rate. On the Docker backend that rate is near zero, so the
  proposal is a floor of 1 credit for each step.

---

## 11. Fences (R7)

Every fence is a new test. Each one is verified red first. The ones on auth,
tenancy and outward writes also get mutation testing
(`engineering_practice.md` §4).

| # | Test file | What breaks it |
|---|---|---|
| SBX-F1 | `tests/unit/test_sandbox_capability.py` | A capability takes its org or member from input, works after its step ends or after 15 minutes, works for another org, or holds a write scope in the smoke step. Its R8 half runs on a real database: a door read for a row of another org returns no row |
| SBX-F2 | `tests/unit/test_sandbox_routes_coverage.py` | A route in `SANDBOX_ROUTES` has no class, a class X route is on the list, or a new gateway route is neither on the list nor in its exclusion list |
| SBX-F3 | `tests/unit/test_sandbox_door_writes.py` | A class B or C request runs with no card, a run with no chat gets a write, the card text comes from the model and not from the held request, a refused card lets the write run, or a write lands without `meta.via = sandbox:…` |
| SBX-F4 | `tests/unit/test_sandbox_no_secrets.py` | The container's environment or file system holds a provider token of the org, `GATEWAY_INTERNAL_TOKEN`, the Router key, the session secret, the signing key or a database URL. It has a `sandbox_docker` half |
| SBX-F5 | `tests/unit/test_sandbox_egress_profiles.py` | A host off the profile passes, a step with a read scope gets a `web:` profile while SBX-Q2 has its default, a marked session gets one, or the code changes its own profile |
| SBX-F6 | `tests/unit/test_sandbox_door_identity.py` | The door's identity step skips a branch 0 step, reads the member from a header, falls through to branch 1a, or a refused request leaves a tenant bound. Its source is pinned by digest, as branch 0's is |
| SBX-F7 | `tests/unit/test_user_tool_injection.py` | `find_tool`, `build_tool` or `run_tool` reaches an agent with no `tool_scope`, a run whose org the scope does not name, or a run with no chat for `build_tool` |
| SBX-F8 | `tests/unit/test_tool_gap_detection.py` | `build_tool` runs with no gap id, with a gap id that is not `no_match`, from another run, or older than 30 minutes, or with no approved build card |
| SBX-F9 | `tests/unit/test_user_tool_registry.py` | R8, on a real database as the NOBYPASSRLS role. (D187) A package persists after a failed smoke test or a failed first run. A failed build leaves a file, a blob or a package. RLS shows a package or a `tool_gaps` row to another org. A private tool shows to another member |
| SBX-F10 | `tests/unit/test_user_tool_versions_immutable.py` | (D187) Any code issues an `UPDATE` of `app_versions.files`, or the trigger of §7.2 lets one through on a real database |
| SBX-F11 | WS43-F1, `tests/unit/test_sandbox_broker_seam.py`, extended | A module outside `sandbox_broker.py` imports `e2b` or starts a `docker` process, or the `tools` target falls back to the host |
| SBX-F12 | `tests/unit/test_user_tool_build.py` | A build package lacks a file of §6.3, a lock line lacks a hash, the smoke test runs more than 3 fix rounds, or a build passes the smoke test with a call outside its scopes. (D187) Or the store keeps a version whose `tool.json` and manifest entry differ |
| SBX-F13 | `tests/unit/test_user_tool_review.py` | (D187) A member who is not the owner runs `apps.live_version`, a new version moves the pin of an install, or a member without `studio:publish` approves, moves a pin or revokes. A code package gets an install for named people, a group or the org with no approved version. A paused install starts a step |
| SBX-F14 | `tests/unit/test_user_tool_audit.py` | A share, approval, pin, disable or revoke writes no audit row |
| SBX-F15 | `tests/unit/test_sandbox_log_lines.py` | A log line of §9.1 holds input data, output data, a need text or a token, or the operator view returns a `need` text |
| SBX-F16 | `tests/unit/test_sandbox_metering.py` | A step ends with no usage event, or a step starts for an org at its hard cap. (D227) Or a step starts while the `sandbox` price row is missing |
| SBX-F17 | `tests/unit/test_user_tool_repair.py` | (D187) A repair starts on a signal outside §6.7, or one failed run gets a second repair build. A repair changes a schema or a scope. A level (b) version runs for a member who is not its author, or moves a pin or the `live_version` of another member |
| SBX-F18 | `tests/unit/test_tool_compose_check.py` | (D187) `find_tool` answers `no_match` when the decide answer is `compose`, `build_tool` takes a `compose` gap, or the compose check offers a catalog row that the member cannot call. `find_tool` opens a session while `catalog_rows` runs. It proves done-when 9 of §12.1 |
| SBX-F19 | `tests/unit/test_no_platform_patch_at_run_time.py` | (D187) A level (c) path writes into the repository or calls `attempt_self_mutation`. A failure report holds error text or code with no consent row of the member |
| SBX-F20 | `tests/unit/test_tool_adoption_counts.py` | (D187) The view of §6.8 returns a value that is not a count, a category slug, a built-in tool name or an error class, or it names an organization |
| SBX-F21 | `tests/unit/test_sandbox_remote_backend.py` | (D187) The `docker-remote` backend binds a mount of the gateway host, runs `runc` in production mode, or gives `DOCKER_HOST` to a child that is not `docker`. It has a `sandbox_docker` half |
| SBX-F22 | `tests/unit/test_sandbox_door_client.py` | (D187) The door client reads an env value other than `METORITE_API_TOKEN`, calls a host other than the door name, or the image lacks it |
| SBX-F23 | `tests/unit/test_user_tool_registry.py`, R8 half | (D187) The library search reads across organizations, or runs a bare full-text query over `apps`. A private package of another member reaches `candidates`, the decide prompt or the gap row. A paused or archived install still comes back. It proves done-when 7 of §12.1 |
| SBX-F24 | `tests/unit/test_sandbox_scope_all_orgs.py` | (D227) `tools:*` parses to an error, an org in `user_tool_disabled_orgs` still runs, or `tools` is absent from `MAF_CODING_TARGETS`. Advisory until SBX-6 and SBX-9 build it |
| SBX-F25 | `tests/unit/test_compute_run.py` | (D227) `compute_run` takes an input that is not a `tr_` ref of its own org, member and thread, or a foreign ref does not answer "not found". A short tool result gets no ref while compute is on. One org evicts the refs of another. The output text is not framed as untrusted. A run passes the daily cap of 50 for an org or 20 for a member. A log line holds code or output text. An org outside the scope runs |
| SBX-F26 | `tests/unit/test_sandbox_broker_compute.py` | (D227) The `compute` argv holds a mount, a network, or limits other than 1 CPU, 1g and pids 64. The tar reader accepts a link, a nested name, a type outside csv, png, json and txt, more than 5 files, a file over 1 MB or more than 3 MB in total. `compute` is absent from `MAF_CODING_TARGETS`, or `compute:*` does not parse, or an unknown target no longer raises `ValueError`. Production runs without `docker-remote` and `runsc`. A start skips the org lock |

**Where the Docker halves run.** On `.github/workflows/sandbox-docker.yml`,
with the `sandbox_docker` marker, as WS43-F4 does. A skip fails that
workflow.

---

## 12. Slices

Each slice is one PR, ships dark, and ends at the next free migration number
at build time (R1). The `tools` target stays out of `MAF_CODING_SCOPE` on
every box until SBX-9.

**The order (D187).** The owner asked for speed. Three phases, in this
order:

1. **Phase 1, no code runs.** SBX-1 as reshaped, and the spec of the tool
   failure log (task 1793). Both cost nothing, and SBX-1 gives real gap
   counts.
2. **Phase 2, on the dev stack.** The door (SBX-2, SBX-3), the door client
   (SBX-13), the build (SBX-4), the run and the library (SBX-5), and repair
   (SBX-12). Then the review controls (SBX-6), metering (SBX-7), gVisor
   (SBX-8), the remote backend (SBX-15) and level (c) (SBX-14).
3. **Phase 3, production.** The owner buys the box and sets up its network
   (SBX-G9, SBX-G10). Then SBX-9 turns on `tools:<org>` on that box, for each beta org (D227).
   SBX-16 and SBX-17 come after real use.

SBX-5 also needs `ai_studio.md` B7, the `runtime` key of a logic module in
the A2 model.

| Slice | Work | Depends on | Gate |
|---|---|---|---|
| **SBX-0** | ✅ Done 2026-10-27. D130 answers §14, and the board row is WS-TO | Nothing | Done |
| **SBX-0b** (task 1962) | (D187) This amendment, 2026-11-07, Metorite task 1962. Docs only: §0a, D187 in `work_plan.md` §3, and one line each in `ai_studio.md` and `package_host.md` | SBX-0 | AGENT-SAFE. Docs |
| **SBX-0d** (task 3786) | (D227) This amendment, 2026-11-19. Docs only: §0c, D227 in `work_plan.md` §3, and the SBX-9 and SBX-G1 rows. SBX-9 and SBX-G1 cover the beta orgs, and rung 6a′ adds the compute run | SBX-0b | AGENT-SAFE. Docs |
| **SBX-1** (task 3800) | (D187) `tool_gaps` and `user_tool_runs` (§7.3, §7.4). `find_tool` with the library check and the compose check (§6.1). (D225) This is the no-match path of `find_tools`, not a second tool. The gap log line of §9.1, and the gap view for an approver. No code runs anywhere. §12.1 holds the full slice | SBX-0b | AGENT-SAFE. Dark |
| **SBX-2** (task 3801) | The run capability (§5.1), the door with reads only (§5.2), `SANDBOX_ROUTES` and its coverage fence | SBX-1, SBX-Q6 | AGENT-SAFE. Dark |
| **SBX-3** (task 3802) | Writes held for a card (§5.3), the batch card, `meta.via` | SBX-2 | AGENT-SAFE. Dark |
| **SBX-4** (task 3803) | The `tools` target, `build_tool`, the build session and the smoke test (§6.2, §6.3), on the Docker backend | SBX-2, SBX-13 (D187), WS-43g for the proxy | AGENT-SAFE. Dark |
| **SBX-5** (task 3804) | (D187) `run_tool`, persist on success as a Studio package, and reuse (§6.4 to §6.6). The `app_versions.files` columns, the CHECK and the trigger (§7.2). `publish_version` writes the files, and `rollback` copies them | SBX-3, SBX-4, `ai_studio.md` B7 | AGENT-SAFE. Dark |
| **SBX-6** (task 3805) | (D187) (D227) The setting `user_tool_disabled_orgs`, which takes `*` (§0c, Gap A). The review controls through the package host: share, approve with `studio:publish`, pin, pause, archive and audit (§8) | SBX-5, `package_host.md` PKG-11, `ai_studio.md` J2 | AGENT-SAFE. Dark |
| **SBX-7** (task 3806) | Metering (§10). (D227) A step is refused when the `sandbox` price row is missing (SBX-F16) | SBX-5, SBX-Q4 | AGENT-SAFE. Dark |
| **SBX-8** (task 3807) | `sandbox_runtime=runsc` in the broker, and a measured cost of gVisor on a local stack. 🔨 **BUILT dark** (2026-11-19, branch `mt-97db21a4-sbx15-remote-runsc`): the setting `sandbox_runtime`, default `runc`. `runsc` adds `--runtime=runsc` to `docker run`, and an unknown value refuses each start. Fences: WS43-F2 and SBX-F21. **Not measured:** no local daemon has `runsc`, so the cost of gVisor waits for a box with it | the broker (D227), not SBX-4 | AGENT-SAFE. The install on a box is WS43-G5, **OWNER-GATE** |
| **SBX-9** (task 3808) | (D187) (D227) Turn it on: `tools:<org>` on production for each beta org of an explicit list, not Fracktal only, on the dedicated box of §4.6. The checklist of §0c binds it: the deployed SHA holds SBX-4, and `*` is allowed only while self-serve signup is off. D144 retired staging, so the local staging gate of D145 comes first | SBX-1 to SBX-8, SBX-12, SBX-13, SBX-15a, SBX-15b, WS43-G1, WS43-G2, WS43-G4, WS43-G5, BH-3, SBX-G6 (D227), SBX-G9, SBX-G10, SBX-G11 | **OWNER-GATE** |
| **SBX-10** (task 3809) | The `e2b` backend (§4.4) and the public door name. (D187) The alternative to the box of §4.6 | SBX-5, SBX-Q1 | AGENT-SAFE to build on a free test account. The account, the key and the DNS record are **OWNER-GATE** |
| **SBX-11** (task 3810) | The SPACE check through ECW (§4.5) | SBX-10, the three triggers of §4.5 | **OWNER-GATE**. A new host costs money |
| **SBX-12** (task 3427) | (D187) **Repair, levels (a) and (b)** (§6.7): the three signals, the cap of 1, the contract check, the card that names the writes that landed, and the repair version of the store with its private pin (§8.2). **Done when:** a fixture tool that exits 1 gets one repair, and the fixed version runs and persists. A second failure stops with no second build. A level (b) version runs for its author only, and the org pin does not move | SBX-5. SBX-6 for level (b) | AGENT-SAFE. Dark |
| **SBX-13** (task 3428) | (D187) **The door client** `metorite_door` in the sandbox image (§6.9). **Done when:** in a `sandbox_docker` test, a tool reads two pages through the door with the capability and no other code. The client calls no host but the door name. The image test (WS43-F25) lists the module | SBX-2 | AGENT-SAFE. The image on a box is WS43-G2 |
| **SBX-14** (task 3429) | (D187) **Repair, level (c)** (§6.7): `find_tool` with `failed_tool`, the private workaround, the report to the failure log with its consent rule, and the archive of a workaround when its fix ships. **Done when:** a fixture failure of a built-in tool files one report with no error text before consent. The workaround runs for this member only. A release that marks the report fixed archives the workaround install | SBX-5, the spec and the store of the tool failure log (task 1793), SBX-Q8 | AGENT-SAFE. Dark |
| **SBX-15a** (task 3430) | (D187) (D227) **The `docker-remote` backend and the org lock** (§4.6, §0c): the broker drives a second Docker daemon over `DOCKER_HOST`, moves the files in and out with no bind mount, and runs `runsc` only. The lock is one org for each remote box across every target, counted by the broker's org label on the remote daemon. **Done when:** on the dev stack, a run goes to a second local daemon and the outputs come back through the safe opener. A second org waits while the first holds the box. In production mode the broker refuses a `runc` run on that backend. 🔨 **BUILT dark** (2026-11-19, branch `mt-97db21a4-sbx15-remote-runsc`). The settings are `sandbox_backend` (default `docker`), `sandbox_docker_host` (default empty), `sandbox_docker_cert_path` (default empty) and `sandbox_remote_workspace_mb` (default 512).<br>• Only the broker's own `docker` child gets `DOCKER_HOST`. A log line shows the scheme and the host only. Outside dev a loopback host refuses, and `tcp://` needs TLS with a client certificate.<br>• A container on the remote box has no mount. Its `/workspace` is a tmpfs. Before each command the broker sends the working dir as a tar on the stdin of `docker exec -i`. After the command it reads `/workspace` back as a tar on the stdout of `docker exec`. Both tars use the GNU format. `docker cp` cannot write a `--read-only` root or read a tmpfs.<br>• The copy out takes regular files only, and writes each file in chunks through the safe opener. The copy never sends or deletes a name with a backslash or a byte that is not UTF-8. The model hears which names stayed behind.<br>• Outside dev (`acb_env`) the backend refuses `runc`. Of the mounted targets it runs `code_task` only, and refuses `projects` and `app_builder`. The `compute` target of SBX-18 runs there too.<br>• The org lock: every start of every target awaits `_remote_org_admit` inside one start gate, until `docker run` returns. The admit reads every registry and the org labels on the daemon. The reaper removes only a container whose removal failed. `sandbox_max_per_org` is the one cap of an org, and a start refuses when it is above `sandbox_max_total`.<br>• The end of a run removes its remote container.<br>• Fence SBX-F21. **Done when: met** for `code_task`. A run went to a second local daemon (`docker:dind`) under `runc`, and the outputs came back through the safe opener. A second org waits (`SandboxBusy`) while the first holds the box | The broker (WS-43c), SBX-8 | AGENT-SAFE to build and test on the dev stack. The box, its network and its DNS are **OWNER-GATE** (SBX-G9, SBX-G10) |
| **SBX-15b** (task 3817) | (D227) **The tools half of the remote backend:** a build, a smoke test and a run of a tool go to the remote daemon. It sets `user_tool_max_total` from the measured box and keeps `user_tool_max_per_org` below it (§0c, Gap B) | SBX-4, WS-43g, SBX-15a | AGENT-SAFE. Dark |
| **SBX-15c** (task 3819) | Three follow-ups of the SBX-15a review, before SBX-G13. 1. `DockerCLI.collect` scrubs its stderr through `_scrub`, because `run_compute` gives that stderr to the model. `_scrub` cuts the daemon address to its scheme and host, and removes the cert dir, the home dir of the gateway and each credential shape of `acb_common.secret_shapes`. On the path to the model it also removes the host of the sandbox box and each port. 2. When the `rm -f` of the startup sweep fails, each id goes to the orphan record, and the reaper removes it. 3. When `docker run` times out or a cancel stops it, its start label goes to the orphan record. The reaper looks it up on each tick for 600 s, so a container that the daemon makes late does not stay. The fence of SBX-F21 also checks that the admit is a statement before the run in the start gate. 🔨 **BUILT dark** (2026-11-25). **Done when:** each fix has a test in `test_sandbox_remote_backend.py` that fails before the fix, and the `sandbox_docker` half passes on a local daemon | SBX-15a, SBX-18 | AGENT-SAFE. Dark |
| **SBX-16** (task 3431) | (D187) **The platform counts view** (§6.8, §9.2) in the Operator Console. A later slice. **Done when:** a platform admin sees counts by category and by built-in tool, and no tenant text. A tenant session gets 404 | SBX-1, SBX-5, SBX-14 | AGENT-SAFE |
| **SBX-17** (task 3432) | (D187) **The adoption consent** (§6.8): the consent card for an owner of the source organization, and the consent record that a PR names. **Done when:** no path reads the files of a tool of a tenant for adoption without a consent row. A consent row names one tool, one version and one person of the source organization | SBX-16, SBX-6, SBX-Q9 | AGENT-SAFE to build. Each adoption is **OWNER-GATE** (SBX-G12) |
| **SBX-18** (task 3815) | (D227) **The `compute` broker target** (§0c, rung 6a′): no mount, a `--tmpfs /workspace` workdir and `--network none`. Files go in and out as tar over `docker exec`, parsed in memory. It takes regular files only, with flat names, no links, the types csv, png, json and txt, at most 5 files, 1 MB each and 3 MB in total. Limits: 1 CPU, 1g and pids 64. Its own caps are `compute_max_per_org=1` and `compute_max_total`, default 1. It starts containers through the org lock of SBX-15a. `compute` is in `MAF_CODING_TARGETS`. The flip is `compute:<org>` in `MAF_CODING_SCOPE` (SBX-G13). `COMPUTE_RUN_ORGS` reloads on SIGHUP and is the hot kill switch only, for one org or all orgs. A reaper stops the containers of a de-listed org. A production guard refuses unless the backend is `docker-remote` with `runsc`. **Done when:** SBX-F26 passes | SBX-8 and SBX-15a (tasks 3807, 3430), for the production guard and the lock | AGENT-SAFE. Dark. The flip is **OWNER-GATE** (SBX-G9 to SBX-G11, SBX-G13, WS43-G2, WS43-G5, SBX-G6). WS43-G1 (BH-3) binds it too (D127.2, §4.6). The owner decides on task 3786 to build BH-3 or waive it for rung 6a′ only. The waiver amends §4.6, and a gateway compromise then gets root on the sandbox box |
| **SBX-19** (task 3816) | (D227) **The `compute_run(code, inputs, timeout_s)` chat tool** for the main chat agent `orchestrator`. The inputs are `tr_` refs of tool results that the turn holds, from the one spill store `tool_result_spill`, keyed by org, member and thread. A `KeepForCompute` step keeps a ref for every plain-text tool result of the turn, from 1 character to 2 MB, while compute is on for the org. It does not depend on `TOOL_RESULT_SPILL` or on the 24,000-character threshold. Eviction works for each owner. The output is framed as untrusted. Files reach the chat as artifacts through the `write_artifact` seam. A daily cap of 50 runs for each org and 20 for each member goes through `tenant_redis`. Log lines hold no code and no output text. A run writes a `usage_event` at price 0 through the one meter if that is one call, and if not, SBX-7 does it. **Done when:** SBX-F25 passes | SBX-18 | AGENT-SAFE. Dark |

### 12.1 SBX-1 — the first dispatchable slice

**Scope (D187).** The tables of §7.3 and §7.4, with the generated RLS.
`find_tool` on the general agent, with the five checks of §6.1. The
`sandbox.tool.gap` log line. A read route for an approver's gap view.

No container starts, no capability exists, and no code runs.

**Not in SBX-1 (D187).** No column of `app_versions` changes, because SBX-5
adds the files (§7.2). `sandbox_capabilities` comes with SBX-2. The library
check finds no tool module until SBX-5, so it finds packages of every kind.

**Why first.** It answers #234, #235 and the counter half of #242. It costs
nothing, needs no owner act after the board row, and gives the team real gap
counts before anyone spends on a runtime. (D187) The compose check sends a
need to Studio before any sandbox exists.

**The flag.** `user_tool_find_enabled`, default off. Off, the general agent
holds no `find_tool`.

**Files (re-verify at dispatch).**

- New: `apps/services/gateway/gateway/routes/user_tools.py`, the gap view
  route.
- New: `packages/acb_skills/acb_skills/user_tool_tools.py`, `find_tool`.
- Changed: `packages/acb_common/acb_common/settings.py`, the flag.
- Read, not changed (D187): `apps/services/gateway/gateway/package_host/resolve.py`
  (`read_installs` at :316, `system_contributions` at :311), and
  `apps/services/gateway/gateway/routes/studio/actions.py` (`catalog_rows` at :149,
  `package_rows` at :203).
- New (D187): `apps/services/gateway/gateway/studio/package_search.py`, with one
  read function `search_visible(db, viewer, groups, query, limit=5)`. It is the ONE
  library search. It keeps a package only when one of three conditions holds:
  1. The viewer owns it, it is not archived, and it has a live version.
  2. `read_installs` returns an install for it.
  3. It is a `system` package.

  It never selects from `apps` across members. `find_tool` imports it inside the
  function, as `history_tools.py:201` does for `gateway.rooms`.
- New (D187): a member filter for the compose check, in `package_search.py`:
  `callable_catalog(user, rows)`. It drops a row that `user.can_use_feature` refuses.
  Call `catalog_rows()` and `package_rows()` BEFORE a session is open. Their docstrings
  require it.
- Changed (D187): `_tool_injection.py`, in the `decide_tool_for` block near :2303. Add
  `find_tool` to `_all_tools` only when `user_tool_find_enabled` is on.
- Changed: the gateway router list and `tests/unit/test_studio_route_gate.py`
  `EXPECTED_ROUTES` if the gap view route sits under `/studio`. The route requires
  the `studio:publish` verb (§8.1) and reads one org only.
- New tests: `test_tool_gap_detection.py`, `test_tool_compose_check.py`,
  `test_user_tool_registry.py`, `test_user_tool_injection.py` and
  `test_sandbox_log_lines.py`, all under `tests/unit/`.
- New: the migration, at the next free number at build time. (D187) It adds
  `tool_gaps` and `user_tool_runs` only.
- New: the fences SBX-F7 (its `find_tool` half), SBX-F8 (its record half),
  SBX-F9 (its RLS half), SBX-F15 (its gap half), SBX-F18 and SBX-F23 (D187).

**Done when.**

1. The migration applies on a fresh dev database and on a copy of the
   current schema, and `test_tenant_coverage.py` passes.
2. As the NOBYPASSRLS role, org A cannot read a `tool_gaps` row of org B.
3. With the flag off, no agent holds `find_tool`.
4. With the flag on, `find_tool` writes one `tool_gaps` row for each call,
   with `answer` and `outcome` set.
5. (D187) A published Studio package of the member that matches the need comes back
   as `reuse`. The test makes it with `create_package`, then `publish_version`. A draft
   with no live version never comes back as `reuse`.
6. (D187) A need that two catalog actions cover comes back as `compose`. Its
   gap row reads `compose`, never `no_match`, so no later build can take it.
7. (D187) As member B, a private package of member A with no install that reaches B never
   comes back. It is not in `candidates`, in the decide prompt or in the gap row. An
   org-scope install of A's package does come back. A paused or archived install does not.
8. The log line holds no `need` text.
9. (D187) A catalog row that the caller's `can_use_feature` refuses never reaches the compose
   check. `find_tool` opens no session while `catalog_rows` runs.

**Verification commands.**

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_tool_gap_detection.py tests/unit/test_tool_compose_check.py tests/unit/test_user_tool_registry.py tests/unit/test_user_tool_injection.py tests/unit/test_sandbox_log_lines.py tests/unit/test_tenant_coverage.py -q -rs
```

A run with any skipped test in the six files fails the slice. R8 needs the
database.

### 12.2 SBX-2 — the run capability and the read-only door

**Board.** Metorite task 3801. Risk tier HIGH: it adds an identity path to
`get_current_user`, an RLS-exempt table and a migration. The anchors below
were read on 2026-11-26 at `main` `20f974917`. Re-verify each anchor at
dispatch.

**Depends on.** SBX-Q6, which D130 answers. SBX-2 uses no code of SBX-1. It
writes no `tool_gaps` row and no `user_tool_runs` row. The §12 row names
SBX-1 for the phase order of D187 only, and Metorite holds that order. The
orchestrator confirmed on 2026-11-26 that Metorite orders SBX-2 now.

**Scope.**

- The table `sandbox_capabilities` (§7.5) and one module that mints,
  revokes, sweeps and looks up a capability.
- The door identity step: a sibling of branch 0 in `get_current_user`.
- `SANDBOX_ROUTES`: GET reads of CRM and Projects only.
- The flag `SANDBOX_DOOR_ORGS`, the call cap, the page cap, the size cap and
  the log lines `sandbox.capability` and `sandbox.door.call`.

Only tests mint a capability. No container starts, and no tool code runs.

**Not in SBX-2.**

- No write. Each method other than GET gets 403. SBX-3 holds a write for a
  card, and builds `POST /sandbox/v1/batch`.
- No mint by the broker. SBX-4 mints a capability for each step.
- No Email, Calendar or API connection read. SBX-2b, SBX-2c and SBX-2d
  below take them.
- No `user_tool_runs` row. The counts go in the door log line, and SBX-4
  copies them into the row.
- No `meta.via` stamp. SBX-3 owns it (decision 12).
- No change to branch 0, to `MCP_ROUTES`, to the edge or to the app door.

#### The decisions

**1. The door calls the existing routes.** The code sends `GET /crm/leads`,
as the browser and the MCP edge do. §5.2 says that the door calls the same
handlers as the browser. A copy of each read under `/sandbox/v1/` is a
second route for each handler, and so a second seam. So SBX-2 adds no
route. `/sandbox/v1/` keeps one reserved path, `POST /sandbox/v1/batch`, for
SBX-3. This replaces the first sentence of §5.2 for reads.

**2. `SANDBOX_ROUTES` comes from the app manifests.** AD-3 and D136 say that
an allowlist comes from the manifests, never from a hand list.

- `Route` (`acb_common/app_manifests/base.py:172`) gets one new field,
  `sandbox: bool = False`. `__post_init__` (`:213`) refuses `sandbox=True`
  on a row that is not class A, or not GET.
- `SANDBOX_ROUTES` is the set of `(method, path)` of each row with
  `sandbox=True` in `CRM` (`crm.py:86`) and `PROJECTS_DOOR` (`projects.py`).
  `SANDBOX_APPS` names these two apps, and no other app.
- **The default is off.** A new row, and each row that this slice does not
  name, stays off the door. A row joins only by an edit to its own line.
- The class alone is not enough. Some class A GET rows write, call a model
  or return a file (the lists below). So each row is a choice.
- §5.2 said "it starts from `MCP_ROUTES`". SBX-2 does not do that.
  `MCP_ROUTES` holds pairs of the `/agent-access` surface of outside agents,
  and the write pairs live beside them in `MCP_WRITE_ROUTES`. A pair joins
  `SANDBOX_ROUTES` only through its manifest row.

**The CRM rows that SBX-2 opens (30).** Each reads the local tables. It
writes nothing, calls no model and returns JSON.

| Family | Rows |
|---|---|
| Records | `GET /crm/<kind>` and `GET /crm/<kind>/{record_id}` for `leads`, `deals`, `contacts` and `organizations` (8 rows) |
| Modules | `GET /crm/modules`, `GET /crm/modules/{module}/records`, `GET /crm/modules/{module}/records/{record_id}` and its `/timeline` |
| Reports | `GET /crm/reports/pipeline`, `/funnel`, `/win-loss` and `/owners` |
| Pipeline | `GET /crm/pipeline`, `GET /crm/pipelines` and `GET /crm/aggregate/stage-dwell` |
| Shape | `GET /crm/fields/{entity}`, `GET /crm/fields-search`, `GET /crm/catalogue`, `GET /crm/statuses/{kind}`, `GET /crm/lost-reasons` and `GET /crm/mode` |
| Other | `GET /crm/owners`, `GET /crm/activities/{activity_id}`, `GET /crm/change-history`, `GET /crm/deals/{deal_id}/contacts` and `GET /crm/views` |

**The CRM class A GET rows that stay off, and why.**

| Rows | Why |
|---|---|
| `GET /crm/aggregate` (`aggregate.py:1884`), `GET /crm/terms/resolve` (`terms.py:122`), `GET /crm/mapping/plans` (`mapping_plans.py:1240`) | Each one can write a row (`aggregate.py:1742`, `terms.py:165`, `mapping_plans.py:1245`) |
| `GET /crm/notes/search` (`note_search.py:163`) | It embeds the query through the Router (`note_search.py:182`). That is a model call, and it costs money |
| `GET /crm/questions` and `GET /crm/questions/ask` | Each writes the evidence cache (`questions.py:1586`) |
| `GET /crm/export/<kind>.csv` (4 rows) | A file download. The record rows give the same data |
| `GET /crm/<kind>/{record_id}/timeline` (4 rows) | Each also returns the subject and the snippet of the member's own mail (`activities.py:397`). Mail waits for SBX-2b |
| `GET /crm/connection`, `GET /crm/sync/status`, `GET /crm/sync/health`, `GET /crm/cutover/status` | Connection and sync state. A tool needs none of it |
| The 15 admin rows: `field-rules`, `conversion-mapping`, `analysis/status`, `cutover/preview`, `mapping/plans/{version}`, `pipelines/preview`, `source-profile`, `questions/{question_id}/sum-check`, `settings`, `settings/counts`, `mapping/suggestions`, and the four above that are admin too | The setup of the CRM, for an admin in CRM settings (§19.9) |
| `GET /crm/questions/mine`, `GET /crm/rules`, `GET /crm/digests`, `GET /crm/verified-queries/{key}`, `GET /crm/sync/short-reads`, `GET /crm/sync/mirror-age` | The questions, the rules and the digests of the CRM assistant, and sync counts. They are not record data. A later slice may open one with a reason |
| `POST /crm/mapping/preview`, `POST /crm/verified-queries/match` | Class A, but not GET. SBX-2 opens GET only |

**The Projects rows that SBX-2 opens (38).** Each one reads through the
visibility seam of Projects, writes nothing, calls no model and returns
JSON. 17 of them are in `MCP_ROUTES` today, so outside agents already read
them. Each path below starts with `/projects`.

| Family | Rows |
|---|---|
| In `MCP_ROUTES` (17) | `/tree`, `/nodes/{project_id}`, `/nodes/{project_id}/updates`, `/tasks`, `/tasks/stats`, `/tasks/{task_id}`, `/tasks/{task_id}/timeline`, `/tasks/{task_id}/attachments`, `/tasks/{task_id}/relations`, `/assignable-members`, `/nodes/{project_id}/statuses`, `/nodes/{project_id}/types`, `/nodes/{project_id}/fields`, `/nodes/{project_id}/tags`, `/plan/order`, `/plan/analysis`, `/search` |
| Summaries and views (5) | `/summary`, `/nodes/{project_id}/summary`, `/nodes/{project_id}/views`, `/calendar`, `/intake` |
| The member's own (6) | `/my/project`, `/my/contexts`, `/my/areas`, `/my/reminders`, `/my/today`, `/notifications` |
| Analytics (4) | `/analytics/stuck`, `/throughput`, `/finished` and `/load` |
| Reports (5) | `/reports`, `/reports/templates`, `/reports/subjects`, `/reports/{report_id}` and `/reports/{report_id}/render` |
| Recurrence (1) | `/tasks/{task_id}/recurrence` |

- `/tasks/{task_id}/attachments` returns metadata only (`attachments.py:404`).
  The bytes route `GET /projects/attachments/{attachment_id}/{filename}` is
  class X, so it stays off.
- The analytics and report rows filter their own answer by the reader's
  permissions. That is the route's own check, and the door keeps it.

**The Projects class A GET rows that stay off (38), and why.**

| Rows | Why |
|---|---|
| `/tasks/{task_id}/refs` (`refs.py:498`) | Its default `refresh=true` calls GitHub and can write a task, its activities and its runs (`refs.py:404`) |
| `/nodes/{project_id}/grants`, `/audience`, `/authorities` | Grant data (§19.11, D-PM-40) |
| `/nodes/{project_id}/status-set` | It names projects with no visibility check (`admin.py:908`) |
| `/assigned-to-me`, `/my/inbox`, `/my/tasks/{task_id}`, `/my/tasks/{task_id}/lanes`, `/my/calendar`, `/my/led` | These read by assignment or lead, with no call of the visibility seam (`MY_TASKS_FROM`, `personal.py:1284`). No door reads them today. A later slice opens one after a review |
| `/assignees` (`assignees.py:184`) | It gives the skills and `away_today` of each person to every member (`assignees.py:228`, `:270`) |
| `/decisions`, `/people/names`, `/people/reach`, `/tasks/{task_id}/candidates`, `/candidates`, `/reports/{report_id}/recipients`, `/tasks/{task_id}/watchers`, `/nodes/{project_id}/watchers`, `/templates` | Each one names other people, their absences or their skills, or an item of a hidden project |
| `/analytics/capacity`, `/analytics/outlook`, `/analytics/conflicts`, `/analytics/rebalance`, `/analytics/dataset` | Each gives HR data (absences, hours, load of people) to a reader who holds `admin:members:read`. A later slice may open one, with its own review |
| `/nodes/{project_id}/cycles`, `/cycles/{cycle_id}`, `/nodes/{project_id}/vocabulary/reach` | Each counts items that the member cannot see. No door reads them today |
| `/tasks/{task_id}/agent-runs`, `/agent-runs`, `/agent-runs/{run_id}`, `/{project_id}/agent-summary`, `/agents/overview`, `/agents/{agent_key}/runs`, `/needs-attention`, `/agent-questions` | The surface of agent runs and questions. `/agent-runs/{run_id}` can write `read_at` (`agent_runs.py:3138`) |
| `/export/tasks.csv` | A file download |
| `POST /projects/reports/preview` | Class A, but not GET |

**3. The trigger and the order of the checks.**

- **The header.** `X-Sandbox-Capability: msbx_<token>`. The token is
  `secrets.token_urlsafe(32)`, 43 characters, so the value matches
  `^msbx_[A-Za-z0-9_-]{43}$`. The header is not `Authorization`, because
  branch 1 reads that header. A separate name keeps the trigger a test of a
  header name, as branch 0's `X-Agent-` prefix is.
- **The trigger.** Any header whose name starts with `x-sandbox-`, in any
  case. A second header cannot hide the first, as `_is_agent_request`
  (`deps.py:410`) does it.
- **The place.** The sibling runs FIRST in `get_current_user`
  (`deps.py:1145`), before branch 0 at `:1176`. The sibling decides each
  request that triggers it. It NEVER falls through to branch 0, 1a, 1b or 2.
- **One credential only.** The sibling refuses with 401 a request that
  also carries `Authorization` (any value, a valid internal bearer too),
  `X-User-Email`, `X-User-Role`, `X-Actor-Via`, any `X-Agent-` header, a
  second `X-Sandbox-Capability`, or another `X-Sandbox-` header. So
  `X-User-Email` never reaches branch 1a.

The steps, in the order that the code runs them:

1. `SANDBOX_DOOR_ORGS` is not empty. Else 401, with no database read and no
   Redis call.
2. The header shape and the one-credential rule. Else 401.
3. The lookup (decision 6) finds a live row: not revoked, and before
   `expires_at`. Else 401. A database outage answers the one 503 of
   `_identity_unavailable` (`deps.py:261`), with `Retry-After: 2`. Any other
   failed read is a 401, as step 4 of branch 0 says.
4. `SANDBOX_DOOR_ORGS` names the row's organization, or it is `*`. Else 401.
5. The method is GET. Else 403 `sandbox_read_only`.
6. The pair of the method and `_matched_template(request)` (`deps.py:1426`)
   is in `SANDBOX_ROUTES`. A class X row of `SANDBOX_APPS` gets 403
   `sandbox_route_excluded`. Any other pair gets 404 `sandbox_route_unknown`.
7. The row's `read_scopes` hold the read scope of the pair's app (decision
   7). Else 403 `sandbox_scope`.
8. The query parses, with no repeated key, through `agent_signing.query_pairs`.
   Else 422 `sandbox_bad_query`. Then the page cap. A value of `limit`,
   `page_size` or `per_lane` passes only when it matches `^[0-9]{1,3}$` and
   is 100 or less. Any other value of these keys gets 422 `sandbox_page_cap`.
9. The call cap (decision 9). Else 429. A Redis outage gives 503.
10. `_with_resolved_access` (`deps.py:244`) resolves the member of the ROW.
    The resolved organization must equal the row's, and the access must be
    active. Else `clear_tenant()` and 401, or the 503 of an outage, as step
    7 of branch 0 does.
11. The sibling returns the member's `UserContext` with a new field
    `sandbox: SandboxScope | None` (`acb_auth/roles.py:81`). It holds the
    run, the thread, the phase, the app id, the version and the read scopes.
    It never holds the token or its hash. `agent` stays `None`, because a
    capability has no pins and no personal switch.

No step before step 10 binds a tenant. So a refused request leaves no tenant
bound.

**Each refusal raises.** The sibling raises an `HTTPException` for each
refusal. It never returns the anonymous context. Branch 0 returns that
context, but `require_authenticated` (`deps.py:1481`) lets an anonymous
context through on a template of its `public` list. So a request with a
capability never reaches a public route. A WebSocket route gets no
`Request`, so the sibling does not run there. Such a request is then
anonymous, and that route's own check refuses it.

The sibling does not call `_bind_session_member`, because no row of
`SANDBOX_ROUTES` calls a model (decision 2).

**The log level.** A refusal before step 3 logs at info, because anybody on
the network can cause it. A refusal after step 3 logs at warning, as
`_UNSIGNED_REFUSALS` (`deps.py:436`) splits branch 0.

**The digest.** The two new functions that see the request,
`_is_sandbox_request` and `_sandbox_user`, join `_BRANCH_ZERO`
(`tests/unit/test_tenant_request_binding.py:238`). The helpers that do not
see the request join `_BRANCH_ZERO_HELPERS`. So the scan, the tripwire and
the ONE digest pin (`_BRANCH_ZERO_DIGESTS`, `:1210`) cover the sibling.

- Why not a second pin: the scan already refuses a request handed to a
  function outside `_BRANCH_ZERO`. A second pin for the same boundary would
  be a second fence that drifts.
- The scan's header list gains `x-sandbox-capability`. It also gains the
  four header names that the sibling reads only to refuse: `authorization`,
  `x-user-email`, `x-user-role` and `x-actor-via`. The sibling reads the
  `x-agent-` prefix through the header names, as branch 0 does.
- `_TRIGGER_PREFIX` (`test_tenant_request_binding.py:360`) becomes a tuple:
  `("x-agent-", "x-sandbox-")`.
- The ContextVar of decision 9 stays. The scan allows only
  `request.scope.get` on the scope, so the sibling cannot write the scope.
- The pin changes in one commit. That commit carries the diff-reviewer
  review and the independent verification of `external_agent_access.md`
  §10.1.
- `acb_auth/sandbox_capabilities.py` is outside that digest, as
  `agent_tokens.read_live_connection` is. SBX-F6 pins its own digest in
  `tests/unit/test_sandbox_door_identity.py`.

**4. The flag: `SANDBOX_DOOR_ORGS`.**

- A `Settings` field `sandbox_door_orgs: str = ""`, beside
  `compute_run_orgs` (`settings.py:2568`). A comma list of organization ids,
  or `*`. The default is empty, so the door is off.
- Its one reader parses it as `api_connections_flag.py:22` does: split on
  commas, trim, lower case.
- The name ends in `_ORGS`, so a SIGHUP reloads it
  (`gateway/flag_reload.py:60`). A flip ends no chat run.
- **The org is not known before the lookup.** An empty flag refuses every
  request at step 1, with no read. A flag with a value lets the lookup run,
  and step 4 checks the row's organization.
- **Why not the `tools:` entry.** `tools` is not in `MAF_CODING_TARGETS`
  (`sandbox_broker.py:125`). `parse_maf_coding_scope` raises `ValueError`
  on an unknown target, and the check then turns every target off (§0c
  item 4). Also, the gateway does not read `MAF_CODING_SCOPE`. SBX-4 adds
  `tools`, and `tools:<org>` stays the feature flag of a run (§6.2).
  `SANDBOX_DOOR_ORGS` is the hot kill switch of the door.
- `*` follows §0c: allowed only while self-serve signup is off. The code
  holds the rule. `door_orgs()` reads `*` as empty while
  `SELF_SERVE_SIGNUP_ENABLED` is on, as `compute_enabled` does
  (`sandbox_broker.py:805`).
- SBX-2 flips nothing. The production value joins the SBX-9 checklist, and
  each flip there is an owner act (SBX-G1).
- **A note for the SBX-9 checklist.** The Caddy block of the public host
  strips each `X-Sandbox-` header, so the door answers on the private
  network only. That is a `deploy/` change, and an owner act.
- The builder adds one paragraph to `docs/flag_reload.md`, under "The flags
  that reload", in alphabetical order:

```text
`SANDBOX_DOOR_ORGS` opens the sandbox door (`agent_sandbox.md` §12.2,
SBX-2). Empty, each request with a capability gets 401 before any read. `*`
lists every org, and it is allowed only while self-serve signup is off. Its
one reader is `acb_auth.sandbox_capabilities.door_orgs`. A reload applies a
change. The flip stays OWNER-GATE (`enforcement-flip`).
```

**5. A new exempt table, not a new `kind` of `agent_token`.**

- `agent_token` has `connection_id NOT NULL` with a foreign key to
  `agent_connection`, and a CHECK of three kinds
  (`248_agent_access_tokens.sql:151`, `:169`, `:177`). A capability has no
  connection. It has a run, a thread, a phase and two scope sets.
- A new kind would relax a NOT NULL and a CHECK on a live auth table. It
  would also give its columns a second meaning. §7.2 calls that a defect.
- So SBX-2 adds `sandbox_capabilities`. It costs one exempt entry.
  `test_tenant_coverage.py:89` raises the cap from 19 to 20. A new test there
  pins the entry and its reason.

The reason in `gen_tenant_migration.EXEMPT` (`scripts/gen_tenant_migration.py:70`):

```text
"sandbox_capabilities": "looked up by token hash before the tenant is known; returns organization_id so the door can bind it"
```

The columns are those of §7.5, with these rules:

| Column | Rule |
|---|---|
| `token_sha256` | Primary key. `CHECK (token_sha256 ~ '^[0-9a-f]{64}$')` |
| `organization_id` | Not null. Foreign key to `organization (id)`, `ON DELETE CASCADE` |
| `member_email`, `run_id`, `thread_id` | Not null |
| `app_id`, `version` | Null |
| `phase` | Not null. `CHECK (phase IN ('build', 'smoke', 'run'))` |
| `read_scopes`, `write_scopes` | Not null, default `'{}'`. `CHECK (phase <> 'smoke' OR cardinality(write_scopes) = 0)` |
| `created_at` | Not null, default `now()`. New beside §7.5, for the next rule |
| `expires_at` | Not null. `CHECK (expires_at <= created_at + interval '15 minutes')` |
| `revoked_at` | Null |

- Two indexes: `(organization_id, run_id)` for the revoke of a step, and
  `(expires_at)` for the sweep.
- **Grants.** No RLS policy, because the table is exempt. The file revokes
  every privilege from `PUBLIC` and from `anon`, `authenticated` and
  `service_role`, in the loop of `222_chat_session_exists.sql:96`. It grants
  `SELECT, INSERT, UPDATE, DELETE` to `acb_app` in a guarded block. The
  default privileges give the same, and the explicit grant makes the
  intent readable.
- **The number (R1).** Take the next free number at build time. `main`
  holds 498, and the production ledger holds up to 501. So take a number
  above both, and run `bash scripts/premerge.sh` before the merge.
- **Expand only (R6).** A new table with `CREATE TABLE IF NOT EXISTS`. It
  changes no table that exists, and old code names nothing new.

**6. The capability API.** One module,
`packages/acb_auth/acb_auth/sandbox_capabilities.py`. `acb_auth` holds it,
because `deps.py` imports it and `acb_auth` cannot import the gateway.

```python
TOKEN_PREFIX = "msbx_"
MAX_TTL_S = 900
PHASES = ("build", "smoke", "run")

async def mint(*, org: str, member: str, run_id: str, thread_id: str,
               phase: str, read_scopes: Iterable[str],
               write_scopes: Iterable[str] = (), app_id: str | None = None,
               version: int | None = None, ttl_s: int = MAX_TTL_S) -> Minted
async def revoke(token_sha256: str) -> bool
async def revoke_step(*, org: str, run_id: str, phase: str) -> int
async def sweep() -> int
async def lookup(raw: str) -> Capability | None
def door_orgs() -> frozenset[str]
```

- **`mint`** writes one row and returns `Minted(token, expires_at)`. The raw
  token leaves the module once, in that value.
- **One clock.** `mint` computes `expires_at` in the INSERT as `now() +
  make_interval(secs => :ttl_s)`, and reads it back with `RETURNING`. The app
  clock never sets `expires_at` or `created_at`. So the CHECK of decision 5
  holds at `ttl_s=900` when the two clocks differ.
- `mint` raises `ValueError` for each of these:
  - `ttl_s` below 1 or above 900. The ceiling is 15 minutes (§5.1).
  - A phase outside `PHASES`.
  - A read scope outside `SANDBOX_READ_SCOPES` (decision 7).
  - Any write scope. In SBX-2 `write_scopes` is always empty. SBX-3 opens it
    for `build` and `run`, never for `smoke`.
  - An `org` that is not a UUID, or an empty `member`, `run_id` or
    `thread_id`.
- **R5e.** `org` and `member` come from the run binding, never from tool
  input or a request: `executor._current_run_org()` (`executor.py:514`) and
  the run's member. The ONE caller is the broker step of SBX-4. Until SBX-4,
  only tests call `mint`. SBX-F1 scans the source and fails when a module
  outside `tests/` calls `mint`. SBX-4 names its one caller in that scan.
- **`revoke`** sets `revoked_at` on one live row. **`revoke_step`** sets it
  on each live row of one run and phase. The end of a step calls it.
- **`sweep`** deletes each row 1 day after `expires_at` (§7.5). SBX-2 has no
  caller of it, because only tests mint. SBX-4 schedules it with the first
  real mint.
- **`lookup`** copies `agent_tokens.lookup_token` (`agent_tokens.py:1942`).
  It rejects a value with the wrong shape before any read. It hashes the
  whole header value, the prefix `msbx_` included, with
  `agent_tokens.hash_token` (`:206`). `mint` hashes the same string. One point read by the key
  `token_sha256`, in an UNBOUND session (`agent_tokens._session`, `:699`), with
  `expires_at > now() AND revoked_at IS NULL` in the WHERE. It binds no
  tenant.
- **One capability for each step.** SBX-4 needs this, because the build,
  the smoke test and the run each have their own phase and scopes. The
  broker passes the environment only at `docker run`
  (`sandbox_broker.py:1292`), and `exec()` takes no environment (`:3488`).
  So SBX-4 must deliver a fresh token for each step. Two ways exist: `docker
  exec -e`, or a file in the run-data dir. That choice is SBX-4's work. The
  API already allows any number of rows for one run.

**7. The scopes.** The read scopes use the `<app>:read` words of
`agent_oauth.py:79` and `App.read_scope` of the manifests.

- `SANDBOX_READ_SCOPES` is the read scope of each app in `SANDBOX_APPS`:
  `crm:read` and `projects:read`.
- Each pair of `SANDBOX_ROUTES` comes from exactly one app. So it needs
  exactly one scope: `read_scope` of that app.
- Step 7 checks it with `agent_oauth.scope_holds` (`agent_oauth.py:120`),
  the one cover rule.
- A capability for a tool holds the scopes of its `tool.json` (SBX-4). In
  SBX-2 a test passes them to `mint`.

**8. The answers.** Each refusal has a fixed code. A route's own answer
passes unchanged.

| Case | Status | Code |
|---|---|---|
| The flag is empty | 401 | none |
| No live row: unknown, expired or revoked | 401 | none |
| The flag does not name the row's org | 401 | none |
| A bad header shape, or a second credential | 401 | none |
| A method other than GET | 403 | `sandbox_read_only` |
| A class X row of `SANDBOX_APPS` | 403 | `sandbox_route_excluded` |
| Any other pair off the list, or no matched route | 404 | `sandbox_route_unknown` |
| The row lacks the app's read scope | 403 | `sandbox_scope` |
| A repeated query key, or a query that does not parse | 422 | `sandbox_bad_query` |
| A page larger than 100 | 422 | `sandbox_page_cap` |
| More than 600 calls in the step | 429 with `Retry-After` | `sandbox_call_cap` |
| An answer larger than 4 MB | 422 | `sandbox_response_too_large` |
| The member is no longer active, or in another org | 401 | none |
| The route refuses the member | The route's own 403 or 404 | The route's own |
| A database or Redis outage | 503 with `Retry-After: 2` | none |

The 401 cases are alike on purpose, so a stranger learns nothing. §5.2 said
429 past each limit. SBX-2 keeps 429 for the call count only. The page and
the size get 422, because the same request fails again. A retry after a
wait would not help.

**9. The limits.**

- **Calls: 600 for each capability**, so for each step. A counter in
  `tenant_redis`, in `organization_scope` of the ROW's organization, under
  the namespace `sandbox_door_calls` and the key of the token hash. Its TTL
  is the time to `expires_at` plus 60 s. It copies `_claim_door_read`
  (`deps.py:694`). A refused call before step 9 does not count.
- **Redis down: fail closed.** The door answers 503, as `_claim_door_read`
  does (`deps.py:715`). The door is reachable from the network of the box,
  so an open count is not safe.
- **The page: 100 rows.** The MCP door clamps its pages in the edge
  (`agent_edge/tools.py:1439`, `LIST_LIMIT_MAX = 100`). The sandbox door has
  no edge. So step 8 refuses a query value above 100 for the keys `limit`,
  `page_size` and `per_lane`. The CRM lists already cap `page_size` at 100
  (`routes/crm/core.py:159`). `GET /projects/tasks` takes `page_size` up to
  500 with `compact` (`tasks.py:586`), and the door refuses each value above
  100 there too.
- **What the page cap does not hold.** A route that takes no size key uses
  its own size. `GET /crm/owners` returns up to 500 rows. The 4 MB cap of the
  answer is the backstop for each such route.
- **The answer: 4 MB.** A pure ASGI middleware, `SandboxDoorLimits` in
  `apps/services/gateway/gateway/sandbox_door.py`, acts only on a request
  that the sibling admitted. It reads a ContextVar that the sibling sets at
  step 11. It buffers the answer up to 4,194,304 bytes. A larger answer
  becomes the 422 of decision 8, and no byte of it leaves. The builder proves
  with a test that the ContextVar reaches the middleware.
- **The log line.** The sibling writes `sandbox.door.call` for each refusal.
  The middleware writes it for each admitted call, after the answer
  (decision 13).

**10. Connector reads.** The owner decided on 2026-11-26, in chat:

```text
"sandbox code acts as the member through the one Metorite door; the gateway calls Zoho, Outlook and other connectors with the stored credential; no provider token or platform secret enters the sandbox; reads run, writes wait for the member's card."
```

- **CRM and Zoho, measured.** Each CRM row of SBX-2 reads the local tables.
  `GET /crm/leads` reads `crm_leads` (`routes/crm/core.py:1714`).
  `GET /crm/modules/{module}/records` reads `crm_module_records`
  (`modules.py:683`). No class A GET calls Zoho at request time. Only the
  sync engine and the connection test call `fresh_zoho_credential`
  (`connection.py:659`). So in Mirror mode a tool reads the mirror, as old as
  its last sync. The tool gets no live Zoho read, and no Zoho credential.
- **Deferred by name.** Each is a slice of its own. The dispatcher makes
  each task and writes its number.

| Slice | Scope | It needs |
|---|---|---|
| **SBX-2b** (task 3991) | Email reads through the door, and the CRM timelines that hold mail | `email.py` of MCP-P2a (`external_agent_access.md` §19.10). It is not built. Then `sandbox=True` on the Email rows of class A |
| **SBX-2c** (task 3992) | Calendar reads through the door | A Calendar manifest. §19.1 of `external_agent_access.md` says that Calendar joins only with its own manifest (MCP-P4, MCP-P5). A decision on its classes comes first |
| **SBX-2d** (task 3993) | `POST /connections/api/{name}/read` (`routes/api_connections.py:539`), dark behind `API_CONNECTIONS_ORGS` | A rule for a class A POST on the door: a body cap, and no write. It also needs a manifest row for the route. The route needs `integrations:use:api-<name>` and `integrations:manage` today (`api_connections.py:194`) |

**11. SBX-F2 is deny by default.** No route reaches the door unless its
manifest row says `sandbox=True`. So a new gateway route needs no entry in
an exclusion list. The coverage fences of each app already force a row with
a class for each route: `tests/unit/test_crm_ui_actions.py` and
`tests/unit/test_projects_door_coverage.py`. An exhaustive exclusion list
would make each lane that adds a route edit a sandbox file. That list would
also open a forgotten route by default. This replaces the last clause of the
SBX-F2 row of §11 ("neither on the list nor in its exclusion list").

**12. The stamp `X-Actor-Via`.** The stamp of §5.2, `sandbox:<slug>@v<n>`,
holds `@`. `ACTOR_VIA_PATTERN` (`agent_oauth.py:983`,
`^[a-z0-9][a-z0-9:_.-]{0,63}$`) refuses `@`. So `capture_actor_via`
(`routes/projects/core.py:88`) would store an empty stamp.

- SBX-2 reads only, so no route records a stamp.
- SBX-2 refuses a request that carries its own `X-Actor-Via`, with 401
  (decision 3).
- **SBX-3 owns the fix.** The recommendation: change the stamp to
  `sandbox:<slug>:v<n>`, which the pattern accepts. Do not widen the
  pattern, because the MCP stamps and the timeline share it. SBX-3 also takes
  the stamp from `UserContext.sandbox`, never from a header. The same change
  applies to §8.4.

**13. The log lines.** Through `acb_common.get_logger`, as §9.1 says.

- `sandbox.capability`: org, run, thread, phase, event (`minted`, `revoked`,
  `revoked_step` or `swept`), `expires_at` and the count of read scopes.
- `sandbox.door.call`: org, run, phase, method, route template, status, the
  refusal code or `allowed`, `call_n` (the counter after the call) and the
  answer bytes. A refusal before step 3 has no org and no run.
- No line holds the token, its hash, a header value, a query value or an
  answer body. SBX-4 copies `call_n` and the bytes into `user_tool_runs`.
- SBX-2 writes no audit row for a read. §8.4 names the acts that write one.

#### Files (re-verify at dispatch)

- New: `packages/acb_auth/acb_auth/sandbox_capabilities.py` (decision 6).
- Changed: `packages/acb_auth/acb_auth/deps.py`. `_is_sandbox_request` and
  `_sandbox_user` beside `_is_agent_request` (`:410`) and `_agent_user`
  (`:952`). One line in `get_current_user` (`:1145`), before `:1176`.
- Changed: `packages/acb_auth/acb_auth/roles.py`. `SandboxScope`, and the
  field `sandbox` on `UserContext` (`:81`), default `None`.
- Changed: `packages/acb_common/acb_common/app_manifests/base.py`. The field
  `Route.sandbox` (`:172`) and its rule in `__post_init__` (`:213`).
- Changed: `app_manifests/crm.py` and `app_manifests/projects.py`.
  `sandbox=True` on each row of decision 2.
- Changed: `app_manifests/__init__.py`. `SANDBOX_APPS`, `SANDBOX_ROUTES`
  and `SANDBOX_READ_SCOPES`.
- Changed: `packages/acb_common/acb_common/settings.py`. `sandbox_door_orgs`.
- New: `apps/services/gateway/gateway/sandbox_door.py`, the middleware.
  Changed: `gateway/main.py`, which installs it.
- Changed: `scripts/gen_tenant_migration.py` `EXEMPT` (`:70`), and the
  regenerated files under `infra/postgres/generated/`.
- New: the migration `infra/postgres/<n>_sandbox_capabilities.sql` (decision
  5).
- Changed: `docs/flag_reload.md`, one paragraph (decision 4).
- Changed tests: `tests/unit/test_tenant_request_binding.py` (the lists, the
  header set and the digest), `tests/unit/test_tenant_coverage.py` (the cap
  and the new test).
- New tests: the four fence files below.

#### Done when

1. With `SANDBOX_DOOR_ORGS` empty, a request with a well-formed capability
   gets 401. A spy on the session factory and on `tenant_redis` records no
   call.
2. R8, on a real database as the NOBYPASSRLS app role: a test mints a
   capability for member M of org A with `crm:read` and `projects:read`.
   `GET /crm/leads` and `GET /projects/tasks` answer 200 with M's rows. No
   row of org B is in either answer, and org B has rows of both kinds.
3. The same capability gets 401 after `revoke` and after `revoke_step`. A
   test moves `created_at` and `expires_at` of the row back by 15 minutes
   and 1 second with an UPDATE. Then the capability gets 401. `mint` with
   `ttl_s=901` raises `ValueError`.
4. A POST with a live capability gets 403 `sandbox_read_only`. A class X
   pair (`GET /crm/export/all.zip`) gets 403 `sandbox_route_excluded`. A
   class A row off the list (`GET /crm/aggregate`) gets 404
   `sandbox_route_unknown`. A capability that the flag refuses gets 401 on a
   template of the `public` list of `require_authenticated` too, and that
   route does not run. Each answer leaves no tenant bound.
5. A capability with `projects:read` only gets 403 `sandbox_scope` on
   `GET /crm/leads`.
6. The member and the org come only from the row. A request that adds
   `X-User-Email` of another member, a valid internal bearer, an
   `X-Agent-` header or its own `X-Actor-Via` gets 401. A spy on branch 1a
   records no call.
7. A member whose seat is no longer active gets 401, and no tenant stays
   bound.
8. `mint(phase="smoke", write_scopes=("crm:write",))` raises `ValueError`.
   A direct INSERT of such a row fails on the CHECK. In SBX-2, `mint` with
   any write scope raises `ValueError`.
9. The 601st call of one capability gets 429 with `Retry-After`. With Redis
   down, a call gets 503.
10. A query `page_size=101`, `page_size=0100` or `page_size=1e2` gets 422
    `sandbox_page_cap`. A query with
    `page_size` twice gets 422 `sandbox_bad_query`. An answer of 4 MB plus 1
    byte gets 422 `sandbox_response_too_large`, and none of its bytes leave.
11. SBX-F2 passes: each pair of `SANDBOX_ROUTES` is a GET row of class A of
    CRM or Projects that the live router serves. No pair is class B, C or
    X, under `/agent-access` or a download, or one of the rows that decision
    2 keeps off. `SANDBOX_ROUTES` holds exactly the 68 rows of decision 2:
    30 of CRM and 38 of Projects.
12. The digest pin of `test_tenant_request_binding.py` changed in a commit
    that names the diff-reviewer review and the independent verification.
13. The lines `sandbox.capability` and `sandbox.door.call` hold no token,
    no hash and no answer text. A test captures each line and searches it
    for the raw token and its hash.
14. `test_tenant_coverage.py` passes with the cap at 20, and its new test
    pins the entry of `sandbox_capabilities` and its reason.
15. With `SANDBOX_DOOR_ORGS` set to org B only, a live capability of org A
    gets 401. With `*` and `SELF_SERVE_SIGNUP_ENABLED=true`, a request gets
    401, and a spy on the session factory records no read. A request with
    another `X-Sandbox-` header, or with two `X-Sandbox-Capability` headers,
    gets 401.

#### Verification commands

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_sandbox_capability.py tests/unit/test_sandbox_routes_coverage.py tests/unit/test_sandbox_door_identity.py tests/unit/test_sandbox_door_log_lines.py tests/unit/test_tenant_request_binding.py tests/unit/test_tenant_coverage.py tests/unit/test_crm_ui_actions.py tests/unit/test_projects_door_coverage.py tests/unit/test_flag_doc_sorted.py -q -rs
uv run ruff check packages/acb_auth packages/acb_common apps/services/gateway/gateway/sandbox_door.py
uv run mypy packages/acb_auth/acb_auth/sandbox_capabilities.py packages/acb_auth/acb_auth/deps.py apps/services/gateway/gateway/sandbox_door.py
```

A run with any skipped test fails the slice. R8 needs the database.

#### Fences

| # | Test file | What it holds in SBX-2 |
|---|---|---|
| SBX-F1 | `tests/unit/test_sandbox_capability.py` | Done when 1, 3, 8 and 9, the R8 half of done when 2, and the source scan of the callers of `mint` |
| SBX-F2 | `tests/unit/test_sandbox_routes_coverage.py` | Done when 11, and the rule of `Route.sandbox` in `__post_init__` |
| SBX-F6 | `tests/unit/test_sandbox_door_identity.py` | Done when 4 to 7, 10 and 15, and the digest of `sandbox_capabilities.py` |
| SBX-F29 | `tests/unit/test_sandbox_door_log_lines.py` | Done when 13. It is a new file, because SBX-1 makes `test_sandbox_log_lines.py` |
| — | `tests/unit/test_tenant_request_binding.py` | Done when 12: the scan, the tripwire and the one digest |
| — | `tests/unit/test_tenant_coverage.py` | Done when 14 |

The builder sees each new fence fail first. They are on auth and tenancy, so they
also get mutation testing (`engineering_practice.md` §4).

### 12.3 SBX-13 — the door client `metorite_door`

**Board.** Metorite task 3428. Risk tier STANDARD: one module in the image,
and no gateway change. It depends on SBX-2.

**Scope.** One module that a tool imports to read Metorite as the member,
through the door of §12.2. It goes in the sandbox image, read-only. It uses
`requests` only.

**Not in SBX-13.**

- No write, and no held write. SBX-3 builds the hold, and a later slice
  adds the write calls to the client.
- No hosted name. `sandbox-api.metorite.com` waits for SBX-10.
- No egress proxy and no network change. The real path waits for SBX-4 and
  the proxy (see "The end-to-end proof").

#### The decisions

**1. Where the module lives.** `apps/services/orchestrator/sandbox/metorite_door.py`,
beside `data_engine.py`.

- The image copies it to the site-packages of the image's Python:
  `/usr/local/lib/python3.12/site-packages/metorite_door.py`, root-owned,
  mode 0444. Use one `COPY --chmod=0444` line, after the `pip install` RUN
  of `Dockerfile.coding-sandbox:86-88`.
- Why not `/opt/sandbox`: no line puts `/opt/sandbox` on `sys.path`, and the
  image has no `PYTHONPATH`. `data_engine.py` runs with `python3 -I`
  (`sandbox/data_engine.py:2734`), and `-I` leaves the script's directory
  off `sys.path`. Site-packages stays on `sys.path` under `-I`.
- Why not a `.pth` file: a `.pth` runs at each start of Python, and it adds
  a second way to find code. A plain module in site-packages needs neither.
- **Not in `SANDBOX_MODULES`.** That map (`acb_common/studio_runtime.py:41`)
  must equal the pip list of `requirements.in`
  (`tests/unit/test_coding_sandbox_packages.py:114`), and `metorite_door` is
  not a pip package. So the old row of SBX-13 ("the image test (WS43-F25)
  lists the module") changes. SBX-F22 pins the `COPY` line, as
  `tests/unit/test_data_engine.py:1565` pins the line of `data_engine.py`.
  Its Docker half imports the module under `-I`.

**2. The door name.**

- `DOOR_URL = "http://metorite-api.sandbox"`, port 80. The proxy of WS-43g
  allows ports 80 and 443 only. The name is a constant of the module. The
  client takes no host from input or from the environment.
- HTTP, not HTTPS, on this hop. The hop is the org's own internal network
  on the box, and the proxy forwards the name to the gateway. With HTTPS,
  the image would need a certificate for a private name.
- The hosted name `https://sandbox-api.metorite.com` is SBX-10's work. SBX-10
  adds it as a second constant, picked by the backend, never by input.

**3. The environment.** SBX-F22 says that the client reads no env value
except `METORITE_API_TOKEN`. The precise rule:

- The module's own code reads `os.environ` for `METORITE_API_TOKEN` only.
  A missing value raises `DoorError("no_capability")` before any call.
- WS-43g sets `HTTP_PROXY` and `HTTPS_PROXY` in the container, with the
  grant token in the URL (`maf_coding_engine.md` §7.3). The client never
  reads them itself. It leaves `requests.Session.trust_env` at its default,
  `True`, so `requests` reads the proxy settings inside the library.
- The client never sets `trust_env = False`, and never passes `proxies=`.
  It never prints or logs an env value.
- SBX-F22 scans the module's source. It fails on a read of `os.environ`,
  `os.getenv` or `os.environb` with a key other than `METORITE_API_TOKEN`.
  It also fails on `trust_env`, `proxies=`, or a URL that does not start
  with `DOOR_URL`. A read of the proxy settings inside `requests` does not
  count.

**4. The calls.**

```python
class Door:
    def __init__(self, timeout_s: float = 30.0) -> None
    def get(self, path: str, params: dict | None = None) -> dict | list
    def pages(self, path: str, params: dict | None = None,
              page_size: int = 100, max_pages: int = 50) -> Iterator[dict]
```

- `get` sends one GET to `DOOR_URL + path`, with the header
  `X-Sandbox-Capability`. It sends no other auth header. `path` must start
  with `/` and hold no scheme, no host and no `..`.
- `get` sends `allow_redirects=False`. A 3xx answer raises `DoorError` with
  the code `redirect`.
- **The paging contract.** `pages` reads a list route page by page. It
  sends `page` and `page_size` for a CRM list, and `limit` and `offset` for a
  route that takes them. A table in the module maps each paged route of
  `SANDBOX_ROUTES` to three keys: its size key, its page or offset key, and
  the key of the answer that holds the rows. The builder reads them from the
  route. `page_size` is at most 100, the door cap.
- SBX-F22 fails when a path of that table is not in `SANDBOX_ROUTES`. The
  test imports `acb_common.app_manifests.SANDBOX_ROUTES`, and a skip is not
  allowed. So whichever of SBX-2 and SBX-13 merges second adds this check.
- It stops on an empty page, on a page shorter than `page_size`, or after
  `max_pages`. It yields each row. It never asks for a page above the door
  cap.

**5. The errors.** Each failed call raises `DoorError`, with `status`,
`code` and `retry_after`. Each `code` comes from the answer, or the client
sets a fixed one.

| Answer | `DoorError.code` | What the client does |
|---|---|---|
| 401 | `not_allowed` | Raises at once. The capability is dead, or the door is off |
| 403 | The door code, such as `sandbox_read_only` or `sandbox_scope`, or `forbidden` for a route's own 403 | Raises at once |
| 404 | `sandbox_route_unknown`, or `not_found` for a route's own 404 | Raises at once |
| 3xx | `redirect` | Raises at once. It never follows a redirect |
| 422 | `sandbox_bad_query`, `sandbox_page_cap`, `sandbox_response_too_large` or `bad_input` | Raises at once |
| 429 | `sandbox_call_cap` | Raises with `retry_after`. It never sleeps and retries, because the cap holds for the whole step |
| 503 | `unavailable` | Retries once after `Retry-After`, then raises. It waits 5 s at most, whatever `Retry-After` says |

The text of `DoorError` holds the status, the code and the path. It never
holds the token or the body of the answer.

**6. Held writes are out.** A write method is not in the client. SBX-3 adds
the hold, and a later slice adds the write calls with the held answer of
§5.3.

#### The end-to-end proof

Before WS-43g exists, the broker runs each container with `--network none`.
So no tool can reach a real door yet.

- **SBX-13's own `sandbox_docker` test** builds a test-only Docker network
  outside the broker. On it, a stub door container answers to the alias
  `metorite-api.sandbox` and binds port 80. The stub checks the header and
  serves two pages.
  The tool container joins the same network, imports `metorite_door`, and
  reads both pages with the capability. The broker is not in the test.
- **The real proof moves to SBX-4, with WS-43g.** A tool in a broker
  container reads two pages through the proxy and the real door. This is
  the "two pages through the door" of the old done-when.
- **The forward is not in WS-43g's scope today.** The scope of WS-43g
  (`maf_coding_engine.md` §11, WS-43g, and §7.3) has no forward of
  `metorite-api.sandbox` to the gateway, and no `run` profile. Its done-when
  8 even drops each packet from a sandbox to the host. CA-4 (task 1032) is
  WS-43g. This spec gives the forward and the `run` profile to the slice
  that builds WS-43g. Its lane must add them to its scope. The forward
  passes only a request that carries `X-Sandbox-Capability`.

#### Files (re-verify at dispatch)

- New: `apps/services/orchestrator/sandbox/metorite_door.py`.
- Changed: `apps/services/orchestrator/Dockerfile.coding-sandbox`, one
  `COPY --chmod=0444` line after `:86-88`.
- New: `tests/unit/test_sandbox_door_client.py` (SBX-F22), with a
  `sandbox_docker` half.

#### Done when

This list replaces the done-when cell of the SBX-13 row in the §12 table.
The proof with the real door moves to SBX-4.

1. SBX-F22 passes: the source scan of decision 3, and no host but
   `DOOR_URL`.
2. A unit test with a fake transport reads 250 rows in three pages of 100,
   100 and 50. No request asks for more than 100 rows.
3. A unit test maps 3xx, 401, 403, 404, 422, 429 and 503 to the
   `DoorError` codes of decision 5. The client does not retry a 429. It
   retries a 503 once, and waits 5 s at most.
4. With `METORITE_API_TOKEN` unset, the first call raises `no_capability`,
   and the fake transport records no request.
5. The `sandbox_docker` test of "The end-to-end proof" reads two pages from
   the stub door.
6. The Dockerfile holds the one `COPY --chmod=0444` line. In the image, as
   uid 1000, `python3 -I -c "import metorite_door"` exits 0, and the file is
   mode 0444 and owned by root.

#### Verification commands

```bash
timeout 900 uv run pytest tests/unit/test_sandbox_door_client.py -q -rs
timeout 900 uv run pytest tests/unit/test_sandbox_door_client.py -q -rs -m sandbox_docker
uv run ruff check apps/services/orchestrator/sandbox/metorite_door.py
uv run mypy apps/services/orchestrator/sandbox/metorite_door.py
```

A skip fails the slice. The `sandbox_docker` half runs on
`.github/workflows/sandbox-docker.yml`, as §11 says.

#### Fences

| # | Test file | What it holds |
|---|---|---|
| SBX-F22 | `tests/unit/test_sandbox_door_client.py` | Done when 1 to 6. Its `sandbox_docker` half holds 5 and 6 |

---

## 13. Owner gates

An agent refuses each of these by name. `maf_coding_engine.md` §12 says the
dev-phase window does not open the WS-43 gates, and the same holds here.

| # | Act |
|---|---|
| SBX-G1 | Add `tools:<org>` to `MAF_CODING_SCOPE` on production (it is WS43-G3 for this target). (D187) D144 retired staging. (D227) The entry is `tools:<org>` for each beta org of an explicit list. The entry `*` is allowed only while self-serve signup is off. A first flip for the HathiLabs org proves the real box, and a second flip opens the list. Each flip is an owner act |
| SBX-G2 | Install `runsc` on a box (WS43-G5) |
| SBX-G3 | Open an account with a sandbox vendor, or put its key on a box |
| SBX-G4 | Create the DNS record `sandbox-api.metorite.com`, or its Caddy block |
| SBX-G5 | Buy or rent a host for SPACE |
| SBX-G6 | Set a credit price for sandbox time |
| SBX-G7 | Turn on org sharing of tools |
| SBX-G8 | Set the `web:` profile policy on production, or the production package-host list (WS43-G4) |
| SBX-G9 | (D187) Buy or rent the dedicated sandbox box of §4.6, and pick its plan. **Done** (D231, task 3912): `aroh-sandbox-1` on GCP |
| SBX-G10 | (D187) Set up the private network between the gateway box and the sandbox box, and each DNS record or firewall rule of either box |
| SBX-G11 | (D187) Set `sandbox_backend=docker-remote` on production, or put the key that reaches the sandbox box on a box |
| SBX-G12 | (D187) Read the code of a tool of a tenant for platform adoption (§6.8). It needs the consent record of the source organization. With no record, refuse it always |
| SBX-G13 | (D227) Add an org to `compute:` in `MAF_CODING_SCOPE`, or to `COMPUTE_RUN_ORGS`, on production. Gate id `ws43-sandbox-flip`. The dev-phase window does not open it (`maf_coding_engine.md` §12) |

**Production stays on `runsc`.** SBX-9 turns on the `tools` target on
production only on a box where `sandbox_runtime=runsc` works. On `runc`, the
target stays off there. (D187) On the dedicated box, the `docker-remote`
backend runs `runsc` only.

---

## 14. Owner calls

**Answered 2026-10-27.** D130 takes the recommendation of each call below.
It also sets a cap of USD 200 a month on the `e2b` backend. The owner still
does each act of §13.

**(D187) Amended 2026-11-07.** D187 amends SBX-Q1 for production: the
dedicated box of §4.6 comes first, and `e2b` stays the alternative. D187
names `studio:publish` as the approver of SBX-Q3, in place of
`tools:approve` (§8.1). SBX-Q8 and SBX-Q9 are new. **The owner accepted both
recommendations on 2026-11-07**, in chat ("all approved go ahead"). Task 1962
records the answer, and SBX-0c (task 2188) writes it here.

Every call has a default. A slice that needs a call waits for it, and the
default never ships to production by itself.

| # | Call | The recommendation | Default until the owner answers |
|---|---|---|---|
| **SBX-Q1** | The runtime, and its spend | Slice 1 on the broker we have, under gVisor, at USD 0. At scale, a hosted E2B-compatible service, about USD 150 a month plus use (estimate). SPACE later, through ECW, when §4.5 holds | Docker backend only. No account, no new host |
| **SBX-Q2** | The default egress policy | `build` and `run` profiles only. A `web:` host by member approval, and never in a step that can read Metorite data | As recommended |
| **SBX-Q3** | May a stored tool be shared org-wide? | Yes, after a person with `tools:approve` reads the code and approves one version | No. Every tool stays private |
| **SBX-Q4** | The credit price of sandbox time | Cost plus the tier margin, with a floor of 1 credit for each step | No charge in staging. No production run until answered |
| **SBX-Q5** | May a stored tool run with no chat, in a workflow? | Reads only, and only an org-approved version | No |
| **SBX-Q6** | Accept the run capability inside the container. This amends one line of D83 for the `tools` target | Yes. It is short-lived, opens only the door, and has the member's access as its ceiling | No. SBX-2 and later wait |
| **SBX-Q7** | May member data go to a hosted vendor? This is a DPDP question for WS-37 | Yes only after a data processing agreement and a region check | No. The `e2b` backend runs no tenant data |
| **SBX-Q8** | (D187) May a failure report carry the error text and the workaround code of a member to the Metorite team (§6.7, level c)? | Only with the consent of the member, one report at a time, as D126 does for a chat | **Decided 2026-11-07: the recommendation.** Without a consent, a report holds the counts, the tool name and the error class only |
| **SBX-Q9** | (D187) Who in the source organization consents to a platform adoption (§6.8)? | A person with the `owner` role, on one card for one tool and one version | **Decided 2026-11-07: the recommendation.** Without a consent, no adoption from the code of a tenant. An engineer may still build from the counts alone |

---

## 15. Side findings

The survey for §2 found three things outside this spec. None is fixed
here, and each needs its own check before anyone acts on it.

1. **A docstring and its code disagree.** The docstrings of
   `_tool_injection._withheld_shell_tools` name three tools. The code
   withholds all four of `SHELL_TOOLS`, `run_command` included. The code is
   the safer half.
2. **Two texts disagree on the permission mode.** The docstring of
   `acb_skills/sandbox_tools.py` says production runs `audit`. D85 measured
   `enforce` on 2026-10-03.
3. **Not verified: the mutation approve route.** The survey saw no admin
   role check in `POST /agent/mutations/pending/{commit_id}/approve`
   (`routes/agent.py:4128`). The route needs `require_internal_auth`. A
   reviewer must read the whole call path before this counts as a defect.

---

## 16. Changelog

- 2026-10-19 — Drafted from the owner's choice of "A full capable sandbox"
  and the nine tasks #234 to #242. Nothing built.
- 2026-10-27 — ACTIVE. The owner deferred the calls of §14 to the
  recommendations, and D130 records them. The board row is WS-TO.
- 2026-11-07 — D187 (SBX-0b, task 1962). A user tool is an AI Studio
  package, and a sandbox serves one chat thread for a short time. A tool that
  fails gets one repair, at three levels. A tool reaches all of Metorite only
  as reviewed repo code. Production runs on a dedicated sandbox box. New
  slices SBX-12 to SBX-17. Nothing built.
- 2026-11-19 — D227 (SBX-0d, task 3786). The owner chose the beta orgs for SBX-9 and
  SBX-G1, and put rung 6 ahead of rungs 1 to 5. The home-server pilot is
  withdrawn. D85 stands, and no owner gate opens. §0c holds the sub-rungs 6a
  to 6f, the critical path, the cross-org escape control and two new gaps.
  Nothing built.
- 2026-11-19 — D227, round 3 (SBX-0d). Rung 6a′ adds the compute run, SBX-18
  and SBX-19, as the first production rung. Fences SBX-F25 and SBX-F26. Task
  numbers are in the §12 rows.
- 2026-11-19 — D227, round 4 (SBX-0d). Production shows open signup, so the entry is a list. Control (b)
  is an org lock across every target. The compute target joins the scope seam
  (SBX-G13). SBX-15 splits into 15a and 15b. An escape that stays on the box is
  an open risk for the owner.
- 2026-11-19 — SBX-8 and SBX-15a built dark (tasks 3807 and 3430). The broker
  takes `sandbox_runtime` and the `docker-remote` backend. The org lock binds
  every target, the compute target of SBX-18 too. Fence SBX-F21.
- 2026-11-25 — SBX-15c built dark (task 3819). The broker scrubs the compute stderr.
  A failed sweep removal and a timed-out `docker run` go to the orphan record.
