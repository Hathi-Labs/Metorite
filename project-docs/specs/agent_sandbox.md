# Agent sandbox — build the tool that does not exist yet

<!-- ste-tier: strict -->

- **Status:** ACTIVE, 2026-10-27. **D130** answers every owner call of §14,
  and takes each recommendation. **D187** amends the spec on 2026-11-07
  (§0a): a user tool is an AI Studio package. **D224** sets the order of
  work on 2026-11-15 (§0b): chains of existing actions first, code last.
  **D227** (2026-11-19, §0c) opens SBX-9 to the beta orgs, and rung 6a′ runs compute first.
  SBX-8, SBX-15a, SBX-18 and SBX-19 are built dark (2026-11-19), and SBX-15c
  too (merged 2026-11-26). SBX-1a is built dark and merged (2026-11-26,
  task 3800, `2c3f6ae0c`), behind `user_tool_find_enabled`. SBX-1b is
  built dark and merged (2026-11-26, task 3984, `ab2928139`).
  SBX-2 is built dark and merged (2026-11-26, §12.2, `209884bac`): only tests
  mint, and `SANDBOX_DOOR_ORGS` is empty. SBX-13 is built and merged
  (2026-11-26, §12.3, `0dc23db0f`). SBX-4a (task 3803, PR 2862) is open. §12.4
  holds the spec of SBX-4b (task 3986). Parts (a), (b) and (c) of SBX-4b1 are
  built dark and merged (2026-11-27, task 3986, `ae5b37cde`): the door
  refusal counter, `user_tool_runs` (migration 506) and the reserved agent
  names. The step engine of SBX-4b1 is not built. §12.5 holds the spec of
  SBX-3 (task 3802), with the owner answers of 2026-11-27.
  SBX-3a is merged dark (task 3802, `8b249bbb7`). SBX-3b (task 4084) is
  built dark on its branch, and it is not merged. No other build slice of this spec is on `main`
  (read 2026-11-27 at `8b249bbb7`). The SBX-1
  audit (2026-11-26, §12.1) splits SBX-1 into SBX-1a and SBX-1b.
  **D233** (2026-11-26, §0d) unpauses AI Studio, and adds the build in the same
  chat turn (SBX-20, SBX-21).
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
  D99, D100, D126, D127, D139, D146, D147, D180, D187, D227, D233, R5 to R8.
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
| 5. Connected systems | A skill uses an approved MCP tool, then a Zoho action (3426) | MCP rows reach the catalog (B1b-2). `action_tool` gives None for an `mcp:` or `integration:` id. B1b-3 is built (migration 455). B1b-4 is in flight. The TOF-5a row is written, and TOF-5a-1 is built, dark (2026-11-18) | TOF-5a MCP actions (3423). TOF-5b integration actions (3424), after IN-3 (3176). TOF-5c a direct provider request (3425) | 3176 is OWNER-GATE. 3425 is a decision. `api_connections.md` D-API-2 asks to replace 3425 with a closed operation list, with no wait for IN-3 |
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
   SBX-F23 bind the no-match path. The SBX-1 audit decided how the path keeps
   the `tool_scope` gate (§12.1 decision 1).
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
   (`sandbox_broker.py:126`). The parser raises `ValueError` on an unknown
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
| **6a. The gap log on production** | The owner opens the gap view on production and sees real counts. No code runs | SBX-1a, SBX-1b | `enforcement-flip` of `user_tool_find_enabled`, under a dated grant |
| **6a′. The compute run** | In the chat, a member asks for a calculation on rows that a tool returned. The chat runs pandas code in a sealed container and shows a CSV or PNG card. This is the first rung that runs code in production | SBX-8, SBX-15a, SBX-18, SBX-19 | SBX-G9 to SBX-G11, SBX-G13, WS43-G1 (BH-3, or the waiver), WS43-G2, WS43-G5, SBX-G6 |
| **6b. The door and a tool (dev-stack preview)** | The chat builds a read-only tool. It reads two pages through the door, and passes its smoke test | SBX-2, SBX-13, SBX-4, WS-43g (WS-43g1 merged dark, WS-43g2a merged dark a95d58d09, PR 2924) | None |
| **6c. Keep and reuse, and a write behind a card (dev-stack preview)** | The chat saves the tool, and a new thread reuses it. A tool that writes shows a card first. (D233) On no match, the chat builds and answers in the same turn, with one progress line | SBX-3, SBX-5, SBX-20, SBX-21 (D233) | None |
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
The rows SBX-4, SBX-8, SBX-15a, SBX-15c, SBX-18 and SBX-19 were read again on
2026-11-26 at `4804312a4` (SBX-0f, task 3996). The other rows keep the first
reading.

| Needs | Status | Evidence |
|---|---|---|
| SBX-1 | SBX-1a built dark and merged (2026-11-26, task 3800, `2c3f6ae0c`). SBX-1b built dark and merged (2026-11-26, task 3984, `ab2928139`), behind the 404 of the route | §12 rows SBX-1a and SBX-1b |
| SBX-2 | Built, dark behind `SANDBOX_DOOR_ORGS` (task 3801, merged 2026-11-26, `209884bac`). Migration 503 | `SANDBOX_ROUTES` in `acb_common/app_manifests`. `acb_auth/sandbox_capabilities.py`. `tests/unit/test_sandbox_capability.py` |
| SBX-3 | SBX-3a built dark and merged (task 3802, 2026-11-27, `8b249bbb7`, PR 2926). SBX-3b (task 4084) built dark on its branch, not merged | `gateway/sandbox_batch.py`. `SANDBOX_WRITE_ROUTES` in `acb_common/app_manifests`. `tests/unit/test_sandbox_door_writes.py` |
| SBX-4 | Not built. PR 2862 (SBX-4a, an inert target) was open on 2026-11-26. §12.4 specifies SBX-4b | `MAF_CODING_TARGETS` holds no `tools` (`sandbox_broker.py:126`) |
| SBX-5 | Not built | `git log --grep SBX-5` finds the spec commit only. Its input B7 is built |
| SBX-6 | Not built | Its inputs are partly built, see PKG-11 and J2 below |
| SBX-7 | Not built | No `sandbox` row in `task_catalog` (`git grep` of the SQL) |
| SBX-8 | Built, dark (PR 2625, 2026-11-19). The cost of gVisor is not measured | `RUNTIME_RUNSC` in `sandbox_broker.py` |
| SBX-12 | Not built | Needs SBX-5 |
| SBX-13 | Built, read only (task 3428, merged 2026-11-26, `0dc23db0f`). The image on a box is WS43-G2. The proof with the real door moves to SBX-4b (§12.3, §12.4) | `apps/services/orchestrator/sandbox/metorite_door.py`. `tests/unit/test_sandbox_door_client.py` (SBX-F22) |
| SBX-15a, the remote backend and the org lock | Built, dark (PR 2625, 2026-11-19) | `BACKEND_REMOTE` in `sandbox_broker.py`. `tests/unit/test_sandbox_remote_backend.py` |
| SBX-15c, three follow-ups of 15a | Built, dark (PR 2839, merged 2026-11-26) | Merge `e8b692f04`. Its §12 row |
| SBX-15b, the tools half | Not built | Needs SBX-4 and WS-43g |
| SBX-18, SBX-19, the compute run | Built, dark (PRs 2616 and 2623, 2026-11-19). No production flip. `compute_run` writes no `usage_event` yet | `acb_skills/compute_run.py`, `COMPUTE_TARGET` in `sandbox_broker.py`. Tests `test_compute_run.py` and `test_sandbox_broker_compute.py` |
| BH-3, the broker unit (WS43-G1) | Not built. The install on the box is an owner gate, not verified | `git ls-files` lists no `acb-sandbox-broker` unit, and `tests/unit/test_sandbox_broker_socket.py` is absent. `box_hardening.md` §5 BH-3 specifies both |
| WS43-G2 | Owner gate. Image code built, box build not verified | WS-43b and WS-43y1a are on `main` |
| WS43-G4 | Owner gate. Not verified | `maf_coding_engine.md` line 827 |
| WS43-G5 | Owner gate. Task 3912 installed `runsc` on `aroh-sandbox-1` (2026-11-23). Production use is not verified | `maf_coding_engine.md` line 1677. `docs/gcp_aroh.md` §4a |
| WS-43g, the egress proxy | WS-43g1 merged dark (task 4009, PR 2887, `d2bffe963`, 2026-11-27). WS-43g2a (task 4070) is merged dark a95d58d09, PR 2924. WS-43g2b (task 4092) and WS-43g3 are not built. The owner answer (task 4009, answer `b5dbe733`, 2026-11-27): "Approve the merge. Build one network per run in WS-43g2." | `maf_coding_engine.md` §11, WS-43g. `grant_egress()` and `sandbox/egress_proxy.py` are on `main`. The network of each run, the live `request_network_access` and the firewall in CI are not |
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

## 0d. Amendment, 2026-11-26 (D233)

**Asked by:** the owner, 2026-11-26, in chat to metorite-78. `work_plan.md`
§3 records it as D233. The text below gives the meaning of the four
decisions. It is not a quote.

### The four decisions

1. **AI Studio is unpaused** for the long tail of use cases. The chat builds
   the tool that it lacks, in the sandbox, and keeps it.
2. **The work is split.** The PC lanes build the sandbox path: rung 6 of this
   spec, with its sub-rungs 6a to 6f (§0b, §0c). Ishaan's lanes build the
   Studio rungs 1 to 5: save, repair, share and connected-system skills. Those
   lanes also land the aging Studio pull requests, and keep chat parity. A PC
   lane takes no task and no pull request of Ishaan's lanes.
3. **Credentials go through the door.** Sandbox code acts as the member
   through the one Metorite door (§5.1 to §5.4). The gateway calls Zoho,
   Outlook and each other connector with the stored credential. No provider
   token and no platform secret enters the sandbox. A read runs. A write waits
   for the card of the member.
4. **The member sees no seam.** While the sandbox builds and tests a tool, the
   member sees one progress line, as for any long turn. The answer arrives as
   usual. A card appears only when the result writes something. The new skill
   is kept, and the chat reuses it silently.

### What changes

- **The order (the reading of the agent).** The split puts rung 6 and rungs 1
  to 5 side by side. It changes the order of the §0b ladder and of "Rung 6
  runs first" in §0c. The owner has not said how the two orders combine.
- **SBX-20 (task 3981), the background build and same-turn hand-back.** It
  starts when the no-match path of `find_tools` answers `no_match` (§6.1,
  D225). A `compose` answer means that a chain covers the ask, so no build
  starts. The build, the smoke test and the first run go to a sandbox sub-run
  inside the same chat turn. The main chat shows one progress line. When the
  smoke test passes, the chat calls the new tool in the same turn and answers.
- **A failure in SBX-20.** The chat answers what it can. It says plainly what
  it could not do. The gap goes to the gap log (SBX-1, task 3800) and to the
  tool failure log (`tool_failure_log.md`, task 1793).
- **The failure row.** TOF-2b built the store, dark
  (`acb_common/failure_log.py`). Its source `sandbox` is for a member report
  through TOF-2g, and that slice is not dispatchable. So the SBX-20 audit
  decides which call and which source write the row.
- **SBX-21 (task 3982), the progress-line contract.** The member sees one
  line. It names no tool, and it does not use the word "sandbox". The line
  lives in the one run status of `chat_run_continuity.md` §4.10. Its words are
  fixed words of the client, as `RUN_PHASE_WORDS` is (`chat_failure_modes.md`,
  CS-3b). The server sends no text for this line.
- **No second line.** A tool call past 6 seconds gets a server line today
  (`chat_run_continuity.md` §4.11, dark). On the SBX-20 path, that would be a
  second line. So the build and run calls of this path send no §4.11 line.
  SBX-21 builds that exception and writes it in §4.11.
- **Member copy.** The busy answer of §6.2 and §0c names "the sandbox". The
  model can read that string, but the member must not see it. SBX-21 gives the
  member copy.
- **Writes.** A write still shows the normal card of §5.3.
- **Keeping.** §6.5 stands: the tool persists only after a successful first
  run. Then the chat reuses it silently.
- **Approval.** §8 stands with no change. The owner of a private tool needs no
  approval (§8.1, D180.2). The first time that a skill is kept for the org, a
  holder of `studio:publish` approves one version. The brief of SBX-0e asked
  for this line.
- **Order.** SBX-20 and SBX-21 belong to rung 6c (keep and reuse). SBX-20
  needs SBX-3 (task 3802) and SBX-5 (task 3804). SBX-21 needs SBX-20. This
  amendment adds neither slice to the dependencies of SBX-9.
- **Tier.** The agent tiers both slices HIGH, because they run code.
- **Dark.** SBX-20 ships behind its own per-org flag, default off. The
  proposed name is `USER_TOOL_INLINE_ORGS`, and the SBX-20 audit confirms it.
  The agent proposes that SBX-21 uses the same flag. The flag must not turn on
  before SBX-21 merges, because the §4.11 exception comes with SBX-21. This
  amendment flips no flag and opens no owner gate of §13.

### The build card: the owner answer, and the defaults of the agent

Decision 4 says that a card appears only when the result writes something.
§6.1, SBX-F8 and the "Build a tool" row of §8.1 ask for an approved build
card before each build.

**The owner answered on task 3981, 2026-11-26** (answer `8c7fc3a3`, option
`safe`): "No build card, but keep the card for outside content". So the SBX-20
path shows no build card in a normal chat. After outside content, the build
card still shows. The silent path gets no web host and no extra package. It
shows no credit estimate, and the cap of the org still binds. The three texts
above stay as they are for each other path. SBX-20 changes the card rule of
SBX-F8 for its path only.

The first four rows below follow from that answer. The question did not name
the last three rows. Those three are defaults of the agent.

| What the card gives today | The rule of the SBX-20 path | Source |
|---|---|---|
| The warning of a marked session (§5.7) | No silent build in a marked session. The build card of §6.1 shows | Owner |
| The approval of a `web:` host (§5.5) | No `web:` profile on the silent path | Owner |
| The estimated credits (§10) | No estimate shows. The metering of SBX-7 and the hard cap of D19.3 still bind | Owner |
| The list of packages | Only packages that the image already holds (§6.9) | Owner |
| What the tool will do | No sentence shows before the build | Agent default |
| The read scopes | Nobody approves them. The access of the member is the ceiling (§5.1), and an approver reads them before a share (§8.1) | Agent default |
| The write scopes | A scope writes nothing alone. Each write waits for the card of §5.3 | Agent default |

The result card of §6.5 has the "This is wrong" control for 24 hours. The
default keeps it as a control on the answer. It asks for no approval.

§6.4 shows each output file as an artifact card. The agent reads this card as
a part of the answer, and it asks for no approval. The default keeps it.

**For the SBX-20 audit.** A build step has up to 600 seconds, and a run step
up to 900 seconds (§5.6). So one chat turn can be long. The audit must show
how the turn survives a lost stream (`chat_run_continuity.md` §4.10), and what
the member sees when the box is busy.

§6.1 refuses a gap id older than 30
minutes (SBX-F8), and the sub-run keeps that rule.

**For the SBX-2 audit.** Decision 3 names Zoho, Outlook and other connectors.
`SANDBOX_ROUTES` starts from `MCP_ROUTES` and adds the CRM and email read
routes (§5.2). The audit lists each other connector route that the door needs. A connector with no
route stays out of reach.

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
| The egress proxy and the grant | `maf_coding_engine.md` §7.3, slice WS-43g | An approved per-host grant, one network for each run (WS-43g2a) and one proxy. `request_network_access` is a stub that answers "off" until WS-43g2b | **WS-43g1 merged dark** (task 4009, PR 2887, `d2bffe963`). WS-43g2a (task 4070) built dark, not merged. WS-43g2b (task 4092) and WS-43g3 not built |
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
| **Built here** | Nothing | Nothing | Named as later hardening (WS43-S1, gate WS43-G5) | WS-43b, WS-43c, WS-43d built. WS-43g1 merged dark (task 4009, `d2bffe963`). WS-43g2a, WS-43g2b and WS-43g3 not built |

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
- (SBX-4b, §12.4 decision 4) The build phase mints no capability. A door
  call from a build command gets 401. The smoke command gets one, and each
  round of the smoke test mints its own.
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
- **The stamp.** Each write of a tool carries the stamp
  `sandbox:<tool slug>:v<n>`. This form is decided (§12.5 decision 5,
  2026-11-27). The door takes it from `UserContext.sandbox`, never from a
  header. A Projects write copies it into `meta.via` through
  `record_activity` (D-PM-36). The timeline then reads "by Priya, through
  the tool `quote-pdf` v3". A CRM record write keeps no stamp (§12.5
  decision 5).
- **Limits.** 600 requests per step and 4 MB per response, both new. A page
  holds at most 100 rows, as on the MCP door. Past a limit, the door answers
  429.

### 5.3 Rule 3 — reads run, writes wait for the member

| Route class (`projects_ai_chat.md` §5.2) | What the door does |
|---|---|
| **A**, a read | Runs at once |
| **B**, a write the app can undo | Holds the request. A write reaches the door only as a row of `POST /sandbox/v1/batch`. A direct non-GET answers 403 `sandbox_read_only`. The member's chat shows the confirmation card. On approval, the gateway runs the request as the member, through the route's own handler. On a refusal or a timeout, the code gets 403 |
| **C**, a write that is hard to undo | As B, one card for each request, with the counts first |
| **X**, excluded | 403 always. Deletes, grants, member and role writes, billing, sign-in, connector connect and disconnect, and admin routes |

- **The card shows the wire, not the model's words.** The door builds the
  card from the request it holds: the method, the route, the target row and
  each changed field. A model cannot describe a write as something else
  ("the card stopped matching the wire", WS-26d-write).
- **A batch.** (Decided 2026-11-27, task 3802, answer `55b542fc`.) Each
  write is a row of `POST /sandbox/v1/batch`. The member sees one card with
  one row for each class B row, through `request_confirmation(rows=…)`. The
  approved rows run one by one, not in one transaction. The card shows a
  result for each row. §12.5 holds the rules.
- **No person, no write.** The card passes `non_interactive_default="deny"`.
  A run with no chat gets 403 for every write.
- **The smoke step writes nothing.** Its capability holds no write scope. A
  write there answers 403 with `sandbox_read_only`, the code of SBX-2. The
  smoke report counts the writes that the code would have made.
- **The card times out** after `sandbox_approval_timeout_seconds` (default
  300). SBX-3a adds that setting, because no code holds it yet. The wait
  counts against the step's time budget (§12.5 decision 6).

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

- (SBX-4b, §12.4 decision 3) **A step opens one host at a time.** The
  install runs with the two PyPI hosts only. The smoke command, and the run
  command of SBX-5, run with the door only. Decided 2026-11-27 (task 3986,
  answer `fd97a2bd`, SBX-Q2 residual). PyPI publishes its download counts, so
  a package host is a channel while a capability is live.
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
> (D222), not a second tool. The SBX-1 audit of 2026-11-26 fixed the shape
> below, and §12.1 lists its eleven decisions. An older line that says
> `find_tool` means this path.

**The rule.** The assistant may build a tool only when two things hold. One
`find_tools` call in the same run wrote a `tool_gaps` row with the outcome
`no_match`. The member approved the build card.

**Where the path runs (§12.1 decision 1).** The path is the branch of
`tool_packs.FindTools._find` where the rank gave no tool. It needs no
`tool_scope` entry, because `find_tools` never enters `_all_tools`. The gate
of `find_tools` is stricter than a scope entry:

- `executor._start_pack_turn` admits only the agent `orchestrator`, in a run
  that is not delegated. The room is the member's solo room with cards, with
  a thread, a bound org and a verified member.
- `executor._apply_pack_turn` offers the finder only in that run. A WhatsApp
  turn gets no finder.
- `tool_packs.tool_search_on` must name the org (`TOOL_SEARCH_ORGS`).
- SBX-1 adds two more conditions. The setting `user_tool_find_enabled` is on,
  and `_studio_member_open()` is true: `STUDIO_ORGS` names the org, and the
  member holds `feature:studio`.

**The hook (§12.1 decision 2).** `FindTools` takes an optional hook
`on_gap(query, live)`, as it takes the builder and the measure. So
`tool_packs` imports no gateway code. With no hook, the answer stays
`FIND_NONE` byte for byte. The description of `find_tools` does not change,
so the prefix of the cache stays stable. The orchestrator module `tool_gap_path`
makes the hook. It runs these steps in this order:

1. **The gate.** `_studio_member_open()` must be true. The org, the member,
   the thread and the run come from the run context only, never from a tool
   argument. An empty query writes no row.
2. **The compose check (D187, decision 7).** `routes/studio/actions.catalog_rows`
   runs before any session of the hook opens. `package_search.callable_catalog`
   then keeps a `manifest` row only when the member holds the feature of its
   app. It keeps a `logic` row only when the member holds `studio`. It drops
   each `workflow`, `integration`, `mcp` and `tool` row (fail closed). A
   lexical match on the title picks at most 20 rows. The check never calls
   `package_rows`.
3. **The library (D187, decision 6).** `package_search.search_visible` reads
   two sources in one session. The first is the packages that the member owns
   (`packages_store.list_summaries`), live and not archived. The second is
   the installs that reach the member (`resolve.visible_installs`), at a
   version. That read drops a paused, archived or expired install. The text
   of a hit is its name and its description, or the `name` and `purpose` of
   the manifest of an install. Python ranks the hits. A hit must share 2 or
   more content stems with the need, and the search keeps 5 at most.
4. **The run's own tools.** The 5 tools of the live list that share the most
   stems with the need. A tool of an MCP server does not count, because its
   name is not first-party text.
5. **One typed question** (D93, D100, decisions 4 and 5). One
   `decide_tools.ask_typed` call holds two items, with the purpose
   `tools.gap`. The first item picks the answer. The second item picks the
   category: one key of `skill_families.SKILL_FAMILIES`, or `other`. The
   call runs only when `decide_tools.typed_choices_on()` is true, and no
   session is open while it runs. `sure_choice` reads each answer.
6. **The record.** One `tool_gaps` row, through `gateway/studio/tool_gaps.record_gap`,
   in its own session.
7. **The log line.** `sandbox.tool.gap` of §9.1, with no need text.
8. **The answer.** `FIND_NONE`, then one fixed sentence for the outcome.

A fault or a timeout of 8 seconds in the hook gives `FIND_NONE` and no row.
The log line `tool_packs.find_gap_failed` names the error class only.

**What the model sees (decision 8).** The decide context holds the need,
ids and fixed sentences only: `pkg:<uuid>` with a count of shared words, the
first-party names of the live tools, and the catalog action ids. No text that
a member wrote in a package reaches it. The `candidates` column holds ids
only.

**The answers (decision 3).**

| The pick of the first item | `answer` | `outcome` | What the assistant does |
|---|---|---|---|
| `pkg:<app_id>` | `tool` | `reuse`, with `app_id` set | Tells the member about the app. `run_tool` comes with SBX-5 (§6.4) |
| `tool:<name>`, a live tool of the run | `tool` | `existing` | Uses that tool |
| `compose` (D187). The question offers it only with 1 or more callable rows | `compose` | `compose` | Offers a Studio skill or workflow of the action ids. No sandbox starts |
| `none` | `none` | `no_match` | Says that no tool does this yet. SBX-4 may then offer a build |
| `unsure`, no engine, or a pick below the threshold | `unsure` | `unsure` | Tries a candidate first, or asks the member |

SBX-1 returns no gap id. SBX-4 adds the gap id to the answer.

**A failed built-in tool (D187).** When a platform tool failed in this run,
the assistant passes its name as `failed_tool`. The checks skip that tool,
and the gap row names it (§6.7, level c). SBX-14 adds this. SBX-1 writes
`failed_tool` as NULL.

**Who the library search can see (D187).** The library search reads one
organization only. It keeps a package in two cases only: the viewer owns it
live, or an install at a version reaches the viewer. It never runs a bare
query over `apps`. Such a query would leak the private package of another
member into the candidates and the decide context (SBX-F23). A `system`
package is not in the search, because `system_contributions()` holds no name
or description. The `system` case comes back with the first system tool (§6.8).

**The build card.** Before a build, the member sees one card:

- what the tool will do, in one sentence,
- the read scopes and write scopes it asks for,
- the packages it may install,
- the estimated credits (§10),
- and, in a marked session, the line "This request came after content from
  outside your organization."

On a refusal, the gap row reads `declined`, and nothing starts.

(SBX-4b, §12.4 decision 12) The card of SBX-4b shows the `need` of the gap
row and the read scopes. It also shows the line "may download Python
packages from PyPI". It shows no credit estimate, because SBX-7 and the
price are not built.

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
- (SBX-4b, §12.4 decisions 1 and 2) `build_tool` starts the container through
  `acquire_tool`, not `acquire()`. The mounts come from a sibling of the
  run-data dir, `.run-data/<org slug>/<thread slug>.user-tool/`. Its
  `outputs/` folder is `/workspace/outputs`, not the output folder of the
  thread.

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

(SBX-4b, §12.4 decision 3) The session runs its commands with
`--network none`. The step engine opens the `build` hosts for the install of
each round only.

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
- (Amended 2026-11-27, task 3802, answer `55b542fc`.) The door holds each
  write of the code for a card (§5.3). After the member approves the card,
  the gateway runs each approved row as the member, through the route's own
  handler and checks. The rows run one by one, not in one transaction. The
  card shows a result for each row. This replaces the D187 text that applied
  proposed writes in one transaction (`ai_studio.md` §3.7). §12.5 holds the
  rules.
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
any wheel that its lock pins with a hash, through the egress proxy (§5.5,
§12.4 decision 6). SBX-5 owns the package rules of `ai_studio.md` §19.5,
and no tool persists without them. B8 states those rules for Studio. The
proxy is WS-43g1 (task 4009, PR 2887), merged on 2026-11-27 (`d2bffe963`).

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
| `search` | (§12.1 decision 6) A rank in Python at query time, over the two reads of `package_search.search_visible`: the live packages that the member owns, and the installs that reach the member. No SQL full text, and no index until a measurement asks for one |

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

> **SBX-4 adds this table** (§12.1 decision 9). SBX-1 adds `tool_gaps` only,
> because no step runs before SBX-4.

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS |
| `app_id`, `version` | `uuid`, `int` | (D187) The package and its version. Null for a build that did not persist. No foreign key, so the row stays after a delete |
| `gap_id` | `uuid` | Null |
| `repair_of` | `uuid` | (D187) Null. The failed run that this repair answers (§6.7) |
| `signal` | `text` | (D187) Null. `'exit_code'`, `'schema'` or `'door_5xx'`, the signal of a failed run (§6.7) |
| `member_email`, `run_id` | `text` | The member and the agent run |
| `thread_id` | `text` | Null. The chat thread. It references `chat_session (id)` with ON DELETE SET NULL, so a Delete of the chat keeps the row (§12.4 decision 10) |
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

### 7.4 `tool_gaps` — one row for each no-match call of `find_tools`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS. The column DEFAULT is the bound tenant, so the writer passes no org |
| `member_email` | `text` | Not null, lower case. The verified member of the run |
| `thread_id` | `text` | Not null. It references `chat_session (id)` with ON DELETE CASCADE, as 459 does (§12.1 decision 11). The writer selects the chat under the bound tenant, so a chat of another org, or no chat, gives no row |
| `run_id` | `text` | Not null. The agent run |
| `need` | `text` | 1 to 280 characters, written by the model. It stays in the tenant |
| `category` | `text` | One key of `skill_families.SKILL_FAMILIES`, or `other` (§12.1 decision 4). The CHECK holds the shape `^[a-z0-9-]{1,40}$`, and a Python test pins the set. It carries no tenant text |
| `candidates` | `text[]` | At most 10 ids: `pkg:<uuid>`, a first-party tool name or a catalog action id (decision 8). No id holds a space |
| `answer` | `text` | `'tool'`, `'compose'` (D187), `'none'` or `'unsure'` |
| `outcome` | `text` | `'existing'`, `'reuse'`, `'compose'` (D187), `'no_match'`, `'unsure'`, `'declined'`, `'built'` or `'build_failed'` (decision 3). SBX-1 writes the first five |
| `app_id` | `uuid` | (D187) Null. The package that answered or that the build made. No foreign key |
| `failed_tool` | `text` | (D187) Null. The name of a failed platform tool (§6.7, level c). A platform name, never tenant text. SBX-14 writes it |
| `created_at` | `timestamptz` | |

`gateway/studio/tool_gaps.py` is the one module that runs SQL on the table.
The index `(organization_id, created_at DESC)` serves the gap view. The member
purge (`routes/admin/members.py`) deletes the rows of a leaver, because the
need is text about the member's work. A Delete of the chat deletes its rows
(the cascade).

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
| Revoke a tool | A holder of `studio:publish` (D187) | Each install goes to `archived`. `resolve.visible_installs` drops an archived install, so the no-match path of `find_tools` never returns it again (§12.1 decision 6) |

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
version and the reason.

Each Projects write that a tool makes carries the stamp
`sandbox:<slug>:v<n>` in `meta.via` (§5.2). This form is decided (§12.5
decision 5, 2026-11-27). A CRM record write keeps no stamp, because its rows
have no `via` field.

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
  organization: the need, the category and the outcome. The route is
  `GET /studio/tool-gaps` (§12.1 decision 10). It gives the counts by outcome
  and by category for 30 days, and the 50 newest rows. SBX-1b shows them on
  `/studio`.
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
| SBX-F1 | `tests/unit/test_sandbox_capability.py` | A capability takes its org or member from input, works after its step ends or after 15 minutes, works for another org, or holds a write scope in the smoke step. (SBX-3) Or it holds a write scope outside `SANDBOX_WRITE_SCOPES`, outside the run phase, or with no package and version. Its R8 half runs on a real database: a door read for a row of another org returns no row |
| SBX-F2 | `tests/unit/test_sandbox_routes_coverage.py` | A route in `SANDBOX_ROUTES` has no class, a class X route is on the list, or a row joins with no `sandbox=True` on its own line (§12.2 decision 11). (SBX-3, §12.5 decision 3) A row of `SANDBOX_WRITE_ROUTES` is not class B or C, is a GET or a DELETE, or is not one of the 29 rows of §12.5. A row joins the write door only with `sandbox_write=True` on its own line |
| SBX-F3 | `tests/unit/test_sandbox_door_writes.py`, and `tests/unit/test_sandbox_held_card.py` (SBX-3b) | A class B or C request runs with no card, a run with no chat gets a write, the card text comes from the model and not from the held request, a refused card lets the write run, or a Projects write lands without `meta.via = sandbox:…`. (SBX-3) A timed-out card lets the write run, an unticked row runs, or a late answer runs a row. Its R8 half runs on a real database (§12.5 done-when 12 and 23) |
| SBX-F4 | `tests/unit/test_sandbox_no_secrets.py` | The container's environment or file system holds a provider token of the org, `GATEWAY_INTERNAL_TOKEN`, the Router key, the session secret, the signing key or a database URL. It has a `sandbox_docker` half |
| SBX-F5 | `tests/unit/test_sandbox_egress_profiles.py` | A host off the profile passes, a step with a read scope gets a `web:` profile while SBX-Q2 has its default, a marked session gets one, or the code changes its own profile |
| SBX-F6 | `tests/unit/test_sandbox_door_identity.py` | The door's identity step skips a branch 0 step, reads the member from a header, falls through to branch 1a, or a refused request leaves a tenant bound. Its source is pinned by digest, as branch 0's is |
| SBX-F7 | `tests/unit/test_user_tool_injection.py` | (§12.1 decision 1) The find half: the no-match hook reaches a run with `user_tool_find_enabled` off, an org off `STUDIO_ORGS` or `TOOL_SEARCH_ORGS`, a member without `feature:studio`, a delegated run, an agent other than `orchestrator` or a WhatsApp turn. `find_tools` or the hook is in `_collect_injectable_platform_tools()`. The hook takes the org, the member, the thread or the run from a tool argument. The build half: `build_tool` or `run_tool` reaches an agent with no `tool_scope`, a run whose org the scope does not name, or a run with no chat for `build_tool` |
| SBX-F8 | `tests/unit/test_tool_gap_detection.py` | The record half (SBX-1): a no-match call writes no row or two rows, a row does not follow the map of §12.1 decision 3, or a ranked call, a refusal, a fault or a timeout writes a row. The build half (SBX-4): `build_tool` runs with no gap id, with a gap id that is not `no_match`, from another run, or older than 30 minutes, or with no approved build card |
| SBX-F9 | `tests/unit/test_user_tool_registry.py` | R8, on a real database as the NOBYPASSRLS role. (D187) A package persists after a failed smoke test or a failed first run. A failed build leaves a file, a blob or a package. RLS shows a package or a `tool_gaps` row to another org. A private tool shows to another member |
| SBX-F10 | `tests/unit/test_user_tool_versions_immutable.py` | (D187) Any code issues an `UPDATE` of `app_versions.files`, or the trigger of §7.2 lets one through on a real database |
| SBX-F11 | WS43-F1, `tests/unit/test_sandbox_broker_seam.py`, extended | A module outside `sandbox_broker.py` imports `e2b` or starts a `docker` process, or the `tools` target falls back to the host |
| SBX-F12 | `tests/unit/test_user_tool_build.py` | A build package lacks a file of §6.3, a lock line lacks a hash, the smoke test runs more than 3 fix rounds, or a build passes the smoke test with a call outside its scopes. (D187) Or the store keeps a version whose `tool.json` and manifest entry differ |
| SBX-F13 | `tests/unit/test_user_tool_review.py` | (D187) A member who is not the owner runs `apps.live_version`, a new version moves the pin of an install, or a member without `studio:publish` approves, moves a pin or revokes. A code package gets an install for named people, a group or the org with no approved version. A paused install starts a step |
| SBX-F14 | `tests/unit/test_user_tool_audit.py` | A share, approval, pin, disable or revoke writes no audit row |
| SBX-F15 | `tests/unit/test_sandbox_log_lines.py` | A log line of §9.1 holds input data, output data, a need text or a token, or the operator view returns a `need` text |
| SBX-F16 | `tests/unit/test_sandbox_metering.py` | A step ends with no usage event, or a step starts for an org at its hard cap. (D227) Or a step starts while the `sandbox` price row is missing |
| SBX-F17 | `tests/unit/test_user_tool_repair.py` | (D187) A repair starts on a signal outside §6.7, or one failed run gets a second repair build. A repair changes a schema or a scope. A level (b) version runs for a member who is not its author, or moves a pin or the `live_version` of another member |
| SBX-F18 | `tests/unit/test_tool_compose_check.py` | (D187) The no-match path writes `no_match` when the decide answer is `compose`, `build_tool` takes a `compose` gap, or the compose check offers a catalog row that the member cannot call. A `workflow`, `integration`, `mcp` or `tool` row reaches the decide context. The hook opens a session while `catalog_rows` runs. It proves done-when 6 and 9 of §12.1 |
| SBX-F19 | `tests/unit/test_no_platform_patch_at_run_time.py` | (D187) A level (c) path writes into the repository or calls `attempt_self_mutation`. A failure report holds error text or code with no consent row of the member |
| SBX-F20 | `tests/unit/test_tool_adoption_counts.py` | (D187) The view of §6.8 returns a value that is not a count, a category slug, a built-in tool name or an error class, or it names an organization |
| SBX-F21 | `tests/unit/test_sandbox_remote_backend.py` | (D187) The `docker-remote` backend binds a mount of the gateway host, runs `runc` in production mode, or gives `DOCKER_HOST` to a child that is not `docker`. It has a `sandbox_docker` half |
| SBX-F22 | `tests/unit/test_sandbox_door_client.py` | (D187) The door client reads an env value other than `METORITE_API_TOKEN`, calls a host other than the door name, or the image lacks it |
| SBX-F23 | `tests/unit/test_user_tool_registry.py`, R8 half | (D187) The library search reads across organizations, or reads `apps` with no owner filter. A private package of another member reaches `candidates`, the decide context or the gap row. A paused or archived install still comes back. A draft with no live version comes back. It proves done-when 5 and 7 of §12.1 |
| SBX-F24 | `tests/unit/test_sandbox_scope_all_orgs.py` | (D227) `tools:*` parses to an error, an org in `user_tool_disabled_orgs` still runs, or `tools` is absent from `MAF_CODING_TARGETS`. Advisory until SBX-6 and SBX-9 build it |
| SBX-F25 | `tests/unit/test_compute_run.py` | (D227) `compute_run` takes an input that is not a `tr_` ref of its own org, member and thread, or a foreign ref does not answer "not found". A short tool result gets no ref while compute is on. One org evicts the refs of another. The output text is not framed as untrusted. A run passes the daily cap of 50 for an org or 20 for a member. A log line holds code or output text. An org outside the scope runs |
| SBX-F26 | `tests/unit/test_sandbox_broker_compute.py` | (D227) The `compute` argv holds a mount, a network, or limits other than 1 CPU, 1g and pids 64. The tar reader accepts a link, a nested name, a type outside csv, png, json and txt, more than 5 files, a file over 1 MB or more than 3 MB in total. `compute` is absent from `MAF_CODING_TARGETS`, or `compute:*` does not parse, or an unknown target no longer raises `ValueError`. Production runs without `docker-remote` and `runsc`. A start skips the org lock |
| SBX-F27 | `tests/unit/test_user_tool_inline_build.py` | (D233) With the flag off, a build starts in a turn without the build card. A silent build runs in a marked session, installs a package that the image lacks, gets a `web:` profile, or starts on an answer that is not `no_match`. A failed smoke test or a failed first run persists a tool, or leaves no gap row or no failure row. Advisory until SBX-20 builds it |
| SBX-F28 | `tests/unit/test_user_tool_progress_line.py`, with a `vitest` half | (D233) The member sees a second progress line, the §4.11 line included, a tool name or the word "sandbox" for a build in the turn. The client draws a server string. A write runs with no card. Advisory until SBX-21 builds it |

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
| **SBX-0e** (task 3980) | (D233) This amendment, 2026-11-26. Docs only: §0d, D233 in `work_plan.md` §3, and the rows SBX-20 and SBX-21 | SBX-0d | AGENT-SAFE. Docs |
| **SBX-1a** (task 3800) | (D187) (D225) `tool_gaps` (§7.4). The no-match path of `find_tools` with the library check and the compose check (§6.1), not a second tool. The gap log line of §9.1, and the route `GET /studio/tool-gaps` for an approver. No code runs anywhere. §12.1 holds the full slice and the eleven audit decisions. `user_tool_runs` moved to SBX-4 (decision 9) | SBX-0b | AGENT-SAFE. HIGH tier: a FORCE RLS table and the chat tool seam. Dark behind `user_tool_find_enabled`. 🔨 **BUILT dark** (2026-11-26, branch `mt-8f625471-sbx1-tool-gaps`): migration 504, the hook, the store, the library search and the route. The flip is the `enforcement-flip` of rung 6a |
| **SBX-1b** (task 3984) | A read-only "Tool gaps" section on `/studio`, for a holder of `studio:publish`. It reads `GET /studio/tool-gaps` through the `/api/studio/[[...path]]` proxy. It shows the counts by outcome and by category, and the newest rows. Frontend only, with no new route | SBX-1a | AGENT-SAFE. STANDARD. Dark. 🔨 **BUILT dark** (2026-11-26, branch `sbx1b-gap-view`): `app/studio/components/ToolGaps.tsx`. A 404 or a 403 draws nothing. The panel draws a skeleton or an error line only in a browser that saw a 200 before, so a dark org never sees it |
| **SBX-2** (task 3801) | The run capability (§5.1), the door with reads only (§5.2), `SANDBOX_ROUTES` and its coverage fence. 🔨 **BUILT dark** (2026-11-26, branch `mt-37f6b3b8-sbx2-door`, §12.2): migration 503, `acb_auth/sandbox_capabilities.py`, the sibling of branch 0, `gateway/sandbox_door.py` and the 68 rows. Fences SBX-F1, SBX-F2, SBX-F6 and SBX-F29. The digest pin names the Opus diff-reviewer review and the independent verification of `external_agent_access.md` §10.1 (2026-11-26). §12.2 holds the full slice | SBX-1, SBX-Q6 | AGENT-SAFE. Dark |
| **SBX-3** (task 3802) | (2026-11-27) SBX-3a is built dark and merged (`8b249bbb7`, PR 2926). SBX-3b (task 4084, rung 6c) is built dark on its branch, not merged. Writes held for a card (§5.3), the batch card, `meta.via`. **§12.5 holds the full slice** and the owner answers of 2026-11-27. Two PRs. **SBX-3a (task 3802), the write door:** the rule of `Route.sandbox_write` and the 29 write rows, the write scopes of `mint`, the held store in tenant Redis, `POST /sandbox/v1/batch` that holds and answers each row, the one-use ticket, the stamp and `sandbox_approval_timeout_seconds`. Tests only, no demo. **SBX-3b (task 4084), the card:** the batch card through the held-card seam, approve, refuse and timeout, and card text from the held wire only. HIGH. **Done when:** the list of §12.5 | SBX-2. SBX-3b needs SBX-3a | AGENT-SAFE. Dark |
| **SBX-4a** (task 3803) | (D227) **The `tools` target, inert** (§0c item 4). `TOOLS_TARGET` and the broker agent name `user-tool` join `MAF_CODING_TARGETS`, so a `tools:` entry of `MAF_CODING_SCOPE` parses and every other target stays on. `acquire()` refuses the target, and `docker-remote` refuses it too, because `REMOTE_TARGETS` still holds `code_task` only. A `tools` agent is never covered, so the D85 seam keeps its host shell withheld. 🔨 **Built dark on PR 2862, not merged** (2026-11-26). **Done when:** SBX-F24 (part) in `tests/unit/test_sandbox_scope_all_orgs.py`, `tests/unit/test_sandbox_broker_tools.py`, and the SBX-F11 test `test_the_tools_target_never_falls_back_to_the_host` pass, and each fails before the change | None | AGENT-SAFE. Dark |
| **SBX-4b** (tasks 3986 and 4049) | `build_tool`, the build session and the smoke test (§6.2, §6.3), on the Docker backend. (§12.1 decision 9) The table `user_tool_runs` (§7.3). The gap id in the answer of the no-match path. The reserved agent names `user-tool` and `compute`. **§12.4 holds the full slice.** Two PRs: SBX-4b1 (task 3986), the step engine with a fixture package, and SBX-4b2 (task 4049), the chat tool and the build session. Parts (a) to (c) of SBX-4b1 can start now. The step engine waits for PR 2862. WS-43g2a (task 4070) is merged dark a95d58d09, PR 2924. PR 2887 merged (`d2bffe963`). HIGH. **Done when:** the list of §12.4. 🔨 **Parts (a), (b) and (c) BUILT dark** (2026-11-26, task 3986, branch `mt-d0a57ed5-sbx4b1-abc`): the door refusal counter and `door_counts`, `user_tool_runs` (migration 506) with its store, and the reserved names. Done-when 1 to 7 pass. Merged 2026-11-27 (`ae5b37cde`, PR 2896). The step engine (decisions 1 to 8) and done-when 8 to 27 are not built | SBX-4a, SBX-1a (a gap id, SBX-F8), SBX-2, SBX-13 (D187), WS-43g1 (task 4009, PR 2887, merged `d2bffe963`) for the proxy and the door name, WS-43g2a (task 4070, merged dark a95d58d09, PR 2924) for the network of each run | AGENT-SAFE. Dark. No production container (task 3786) |
| **SBX-5** (task 3804) | (D187) `run_tool`, persist on success as a Studio package, and reuse (§6.4 to §6.6). The `app_versions.files` columns, the CHECK and the trigger (§7.2). `publish_version` writes the files, and `rollback` copies them. (SBX-4b) SBX-5 is the single owner of the package rules of `ai_studio.md` §19.5 (age, advisory, licence and layer), and it closes the residual of §12.4 decision 9. (SBX-Q2, decided 2026-11-27, task 3986, answer `fd97a2bd`) The run step opens one host at a time, as the smoke round does: a reinstall runs with the package hosts only, and the run command runs with the door host only. (§12.5) `run_tool` mints the write scopes of `tool.json` and runs the held-card watcher of SBX-3b beside the run command. **Open decision of SBX-5:** one run command has 300 s (§5.6), the same as the default card wait of §12.5 decision 6. SBX-5 decides whether a run command gets more time. **Done when** (part): a tool whose lock breaks a §19.5 rule does not persist, and a test proves it | SBX-3, SBX-4, `ai_studio.md` B7 | AGENT-SAFE. Dark |
| **SBX-6** (task 3805) | (D187) (D227) The setting `user_tool_disabled_orgs`, which takes `*` (§0c, Gap A). The review controls through the package host: share, approve with `studio:publish`, pin, pause, archive and audit (§8) | SBX-5, `package_host.md` PKG-11, `ai_studio.md` J2 | AGENT-SAFE. Dark |
| **SBX-7** (task 3806) | Metering (§10). (D227) A step is refused when the `sandbox` price row is missing (SBX-F16) | SBX-5, SBX-Q4 | AGENT-SAFE. Dark |
| **SBX-8** (task 3807) | `sandbox_runtime=runsc` in the broker, and a measured cost of gVisor on a local stack. 🔨 **BUILT dark** (2026-11-19, branch `mt-97db21a4-sbx15-remote-runsc`): the setting `sandbox_runtime`, default `runc`. `runsc` adds `--runtime=runsc` to `docker run`, and an unknown value refuses each start. Fences: WS43-F2 and SBX-F21. **Not measured:** no local daemon has `runsc`, so the cost of gVisor waits for a box with it | the broker (D227), not SBX-4 | AGENT-SAFE. The install on a box is WS43-G5, **OWNER-GATE** |
| **SBX-9** (task 3808) | (D187) (D227) Turn it on: `tools:<org>` on production for each beta org of an explicit list, not Fracktal only, on the dedicated box of §4.6. The checklist of §0c binds it: the deployed SHA holds SBX-4, and `*` is allowed only while self-serve signup is off. D144 retired staging, so the local staging gate of D145 comes first | SBX-1 to SBX-8, SBX-12, SBX-13, SBX-15a, SBX-15b, WS43-G1, WS43-G2, WS43-G4, WS43-G5, BH-3, SBX-G6 (D227), SBX-G9, SBX-G10, SBX-G11 | **OWNER-GATE** |
| **SBX-10** (task 3809) | The `e2b` backend (§4.4) and the public door name. (D187) The alternative to the box of §4.6 | SBX-5, SBX-Q1 | AGENT-SAFE to build on a free test account. The account, the key and the DNS record are **OWNER-GATE** |
| **SBX-11** (task 3810) | The SPACE check through ECW (§4.5) | SBX-10, the three triggers of §4.5 | **OWNER-GATE**. A new host costs money |
| **SBX-12** (task 3427) | (D187) **Repair, levels (a) and (b)** (§6.7): the three signals, the cap of 1, the contract check, the card that names the writes that landed, and the repair version of the store with its private pin (§8.2). **Done when:** a fixture tool that exits 1 gets one repair, and the fixed version runs and persists. A second failure stops with no second build. A level (b) version runs for its author only, and the org pin does not move | SBX-5. SBX-6 for level (b) | AGENT-SAFE. Dark |
| **SBX-13** (task 3428) | (D187) **The door client** `metorite_door` in the sandbox image (§6.9). **Done when:** the list of §12.3 replaces this cell. The proof with the real door moves to SBX-4. Built 2026-11-26 | SBX-2 spec (§12.2) | AGENT-SAFE. The image on a box is WS43-G2 |
| **SBX-14** (task 3429) | (D187) **Repair, level (c)** (§6.7): `find_tool` with `failed_tool`, the private workaround, the report to the failure log with its consent rule, and the archive of a workaround when its fix ships. **Done when:** a fixture failure of a built-in tool files one report with no error text before consent. The workaround runs for this member only. A release that marks the report fixed archives the workaround install | SBX-5, the spec and the store of the tool failure log (task 1793), SBX-Q8 | AGENT-SAFE. Dark |
| **SBX-15a** (task 3430) | (D187) (D227) **The `docker-remote` backend and the org lock** (§4.6, §0c): the broker drives a second Docker daemon over `DOCKER_HOST`, moves the files in and out with no bind mount, and runs `runsc` only. The lock is one org for each remote box across every target, counted by the broker's org label on the remote daemon. **Done when:** on the dev stack, a run goes to a second local daemon and the outputs come back through the safe opener. A second org waits while the first holds the box. In production mode the broker refuses a `runc` run on that backend. 🔨 **BUILT dark** (2026-11-19, branch `mt-97db21a4-sbx15-remote-runsc`). The settings are `sandbox_backend` (default `docker`), `sandbox_docker_host` (default empty), `sandbox_docker_cert_path` (default empty) and `sandbox_remote_workspace_mb` (default 512).<br>• Only the broker's own `docker` child gets `DOCKER_HOST`. A log line shows the scheme and the host only. Outside dev a loopback host refuses, and `tcp://` needs TLS with a client certificate.<br>• A container on the remote box has no mount. Its `/workspace` is a tmpfs. Before each command the broker sends the working dir as a tar on the stdin of `docker exec -i`. After the command it reads `/workspace` back as a tar on the stdout of `docker exec`. Both tars use the GNU format. `docker cp` cannot write a `--read-only` root or read a tmpfs.<br>• The copy out takes regular files only, and writes each file in chunks through the safe opener. The copy never sends or deletes a name with a backslash or a byte that is not UTF-8. The model hears which names stayed behind.<br>• Outside dev (`acb_env`) the backend refuses `runc`. Of the mounted targets it runs `code_task` only, and refuses `projects` and `app_builder`. The `compute` target of SBX-18 runs there too.<br>• The org lock: every start of every target awaits `_remote_org_admit` inside one start gate, until `docker run` returns. The admit reads every registry and the org labels on the daemon. The reaper removes only a container whose removal failed. `sandbox_max_per_org` is the one cap of an org, and a start refuses when it is above `sandbox_max_total`.<br>• The end of a run removes its remote container.<br>• Fence SBX-F21. **Done when: met** for `code_task`. A run went to a second local daemon (`docker:dind`) under `runc`, and the outputs came back through the safe opener. A second org waits (`SandboxBusy`) while the first holds the box | The broker (WS-43c), SBX-8 | AGENT-SAFE to build and test on the dev stack. The box, its network and its DNS are **OWNER-GATE** (SBX-G9, SBX-G10) |
| **SBX-15b** (task 3817) | (D227) **The tools half of the remote backend:** a build, a smoke test and a run of a tool go to the remote daemon. It sets `user_tool_max_total` from the measured box and keeps `user_tool_max_per_org` below it (§0c, Gap B) | SBX-4, WS-43g, SBX-15a | AGENT-SAFE. Dark |
| **SBX-15c** (task 3819) | Three follow-ups of the SBX-15a review, before SBX-G13. 1. `DockerCLI.collect` scrubs its stderr through `_scrub`, because `run_compute` gives that stderr to the model. `_scrub` cuts the daemon address to its scheme and host, and removes the cert dir, the home dir of the gateway and each credential shape of `acb_common.secret_shapes`. On the path to the model it also removes the host of the sandbox box and each port. 2. When the `rm -f` of the startup sweep fails, each id goes to the orphan record, and the reaper removes it. 3. When `docker run` times out or a cancel stops it, its start label goes to the orphan record. The reaper looks it up on each tick for 600 s, so a container that the daemon makes late does not stay. The fence of SBX-F21 also checks that the admit is a statement before the run in the start gate. 🔨 **BUILT dark** (2026-11-25). **Done when:** each fix has a test in `test_sandbox_remote_backend.py` that fails before the fix, and the `sandbox_docker` half passes on a local daemon | SBX-15a, SBX-18 | AGENT-SAFE. Dark |
| **SBX-16** (task 3431) | (D187) **The platform counts view** (§6.8, §9.2) in the Operator Console. A later slice. **Done when:** a platform admin sees counts by category and by built-in tool, and no tenant text. A tenant session gets 404 | SBX-1, SBX-5, SBX-14 | AGENT-SAFE |
| **SBX-17** (task 3432) | (D187) **The adoption consent** (§6.8): the consent card for an owner of the source organization, and the consent record that a PR names. **Done when:** no path reads the files of a tool of a tenant for adoption without a consent row. A consent row names one tool, one version and one person of the source organization | SBX-16, SBX-6, SBX-Q9 | AGENT-SAFE to build. Each adoption is **OWNER-GATE** (SBX-G12) |
| **SBX-18** (task 3815) | (D227) 🔨 **BUILT dark** (2026-11-19, PR 2616). **The `compute` broker target** (§0c, rung 6a′): no mount, a `--tmpfs /workspace` workdir and `--network none`. Files go in and out as tar over `docker exec`, parsed in memory. It takes regular files only, with flat names, no links, the types csv, png, json and txt, at most 5 files, 1 MB each and 3 MB in total. Limits: 1 CPU, 1g and pids 64. Its own caps are `compute_max_per_org=1` and `compute_max_total`, default 1. It starts containers through the org lock of SBX-15a. `compute` is in `MAF_CODING_TARGETS`. The flip is `compute:<org>` in `MAF_CODING_SCOPE` (SBX-G13). `COMPUTE_RUN_ORGS` reloads on SIGHUP and is the hot kill switch only, for one org or all orgs. A reaper stops the containers of a de-listed org. A production guard refuses unless the backend is `docker-remote` with `runsc`. **Done when:** SBX-F26 passes | SBX-8 and SBX-15a (tasks 3807, 3430), for the production guard and the lock | AGENT-SAFE. Dark. The flip is **OWNER-GATE** (SBX-G9 to SBX-G11, SBX-G13, WS43-G2, WS43-G5, SBX-G6). WS43-G1 (BH-3) binds it too (D127.2, §4.6). The owner decides on task 3786 to build BH-3 or waive it for rung 6a′ only. The waiver amends §4.6, and a gateway compromise then gets root on the sandbox box |
| **SBX-19** (task 3816) | (D227) 🔨 **BUILT dark** (2026-11-19, PR 2623). **The `compute_run(code, inputs, timeout_s)` chat tool** for the main chat agent `orchestrator`. The inputs are `tr_` refs of tool results that the turn holds, from the one spill store `tool_result_spill`, keyed by org, member and thread. A `KeepForCompute` step keeps a ref for every plain-text tool result of the turn, from 1 character to 2 MB, while compute is on for the org. It does not depend on `TOOL_RESULT_SPILL` or on the 24,000-character threshold. Eviction works for each owner. The output is framed as untrusted. Files reach the chat as artifacts through the `write_artifact` seam. A daily cap of 50 runs for each org and 20 for each member goes through `tenant_redis`. Log lines hold no code and no output text. A run writes a `usage_event` at price 0 through the one meter if that is one call, and if not, SBX-7 does it. **Done when:** SBX-F25 passes | SBX-18 | AGENT-SAFE. Dark |
| **SBX-20** (task 3981) | (D233) **The background build and same-turn hand-back** (§0d, rung 6c). On `no_match`, the build, the smoke test and the first run go to a sandbox sub-run inside the same chat turn. The chat shows one progress line. On a smoke pass it calls the new tool in the same turn and answers. On a failure it answers what it can and says what it could not do. The gap goes to the gap log and the tool failure log. HIGH. **Done when:** on the dev stack, one turn builds, calls and answers. A failed build gives a plain answer and both log rows. With the flag off, nothing changes. Fence SBX-F27 | SBX-3, SBX-5 | AGENT-SAFE. Dark |
| **SBX-21** (task 3982) | (D233) **The progress-line contract** (§0d, rung 6c): one line in the run status, with fixed client words, no tool name and no "sandbox". A write still shows the card of §5.3. The kept skill is reused silently. The build and run calls of the SBX-20 path send no §4.11 line. The SBX-20 flag stays off until this slice merges. The approval of §8 does not change. HIGH. **Done when:** a test fails on a second progress line, on a tool name or on "sandbox" in member copy. Fence SBX-F28 | SBX-20 | AGENT-SAFE. Dark |

### 12.1 SBX-1a — the first dispatchable slice

**Scope.** The table of §7.4, with RLS. The no-match path of `find_tools`
with the checks of §6.1. The `sandbox.tool.gap` log line. The read route
`GET /studio/tool-gaps` for an approver.

No container starts, no capability exists, and no code runs.

**Not in SBX-1a.** No column of `app_versions` changes, because SBX-5 adds
the files (§7.2). `sandbox_capabilities` comes with SBX-2, and
`user_tool_runs` with SBX-4. The library check finds no tool module until
SBX-5, so it finds packages of every kind. SBX-1b draws the gap view.

**Why first.** It answers #234 and the counter half of #242. It costs
nothing, needs no owner act except the flag flip, and gives the team real gap
counts before anyone spends on a runtime. (D187) The compose check sends a
need to Studio before any sandbox exists.

**The flag.** `user_tool_find_enabled`, default off. A change needs a
restart. Off, `_make_finder` passes no hook, so the no-match answer is
`FIND_NONE` byte for byte. Off, nothing writes a row, asks the decide engine
or reads the catalog. The flip on production is the `enforcement-flip` of
rung 6a (§0c).

**The audit decisions (2026-11-26).** The SBX-1 audit fixed these eleven
points. The text of §6.1, §7 and §11 follows them.

1. **The gate holds by construction.** `find_tools` never enters
   `_all_tools`, so a `tool_scope` entry would only log
   `executor.tool_scope_unknown_entry`. The gate of `find_tools` is
   stricter (§6.1). SBX-1a adds `user_tool_find_enabled` and
   `_studio_member_open()`. They take the place of the 404 of `STUDIO_ORGS`
   and the feature gate, which an in-process call of `catalog_rows` skips.
2. **The one no-match site** is `if not ranked:` in `FindTools._find`. The
   hook comes in as the builder and the measure do, so `tool_packs` imports
   no gateway code. With no hook, the answer is `FIND_NONE` byte for byte.
   `_FIND_DESCRIPTION` and `find_tool()` do not change.
3. **The map of an answer.** `pkg:<app_id>` gives `tool` and `reuse`, with
   `app_id`. `tool:<name>`, a live tool of the run, gives `tool` and
   `existing`. `compose` gives `compose` and `compose`. The question offers
   it only with 1 or more callable rows. `none` gives `none` and `no_match`.
   `unsure`, no engine or a pick below the threshold gives `unsure` and
   `unsure`. The `outcome` CHECK holds all eight values. SBX-1a returns no
   gap id.
4. **The category.** A second item of the same question picks one key of
   `skill_families.SKILL_FAMILIES`, or `other`. No answer gives `other`.
   The CHECK holds the shape only, and a Python test pins the set.
5. **The engine.** `decide_tools.ask_typed(context, [answer, category],
   purpose="tools.gap")`, only when `decide_tools.typed_choices_on()` is
   true. `sure_choice` reads each answer. SBX-1a adds no entry to
   `gateway/decide_features.py`.
6. **The library check** reads `packages_store.list_summaries` and
   `resolve.visible_installs` only. It keeps an owned package that is live
   and not archived, and an install at a version. Python ranks by the count
   of shared content stems, keeps 2 or more, and keeps 5 at most. A
   `system` package leaves the check.
7. **The compose check** calls `catalog_rows()` before any session of the
   hook opens. `callable_catalog(features, rows)` keeps a `manifest` row when
   `in_app_path.member_features()` holds the feature of its app, and a
   `logic` row when it holds `studio`. It drops every other row. At most 20
   rows, by a lexical match on the title. It never calls `package_rows`.
8. **The model sees ids and fixed sentences only.** No text that a member
   wrote in a package. `candidates` holds ids only.
9. **`user_tool_runs` moves to SBX-4.**
10. **The route** `GET /studio/tool-gaps` lives in `routes/studio/`, not in
    a new `routes/user_tools.py`. The `/api/studio/[[...path]]` proxy of the
    Control Plane forwards it.
11. **`thread_id` references `chat_session (id)`** with ON DELETE CASCADE,
    as 459 does.

**Files.**

- New: `infra/postgres/504_tool_gaps.sql`. The lane booked the number for
  this task (R1). Check it again at merge.
- New: `apps/services/gateway/gateway/studio/tool_gaps.py`, the one writer
  and reader: `record_gap` and `gap_summary`.
- New: `apps/services/gateway/gateway/studio/package_search.py`:
  `search_visible(db, *, viewer, query, limit=5)` and
  `callable_catalog(features, rows)`.
- New: `apps/services/gateway/gateway/routes/studio/tool_gaps.py`, the route.
- New: `apps/services/orchestrator/orchestrator/tool_gap_path.py`:
  `make_gap_hook()` and the hook.
- Changed: `packages/acb_skills/acb_skills/tool_packs.py` (`FindTools`
  takes `on_gap`), `packages/acb_skills/acb_skills/tool_search.py` (the pure
  helper `shared_stems`), `apps/services/orchestrator/orchestrator/_tool_injection.py`
  (`_make_finder` passes the hook), `packages/acb_common/acb_common/settings.py`
  (the flag), `apps/services/gateway/gateway/routes/admin/members.py` (the
  purge) and the generated tenancy phases.

**Done when.**

1. The migration applies on a fresh dev database and on a copy of the
   current schema, and `test_tenant_coverage.py` passes.
2. As the NOBYPASSRLS role, org A cannot read a `tool_gaps` row of org B.
3. With the flag off, the finder gets no hook. The no-match answer equals
   `FIND_NONE` byte for byte. No row, no decide call and no catalog read.
4. With the flag on, each no-match call of `find_tools` writes one row,
   mapped by decision 3. A call that ranked a tool writes none. A call that
   a cap, the budget, the MCP rule or "unavailable" stopped writes none.
5. (D187) A published Studio package of the member that matches the need
   comes back as `reuse`, with `app_id`. The test makes it with
   `create_package`, then `publish_version`. A draft with no live version
   never comes back.
6. (D187) A need that two callable catalog rows cover gives a `compose`
   row, never `no_match`.
7. (D187) As member B, a private package of member A with no install that
   reaches B never comes back. It is not in `candidates`, in the decide
   context or in the row. An org install of A's package does come back. A
   paused install does not.
8. The `sandbox.tool.gap` line holds no need text.
9. (D187) A catalog row whose feature the member lacks never reaches the
   decide context. No session of the hook is open while `catalog_rows` runs.
10. No row for a delegated run, an agent other than `orchestrator`, a
    WhatsApp turn, an org off `TOOL_SEARCH_ORGS` or `STUDIO_ORGS`, or a
    member without `feature:studio`. Neither `find_tools` nor the hook is in
    `_collect_injectable_platform_tools()`. The org, the member, the thread
    and the run come from the run context only.
11. No engine answer, or a pick below the threshold, gives `unsure`,
    `unsure` and `other`. The category is always a key of
    `SKILL_FAMILIES` or `other`.
12. The route answers 404 for an org off `STUDIO_ORGS` or with the flag
    off, and 403 without `studio:publish`. The 200 holds the counts by
    outcome and by category for 30 days, and the 50 newest rows with the
    need, the category, the outcome and `created_at` only. Rows of org B
    never show.
13. A fault, or a timeout after 8 seconds, in the hook gives `FIND_NONE` and
    no row. The log line names the error class only.

**Fences.** SBX-F7 (the find half), `test_user_tool_injection.py`. SBX-F8
(the record half), `test_tool_gap_detection.py`. SBX-F9 (the RLS half) and
SBX-F23, `test_user_tool_registry.py` (R8, in the must-run list). SBX-F15
(the gap half), `test_sandbox_log_lines.py`. SBX-F18,
`test_tool_compose_check.py`.

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

**The log level.** A refusal before step 4 logs at info, because anybody on
the network can cause it. That includes step 3: an unknown, expired or
revoked token. A refusal at step 4 or later logs at warning, as
`_UNSIGNED_REFUSALS` (`deps.py:436`) splits branch 0.

**The digest.** The two new functions that see the request,
`_is_sandbox_request` and `_sandbox_user`, join `_BRANCH_ZERO`
(`tests/unit/test_tenant_request_binding.py:238`). The helpers that do not
see the request join `_BRANCH_ZERO_HELPERS`. So the scan, the tripwire and
the ONE digest pin (`_BRANCH_ZERO_DIGESTS`, `:1210`) cover the sibling.

- Why not a second pin: the scan already refuses a request handed to a
  function outside `_BRANCH_ZERO`. A second pin for the same boundary would
  be a second fence that drifts.
- The sibling gets its own header set in the scan: `x-sandbox-capability`,
  and the header names that it reads only to refuse: `authorization`,
  `x-user-email` and `x-user-role` (`x-actor-via` is in the shared set
  already). Only `_is_sandbox_request` and `_sandbox_user` may name them, so
  the scan still refuses such a read in `_agent_user`. The tripwire allows
  them only while the sibling runs. The sibling reads the `x-agent-` prefix
  through the header names, as branch 0 does.
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
    for `run` only (§12.5 decision 6).
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
  answer bytes. A refusal before step 4 has no org and no run.
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
and no gateway change. Its end-to-end proof needs SBX-2. It may merge
before SBX-2.

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
  test imports `acb_common.app_manifests.SANDBOX_ROUTES` when it exists.
  SBX-13 may merge first. Then SBX-2 adds the check that each path of
  `PAGED_ROUTES` of `metorite_door` is in `SANDBOX_ROUTES`. When SBX-2 merges
  first, the test of SBX-13 makes the check, and a skip is not allowed.
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

### 12.4 SBX-4b — build_tool, the build session and the smoke test

**Board.** SBX-4b1 is Metorite task 3986. SBX-4b2 is Metorite task 4049.
Risk tier HIGH. Code runs in a container that holds a live read capability.
The slice also adds a FORCE RLS table, a migration and a chat tool.

The anchors below were read on 2026-11-26 at `main` `3064753ee`, on PR 2862
(SBX-4a) and on PR 2887 (WS-43g1). Each anchor names its source: **main**,
**PR 2862** or **PR 2887**. Re-verify each anchor at dispatch.

**The split.** SBX-4b is two pull requests, because one Opus builder cannot
hold all of it in one round. Each half has its own proof.

- **SBX-4b1 (task 3986), the step engine.** The start path, the mounts, the
  grants, the capability, the package check, the install, the smoke test and
  the round cap (decisions 1 to 8). Also three parts with no open PR below
  them: (a) the door refusal counter, (b) `user_tool_runs`, and (c) the
  reserved agent names. A fixture package stands in for the model.
- **SBX-4b2 (task 4049), the chat half.** `build_tool`, the build card, the
  gap id in the answer of the no-match path, and the build session on the
  one harness. Also the outcome of the gap row and the line
  `sandbox.tool.build`.

**Status.** Parts (a), (b) and (c) of SBX-4b1 are built dark and merged
(2026-11-27, task 3986, PR 2896, `ae5b37cde`). Done-when 1 to 7 pass. The
merge commit names the Opus diff-reviewer review of the store digest.

- Part (c) pins the literal `"user-tool"` until PR 2862 merges, and then
  uses `USER_TOOL_AGENT`.
- The step engine (decisions 1 to 8) is not built. It waits for PR 2862
  only. WS-43g2a (task 4070) merged dark (`a95d58d09`, PR 2924), and PR 2887
  merged (`d2bffe963`).
- SBX-4b2 waits for SBX-4b1.

**Depends on.**

- **SBX-4a** (task 3803, PR 2862, open): `TOOLS_TARGET`, `USER_TOOL_AGENT`
  and the refusal in `acquire()`.
- **WS-43g1** (task 4009, PR 2887, merged dark `d2bffe963`): `grant_egress`,
  `revoke_egress`, `check_grant_hosts`, the forward of `DOOR_HOST` and
  `sandbox_egress_door_upstream`. pip needs it to reach PyPI. `metorite_door`
  needs it to reach the door name.
- **WS-43g2a** (task 4070, merged dark a95d58d09, PR 2924): one egress network for each run
  (decision 3), and the door read timeout of the proxy.
  `maf_coding_engine.md` §7.3 and §11 own it.
- **Not WS-43g2b** (task 4092). It holds the live `request_network_access`
  and the network card. SBX-4b needs neither. SBX-3b (task 4084) owns the
  `timeout_s` parameter of `request_confirmation`, and WS-43g2b uses it.
- **SBX-1a, SBX-2 and SBX-13**, on `main`: the gap row, the capability and
  the door, and the door client.
- **Not SBX-3.** SBX-4b holds no write. The smoke capability holds no write
  scope. The package check refuses a `tool.json` with a write scope. (The
  owner picked the SBX-3 design on 2026-11-27, §12.5. The smoke step still
  writes nothing.)

**Why not a half with no network.** A half with image packages only and no
door misses the demo of rung 6b. That demo reads two pages through the door
(§0c), and SBX-13 moved its real-door proof here (§12.3). Such a half also
needs a second start path, which WS-43g1 replaces at once. So SBX-4b waits
for WS-43g1, and no slice takes the half with no network.

**Scope.** SBX-4b1 and SBX-4b2 as above, on the Docker backend, on the dev
stack, dark. No production box gets `tools:<org>` in `MAF_CODING_SCOPE`
before SBX-9.

**Not in SBX-4b.**

- No `run_tool`, no persist, no reuse and no `app_versions.files`. SBX-5
  owns them. A pass of the smoke test keeps nothing (§6.5).
- No write and no held write (SBX-3).
- No `usage_event` and no credit check. Task 3806 asks the owner which
  credential writes the sandbox usage row. SBX-7 owns the meter. The column
  `user_tool_runs.credits` stays NULL (§7.3). The build card shows no
  estimate.
- No `docker-remote` and no `runsc` start of the target. SBX-15b owns them.
  `REMOTE_TARGETS` keeps `code_task` only (PR 2862).
- No production container and no flip. Task 3786 (BH-3, the escape risk) is
  open. SBX-4b runs in `sandbox_docker` tests and on the dev stack only.
- No `web:` profile, and no `registry.npmjs.org`. A tool is Python only.
- No age check, no advisory check, no licence check and no layer. These are
  the package rules of `ai_studio.md` §19.5. SBX-5 is their single owner,
  and no tool persists without them (the SBX-5 row).
- No silent build. SBX-20 owns the path with no card (§0d).
- No change to `deps.py`, so the branch-0 digest stays.

#### The decisions

**1. The start path.** `acquire()` keeps its refusal of `tools` (PR 2862).
SBX-4b1 adds one way in, as `run_compute` does for `compute` (main).

- `read_tool_binding()` sits beside `read_compute_binding()`
  (`sandbox_broker.py:849`, main). It sets the agent to `USER_TOOL_AGENT`.
  So the container key is (org, `user-tool`, thread), as §6.2 says.
- The org, the member and the thread come from the run binding only (R5).
- `SandboxBroker.acquire_tool()` starts or reuses that container. It
  refuses unless `maf_coding_scope_allows(TOOLS_TARGET, org)` is true.
- Two new settings cap it: `user_tool_max_per_org` and
  `user_tool_max_total`, each with the default 1 (§5.6). At a cap it raises
  `SandboxBusy`.
- One build runs at a time in a thread. The step engine holds a lock of
  (org, thread). A second build in the thread gets "busy".
- Only the step engine calls `acquire_tool()`. A source scan in SBX-F11
  fails on a second caller.

**2. The mounts.** `mount_list` (`sandbox_broker.py:1122`, main) gets a
`tools` branch. It mounts no working dir of an agent and no tenant dir.

`projects_mounts` mounts the run-data dir of the same thread read-write
(`sandbox_broker.py:1115`, main). A folder inside that dir is still inside
that mount. So the `tools` branch mounts a sibling dir outside it:
`.run-data/<org slug>/<thread slug>.user-tool/`, called `<tool-data>` below.

| Host source | Container target | Mode |
|---|---|---|
| `<tool-data>/` | `/workspace/.run` | Read-write |
| `<tool-data>/outputs/` | `/workspace/outputs` | Read-write |

- `RunBinding` gains `tool_data_rel`, beside `run_data_rel`
  (`agent_paths.run_data_rel`, main). A thread slug ends in 8 hex digits,
  so `<thread slug>.user-tool` is never the slug of another thread.

- The package lives in `/workspace/.run/pkg/`. The lock installs into
  `/workspace/.run/site/`. The smoke input is `/workspace/.run/input.json`.
- `/workspace/outputs` is not the `outputs/` folder of the member. So a build
  or a smoke test never writes a member file. SBX-5 decides the outputs of a
  real run.
- Each build starts with an empty `pkg/`.
- Each round deletes `site/`, `outputs/` and `input.json` first.
- The run end deletes `<tool-data>` too. `_note_run_data` (main) records
  the rel of `<tool-data>` for the thread. `end_run` (main) removes it with
  `_remove_run_data`, as it removes the run-data dir (`maf_coding_engine.md`
  §16.3). The startup sweep `remove_all_run_data` removes the whole
  `.run-data` dir, so it covers `<tool-data>` with no change.
- Today the map of `_note_run_data` holds ONE rel for each (workspace,
  thread) key. The step engine changes it to hold a set of rels for each
  key, and a `tools` binding adds `tool_data_rel`. Without the change, two
  bindings of one key overwrite each other's rel.

**3. The grants of a round.** The build card is the approval of the member.
The build session runs its commands with `--network none`. Each round of the
smoke test then moves through two grants:

1. `grant_egress` with `pypi.org` and `files.pythonhosted.org`. The install
   of decision 6 runs.
2. `grant_egress` again with `metorite-api.sandbox` only. The smoke command
   runs.
3. `revoke_egress`. The container goes back to `--network none`. This step
   also runs on a failure and on a cancel.

- **Decided 2026-11-27 (task 3986, answer `fd97a2bd`, SBX-Q2 residual).**
  Only the door is reachable while a capability is live. The install runs
  first, and then the door host only, for each smoke round and each run.
  PyPI publishes its download counts as a public dataset. So a package name
  that the code asks for can carry data out. SBX-4b builds the narrow grant
  of step 2.
- `EGRESS_TARGETS` (PR 2887) gains `TOOLS_TARGET`. `projects` and `compute`
  stay out.
- `check_grant_hosts` (PR 2887) refuses the door name while
  `sandbox_egress_door_upstream` is empty. So a stack with no door upstream
  refuses the build before any start.

**This edits the seam of PR 2887.** `_grant_inputs` (PR 2887, about line
3980) compares the name of `read_run_binding()` with the name of the handle.
A `tools` handle has the key (org, `user-tool`, thread), so that check
refuses it. SBX-4b1 changes `_grant_inputs`: for a `tools` handle it compares
with `read_tool_binding()`. The org and the member still come from the run
binding.

**The network: one for each run (decided 2026-11-27, task 4009, answer
`b5dbe733`).** WS-43g1 puts each org on one egress network, and
inter-container traffic (ICC) is on. So a `tools` container on that network
sits next to the `code_task` containers of other members of the org.

The owner chose one egress network for each run. WS-43g1 merged as it is
(`d2bffe963`), and WS-43g2a (task 4070) builds the network of each run. The
`tools` container uses the network of its own run from WS-43g2a. So the
step engine of SBX-4b1 waits for WS-43g2a too.

**4. The capability reaches the smoke command only.**

- The build phase mints no capability. A door call from a build command gets
  401 (§5.1). The session gets the list of `SANDBOX_ROUTES` and their read
  scopes as text.
- Each round mints one capability: `phase="smoke"`, the read scopes of
  `tool.json`, no write scope and `ttl_s=180` (`sandbox_capabilities.mint`,
  main).
- `SandboxBroker.exec` gains `env_file`. The broker writes the token to a
  file of mode 0600 in a folder of mode 0700.
- The broker passes that file with `docker exec --env-file`, and deletes it
  when the command ends. This is the path of the grant token of PR 2887. No
  process list shows the token.
- **No build process lives at the smoke command.** The grant of step 2
  recreates the container through `_restart` (PR 2887). So each process of
  the build ends before the token arrives. No build process can read
  `/proc/<pid>/environ` of the smoke command.
- The round ends with `revoke_step(org=…, run_id=…, phase="smoke")` (main).
  This also runs on a failure or a cancel.

**5. The package check.** `check_package(pkg_dir, approved_scopes)` runs on
the host, in a new module `acb_skills/user_tool_build.py`. It reads through
the safe opener and follows no link. It refuses a package when:

- the package lacks one of the five files of §6.3,
- the package holds a sixth top-level name or a file that is not regular,
- the package holds more than 262144 bytes (the cap of §7.2),
- `tool.json` is not one JSON object with `input_schema`, `output_schema`,
  `read_scopes` and `write_scopes`,
- a read scope is not in `SANDBOX_READ_SCOPES` (main),
- a read scope is not in the set that the member approved on the card,
- `write_scopes` is not empty,
- `smoke.json` holds no `input` that is valid under `input_schema`,
- `SKILL.md` has no `name` and `description` front matter,
- a lock line is not `<name>==<version>` with a `--hash=sha256:<64 hex>`,
- the lock has more than 20 lines.

An empty lock is valid: the tool then uses the image packages only. Each
refusal has one fixed code, and the model hears that code only.

**6. The install.** The step engine runs pip, never the model. The command is
`pip install --require-hashes --only-binary :all: --no-deps --target
/workspace/.run/site -r /workspace/.run/pkg/requirements.lock`.

- `--no-deps` makes the lock hold each indirect package too (§19.5 of
  `ai_studio.md`).
- The install has 180 s, the limit of §19.5.
- `site/` must hold 300 MiB or less after the install, the layer cap of
  §19.5. Above it the round fails with `install_too_large`.
- The broker's workspace quota (`sandbox_workspace_quota_mb`) bounds each
  download on the disk.

**7. The smoke test.** `run_smoke(handle, pkg)` runs one round:

1. The package check (decision 5), and the install with grant 1.
2. It writes the `input` of `smoke.json` to `/workspace/.run/input.json`.
3. It moves to grant 2, and mints the capability (decision 4).
4. It runs `python3 /workspace/.run/pkg/main.py` with a timeout of 120 s.
   The env holds `PYTHONPATH=/workspace/.run/site` and `PYTHONNOUSERSITE=1`.
5. It reads the door counts (decision 9), and revokes the capability.

The round passes when the four conditions of §6.3 hold:

| Condition | The test of SBX-4b |
|---|---|
| 1. The exit code is 0 | `ExecResult` exit code 0 |
| 2. The output matches the output schema | `/workspace/outputs/result.json` is one JSON value, valid under `output_schema`. The check uses `jsonschema`, a new direct dependency of `acb_skills`. Today `uv.lock` holds it only as an indirect package |
| 3. The step stayed inside its limits | No timeout, no exit code 137, a call count of 600 or less, and the broker's quota check passed |
| 4. No call hit a route outside the declared scopes | The refusal count of the token is 0 (decision 9). A read error of the count fails the round |

A smoke test in SBX-4b writes nothing. A write gets 403 `sandbox_read_only`
from the door of SBX-2, and the refusal counts under condition 4. §5.3 names
the code `smoke_is_read_only`. No code holds that name today, and SBX-3
decides it.

**8. The round cap and the time.**

- A round is one call of `run_smoke` for one build. A build has at most 3
  rounds. The step engine holds the count, not the model.
- The fourth call raises `SmokeRoundsExhausted` before any command, and the
  build fails.
- The whole build has 600 s (`user_tool_step_seconds_build`, new, §5.6).
  The session, the installs and the rounds all count against it.
- Each round has the smoke budget of §5.6, 180 s, for steps 2 to 5. The
  install of step 1 counts against the build budget only.
- Past a budget the broker kills the step, and its row reads `timeout`.

**9. The door refusal counter.** Condition 4 needs a count that the code
cannot hide. A tool can catch a `DoorError` and still exit 0.

- `sandbox_capabilities.lookup` (main) notes the token hash and the org of a
  live row on the open `DoorCall` record.
- `sandbox_capabilities.log_door_call` (main) marks that record as refused
  for one of four codes. The codes are `sandbox_read_only`,
  `sandbox_route_excluded`, `sandbox_route_unknown` and `sandbox_scope`.
- `SandboxDoorLimits` (`gateway/sandbox_door.py`, main) then adds 1 to the
  Redis key `sandbox_door_refusals` of the token hash. It uses the tenant
  Redis wrapper of the row's org, with the life of the call counter.
- **The pass-through path changes.** Today a request that the door did not
  admit passes through byte for byte. Now the middleware makes the INCR
  before it forwards `http.response.start` of a refused request.
- `sandbox_capabilities.door_counts(org, token_sha256)` reads the call count
  (`CALLS_NAMESPACE`, main) and the refusal count. Each round has its own
  token, so no round sees the count of another round.
- `deps.py` does not change, so the branch-0 digest of
  `test_tenant_request_binding.py` stays. The store digest of
  `test_sandbox_door_identity.py` changes. Its commit names the Opus
  diff-reviewer review and the independent verification, as SBX-2 did.
- **Residual, open until SBX-5.** A Redis failure at the INCR loses one
  count. The refused call returned no data. SBX-5 must close this residual
  before it persists a tool.

**10. `user_tool_runs`.** SBX-4b1 adds the table of §7.3 in the next free
migration (R1). Book the number with `~/lanes/reserve_migration.sh` first.

- FORCE RLS through the generated migration, and an entry in
  `test_tenant_coverage.py`.
- `thread_id` is nullable, and references `chat_session (id)` with ON DELETE
  SET NULL. A run row is the record of a step that ran code, and SBX-7 adds
  its credits. A Delete of the chat must not delete that record. The writer
  still selects the chat under the bound tenant, so a chat of another org
  gives no row.
- One module, `gateway/studio/user_tool_runs.py`, runs the SQL.
- It writes one `build` row for each build, and one `smoke` row for each
  round. SBX-4b writes no `run` row.
- A refusal of `check_package` writes a `smoke` row with the outcome
  `refused`, and `error_class` holds the fixed code of decision 5.
- `backend` is `docker-runc` on the dev stack (§7.3).
- SBX-4b fills `api_reads`, `wall_ms`, `outcome`, `exit_code` and
  `error_class`. It writes the three write counts as 0. `cpu_ms`,
  `peak_mem_mb`, `egress_bytes` and `credits` stay NULL.
- The member purge (`routes/admin/members.py`, `_PersonRows`, main) deletes
  the rows of a leaver, as it does for `tool_gaps`. SBX-7 reviews this rule
  when credits land.
- The row writes no `usage_event`. SBX-7 adds the meter after task 3806.

**11. The reserved names.** The broker names `user-tool` and `compute` are
reserved at agent registration.

- `acb_skills/agent_paths.py` (main) gains `BROKER_AGENT_NAMES =
  frozenset({"user-tool", "compute"})`.
- Both names pass `AGENT_NAME_RE` (`agent_paths.py:131`, main). So the
  regex alone does not stop them.
- `register_agent` (`routes/agent.py:2250`, main) adds the set to its
  `reserved` set (`:2275`). A request for either name gets 409.
- A test pins the set to `COMPUTE_AGENT` (`sandbox_broker.py:137`, main) and
  to the literal `"user-tool"`. After PR 2862 merges, the test reads
  `USER_TOOL_AGENT`.
- The reason: a real agent with either name shares the container key of a
  broker step (§6.2).

**12. `build_tool` (SBX-4b2).** One chat tool,
`build_tool(gap_id, read_scopes, brief)`, for the agent `orchestrator`, in
`acb_skills/user_tool_build.py`.

- **Offered** only when four things hold. `MAF_CODING_SCOPE` names
  `tools:<org>`. `user_tool_find_enabled` is on. `SANDBOX_DOOR_ORGS` names
  the org. `sandbox_egress_enabled` is on.
- A `_build_tool_withheld()` step in `_tool_injection.py` follows
  `_compute_withheld()` (main). It holds the rules of SBX-F7.
- SBX-F7: the agent has a `tool_scope`, the scope names the org, and the run
  has a chat. A WhatsApp turn and a delegated run do not get the tool.
- **The gap id.** The tool reads the row through `gateway/studio/tool_gaps.py`
  (main) under the bound tenant. It refuses a row whose outcome is not
  `no_match`, or whose `run_id` is not this run (SBX-F8).
- It also refuses a row older than 30 minutes, or a row that already has a
  `build` row in `user_tool_runs` (SBX-F8).
- **The card.** `request_confirmation` (`acb_skills/ask_tools.py:2822`,
  main) shows the card of §6.1 with `non_interactive_default="deny"`.
- The card shows the `need` of the gap row, never the words of the model. It
  shows the read scopes of the call and the line "may download Python
  packages from PyPI".
- It shows the line of a marked session when `egress.session_is_marked` is
  true. It shows no credit estimate.
- A refusal sets the gap to `declined`, and nothing starts. An approval
  fixes the approved read scopes, and `check_package` holds the package to
  them.
- `brief` goes to the build session only. It never reaches the card.
- **The answer of the no-match path.** `tool_gap_path.sentence` (main) adds
  the gap id to the `no_match` sentence, so the model can pass it. SBX-1a
  returned no gap id (§6.1).

**13. The build session (SBX-4b2).** It runs on `coding_harness_agent`
(`code_session.py:365`, main), the one harness of CA-14, with the profile
`tool_builder` (§6.3, §6.9).

- Its tools are the file tools over `/workspace/.run/pkg`, `run_command`
  and `smoke_test`.
- This `run_command` is a wrapper of the step engine on the handle of
  `acquire_tool`. It is not `sandbox_tools.run_command`.
- `smoke_test` calls `run_smoke`.
- A tool pin like `_session_tool_pin` (`code_session.py:397`, main)
  withholds and refuses each other tool.
- The model is the setting `user_tool_build_model` (§6.9). Its default is
  `tier-balanced`, as for `code_task`.
- The session ends when a round passes, when the cap of decision 8 stops
  it, or at 600 s.

**14. The outcome of the gap row (SBX-4b2).** A new store call
`set_gap_outcome` in `tool_gaps.py` moves a row from `no_match` to
`declined` or `build_failed`, and from no other outcome. A smoke pass leaves
`no_match`. SBX-5 writes `built` after the first good run (§6.5). The
`build` row of `user_tool_runs` stops a second build of the same gap.

**15. The log line.** `sandbox.tool.build` of §9.1: org, run, gap id,
outcome, wall ms and smoke rounds. No need text, no code and no token.

**16. The settings of the dev-stack demo.** The demo of rung 6b needs all of
these on the dev stack. None of them is set on a production box.

- `MAF_CODING_SCOPE` holds `tools:<org>`.
- `SANDBOX_DOOR_ORGS` names the org.
- `sandbox_egress_enabled` is on.
- `sandbox_egress_door_upstream` holds the address of the dev gateway.
- `user_tool_find_enabled` is on, with `STUDIO_ORGS` and `TOOL_SEARCH_ORGS`.

#### Files (re-verify at dispatch)

SBX-4b1:

- Changed: `apps/services/orchestrator/orchestrator/sandbox_broker.py`.
  `read_tool_binding`, `acquire_tool`, the `tools` branch of `mount_list`,
  `env_file` on `exec`, and `TOOLS_TARGET` in `EGRESS_TARGETS` (PR 2887).
  `_grant_inputs` (PR 2887) takes the tool binding (decision 3).
- Changed: `packages/acb_common/acb_common/settings.py`.
  `user_tool_max_per_org`, `user_tool_max_total` and
  `user_tool_step_seconds_build`.
- New: `packages/acb_skills/acb_skills/user_tool_build.py`. `check_package`,
  `run_smoke`, the round cap and the step engine. Changed:
  `packages/acb_skills/pyproject.toml`, with `jsonschema`.
- Changed: `packages/acb_auth/acb_auth/sandbox_capabilities.py`, for the
  refusal note and `door_counts`. Changed:
  `apps/services/gateway/gateway/sandbox_door.py`.
- New: `apps/services/gateway/gateway/studio/user_tool_runs.py`, and the
  migration `infra/postgres/<n>_user_tool_runs.sql` with its generated RLS.
- Changed: `packages/acb_skills/acb_skills/agent_paths.py` and
  `apps/services/gateway/gateway/routes/agent.py` (decision 11).
- Changed: `apps/services/gateway/gateway/routes/admin/members.py`, one
  `_PersonRows` entry.

SBX-4b2:

- Changed: `packages/acb_skills/acb_skills/user_tool_build.py`, for
  `build_tool` and the build session.
- Changed: `apps/services/orchestrator/orchestrator/_tool_injection.py`
  (`_build_tool_withheld`).
- Changed: `apps/services/orchestrator/orchestrator/tool_gap_path.py` (the
  gap id) and `apps/services/gateway/gateway/studio/tool_gaps.py`
  (`set_gap_outcome`).
- Changed: `packages/acb_common/acb_common/settings.py`, for
  `user_tool_build_model`.

#### Done when

Each item names its test. The builder sees each test fail first.

SBX-4b1, part (a), the door refusal counter:

1. The door counts a refusal for each of the four codes of decision 9. It
   counts none for an admitted call or a 422. The line `sandbox.door.call`
   of a refusal holds no token and no token hash.
   `test_sandbox_door_identity.py::test_the_door_counts_each_scope_refusal`.
2. The count exists before the client reads the status of the refusal.
   `test_sandbox_door_identity.py::test_the_refusal_count_lands_before_the_answer`.
3. The store digest changes in a commit that names the review:
   `tests/unit/test_sandbox_door_identity.py::test_the_digest_of_the_capability_store_is_pinned`.
   The branch-0 digest does not change:
   `tests/unit/test_tenant_request_binding.py::test_branch_zero_digest_is_pinned`.

SBX-4b1, part (b), `user_tool_runs`:

4. R8, on a real database as the NOBYPASSRLS role: org B cannot see a row
   of org A. `tests/unit/test_user_tool_registry.py::test_user_tool_runs_rls`.
5. R8: a Delete of the chat sets `thread_id` to NULL and keeps the row. A
   chat of another org gives no row.
   `test_user_tool_registry.py::test_a_chat_delete_keeps_the_run_row`.
6. The member purge deletes the rows of a leaver.
   `test_user_tool_registry.py::test_the_member_purge_deletes_run_rows`.

SBX-4b1, part (c), the reserved names:

7. `register_agent` answers 409 for `user-tool` and `compute`.
   `test_user_tool_build.py::test_the_broker_agent_names_are_reserved_at_registration`.

SBX-4b1, the step engine:

8. A `sandbox_docker` test builds a fixture tool through the step engine,
   with a fixed package in place of the model.
   `test_user_tool_build.py::test_a_fixture_tool_builds_and_passes_its_smoke_test`.
9. In that test, the package has the five files of §6.3. Its lock pins one
   small wheel that the image lacks, with its hash. The install reaches PyPI
   through the proxy.
10. In that test, the fixture reads two pages through `metorite_door`, the
    proxy and the door kit (`tests/unit/_sandbox_door_kit.py`). Smoke
    conditions 1 to 4 pass. This is the real-door proof of §12.3.
11. `check_package` refuses a package that lacks each one of the five files,
    and a lock line with no hash. `test_user_tool_build.py::test_a_package_without_each_file_is_refused`
    and `::test_a_lock_line_without_a_hash_is_refused`.
12. The fourth `run_smoke` of one build raises `SmokeRoundsExhausted`, and
    no command runs. `test_user_tool_build.py::test_a_fourth_smoke_round_is_refused`.
13. A fixture tool with `projects:read` only calls `GET /crm/leads`, catches
    the `DoorError` and exits 0. The round fails on condition 4.
    `test_user_tool_build.py::test_a_call_outside_the_declared_scopes_fails_the_smoke_test`.
14. A fixture tool that sends a POST fails condition 4. The door answers 403
    `sandbox_read_only`. `test_user_tool_build.py::test_a_write_in_the_smoke_test_fails_it`.
15. `check_package` refuses a write scope, and a read scope outside the
    approved set. `test_user_tool_build.py::test_a_scope_outside_the_approval_is_refused`.
16. Each condition fails alone: exit 1, a `result.json` off the schema, a
    timeout, and one refusal.
    `test_user_tool_build.py::test_each_smoke_condition_fails_alone`.
17. A round deletes `site/`, `outputs/` and `input.json` first, and a build
    starts with an empty `pkg/`.
    `test_user_tool_build.py::test_each_round_starts_clean`.
18. An install past 180 s, or a `site/` over 300 MiB, fails the round. A
    second build in the same thread gets "busy".
    `test_user_tool_build.py::test_the_install_and_the_thread_limits`.
19. R8: the step engine revokes the capability after each round, also after
    a failure. `test_sandbox_capability.py::test_the_smoke_round_revokes_its_capability`.
20. The token never shows in the `docker exec` argv, and the env file is
    gone after the command.
    `test_sandbox_broker_tools.py::test_the_smoke_token_reaches_the_command_by_env_file_only`.
21. A background process that a build command starts does not live at the
    smoke command. A `sandbox_docker` test reads `/proc` in the smoke command.
    `tests/unit/test_sandbox_no_secrets.py::test_no_build_process_lives_at_the_smoke_command`.
22. The env of the smoke command holds `METORITE_API_TOKEN` and no platform
    secret of §5.4. A build command holds no `METORITE_API_TOKEN`.
    `test_sandbox_no_secrets.py::test_the_tools_container_holds_no_secret`
    (SBX-F4, first part, with a `sandbox_docker` half).
23. The `tools` container mounts the two dirs of decision 2 and nothing
    else. No source of it lies inside a source of `projects_mounts` for the
    same thread, and no source of `projects_mounts` lies inside one of it.
    `end_run` removes `<tool-data>`.
    `test_sandbox_broker_tools.py::test_the_tools_mounts_do_not_overlap_projects`.
24. The install runs with the two PyPI hosts only. The smoke command runs
    with the door name only. The step revokes on a pass and on a failure.
    `tests/unit/test_sandbox_egress_profiles.py::test_the_round_grants_are_narrow`
    (SBX-F5, first part).
25. With `sandbox_egress_door_upstream` empty, the build refuses before any
    start. A `tools` handle passes `_grant_inputs`, and a handle of another
    run fails it. `test_sandbox_egress_profiles.py::test_the_tools_grant_takes_the_tool_binding`.
26. `acquire()` still refuses `tools`, and a source scan finds no caller of
    `acquire_tool` outside the step engine.
    `test_sandbox_broker_seam.py::test_only_the_step_engine_starts_a_tool_container`.
27. Each step writes its row: one `build` row, one `smoke` row for each
    round, and `refused` for a failed package check.
    `test_user_tool_build.py::test_each_step_writes_one_run_row`.

SBX-4b2:

28. `build_tool` refuses a gap that is not `no_match`, from another run,
    older than 30 minutes, or with a build row. It refuses with no approved
    card. `tests/unit/test_tool_gap_detection.py::test_build_tool_refuses_each_bad_gap`
    (SBX-F8, build half).
29. `build_tool` does not reach an agent with no `tool_scope`, a run whose
    org the scope does not name, or an org off `SANDBOX_DOOR_ORGS`.
    `tests/unit/test_user_tool_injection.py::test_build_tool_reaches_only_the_gated_run`
    (SBX-F7, build half).
30. In the same test, a delegated run, a WhatsApp turn and a run with no chat
    do not get the tool.
31. The card shows the `need` of the gap row and the read scopes. The
    `brief` of the model is not on the card.
    `test_user_tool_build.py::test_the_card_shows_the_gap_need_and_the_scopes`.
32. The `no_match` answer holds the gap id, and the other answers do not.
    `test_tool_gap_detection.py::test_the_no_match_answer_holds_the_gap_id`.
33. A refused card sets `declined`. A failed build sets `build_failed`. A
    pass leaves `no_match`. `set_gap_outcome` refuses each other move.
    `test_tool_gap_detection.py::test_the_build_moves_the_gap_outcome`.
34. The line `sandbox.tool.build` holds no need text, no code and no token.
    `tests/unit/test_sandbox_log_lines.py::test_the_build_line_holds_no_text`.
35. The session pin refuses a tool outside the session tools.
    `test_user_tool_build.py::test_the_build_session_holds_its_own_tools_only`.
36. A scripted `sandbox_docker` test runs the chat path end to end with a
    stub model. The model calls `build_tool`, writes the package and calls
    `smoke_test`. The tool reads two pages through the door and passes.
    `test_user_tool_build.py::test_the_chat_builds_a_read_only_tool_end_to_end`.

The owner preview of rung 6b on the dev stack is a separate D145 step. It
uses the settings of decision 16. It is not a test.

#### Verification commands

SBX-4b1:

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_user_tool_build.py tests/unit/test_sandbox_broker_tools.py tests/unit/test_sandbox_broker_seam.py tests/unit/test_sandbox_no_secrets.py tests/unit/test_sandbox_egress_profiles.py tests/unit/test_sandbox_capability.py tests/unit/test_sandbox_door_identity.py tests/unit/test_sandbox_door_log_lines.py tests/unit/test_tenant_request_binding.py tests/unit/test_user_tool_registry.py tests/unit/test_tenant_coverage.py tests/unit/test_spawn_hardening.py -q -rs
timeout 1800 uv run pytest tests/unit/test_user_tool_build.py tests/unit/test_sandbox_no_secrets.py -q -rs -m sandbox_docker
uv run ruff check packages/acb_skills/acb_skills/user_tool_build.py packages/acb_skills/acb_skills/agent_paths.py packages/acb_auth apps/services/gateway/gateway/sandbox_door.py apps/services/gateway/gateway/studio apps/services/gateway/gateway/routes/agent.py apps/services/orchestrator/orchestrator/sandbox_broker.py
uv run mypy packages/acb_skills/acb_skills/user_tool_build.py packages/acb_auth/acb_auth/sandbox_capabilities.py apps/services/orchestrator/orchestrator/sandbox_broker.py apps/services/gateway/gateway/studio/user_tool_runs.py
bash scripts/premerge.sh
```

SBX-4b2:

```bash
timeout 900 uv run pytest tests/unit/test_tool_gap_detection.py tests/unit/test_user_tool_injection.py tests/unit/test_sandbox_log_lines.py tests/unit/test_user_tool_build.py -q -rs
timeout 1800 uv run pytest tests/unit/test_user_tool_build.py -q -rs -m sandbox_docker
uv run ruff check packages/acb_skills/acb_skills/user_tool_build.py apps/services/orchestrator/orchestrator/_tool_injection.py apps/services/orchestrator/orchestrator/tool_gap_path.py apps/services/gateway/gateway/studio/tool_gaps.py
uv run mypy packages/acb_skills/acb_skills/user_tool_build.py apps/services/orchestrator/orchestrator/tool_gap_path.py apps/services/gateway/gateway/studio/tool_gaps.py
bash scripts/premerge.sh
```

A run with any skipped test fails the slice. R8 needs the database. The
`sandbox_docker` half runs on `.github/workflows/sandbox-docker.yml`, as §11
says.

#### Fences

| # | Test file | What it holds in SBX-4b |
|---|---|---|
| SBX-F12 | `tests/unit/test_user_tool_build.py` | Done when 7 to 18, 27, 31, 35 and 36. New file |
| SBX-F11 | `tests/unit/test_sandbox_broker_seam.py`, extended | Done when 26 |
| SBX-F4 | `tests/unit/test_sandbox_no_secrets.py` | Done when 21 and 22, the first part. New file, with a `sandbox_docker` half |
| SBX-F5 | `tests/unit/test_sandbox_egress_profiles.py` | Done when 24 and 25, the first part. New file |
| SBX-F1 | `tests/unit/test_sandbox_capability.py` | Done when 19 |
| SBX-F6 | `tests/unit/test_sandbox_door_identity.py` | Done when 1 to 3, and the new store digest |
| SBX-F9 | `tests/unit/test_user_tool_registry.py`, R8 half | Done when 4 to 6 |
| SBX-F8 | `tests/unit/test_tool_gap_detection.py` | Done when 28, 32 and 33, the build half |
| SBX-F7 | `tests/unit/test_user_tool_injection.py` | Done when 29 and 30, the build half |
| SBX-F15 | `tests/unit/test_sandbox_log_lines.py` | Done when 34 |
| — | `tests/unit/test_sandbox_broker_tools.py` (PR 2862) | Done when 20 and 23 |
| — | `tests/unit/test_tenant_coverage.py` | The entry of `user_tool_runs` |

These fences are on tenancy, a capability and code that runs. So they also
get mutation testing (`engineering_practice.md` §4).

### 12.5 SBX-3 — writes held for a card

**Status (2026-11-27).** SBX-3a is merged dark (task 3802, `8b249bbb7`, PR
2926). The pins of the two digests changed. The Opus diff-reviewer approved
them at 8f89b8ca57. The independent verification passed at 44d12c5cb0.

`SANDBOX_DOOR_ORGS` stays empty. SBX-3b (task 4084) is built dark on the
branch `mt-b8fbec0f-sbx3b-card`, and it is not merged. Its audit
(2026-11-27) added rules to decisions 7 and 8. They cover the answer record,
the open card, the owner check, the results and the door client.

**Board.** SBX-3a is Metorite task 3802. SBX-3b: task 4084. Risk tier HIGH.
The slice changes the identity step of `get_current_user`, opens writes to
code that a model wrote, and runs those writes as the member.

The anchors below were read on 2026-11-27 at `main` `98d393683`. Re-verify
each anchor at dispatch. The Opus spec-auditor gave NO-GO on 2026-11-26, and
asked for decisions B1 to B8. The owner answered B1 and B2. This section
decides B3 to B8.

**The owner answers (2026-11-27, binding).**

| Id | Task, answer | The decision |
|---|---|---|
| B1 | Task 3802, answer `55b542fc` | §5.3 holds. The door holds each HTTP write. After the member approves the card, the gateway runs it as the member, through the route's own handler and checks. The approved rows run one by one, not in one transaction. The card shows a result for each row. §6.4 and `ai_studio.md` §3.7 follow it |
| B2 | Task 3802, answer `55b542fc` | Reuse the held-send card seam of email (`routes/email/automation/held_card.py`). The gateway mints a 32-hex `request_id`, and `request_confirmation(request_id=…)` (`acb_skills/ask_tools.py`) raises the card. The held rows live in Redis through the tenant prefix wrapper, with the TTL `sandbox_approval_timeout_seconds` (default 300). No migration |
| SBX-Q2 residual | Task 3986, answer `fd97a2bd` | Only the door is reachable while a capability is live. The install runs first, and then the door host only, for each smoke round and each run (§5.5, §12.4 decision 3, the SBX-5 row) |
| WS-43g network | Task 4009, answer `b5dbe733` | WS-43g1 merges. WS-43g2 (task 4070) builds one egress network for each run. The `tools` container uses that network (§12.4 decision 3) |

**The split.** Two pull requests. Each has its own proof.

- **SBX-3a (task 3802), the write door.** Decisions 3 to 6. They are the
  manifest rule and the write rows, the write scopes of `mint` and the held
  store. They are also the batch route, the ticket, the stamp and the
  timeout setting. A
  store function decides a card in the tests. Tests only, with no demo.
- **SBX-3b (task 4084), the card.** Decisions 7 and 8: the card of a held
  batch through the held-card seam, the approve, the refusal and the
  timeout. The card text comes from the held wire only.

**Can the gateway raise a card by itself? No.** `request_confirmation`
draws the card through the event emitter of the running chat turn. It then
waits on a future in the orchestrator process (`_pending_user_input`). The
email assistant raises the held-send card from its own tool call, with the
id that the gateway minted. So only a tool call of the turn can raise a card.

- SBX-3b builds the watcher that the tool call runs. Its first caller with
  a write scope is `run_tool` of SBX-5. The smoke step of SBX-4b writes
  nothing.
- So SBX-3b proves the card with tests and a fake emitter. The owner sees a
  write card on the dev stack in the demo of rung 6c, after SBX-5.

**Depends on.** SBX-2 (merged, `209884bac`). SBX-3b depends on SBX-3a. SBX-3
needs no part of SBX-4b. SBX-5 depends on SBX-3.

**Scope.** As the split says, on the dev stack, dark. No flag flips. Only
tests mint a capability.

**Not in SBX-3.**

- No `usage_event`, no credit check and no price. Task 3806 asks the owner
  which credential writes the sandbox usage row. SBX-3 touches neither the
  question nor the meter.
- No broker mint. The one caller of `mint` stays the step engine of SBX-4
  (SBX-F1 scans it). Task 3786 (BH-3 and the escape risk) stays open, and
  SBX-3 changes nothing that it covers.
- No flip. `SANDBOX_DOOR_ORGS` stays empty on every box.
- No migration. The held rows live in tenant Redis. The CHECK of
  `sandbox_capabilities` already refuses a write scope in the smoke phase.
- No DELETE through the door, no grant, no setting of an app and no send to
  a person (decision 3).
- No `run_tool`. SBX-5 runs the watcher of SBX-3b beside the run command.
- No email, calendar or connector write. SBX-2b, SBX-2c and SBX-2d own the
  reads first.

#### The decisions

**3. The write rows (B3).** `Route` (`app_manifests/base.py:172`) gets a
second field, `sandbox_write: bool = False`. `Route.sandbox` stays the read
field, and its rule at `base.py:223` does not change.

- `__post_init__` refuses `sandbox_write=True` on these rows:
  - a row of class A or X,
  - a GET or a DELETE,
  - a row with `sends=True`. No row of decision 3 sets it, so this rule
    guards a later row with a door op,
  - a row that also sets `sandbox=True`.
- `SANDBOX_WRITE_ROUTES` (`app_manifests/__init__.py`) maps each
  `(method, path)` with `sandbox_write=True` in `SANDBOX_APPS` to its class.
- `SANDBOX_WRITE_SCOPES` is the write scope of each app of `SANDBOX_APPS`:
  `crm:write` and `projects:write`.
- **The default is off.** A row joins only by an edit to its own line, as
  for reads. A DELETE never joins, because §5.3 puts deletes in class X for
  the door, also where the manifest says class C.
- Class X stays 403 `sandbox_route_excluded`.

**The CRM rows that SBX-3 opens (16).** Each changes one record of the
member's CRM, with the route's own checks.

| Family | Rows | Class |
|---|---|---|
| Create | `POST /crm/leads`, `/crm/deals`, `/crm/contacts`, `/crm/organizations` | B |
| Change | `PATCH /crm/<kind>/{record_id}` for the four kinds | B |
| Log an activity | `POST /crm/<kind>/{record_id}/activities` for the four kinds | B |
| Activities | `PATCH /crm/activities/{activity_id}` | B |
| Modules | `PATCH /crm/modules/{module}/records/{record_id}` | B |
| Deal contacts | `POST /crm/deals/{deal_id}/contacts` | B |
| Convert | `POST /crm/leads/{lead_id}/convert` | C |

**The Projects rows that SBX-3 opens (13).** Each changes a task or a
project update, through the visibility seam of Projects. Each path starts
with `/projects`.

| Rows | Class |
|---|---|
| `POST /tasks`, `PATCH /tasks/{task_id}`, `POST /tasks/{task_id}/move`, `PUT /tasks/{task_id}/assignees` | B |
| `POST /tasks/{task_id}/comments`, `POST /tasks/{task_id}/complete`, `POST /tasks/{task_id}/defer` | B |
| `POST /tasks/{task_id}/links`, `PUT /tasks/{task_id}/recurrence`, `POST /nodes/{project_id}/updates` | B |
| `POST /my/tasks`, `PATCH /tasks/{task_id}/personal` | B |
| `POST /tasks/{task_id}/archive` | C |

**The class B and C rows that stay off, and why.**

| Rows | Why |
|---|---|
| Each DELETE of CRM and Projects | §5.3 puts deletes in class X for the door |
| `POST /projects/tasks/bulk` | Its action can be `delete` (`BULK_ACTIONS`, `routes/projects/bulk.py:103`) |
| Grants, authorities, statuses, types, fields, tags, views, cycles and templates of Projects | The shape and the access of a project. A person changes them in the app |
| `statuses`, `lost-reasons`, `fields`, `field-sections`, `field-layout`, `field-rules`, `catalogue`, `settings`, `views`, `pipelines` and `conversion-mapping` of CRM | The setup of the CRM, for an admin. Statuses, lost reasons, settings and pipelines write `crm_change_log`, and its `via` has no sandbox value |
| `connection/disconnect`, `cutover`, `import/zoho`, `sync`, `reset`, `mapping`, `analysis/rerun` of CRM | Connection and sync acts. A tool needs none of them |
| `questions`, `rules` and `mapping/suggestions` of CRM | The state of the CRM assistant, not record data |
| `agent-runs`, `archive-request`, `asks`, `nudge`, `decisions`, `watch`, `intake`, `notifications/read` and `my/reminders` of Projects | Agent runs, a message to a person, or the member's own inbox. A later slice may open one with a reason |
| `reports` and their recipients and schedule, and `attachments` of Projects | A report can mail a person. An attachment is a file upload |
| `POST /projects/tasks/move/preview`, `POST /projects/plan/preview`, and each GET of class B or C | A preview or a GET. They are not writes of the door |
| `POST /crm/change-history/{change_id}/undo`, `POST /projects/activities/{activity_id}/revert`, `POST /projects/tasks/{task_id}/merge` | An undo, a revert or a merge acts on other rows. A person does it in the app |
| Each other class B or C row, for example `unarchive`, `claim`, `check-result`, `epic-contract`, `POST /projects/tasks/move`, `PATCH /projects/comments/{activity_id}`, `refs` and `my/areas` of Projects | Each one stays off until a later slice opens it with a reason |

**4. The batch (B4).** Each write of the code is a row of
`POST /sandbox/v1/batch`. That is the one path in.

- A direct write is a non-GET on a route of an app. It keeps 403
  `sandbox_read_only`, the code of SBX-2, in every phase. The answer is
  403 `{"detail": "sandbox_read_only"}`, a fixed code (SBX-2 decision 8).
  The door client sends writes only through `POST /sandbox/v1/batch`.
- So step 5 of the door (`deps.py`, `_sandbox_gate`) keeps its rule. It
  admits two more cases: the batch pair, and a row with a live ticket.

**The body.**

```json
{"rows": [
  {"method": "PATCH", "path": "/projects/tasks/7f3c…", "query": {}, "body": {"status": "done"}},
  {"method": "POST", "path": "/crm/leads", "query": {}, "body": {"last_name": "Rao"}}
]}
```

- 1 to 25 rows. Each row has exactly the four keys. Any other key gets 422
  `sandbox_bad_batch`, so the model can put no label on the card.
- `method` is POST, PUT or PATCH. `path` is the path of one route, with no
  query and no empty, `.` or `..` segment. The URL that the in-process
  client builds must keep the same path, so the handler runs the row that
  it checked. Else 422 `sandbox_bad_batch`. `query` is a flat object
  of strings. `body` is a JSON object.
- Each row is at most 64 KiB as JSON, and the body is at most 512 KiB. Past
  a cap, the answer is 422 `sandbox_batch_cap`.
- **No agent assignee.** An assignee `agent:<name>` starts an agent run
  (`routes/projects/agent_dispatch.py`, after `pm.task.assigned` at
  `routes/projects/tasks.py:1303` and `:2863`). So any row whose body names
  an `agent:` assignee gets 422 `sandbox_bad_batch`, and nothing is held.
  Three rows have an assignee field (`assignees`): `POST /projects/tasks`,
  `POST /projects/tasks/{task_id}/move` and
  `PUT /projects/tasks/{task_id}/assignees`. `AGENT_ASSIGNEE_FIELDS` in
  `gateway/sandbox_batch.py` lists them. Fence:
  `test_sandbox_door_writes.py::test_every_assignee_field_of_a_write_row_is_scanned`.
  It walks the body model of each write row on the live router.
- **No Check.** An agent runs the Check of a task (`check_text`) as a shell
  command. So any row whose body holds `check_text` gets 422
  `sandbox_bad_batch`, whatever the value, and nothing is held. Two rows
  have the field: `POST /projects/tasks` and
  `PATCH /projects/tasks/{task_id}`. `REFUSED_FIELDS` in
  `gateway/sandbox_batch.py` lists them. Fence:
  `test_sandbox_door_writes.py::test_every_check_field_of_a_write_row_is_refused`.
- **One open batch for each capability.** The handler writes
  `tenant_redis.key("sandbox_batch_open", <token hash>)` with `SET NX`
  before it holds a card, and deletes it when the batch ends. A second
  batch of the same capability while one is open gets 429
  `sandbox_batch_busy`, and nothing is held.

**The checks, before anything is held.** The batch is all or nothing here.
The first failed row refuses the whole batch, and the answer names its
index.

1. The capability is not in the `smoke` phase. Else 403 `sandbox_read_only`.
2. The capability holds a write scope. Else 403 `sandbox_scope`.
3. The gateway router matches the path of each row to one template, as
   `_matched_template` (`deps.py:1704`) does for a request.
4. Each pair is in `SANDBOX_WRITE_ROUTES`. A class X pair gets 403
   `sandbox_route_excluded`. Any other pair gets 404 `sandbox_route_unknown`.
5. The write scopes hold the write scope of the pair's app. Else 403
   `sandbox_scope`.

**The cards.** The class B rows of one batch share one card, with one row
each. Each class C row gets a card of its own, with the counts first
(`projects_ai_chat.md` §5.2). The cards keep the order of the rows.

**The wait.** The batch call stays open until each card has an answer or
times out (decision 6). One open call is simpler for the code than a poll.
All the cards of one batch share one deadline. The route takes no
`get_db` and no `tenant_session` dependency. Fence:
`test_sandbox_door_writes.py::test_the_wait_holds_no_database_session`.

- **The proxy cuts a held batch at 60 seconds today.** On `main` the egress
  proxy reads with `FORWARD_TIMEOUT_S = 60.0` (`sandbox/egress_proxy.py:110`).
- WS-43g2a (task 4070) owns the fix. The broker passes
  `--door-read-timeout` with `sandbox_approval_timeout_seconds` plus 30, and
  the proxy uses it for the door host only. `maf_coding_engine.md` §7.3, "The
  door wait", holds it. A held batch through the proxy waits for WS-43g2a.
- **One owner for each part.** SBX-3a (task 3802) owns
  `sandbox_approval_timeout_seconds` (decision 6). SBX-3b (task 4084) owns
  the `timeout_s` parameter of `request_confirmation` (done-when 22).
  WS-43g2b (task 4092) uses both, for the network card.

**No database session for the wait.** The batch route takes no
`Depends(get_db)`. It reads `apps.slug` in a short session, and closes that
session before the wait. So a held batch holds no database connection.

**The answer.** 200, with one entry for each row, in the order of the rows:

```json
{"rows": [
  {"i": 0, "status": 200, "body": {"id": "7f3c…", "status": "done"}},
  {"i": 1, "status": 403, "code": "sandbox_write_refused"}
]}
```

| Case of one row | `status` | `code` |
|---|---|---|
| Approved, and the route answered | The route's own status | The route's own body in `body` |
| Not ticked, or the card refused | 403 | `sandbox_write_refused` |
| The card timed out | 403 | `sandbox_approval_timeout` |
| The capability expired or was revoked before the row ran | 401 | `sandbox_not_run` |

- **One by one (B1).** The rows run only after every card of the batch has
  a decision. Then the approved rows run in row order, each in its own
  request, under the one deadline of the batch. A failed row does not stop
  the next one. No row sees another
  row's answer. A row that needs the id of a new record goes in a later
  batch.
- The answer of the batch obeys the 4 MB cap of SBX-2. The batch call and
  each row that runs count against the call cap of 600.

**How a row runs as the member (B1).** The batch handler holds the
capability header of its own request. For each approved row, it sends one
request to the gateway app in the same process
(`httpx.ASGITransport(app=request.app)`).

- That request carries the same `X-Sandbox-Capability` and one new header,
  `X-Sandbox-Ticket: <32 hex>`. The sibling of branch 0 runs every step
  again. So the flag, the live row, the member's access, the route's own
  checks and FORCE RLS all apply.
- **The ticket.** The batch handler mints it after the approval, one for
  each approved row. It lives in tenant Redis, under the namespace
  `sandbox_ticket`, for 30 seconds. It holds the token hash, the method, the
  path, the query, the SHA-256 of the body, the stamp and the held id.
- **The body hash.** The sibling reads the body through `_signed_body`
  (`deps.py:936`), the one body reader. It reads no body in a second way.
- **The sibling takes the ticket once** (`GETDEL`), after step 4, in the
  scope of the row's organization. It compares each value with the request.
  A miss, a second use, another capability or another wire gets 403
  `sandbox_ticket`, and no tenant stays bound.
- With a ticket, step 5 admits the non-GET. Step 6 checks the pair in
  `SANDBOX_WRITE_ROUTES`. Step 7 checks the write scope.
- **The batch pair skips steps 6 to 8 of `_sandbox_gate`.** It is not a
  route of an app, so the batch handler makes the checks of its rows. Step
  9 (the call cap) still counts it.
- The one-credential rule of §12.2 decision 3 allows exactly one
  `X-Sandbox-Ticket` beside the capability. Each other `X-Sandbox-` header
  still gets 401.
- Each row runs in its own task, with a copy of the context. So the inner
  request cannot clear the tenant of the batch request.
- The code never sees a ticket. The answer of the batch holds none.
- `_is_sandbox_request`, `_sandbox_user` and `_sandbox_gate` change, and
  all three are in the digest. So the one digest pin of
  `test_tenant_request_binding.py` changes. The commit names the Opus
  diff-reviewer review and the independent verification
  (`external_agent_access.md` §10.1).
- `test_tenant_request_binding.py` adds `x-sandbox-ticket` to
  `_SANDBOX_HEADERS`. It allows the body read through `_signed_body` for
  the sandbox functions only.

**The held store (B2).** One module,
`apps/services/gateway/gateway/sandbox_batch.py`, copies the record calls of
`held_card.py` (`write_record` at `:623`, `take_record` at `:650`).

- One record for each card, under the namespace `sandbox_held`. The key is
  `tenant_redis.key("sandbox_held", card_id)`, inside `organization_scope`
  of the capability row's organization. The card id is `uuid4().hex`, 32
  hex digits, so `request_confirmation` takes it as `request_id`.
- The record holds the member, the run, the thread, the token hash, the
  held id of the batch, the class, the stamp and the rows. Each row holds
  its index, the method, the path, the template, the query and the body.
  It never holds the raw token.
- A list under `tenant_redis.key("sandbox_held_run", run_id)` in the same
  scope names the open cards of a run, for the watcher of SBX-3b.
- **The decision key.** `tenant_redis.key("sandbox_held_decision",
  card_id)`, written once with `SET NX`. Its value is `approved` with the ticked ids, `refused` or
  `expired`. The first writer wins. So an answer after the timeout runs
  nothing, and a timeout after an answer changes nothing.
- The batch handler reads the decision key every 0.5 seconds. At the
  timeout it writes `expired` with `SET NX`, and reads the key once more.
- SBX-3a adds the store function `decide(card_id, ticked)`. The act of
  SBX-3b calls it, and the SBX-3a tests call it.
- The TTL of each key is the wait of decision 6 plus 60 seconds.
- A Redis fault answers 503, as `write_record` does. Nothing is held, and
  nothing runs.

**5. The stamp (B5).** The form is `sandbox:<slug>:v<n>`. It matches
`ACTOR_VIA_PATTERN` (`agent_oauth.py:1049`). This replaces the
recommendation of §12.2 decision 12.

- **Where it comes from.** The batch handler runs with the member's tenant
  bound. It reads `apps.slug` for `UserContext.sandbox.app_id` under RLS,
  and builds the stamp with the version. It keeps the stamp in the held
  record and in each ticket.
- When the stamp does not match the pattern, for a long slug, the handler
  uses `sandbox:app-<first 8 hex of the app id>:v<n>`.
- `SandboxScope` (`acb_auth/roles.py:81`) gains `write_scopes` and `stamp:
  str = ""`. The sibling sets `stamp` only from a live ticket. A header
  never sets it.
- **Projects.** `capture_actor_via` (`routes/projects/core.py:88`) reads only
  the header today. It gains the cached `get_current_user` dependency. When
  `user.sandbox` is set, it binds `user.sandbox.stamp`, and it ignores the
  header. Each other request keeps the header rule. `record_activity` then
  copies the stamp into `meta.via`, with no change.
- **CRM.** `change_log.via_of` (`routes/crm/change_log.py:224`) returns
  `chat` only for a `chat:` stamp with the internal bearer. The CHECK of
  `crm_change_log.via` (`341_crm_change_log.sql:106`) allows no sandbox
  value. None of the 16 CRM rows of decision 3 writes `crm_change_log`. So a
  CRM row records the member as its actor, and no stamp. SBX-3 does not
  change `via_of`.
- **The provenance of a CRM write** is the log line `sandbox.door.write` and
  the audit line of the act. A CRM row that writes `crm_change_log` joins
  the door only with a later migration that adds a sandbox `via`.

**6. The mint, the smoke rule and the timeout (B6).**

- `mint` (`acb_auth/sandbox_capabilities.py:220`) takes write scopes in the
  `run` phase only. `mint` raises `ValueError` for each of these:
  - a write scope outside `SANDBOX_WRITE_SCOPES`,
  - a write scope in the `build` or `smoke` phase,
  - a write scope with no `app_id` or no `version`.
- This narrows §12.2 decision 6, which named build and run. §12.4 decision 4
  mints no capability in the build phase, so only the run step of SBX-5
  writes.
- **The smoke rule.** A smoke capability holds no write scope (the CHECK of
  migration 503 and `mint`). A direct write answers 403 `sandbox_read_only`
  in every phase. The batch route answers 403 `sandbox_read_only` to a
  smoke capability. No new code name: SBX-3 does not add
  `smoke_is_read_only`.
- **The setting.** `sandbox_approval_timeout_seconds: int = 300` in
  `settings.py`. The flag `sandbox_door_orgs` lives in `flag_registry.py`.
- **The wait and the step budget.** The wait of a batch is the setting, or
  the time left on the capability less 30 seconds, whichever is less. So the
  batch always answers before the step ends. The wait counts against the
  step's time budget (§5.6). The step engine does not stop its clock.
- **No time left.** When the wait is 0 seconds or less, each row answers 403
  `sandbox_approval_timeout` at once. Nothing is held.
- One command of the run step has 300 seconds (§5.6), the same as the
  default wait. This is an open decision of SBX-5 (its §12 row).

**7. The card (SBX-3b).** Three internal routes in `gateway/sandbox_batch.py`.
Each takes `Depends(require_internal_auth)` (`acb_auth/deps.py:1610`) and
`Depends(get_current_user)`, as the skills call the gateway.
`get_current_user` alone refuses no caller, so the internal bearer check is
the gate.

The door never reaches the three routes, because they are not in
`SANDBOX_ROUTES`. A browser session gets 401 or 404. A sandbox capability
gets 403 `sandbox_read_only` from step 5 of `_sandbox_gate`, before the
route runs (done-when 26).

- **The gate of a route.** Each route answers 404 when the org of the session
  is not in `door_orgs()` (`sandbox_capabilities.py:505`). This is the one
  reader of `SANDBOX_DOOR_ORGS`.
- **An open card.** A card is open when all four are true:
  - its held record exists,
  - it has no decision key,
  - `owner` of the record is the member of the session,
  - `run` of the record is the run id of the call.

  The held
  records of a batch stay until every card of the batch has a decision
  (`_drop` in `_hold_and_run`). So a card that has a decision is not open,
  even while its record stays.
- `POST /sandbox/held/card` takes a run id. It returns the next open card
  of that run of the member, in the order of the run list. The answer holds
  the card id, `title`, `detail`, `context`, `rows` and `seconds_left`. It reads only the
  held record and the decision key. It writes nothing.
- `POST /sandbox/held/act` takes the card id, the answer and the card
  thread. It copies `held_card_act` (`held_card.py:1873`):
  1. It reads the held record with `GET`, not `GETDEL`, because `decide`
     needs the record. A missing record runs nothing (409). When `owner` is
     not the member of the session, or `thread` is not the card thread of
     the call, it runs nothing (409). `decide` alone checks only the TTL.
  2. It takes the answer once with `reply_card.take_reply_answer`. A miss, a
     second answer, another member or another thread runs nothing (409).
  3. It checks the ticked ids against the RECORDED ids with
     `ask_tools.ticked_rows`. A forged id runs nothing (409).
  4. It calls `decide(card_id, ticked)`.
- A refusal needs no answer record. `POST /sandbox/held/act` with `refused`
  makes the check of step 1, then calls `decide(card_id, None)`. A refusal
  can only stop a write.
- **The answer record (A).** `reply_card.record_reply_answer` records a
  send answer (`APPROVE {"rows": […]}`) today only while
  `EMAIL_HELD_CARD_SEND` is on (`_recordable`, `reply_card.py:841`).
  SBX-3b adds a second case: it also records a send answer when the org of
  the call is in `door_orgs()`. `_recordable` takes the org for this.
  `EMAIL_HELD_CARD_SEND` keeps its own meaning, and the email act route
  keeps its own flag check. With `SANDBOX_DOOR_ORGS` empty, nothing
  changes (done-when 27).
- **The card text comes from the wire.** The gateway builds the title, the
  detail and each row label from the held method, template, target id and
  body keys. One pure function in `sandbox_batch.py` does this. A body value shows
  as a fenced value («…», `fenced=True`). The batch has no free-text field,
  so no model text frames the card.
- **Code text is fenced too.** Sandbox code chooses the body keys and the
  target id in the path (up to 511 characters of `[A-Za-z0-9._~:@/-]`). So
  a body key and the target id each show fenced, as a body value does.
  Only the method, the template and the fixed words of the gateway show
  unfenced.
- `context` is under 4,000 characters, with the warning line first, as
  `projects_ai_chat.md` §5.3 says. A class C card starts with the counts,
  for example "1 lead converts" for `POST /crm/leads/{lead_id}/convert`.

**8. The watcher (SBX-3b).** A new module,
`packages/acb_skills/acb_skills/sandbox_held.py`, with
`watch_held(run_id, thread_id)`. A tool call of the turn runs it beside the
command. The org and the member come from the run binding (R5).

- **The poll.** It asks `POST /sandbox/held/card` for the next open card
  every 1 second.
- **The stop.** It stops when the run command ends. It then finishes the
  card that it shows, and asks for no new card.
- It calls `request_confirmation(title, detail, context, rows=…,
  request_id=card_id, with_answer=True, non_interactive_default="deny",
  fenced=True, responder=<the run's member>, timeout_s=<seconds_left>)`.
  `responder` follows WS-63 PZ-32, as the other callers of a card do.
- `POST /sandbox/held/card` returns `seconds_left`, the time to the
  deadline of the batch. The watcher passes that value as `timeout_s`, not
  the full wait.
- A `RowsAnswer` goes to `POST /sandbox/held/act` unchanged. A refusal or
  `None` goes as `refused`.
- **No chat, no write.** With no channel, `request_confirmation` gives
  `None` (`"deny"`). The watcher sends `refused`, and each row answers 403
  `sandbox_write_refused`.
- **The timeout.** `request_confirmation` already waits 3,600 seconds
  (`wait_user_future(_fut, 3600)`, `ask_tools.py:3253`). Its `TIMEOUT`
  branch records the outcome `CARD_TIMED_OUT` ("timed out",
  `ask_tools.py:1534`).
  - SBX-3b extends that seam with an optional `timeout_s` parameter. The
    default stays 3,600. SBX-3b does not use `asyncio.wait_for`.
  - The watcher passes the wait of decision 6. On "timed out" it sends
    `refused`. A late Approve then finds the decision key set, and runs
    nothing.
- **A result for each row (B1).** The batch handler writes the answer of
  each row under `tenant_redis.key("sandbox_held_result", card_id)`.
  - This changes `_hold_and_run` of SBX-3a. It writes the results after the
    rows run, with the same `owner` and `run` as the held record. The TTL is
    the deadline of the batch plus 90 seconds, so the watcher can read it
    for 30 seconds after the deadline.
  - `POST /sandbox/held/results` spends each result once with `GETDEL`. It
    returns a result only when its `owner` is the member of the session and
    its `run` is the run id of the call. It writes back nothing else.
  - **The act does not wait.** It returns right after `decide`. The rows run
    only after every card of the batch has a decision, and the watcher shows
    one card at a time. So an act that waited would block the next card.
  - A third route, `POST /sandbox/held/results`, takes a run id. It returns
    the results of each card of that run that has them, and spends each
    result once.
  - The watcher reads it on each poll. After the run command ends, it keeps
    polling for results until the deadline of the batch plus 30 seconds.
  - For each card with results, the watcher sends one new event, `confirmation_row_results`, keyed by
    the card's `request_id`. It carries the status of each row, and no body.
  - A new `publish_card_rows` in `ask_tools.py` sends it. It copies
    `publish_card_undo` (`ask_tools.py:791`) and `confirmation_not_done`
    (`ask_tools.py:1639`).
  - The client hides the event from the chat stream by name. It joins
    `HITL_CONTROL_EVENTS` (`src/hooks/useAgentChat.ts:102`) and
    `PANEL_HIDDEN_EVENTS` (`src/components/GenerativeUIPanel.tsx:98`).
  - The client shows the results through the one receipt seam:
    `ConfirmationReceipts.tsx` and `receiptText` in
    `src/lib/confirmationQueue.ts` (UXD-3). `ConfirmationCard.tsx` gets no
    new line. `AgentChat.tsx` reads the event, as it reads
    `confirmation_not_done`.
- `metorite_door` (`apps/services/orchestrator/sandbox/metorite_door.py`)
  gains `batch(rows)`. It posts to the door name only (SBX-F22), to the one
  path `/sandbox/v1/batch`.
  - Its timeout is `sandbox_approval_timeout_seconds` plus 60 seconds. A
    batch waits up to 300 seconds by default, and `Door(timeout_s=30.0)`
    (`metorite_door.py:210`) is too short for it.
  - The image cannot read the settings. So the timeout is the constant 360
    seconds, and a test pins it to the default of the setting plus 60. A box
    that changes the setting must change this constant too.
  - The module rule "It reads only" becomes "It reads, plus the one batch
    POST". The GET-only fence
    (`test_sandbox_door_client.py::test_the_client_has_one_host_and_sends_get_only`)
    changes to allow GET and that one POST, and nothing else.

**9. The log lines.** Through `acb_common.get_logger`.

- `sandbox.door.write`: org, run, phase, the held id and the count of rows
  by class. It also holds the event (`held`, `approved`, `refused` or
  `expired`) and the count of rows that ran, by status. It holds no body, no
  path value, no token and no hash.
- The act writes one audit line of counts, as `_audit` of `held_card.py`
  does.

#### Files (re-verify at dispatch)

SBX-3a:

- Changed: `packages/acb_common/acb_common/app_manifests/base.py`.
  `Route.sandbox_write` and its rule in `__post_init__`.
- Changed: `app_manifests/crm.py` and `app_manifests/projects.py`.
  `sandbox_write=True` on the 29 rows of decision 3.
- Changed: `app_manifests/__init__.py`. `SANDBOX_WRITE_ROUTES`,
  `SANDBOX_WRITE_SCOPES` and the batch pair.
- Changed: `packages/acb_auth/acb_auth/sandbox_capabilities.py`. The write
  scope rules of `mint`, and the codes of decision 4.
- Changed: `packages/acb_auth/acb_auth/roles.py`. `SandboxScope.write_scopes`
  and `SandboxScope.stamp`.
- Changed: `packages/acb_auth/acb_auth/deps.py`. The batch pair and the
  ticket in `_sandbox_user` and `_sandbox_gate`. The ticket body hash reads
  the body through `_signed_body`.
- New: `apps/services/gateway/gateway/sandbox_batch.py`. The batch route,
  the held store, `decide` and the run of each row. Changed:
  `gateway/main.py`, which installs its router.
- Changed: `apps/services/gateway/gateway/routes/projects/core.py`.
  `capture_actor_via`.
- Changed: `packages/acb_common/acb_common/settings.py`.
  `sandbox_approval_timeout_seconds`.
- Changed tests: `tests/unit/test_tenant_request_binding.py` (the digest,
  `x-sandbox-ticket` in `_SANDBOX_HEADERS`, and the body read of the sandbox
  functions),
  `tests/unit/test_sandbox_routes_coverage.py`,
  `tests/unit/test_sandbox_capability.py`,
  `tests/unit/test_sandbox_door_identity.py`,
  `tests/unit/test_sandbox_door_log_lines.py` and
  `tests/unit/test_projects_actor_via.py`. New:
  `tests/unit/test_sandbox_door_writes.py`.

SBX-3b:

- Changed: `apps/services/gateway/gateway/sandbox_batch.py`. The two routes
  of decision 7, the card text function, and `POST /sandbox/held/results` of
  decision 8. `_hold_and_run` writes the results (decision 8).
- Changed: `packages/acb_skills/acb_skills/reply_card.py`. `_recordable`
  takes the org, for the second case of decision 7.
- New: `packages/acb_skills/acb_skills/sandbox_held.py`. `watch_held`.
- Changed: `apps/services/orchestrator/sandbox/metorite_door.py`. `batch`.
- Changed: `packages/acb_skills/acb_skills/ask_tools.py`. The `timeout_s`
  parameter of `request_confirmation`, and `publish_card_rows` with the
  event `confirmation_row_results`.
- Changed: `workbench/control_plane/src/lib/confirmationQueue.ts`
  (`receiptText`) and `src/components/AgentChat.tsx`. The result of each row
  on the receipt. `src/components/ConfirmationReceipts.tsx` does not change,
  because it already draws `receiptText`.
- Changed: `workbench/control_plane/src/hooks/useAgentChat.ts`
  (`HITL_CONTROL_EVENTS`) and `src/components/GenerativeUIPanel.tsx`
  (`PANEL_HIDDEN_EVENTS`). Each hides `confirmation_row_results`.
- New: `tests/unit/test_sandbox_held_card.py`. Changed:
  `tests/unit/test_held_send_card_record.py`,
  `tests/unit/test_sandbox_door_client.py`,
  `tests/unit/test_confirmation_rows.py` and
  `workbench/control_plane/src/lib/confirmationReceipts.test.ts`.

#### Done when

Each item names its test. The builder sees each test fail first.

SBX-3a:

1. `Route(sandbox_write=True)` raises `ValueError` on class A or X, on a GET
   or a DELETE, with `sends=True`, or with `sandbox=True`.
   `test_sandbox_routes_coverage.py::test_a_write_row_is_class_b_or_c_and_no_delete`.
2. `SANDBOX_WRITE_ROUTES` holds exactly the 29 rows of decision 3, 16 of
   CRM and 13 of Projects. `POST /projects/tasks/bulk` is not one of them.
   The live router serves each one, and its class is the class of the
   manifest.
   `test_sandbox_routes_coverage.py::test_the_write_rows_are_the_29_of_sbx3`.
3. A direct POST to `/projects/tasks` with a live run capability that holds
   `projects:write` gets 403 `sandbox_read_only`. A spy shows that the route
   did not run. (SBX-F3: no write runs with no card.)
   `test_sandbox_door_writes.py::test_a_direct_write_never_runs`.
4. `mint` raises `ValueError` for four write scopes. They are one in the
   smoke phase, one in the build phase, `knowledge:write`, and one with no
   `app_id` or `version`.
   `test_sandbox_capability.py::test_mint_refuses_a_bad_write_scope`.
5. The batch route refuses each case below before it holds anything. The
   held store stays empty.
   - A smoke capability: 403 `sandbox_read_only`.
   - No write scope: 403 `sandbox_scope`.
   - A row off the list: 404 `sandbox_route_unknown`, with its index.
   - A class X row: 403 `sandbox_route_excluded`.
   - 26 rows, or a row of 65 KiB: 422 `sandbox_batch_cap`.
   - A DELETE row, or a fifth key in a row: 422 `sandbox_bad_batch`.
   - Any row whose body names an `agent:` assignee (create, move or set
     assignees): 422 `sandbox_bad_batch`, and no agent run starts.
   `test_sandbox_door_writes.py::test_the_batch_refuses_before_it_holds`.
6. A batch of two class B rows and one class C row writes two held records.
   Each card id is 32 hex digits.
   `test_sandbox_door_writes.py::test_class_b_shares_a_card_and_class_c_gets_its_own`.
7. With no decision, each row answers 403 `sandbox_approval_timeout` after
   the wait, and no route runs. A `decide` after the timeout runs nothing.
   (SBX-F3: a timed-out card.)
   `test_sandbox_door_writes.py::test_a_held_batch_times_out_and_lands_nothing`.
8. `decide(card_id, None)` makes each row answer 403
   `sandbox_write_refused`, and no route runs. (SBX-F3: a refused card.)
   `test_sandbox_door_writes.py::test_a_refused_card_runs_nothing`.
9. With two of three rows ticked, the two rows run in card order, and the
   third answers 403 `sandbox_write_refused`. A row whose route answers 404
   does not stop the next row.
   `test_sandbox_door_writes.py::test_the_ticked_rows_run_one_by_one`.
10. The held record and the ticket live under the prefix of the row's org.
    In the scope of org B, a read finds neither.
    `test_sandbox_door_writes.py::test_the_held_rows_are_tenant_scoped`.
11. A ticket admits one write once. A second use, a ticket of another
    capability, another body, or a ticket with no capability gets 403
    `sandbox_ticket` or 401, and no tenant stays bound. A second
    `X-Sandbox-Ticket` gets 401.
    `test_sandbox_door_identity.py::test_a_ticket_admits_one_write_once`.
12. R8, on a real database as the NOBYPASSRLS role: a capability of member
    M of org A holds `projects:write` and `crm:write`. The CRM of org A is
    native, so no Zoho call runs. A batch that `decide` approves changes a
    task and a lead. The task row and the lead row change, with M
    as the actor. The task activity holds `meta.via =
    sandbox:<slug>:v1`. Org B has no new row. A refused, a timed-out and an
    unticked row each change nothing.
    `test_sandbox_door_writes.py::test_r8_an_approved_write_lands_as_the_member`.
13. `capture_actor_via` binds `user.sandbox.stamp` for a request with a
    sandbox scope, and the header rule for each other request.
    `test_projects_actor_via.py::test_a_sandbox_write_stamps_from_the_scope`.
14. The stamp matches `ACTOR_VIA_PATTERN`. A slug that makes it too long
    gives the `app-<8 hex>` form.
    `test_sandbox_door_writes.py::test_the_stamp_form`.
15. `sandbox_approval_timeout_seconds` is 300 by default. A capability with
    100 seconds left waits 70 seconds. A capability with 20 seconds left
    holds nothing, and each row answers 403 `sandbox_approval_timeout` at
    once.
    `test_sandbox_door_writes.py::test_the_wait_ends_before_the_step`.
16. The line `sandbox.door.write` holds no body, no path value, no token and
    no hash. `test_sandbox_door_log_lines.py::test_the_write_line_holds_no_body`.
17. The branch-0 digest changes in a commit that names the review:
    `test_tenant_request_binding.py::test_branch_zero_digest_is_pinned`.

SBX-3b:

18. The card text holds only what the held record holds. A body whose value
    is an instruction shows it as a fenced value. A body key and a target id
    that hold an instruction each show fenced too. (SBX-F3: the card text
    does not come from the model.)
    `test_sandbox_held_card.py::test_the_card_text_comes_from_the_wire`.
19. A class C card starts with its counts.
    `test_sandbox_held_card.py::test_a_class_c_card_leads_with_the_counts`.
20. With no channel, the watcher sends `refused`, and nothing lands.
    (SBX-F3: a run with no chat gets no write.)
    `test_sandbox_held_card.py::test_a_run_with_no_chat_writes_nothing`.
21. The act takes the answer once. A forged row id, a second act, another
    member or another thread runs nothing (409). An act on a card whose
    held record names another `owner` or another `thread` runs nothing
    (409), for an approval and for a refusal.
    `test_sandbox_held_card.py::test_the_act_takes_the_answer_once`.
22. With `timeout_s` set, `request_confirmation` gives the outcome "timed
    out" (`CARD_TIMED_OUT`) at that time, and the watcher sends `refused`.
    A late Approve after that runs nothing. The default wait stays 3,600
    seconds. `test_sandbox_held_card.py::test_a_late_answer_runs_nothing`
    and `test_confirmation_rows.py::test_timeout_s_gives_timed_out`.
23. R8, end to end with a fake emitter: the watcher raises the card, the
    member ticks one of two rows, and only that row lands as member M of org
    A, with `meta.via` set. Org B has no new row.
    `test_sandbox_held_card.py::test_r8_the_card_runs_only_the_ticked_row`.
23b. A batch with one class B card and one class C card shows both cards.
    The member approves both, and both rows land. Each card gets its
    results. The act of the first card returns before the second card
    shows. After its decision, the first card is not open, so
    `POST /sandbox/held/card` returns the second card and never the first.
    `test_sandbox_held_card.py::test_two_cards_of_one_batch_both_show_and_land`.
24. The receipt shows a result for each row, through `receiptText`.
    `src/lib/confirmationReceipts.test.ts` (vitest), and
    `test_sandbox_held_card.py::test_the_card_gets_a_result_for_each_row`.
    The event `confirmation_row_results` carries no body:
    `test_confirmation_rows.py::test_row_results_carry_no_body`.
25. `metorite_door.batch` posts to the door name only, to the one path
    `/sandbox/v1/batch`, with a timeout of the setting plus 60 seconds. The
    client sends GET and that one POST, and nothing else.
    `test_sandbox_door_client.py::test_batch_posts_to_the_door_only` and the
    changed
    `test_sandbox_door_client.py::test_the_client_has_one_host_and_sends_get_only`.
26. Each of the three held routes refuses a browser session (401 or 404)
    and a sandbox capability (403 `sandbox_read_only`). Each answers 404 for an org that is not in
    `SANDBOX_DOOR_ORGS`. A result of another member or another run is not
    returned, and a result is returned once.
    `test_sandbox_held_card.py::test_the_held_routes_are_internal_only` and
    `test_sandbox_held_card.py::test_results_go_to_the_owner_once`.
27. A send answer is recorded for an org in `SANDBOX_DOOR_ORGS` while
    `EMAIL_HELD_CARD_SEND` is off. It is not recorded for another org. With
    `SANDBOX_DOOR_ORGS` empty, the record rule is as before.
    `test_held_send_card_record.py::test_a_sandbox_org_records_a_send_answer`.

The owner preview of a write card is part of the demo of rung 6c, after
SBX-5. It is not a test.

#### Verification commands

SBX-3a:

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_sandbox_door_writes.py tests/unit/test_sandbox_capability.py tests/unit/test_sandbox_routes_coverage.py tests/unit/test_sandbox_door_identity.py tests/unit/test_sandbox_door_log_lines.py tests/unit/test_projects_actor_via.py tests/unit/test_tenant_request_binding.py tests/unit/test_crm_ui_actions.py tests/unit/test_projects_door_coverage.py -q -rs
uv run ruff check packages/acb_auth packages/acb_common/acb_common/app_manifests apps/services/gateway/gateway/sandbox_batch.py apps/services/gateway/gateway/routes/projects/core.py
uv run mypy packages/acb_auth/acb_auth/sandbox_capabilities.py packages/acb_auth/acb_auth/deps.py apps/services/gateway/gateway/sandbox_batch.py
bash scripts/premerge.sh
```

SBX-3b:

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
timeout 900 uv run pytest tests/unit/test_sandbox_held_card.py tests/unit/test_sandbox_door_writes.py tests/unit/test_sandbox_door_client.py tests/unit/test_confirmation_rows.py tests/unit/test_held_send_card_record.py -q -rs
cd workbench/control_plane && timeout 600 npx vitest run src/lib/confirmationReceipts.test.ts && cd ../..
uv run ruff check packages/acb_skills/acb_skills/sandbox_held.py packages/acb_skills/acb_skills/ask_tools.py apps/services/gateway/gateway/sandbox_batch.py apps/services/orchestrator/sandbox/metorite_door.py
uv run mypy packages/acb_skills/acb_skills/sandbox_held.py apps/services/gateway/gateway/sandbox_batch.py
bash scripts/premerge.sh
```

A run with any skipped test fails the slice. R8 needs the database.

#### Fences

| # | Test file | What it holds in SBX-3 |
|---|---|---|
| SBX-F3 | `tests/unit/test_sandbox_door_writes.py` (new) and `tests/unit/test_sandbox_held_card.py` (new, SBX-3b) | Done when 3, 5 to 10, 12, 14, 15, 18 to 23, 23b and 26 |
| — | `tests/unit/test_held_send_card_record.py` | Done when 27 |
| — | `tests/unit/test_confirmation_rows.py` and `src/lib/confirmationReceipts.test.ts` | Done when 22 and 24 |
| SBX-F2 | `tests/unit/test_sandbox_routes_coverage.py` | Done when 1 and 2 |
| SBX-F1 | `tests/unit/test_sandbox_capability.py` | Done when 4 |
| SBX-F6 | `tests/unit/test_sandbox_door_identity.py` | Done when 11 |
| SBX-F29 | `tests/unit/test_sandbox_door_log_lines.py` | Done when 16 |
| SBX-F22 | `tests/unit/test_sandbox_door_client.py` | Done when 25 |
| — | `tests/unit/test_projects_actor_via.py` | Done when 13 |
| — | `tests/unit/test_tenant_request_binding.py` | Done when 17 |

These fences are on auth, tenancy and outward writes. So they also get
mutation testing (`engineering_practice.md` §4).

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
- 2026-11-26 — D233 (SBX-0e, task 3980). AI Studio is unpaused. PC lanes
  take rung 6, and Ishaan's lanes take rungs 1 to 5. New slices SBX-20 and
  SBX-21: the build in the same turn, with one progress line. §0d holds the
  open points of the build card. Nothing built.
- 2026-11-26 — SBX-0f (task 3996). Status refresh only: the header, five rows
  of the §0c critical path, one line anchor, the §12 rows of SBX-18 and SBX-19,
  and the INDEX row. No rule changes.
- 2026-11-26 — SBX-0g (task 4000). The owner answered the build card question
  of SBX-20 on task 3981 (answer `8c7fc3a3`). §0d, SBX-F27 and D233 record it.
- 2026-11-26 — SBX-13 built (task 3428). The door client `metorite_door` is in
  the sandbox image, read only. Fence SBX-F22. The §0c row of SBX-13 says so.
- 2026-11-26 — SBX-2 built dark (task 3801). Migration 503 adds
  `sandbox_capabilities`. The sibling of branch 0 reads `X-Sandbox-Capability`,
  and `SANDBOX_ROUTES` opens 68 GET rows. The flag `SANDBOX_DOOR_ORGS` stays
  off. The member purge deletes her capabilities. SBX-F22 now reads the real set.
- 2026-11-26 — The SBX-1 audit (task 3800). §6.1, the `search` row of §7.1,
  §7.4, the Revoke row of §8.1, the rows SBX-F7, SBX-F8, SBX-F18 and SBX-F23,
  and §12.1 follow its eleven decisions. SBX-1 splits into SBX-1a (the store,
  the path and the route) and SBX-1b (the gap view on `/studio`, task 3984).
  `user_tool_runs` moves to SBX-4. The header and the critical path of §0c
  now say that SBX-18 and SBX-19 are built dark.
- 2026-11-26 — SBX-1a built dark (task 3800). Migration 504 adds `tool_gaps`.
  `FindTools` takes the hook `on_gap`, and `orchestrator/tool_gap_path.py` is
  the no-match path. `gateway/studio/tool_gaps.py` writes and reads the rows,
  and `GET /studio/tool-gaps` serves an approver. The flag
  `user_tool_find_enabled` stays off.
- 2026-11-26 — SBX-1b built dark (task 3984). The Studio page shows a read-only
  Tool gaps section. It draws nothing while the route answers 404 or 403. A
  failed first read in a browser that never saw a 200 draws nothing too.
- 2026-11-26 — SBX-4b spec (task 3986). §12.4 specifies `build_tool`, the
  build session and the smoke test. SBX-4b1 (task 3986) is the step engine,
  and SBX-4b2 (task 4049) is the chat half. SBX-4b waits for WS-43g1 (PR
  2887). No slice takes a half with no network. The §12 table gets the rows
  SBX-4a and SBX-4b. The header and three rows of §0c name the merges of
  SBX-1a, SBX-1b, SBX-2 and SBX-13. §5.2 and §8.4 show the stamp form of
  §12.2 decision 12, which SBX-3 confirms.
- 2026-11-26 — SBX-4b audit fixes (tasks 3986 and 4049). The round opens one
  host at a time, and the owner confirms it (SBX-Q2 residual). The `tools`
  mounts use their own subfolder. The door counts a refusal by token. The
  run rows keep a deleted chat as NULL. §5.1, §5.5, §6.1, §6.2, §6.9 and the
  SBX-5 row follow §12.4.
- 2026-11-26 — SBX-4b1 parts (a), (b) and (c) built dark (task 3986, branch
  `mt-d0a57ed5-sbx4b1-abc`). The door counts each of the four refusals by
  token hash before it sends the status, and `door_counts` reads the counts.
  Migration 506 adds `user_tool_runs` with FORCE RLS, its one store and its
  member purge entry. `register_agent` refuses `user-tool` and `compute`. The
  store digest changed, and its pin waits for a named Opus review.
- 2026-11-27 — SBX-3 spec (task 3802). §12.5 specifies the writes held for a
  card. It records four owner answers. B1 and B2 are task 3802, answer
  `55b542fc`. The SBX-Q2 residual is task 3986, answer `fd97a2bd`. The
  network of each run is task 4009, answer `b5dbe733`. It decides B3 to B8.
  SBX-3 splits into SBX-3a (task 3802) and SBX-3b (task 4084). §5.2, §5.3,
  §5.5, §6.4, §8.4, §11 and §12.4 follow. The header, the §0c row and the
  rows SBX-3, SBX-4b and SBX-5 follow too. `ai_studio.md` §3.7 follows B1.
  No code.
- 2026-11-27 — SBX-3 audit fixes (tasks 3802 and 4084). `POST /projects/tasks/bulk`
  leaves the write rows, so 29 rows open. A batch refuses an `agent:`
  assignee. Only the run phase may hold a write scope. The proxy cut of 60
  seconds goes to WS-43g2 (task 4070). The card uses the `timeout_s` of
  `request_confirmation` and the one receipt seam. §12.4 names the merge of
  PR 2896 and WS-43g2. `ai_studio.md` §19.3 follows the door batch.
- 2026-11-27 — SBX-3 delta audit fixes (task 4084). The act returns after
  `decide`, and `POST /sandbox/held/results` gives the results of each card
  later. So a batch with two cards shows both. The card waits only the time
  left to the deadline of the batch. The client hides the new event.
- 2026-11-27 — WS-43g2 spec (tasks 4070 and 4092). WS-43g1 merged dark
  (`d2bffe963`, PR 2887). WS-43g2 splits in two. WS-43g2a (task 4070) is
  the network of each run and the door read timeout. WS-43g2b (task 4092)
  is the live tool and the card. SBX-3a owns `sandbox_approval_timeout_seconds`,
  and SBX-3b owns `timeout_s`. WS-43g2b uses both. §0c, §2, §4.2, §6.9, §12.4,
  §12.5 and the SBX-4b row follow. No code.
- 2026-11-27 — WS-43g2a built dark (task 4070, branch `mt-8d60758e-ws43g2a`).
  Each grant gets its own egress network, and a revoke removes only its own
  network. A full address pool refuses the grant. The proxy reads the door
  for the card wait plus 30 s. The SBX-4b rows follow. It is not merged.
- 2026-11-27 — SBX-3a built dark on its branch (task 3802). It is not
  merged. The pins of the two digests change, and the Opus review and the
  independent verification are owed. The setting
  `sandbox_approval_timeout_seconds` is in `settings.py`. The flag
  `sandbox_door_orgs` is in `flag_registry.py`, not beside it. The ticket
  functions are in `sandbox_capabilities.py`, because the door reads them.
- 2026-11-27 — SBX-3a fix (task 3802). The batch refuses an `agent:`
  assignee in any assignee field of a write row. The move row joins the
  create and the set-assignees rows, and a fence walks each body model.
- 2026-11-27 — SBX-3a review fixes (task 3802). The batch refuses a row path
  with a `.` or an empty segment. One batch is open for each capability. The
  batch refuses a row that sets `check_text`. A fence holds the rule of no database
  session for the wait. The answer of a direct write is the fixed code.
- 2026-11-27 — SBX-3a: the commit after 44d12c5cb0 names the Opus review
  and the independent verification of the two digest pins (done-when 17).
- 2026-11-27 — SBX-3a merged (`8b249bbb7`, PR 2926). The audit of SBX-3b
  (task 4084) gave GO-NARROWED with six spec defects. Decisions 7 and 8 now
  hold the fixes:
  - the answer record for an org of `SANDBOX_DOOR_ORGS`
  - the meaning of an open card, and the owner and thread check of the act
  - the results and their TTL
  - the timeout of `metorite_door.batch` and its fence
  - the internal bearer of the held routes, and a fence for code text on the card

  Done-when 26 and 27 are new.
- 2026-11-27 — SBX-3b built dark (task 4084, branch `mt-b8fbec0f-sbx3b-card`).
  It is not merged. Done-when 26 now names 403 `sandbox_read_only` for a
  capability, because the pinned gate answers first. The batch timeout of the
  door client is the constant 360 seconds.
