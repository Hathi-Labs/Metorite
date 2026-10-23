# Agent sandbox — build the tool that does not exist yet

<!-- ste-tier: strict -->

- **Status:** DRAFT, 2026-10-19. Awaiting the owner calls of §14. Nothing
  built. Not ACTIVE until the owner approves it, so nothing dispatches from
  it yet.
- **Verified against code on 2026-10-19** at `main` `daad004f9`.
- **Board row:** none yet. SBX-0 asks the owner to mint one. The work is a
  child of **WS-43** (`maf_coding_engine.md`), because it extends the one
  sandbox broker of D83.
- **Asked by:** the owner, 2026-10-18 and 2026-10-19, in chat. The owner
  asked whether the AI may write code for a function that no tool covers. The
  owner chose **"A full capable sandbox"**, and asked us to check
  `perplexityai/SPACE` for it.
- **Owner tasks:** #234 to #242 in the Metorite project. §1 maps each one to
  a section.
- **Reads:** D12, D15, D16, D56, D58, D64, D83, D85, D86, D90, D93, D97,
  D99, D100, R5 to R8.
- **Owns:**
  - tool-gap detection and the user-tool registry,
  - the run capability and the sandbox API door,
  - the build, smoke and run steps of a user tool,
  - the review controls of a stored tool,
  - the choice of sandbox backend.
- **Does not own:**
  - the broker, the image, the egress proxy and the host rules
    (`maf_coding_engine.md`),
  - the confirmation card (`projects_ai_chat.md` §5),
  - branch 0 and the route allowlists (`external_agent_access.md` §10.1),
  - credit prices (`credit_pricing.md`),
  - the box (`box_hardening.md`).

**Ids in this spec.** Every id carries the `SBX-` prefix (R2). The slices
are `SBX-0` to `SBX-11`, the fences `SBX-F1` and up, the owner gates
`SBX-G1` and up, and the owner calls `SBX-Q1` and up. The owner's approval
records each accepted call as a numbered entry in `work_plan.md` §3.

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

**Runtime.** Slice 1 runs on the broker we have, under gVisor where the box
has it, with no spend and no third party. SPACE cannot run on our boxes, and
it is 18 days old. Scale moves user code off the gateway box to hosted
microVMs with an E2B-compatible API. SPACE's E2B wrapper is the exit to a
self-hosted microVM host later, with the same SDK. §4 gives the comparison,
and §14 lists the calls that cost money.

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
| The D85 withhold | `orchestrator/_tool_injection.py:235`, `_withheld_shell_tools` | A shared agent loses all four tools unless the broker covers it. The log line is `executor.shell_tools_withheld` (`:2128`). A personal agent and the root `metorite` agent keep them | Live (D85) |
| The no-egress rule | `_tool_injection.py:2619`, `_apply_no_egress`, and `acb_skills/egress.py` | A covered run, and each run under it, holds only tools that say `open_world=False`. The log line is `executor.egress_tools_withheld`. `egress.mark_untrusted_session` marks a session that read untrusted content | Live (H-236) |
| `run_script` on the host | `acb_skills/code_tools.py:204` | `subprocess.run` in the gateway's process tree, with the network and the agent's declared credentials. Its docstring says "process-level hygiene, not a container sandbox" | Live for personal agents only |
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
2. The user-tool registry: four tenant tables and one lookup table (#235).
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

### 3.2 Non-goals

- **A second sandbox, a second proxy or a second confirmation card.** Each
  one exists. This spec extends them.
- **A provider token in the sandbox.** The code reaches Zoho, Gmail and
  Microsoft Graph only through the Metorite routes that front them (§5.4).
- **Code that runs with no person to ask.** A workflow or a background job
  cannot build a tool, and cannot pass a write card. A stored tool may run
  there only for reads, after SBX-Q5.
- **A tool marketplace across organizations.** A tool never leaves its
  organization.
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

**Where it runs first.** Staging, then Fracktal on production (ring 0,
`engineering_practice.md` §2). Each step is an owner act (§13).

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

### 6.1 Gap detection and reuse (#234, #240)

**The rule.** The assistant may build a tool only when two things hold. One
`find_tool` call in the same run answered `no_match`. The member approved
the build card.

**`find_tool(need)`.** A new platform tool on the general agent
(`orchestrator`, D99). It runs four checks in this order:

1. **The registry.** A lexical search over the `name` and `description` of
   the tools the member can see (§7), with `status = 'active'`. The search
   uses Postgres full text on one GIN index. At most 5 candidates.
2. **The run's own tools.** The names and descriptions of the tools in the
   run's scope after injection, and the skills catalog of WS-23.
3. **One typed question** on `tier-decide` (D93, D100): "Can one of these
   tools do this need? Answer with its name, `none` or `unsure`."
4. **The record.** One `tool_gaps` row with the candidates, the answer and
   the outcome.

| Answer | What `find_tool` returns | What the assistant does |
|---|---|---|
| A platform tool | `existing`, with its name | Uses that tool |
| A stored tool | `reuse`, with its id and version | Calls `run_tool` (§6.4) |
| `unsure` | The candidates, and no gap id | Tries a candidate first, or asks the member |
| `none` | `no_match`, with a gap id | May offer a build |

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

### 6.3 Build and smoke test (#237)

**The build** is a MAF harness session, the same as `code_task` on MAF
(`maf_coding_engine.md` §7.6). It gets `run_command`, the file tools over the
run-data dir, and the `build` profile. It must write one package in the MAF
skill format, so the existing `SkillsProvider` can load it:

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

Then, at the end of that run, the gateway writes the `user_tools` row and
its first `user_tool_versions` row in one transaction.

**Before that, nothing is kept.** Until then, the package lives only in the
run-data dir, which the host deletes at the end of the run
(`maf_coding_engine.md` §16.3). So a failed build leaves no row, no file and
no blob. Only its `tool_gaps` row and its `user_tool_runs` rows stay.

**The member has the last word.** The result card shows "This is wrong" for
24 hours. If the member presses it, the gateway deletes the tool, while it
still has one version and no run by another member. The gap row then reads
`build_failed`.

A new version follows the same rule. The member asks for a change, the build
makes a new package, and the new version row lands only after its first real
run succeeds.

### 6.6 Reuse

- `find_tool` puts stored tools first (§6.1 step 1).
- The assistant's system text lists at most 20 of the member's visible tools,
  by last use, with one line each. It does not inject each tool as a
  function, because each function costs context on every turn (WS-23).
- `run_tool` reads the version to run from the pin rule of §8.2.

---

## 7. The registry schema (#235)

Five new tables. Each tenant table follows R5a: `organization_id`, the
generated RLS migration, and `tests/unit/test_tenant_coverage.py`. The
migration is expand-only (R6): new tables, nullable columns or columns with
defaults, and no change to an existing table. Its number is the next free
number at build time (R1). Every query runs against a real Postgres as the
NOBYPASSRLS app role before it is believed (R8).

### 7.1 `user_tools` — one row for each stored tool

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key, `gen_random_uuid()` |
| `organization_id` | `uuid` | Not null. RLS |
| `slug` | `text` | Not null. Unique per organization |
| `name` | `text` | Not null, at most 80 characters |
| `description` | `text` | Not null, at most 500 characters |
| `owner_email` | `text` | Not null. The member who built it |
| `visibility` | `text` | Not null, default `'private'`. `'private'` or `'org'` (D12) |
| `status` | `text` | Not null, default `'active'`. `'active'`, `'disabled'` or `'revoked'` |
| `latest_version` | `int` | Not null |
| `org_version` | `int` | Null. The version that other members run. Set only by an approval |
| `approved_by` | `text` | Null. Who approved `org_version` |
| `approved_at` | `timestamptz` | Null |
| `disabled_by` | `text` | Null |
| `disabled_at` | `timestamptz` | Null |
| `disabled_reason` | `text` | Null, at most 500 characters |
| `last_run_at` | `timestamptz` | Null |
| `search` | `tsvector` | Generated from `name` and `description`. GIN index |
| `created_at`, `updated_at` | `timestamptz` | Not null, default `now()` |

A Center scope and `group:<slug>` grants (D12) come later, when the owner
asks. Version 1 has private and org only.

### 7.2 `user_tool_versions` — one immutable row for each version

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS |
| `tool_id` | `uuid` | Not null. References `user_tools(id)` with `ON DELETE CASCADE` |
| `version` | `int` | Not null. Unique with `tool_id` |
| `files` | `jsonb` | Not null. Path to text, the five files of §6.3. At most 256 KB in all |
| `files_sha256` | `text` | Not null |
| `input_schema`, `output_schema` | `jsonb` | Not null |
| `read_scopes`, `write_scopes` | `text[]` | Not null, default empty |
| `image_ref` | `text` | Not null. The sandbox image digest of the build |
| `built_by_email` | `text` | Not null |
| `built_in_thread` | `text` | Null |
| `smoke_passed_at` | `timestamptz` | Not null |
| `first_run_id` | `uuid` | Not null. The run that made it persist |
| `created_at` | `timestamptz` | Not null |

A version row never changes. The gateway issues no `UPDATE` on this table,
and the app role holds no `UPDATE` grant on it (SBX-F10). A delete happens
only through the cascade of §6.5. The `tool_id` of a `user_tool_runs` row has
no foreign key, so the run record stays after that delete.

### 7.3 `user_tool_runs` — one row for each step

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` | Primary key |
| `organization_id` | `uuid` | Not null. RLS |
| `tool_id`, `version` | `uuid`, `int` | Null for a build that did not persist |
| `gap_id` | `uuid` | Null |
| `member_email`, `thread_id`, `run_id` | `text` | The member, the chat thread, the agent run |
| `phase` | `text` | `'build'`, `'smoke'` or `'run'` |
| `backend` | `text` | `'docker-runc'`, `'docker-runsc'` or `'e2b'` |
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
| `answer` | `text` | `'tool'`, `'none'` or `'unsure'` |
| `outcome` | `text` | `'existing'`, `'reuse'`, `'no_match'`, `'declined'`, `'built'` or `'build_failed'` |
| `tool_id` | `uuid` | Null |
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
| `tool_id`, `version` | `uuid`, `int` | Null during a build |
| `phase` | `text` | Not null |
| `read_scopes`, `write_scopes` | `text[]` | Not null |
| `expires_at` | `timestamptz` | Not null |
| `revoked_at` | `timestamptz` | Null |

A sweep deletes rows 1 day after `expires_at`. The table never holds the
token itself.

---

## 8. Review controls (#241)

### 8.1 Who can do what

| Act | Who | Card or check |
|---|---|---|
| Build a tool | Any member, when the scope names the org | The build card (§6.1) |
| Run a private tool | Its owner | Write cards (§5.3) |
| Share a tool with the org | Its owner asks, and an approver approves | The approver sees the files, the scopes and the last 5 runs |
| Run an org tool | Any member of the org | That member's own access and cards. A shared tool shares code, never authority |
| Disable a tool | Its owner, or an approver | Immediate. Running steps finish, and no new step starts |
| Revoke a tool | An approver | As disable, and `find_tool` never returns it again |

**The approver.** A new permission, `tools:approve`, in the catalog of
`org_access_control.md`. The admin role holds it by default. SBX-6 checks
the name against the naming rule of `feature_catalog` (for example
`admin:access:manage`) at dispatch. Whether stored
tools may be shared org-wide at all is the owner's call (SBX-Q3). Until the
owner answers, every tool stays private, and the share button is not built.

### 8.2 Versions and pins

- A version is immutable (§7.2).
- The owner runs `latest_version`. Every other member runs `org_version`.
- A new version never changes `org_version`. An approver must approve it.
- An approver may pin `org_version` back to any earlier version.
- `run_tool` refuses a version that the caller may not run, and a tool that
  is not `active`.

### 8.3 Kill switches

- `MAF_CODING_SCOPE` without `tools:` stops every build and every run.
- `user_tool_runs_enabled` is an incident switch, default true. Set to
  false, it stops runs of stored tools and keeps builds. The scope entry
  keeps the whole feature dark, so this default changes nothing on its own.
- An approver disables one tool in place.

### 8.4 Audit

Each share, approval, pin, disable and revoke writes one `audit_event` row
through `acb_audit.record`. The row holds the actor, the tool, the version
and the reason. Each write that a tool makes carries the stamp
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
| SBX-F9 | `tests/unit/test_user_tool_registry.py` | R8, on a real database as the NOBYPASSRLS role. A tool persists after a failed smoke test or a failed first run. A failed build leaves a file or a blob. RLS shows a tool to another org. A private tool shows to another member |
| SBX-F10 | `tests/unit/test_user_tool_versions_immutable.py` | Any code issues `UPDATE` on `user_tool_versions`, or the app role holds that grant |
| SBX-F11 | WS43-F1, `tests/unit/test_sandbox_broker_seam.py`, extended | A module outside `sandbox_broker.py` imports `e2b` or starts a `docker` process, or the `tools` target falls back to the host |
| SBX-F12 | `tests/unit/test_user_tool_build.py` | A build package lacks a file of §6.3, a lock line lacks a hash, the smoke test runs more than 3 fix rounds, or a build passes the smoke test with a call outside its scopes |
| SBX-F13 | `tests/unit/test_user_tool_review.py` | A member who is not the owner runs `latest_version`, a new version changes `org_version`, a member without `tools:approve` approves or revokes, or a disabled tool starts a step |
| SBX-F14 | `tests/unit/test_user_tool_audit.py` | A share, approval, pin, disable or revoke writes no audit row |
| SBX-F15 | `tests/unit/test_sandbox_log_lines.py` | A log line of §9.1 holds input data, output data, a need text or a token, or the operator view returns a `need` text |
| SBX-F16 | `tests/unit/test_sandbox_metering.py` | A step ends with no usage event, or a step starts for an org at its hard cap |

**Where the Docker halves run.** On `.github/workflows/sandbox-docker.yml`,
with the `sandbox_docker` marker, as WS43-F4 does. A skip fails that
workflow.

---

## 12. Slices

Each slice is one PR, ships dark, and ends at the next free migration number
at build time (R1). The `tools` target stays out of `MAF_CODING_SCOPE` on
every box until SBX-9.

| Slice | Work | Depends on | Gate |
|---|---|---|---|
| **SBX-0** | The owner answers §14, and mints a board row | Nothing | **OWNER-GATE** |
| **SBX-1** | The registry tables (§7), `find_tool` with the gap record (§6.1), the log lines of §9.1 for gaps, and the gap view for an approver. No code runs anywhere | SBX-0 for the board row only | AGENT-SAFE. Dark |
| **SBX-2** | The run capability (§5.1), the door with reads only (§5.2), `SANDBOX_ROUTES` and its coverage fence | SBX-1, SBX-Q6 | AGENT-SAFE. Dark |
| **SBX-3** | Writes held for a card (§5.3), the batch card, `meta.via` | SBX-2 | AGENT-SAFE. Dark |
| **SBX-4** | The `tools` target, `build_tool`, the build session and the smoke test (§6.2, §6.3), on the Docker backend | SBX-2, WS-43g for the proxy | AGENT-SAFE. Dark |
| **SBX-5** | `run_tool`, persist on success, reuse (§6.4 to §6.6) | SBX-3, SBX-4 | AGENT-SAFE. Dark |
| **SBX-6** | Review controls: share, approve, pin, disable, revoke, audit (§8) | SBX-5, SBX-Q3 | AGENT-SAFE. Dark |
| **SBX-7** | Metering (§10) | SBX-5, SBX-Q4 | AGENT-SAFE. Dark |
| **SBX-8** | `sandbox_runtime=runsc` in the broker, and a measured cost of gVisor on a local stack | SBX-4 | AGENT-SAFE. The install on a box is WS43-G5, **OWNER-GATE** |
| **SBX-9** | Turn it on: staging first, then `tools:<Fracktal>` on production | SBX-1 to SBX-8, WS43-G1, WS43-G2, WS43-G4, WS43-G5 | **OWNER-GATE** |
| **SBX-10** | The `e2b` backend (§4.4) and the public door name | SBX-5, SBX-Q1 | AGENT-SAFE to build on a free test account. The account, the key and the DNS record are **OWNER-GATE** |
| **SBX-11** | The SPACE check through ECW (§4.5) | SBX-10, the three triggers of §4.5 | **OWNER-GATE**. A new host costs money |

### 12.1 SBX-1 — the first dispatchable slice

**Scope.** The tables of §7.1 to §7.4, with the generated RLS. `find_tool`
on the general agent, with checks 1 to 4 of §6.1. The `sandbox.tool.gap` log
line. A read route for an approver's gap view. No container starts, no
capability exists, and no code runs.

**Why first.** It answers #234, #235 and the counter half of #242. It costs
nothing, needs no owner act after the board row, and gives the team real gap
counts before anyone spends on a runtime.

**The flag.** `user_tool_find_enabled`, default off. Off, the general agent
holds no `find_tool`.

**Files (re-verify at dispatch).**

- New: `apps/services/gateway/gateway/routes/user_tools.py`, the gap view
  route.
- New: `packages/acb_skills/acb_skills/user_tool_tools.py`, `find_tool`.
- Changed: `apps/services/orchestrator/orchestrator/_tool_injection.py`, to
  give `find_tool` to the general agent when the flag is on.
- Changed: `packages/acb_common/acb_common/settings.py`, the flag.
- New: the migration, at the next free number at build time.
- New: the fences SBX-F7 (its `find_tool` half), SBX-F8 (its record half),
  SBX-F9 (its RLS half) and SBX-F15 (its gap half).

**Done when.**

1. The migration applies on a fresh dev database and on a copy of the
   current schema, and `test_tenant_coverage.py` passes.
2. As the NOBYPASSRLS role, org A cannot read a `tool_gaps` row of org B.
3. With the flag off, no agent holds `find_tool`.
4. With the flag on, `find_tool` writes one `tool_gaps` row for each call,
   with `answer` and `outcome` set.
5. A stored tool that matches the need comes back as `reuse` (a fixture row).
6. The log line holds no `need` text.

**Verification commands.**

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_tool_gap_detection.py tests/unit/test_user_tool_registry.py tests/unit/test_user_tool_injection.py tests/unit/test_sandbox_log_lines.py tests/unit/test_tenant_coverage.py -q -rs
```

A skip in that run is a failure. R8 needs the database.

---

## 13. Owner gates

An agent refuses each of these by name. `maf_coding_engine.md` §12 says the
dev-phase window does not open the WS-43 gates, and the same holds here.

| # | Act |
|---|---|
| SBX-G1 | Add `tools:<org>` to `MAF_CODING_SCOPE` on staging or production (it is WS43-G3 for this target) |
| SBX-G2 | Install `runsc` on a box (WS43-G5) |
| SBX-G3 | Open an account with a sandbox vendor, or put its key on a box |
| SBX-G4 | Create the DNS record `sandbox-api.metorite.com`, or its Caddy block |
| SBX-G5 | Buy or rent a host for SPACE |
| SBX-G6 | Set a credit price for sandbox time |
| SBX-G7 | Turn on org sharing of tools |
| SBX-G8 | Set the `web:` profile policy on production, or the production package-host list (WS43-G4) |

**Production stays on `runsc`.** SBX-9 turns on the `tools` target on
production only on a box where `sandbox_runtime=runsc` works. On `runc`, the
target stays off there.

---

## 14. Owner calls

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
