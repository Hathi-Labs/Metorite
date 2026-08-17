# B6 — Permissions & Sandboxing (HH-6)

> **Status: verified against code on 2026-08-03** (truth pass, WS-3). Near-term
> risk-aware permission handler **shipped 2026-07-03** and still wired at all five
> executor sites. Phase 5 (isolation): **P5-a shipped** (per-run credential scoping,
> 2026-07-04) · **P5-b partially shipped** (container cap/resource ceilings landed
> 2026-07-27; egress + read-only rootfs unbuilt) · **P5-c (T2) parked as a
> deprioritised sub-project** under the internal-tool threat model (owner decision
> 2026-08-03) · **P5-d not started.** The two dispatchable slices are **WS-3a**
> (§P5-a.2) and **WS-3b** (§P5-b.2). Board row: `work_plan.md` §2 WS-3.
> *[Update 2026-08-09 — D16: the parking survives, the trigger changed. P5-c/T2
> is now a precondition of the pooled cutover (`saas_multitenancy.md` §5.1,
> MT-0c-2); the internal-tool premise expires with the first external tenant.
> Acceptance remains unwritten by design until the owner un-parks.]*
> **Module:** B6 (core_module_map.md).
>
> **Isolation ladder (R2).** This doc's Phase-5 build order is lettered **P5-a/b/c/d**.
> The **isolation-strength ladder is T0/T1/T2**, defined once in
> [`agent_platform_hardening_2026-07.md`](agent_platform_hardening_2026-07.md) §1.2 and
> implemented in `AgentManifest.isolation_tier()`. Until 2026-08-03 this doc used
> "Tier 0/1/2/3" for its build order while the hardening doc used "T0/T1/T2" for
> isolation strength — two incompatible ladders inside one board cell. They are not the
> same thing and the numbers never lined up: this doc's "Tier 2" (generalise the
> container to live runs) is the hardening doc's **T2**, but this doc's "Tier 1" (harden
> the mutation container) has **no T-equivalent at all** — it hardens an existing
> container rather than choosing a tier for a run. Say **T0/T1/T2** when you mean
> isolation strength; say **P5-a…d** when you mean this doc's build order.
> **Scope of THIS pass:** replace the blanket `PermissionHandler.approve_all`
> with a **risk-aware allowlist handler** that gates shell / file-write /
> network / tool operations using the SDK's own request classification + our
> `tool_annotations` risk vocabulary. **Out of scope (Phase 5):** container
> isolation for normal runs — the in-process `importlib` execution model stays;
> that's a much larger infra change tracked separately.

## The gap (audited 2026-07-03 — **closed**; kept as the record of what was wrong)

> **Anchor refresh 2026-08-03.** The five sites are now
> `executor.py:632, 2483, 3011, 3909, 4442` and every one of them installs
> `_copilot_permission_handler()`, not `_PH.approve_all`. The paragraph below
> describes the **pre-2026-07-03** state.

- **Copilot-SDK agents ran with `PermissionHandler.approve_all`** — set at FIVE
  sites in `executor.py` (then `~1190, ~2572, ~3023, ~3796, ~4297`), always as
  `if agent._permission_handler is None: agent._permission_handler = _PH.approve_all`.
  `approve_all` returns `PermissionRequestResult(kind="approved")` for EVERY
  request: every shell command, file write, and network fetch the model decides
  to run is auto-approved with no policy. This is the OWASP "excessive agency"
  exposure the module map flags.
- The risk vocabulary exists (`tool_annotations.py`: `read_only` /
  `destructive` / `idempotent` / `open_world` per tool) but **nothing gates on
  it** — it only feeds the prompt addendum and the opt-in `request_confirmation`
  gate (which individual destructive TOOLS call themselves; HH-2). There is no
  gate on the tool *call* itself, and none at all on raw shell/file/network ops.
- The SDK hands the handler a rich `PermissionRequest`: `kind`,
  `commands`/`full_command_text` (shell), `has_write_file_redirection`,
  `path`/`new_file_contents` (file write), `url`/`possible_urls` (network),
  `read_only`, `tool_name`, `warning`. **We were throwing all of that away.**

## Design — a risk-aware permission handler

New `acb_skills/permission_policy.py::risk_aware_permission_handler` — a drop-in
replacement for `approve_all` with the same `(request, invocation) ->
PermissionRequestResult` signature, so it swaps in at all five sites with a
one-line change each. It decides from the request + policy:

| Request shape | Default decision | Why |
|---|---|---|
| `read_only` true, or a `tool_name` annotated `read_only` | **approve** | observation only — safe to call freely |
| named `tool_name`, annotated non-destructive (write/idempotent) | **approve** | reversible platform writes (write_artifact, save_memory…) |
| named `tool_name`, annotated `destructive` | **approve** (defer) | the destructive tool ALREADY self-gates via `request_confirmation` (fail-closed, HH-2) — the handler must NOT double-gate or it deadlocks the confirmation card |
| shell command (`commands`/`full_command_text`) | **policy** | approve unless it matches a dangerous-command denylist (rm -rf /, mkfs, dd to a device, curl|sh, fork bombs, shutdown) |
| file write (`has_write_file_redirection`/`new_file_contents`) with a path OUTSIDE the agent workspace | **deny** | writing outside `repos/{agent}` is out-of-bounds; in-workspace writes approve |
| network (`url`/`possible_urls`) | **approve** | open_world is expected for agents; blocking web breaks normal use. Logged for audit (exfil visibility), not blocked |
| unknown / unclassifiable | **approve + WARN-log** | fail *open* but *loud* — this is the near-term slice; a stricter default-deny is a follow-up once we've observed what real runs request. Every decision is logged with the run correlation (E2) so we can tighten from data |

**Fail-open-but-logged, not fail-closed, for unknowns** — deliberately. A hard
default-deny on an in-process model that already runs arbitrary agent code would
break far more than it protects and give false assurance; the honest near-term
win is (a) kill the truly-dangerous shell/out-of-workspace-write cases, (b)
make every privileged op *observable* (logged + attributable via E2), so the
Phase-5 container work and any tightening is driven by real data, not guesses.
The dangerous-shell denylist + out-of-workspace-write denial ARE fail-closed.

Config: env `AGENT_PERMISSION_MODE` = `enforce` (default — apply the policy) |
`audit` (log the decision the policy WOULD make, but always approve — a safe
rollout mode to see what would be denied before turning it on) | `approve_all`
(the old behaviour, escape hatch). Denylist patterns overridable via env.

The handler consults `tool_annotations.get_annotations` for named tools and the
workspace root from `write_artifact._WRITE_ARTIFACT_CONTEXT` for the
file-write-scope check (the same plain-dict context the tools already use).

> **Gate labels (added 2026-08-03, contract point 7).** The handler, its policy
> table, and its wiring are **AGENT-SAFE** and shipped. Moving
> `AGENT_PERMISSION_MODE` off `audit` to the enforcing mode is **OWNER-GATE**
> (`work_plan.md` §6) — an agent must refuse it and say so. Note the honest
> discrepancy: the code's *default* is the enforcing mode, but **prod is pinned
> to `audit`** (see the 2026-07-03 production-verification entry below), so
> reading this section's "default" as the live posture is wrong.

## Wiring
Replace `_PH.approve_all` at all five executor sites with our handler (guarded:
if `AGENT_PERMISSION_MODE=approve_all`, keep `_PH.approve_all`). Handler lives
in `acb_skills` (importable by the executor; no new dep). Native-MAF tool calls
already flow through `_make_tool_shim` — a lighter tool-name allowlist check
goes there too (belt-and-suspenders for the non-Copilot path), but the primary
win is the Copilot `PermissionRequest` handler because that's where raw
shell/file/network requests surface.

## Tests
- Unit (`tests/unit/test_permission_policy.py`): read-only approve; annotated
  reversible approve; destructive approve (defers to request_confirmation);
  dangerous shell (rm -rf /) deny; benign shell approve; out-of-workspace write
  deny, in-workspace write approve; network approve+logged; unknown
  approve+warn; `audit` mode always approves but logs the would-be decision;
  `approve_all` mode bypasses.
- Trajectory (`evals/trajectories/test_permission_trajectory.py`): the policy
  decision table is locked as the contract.

## Status

> **A third "Tier" lives in this section (R2 warning).** The 2026-07-03 entry
> below says "Native-MAF **Tier-2** `_make_tool_shim`" and "Native-MAF **Tier-1**
> streaming". Those are the **MAF runtime tiers** (which execution path a run
> takes), not isolation strength (`T0/T1/T2`) and not this doc's build order
> (`P5-a…d`). Three unrelated ladders share the word. Left as-is because the
> entry is a historical record, but do not read them across.

- 2026-07-03 — Design from the B6/HH-6 audit. Building the handler + wiring.
- 2026-07-03 — **Shipped.** `acb_skills/permission_policy.py`
  (`decide` pure fn + `risk_aware_permission_handler`). Wired into all FIVE
  Copilot `_permission_handler` sites in `executor.py` via a
  `_copilot_permission_handler()` helper (mode-guarded: `approve_all` mode keeps
  the SDK's blanket handler). Native-MAF Tier-2 `_make_tool_shim` also gates +
  logs by tool name. 30 unit tests + 4 trajectory (decision-table contract);
  full suite 701 green, zero regressions. Recon confirmed the native-MAF
  **Tier-1 streaming** path has NO tool choke point (`agent.run(stream=True)`
  calls tools directly) — gating it needs wrapping callables at injection in
  `_inject_agent_tools`; deferred as a follow-up (the Copilot handler is where
  raw shell/file/network surface, so it's the primary win). Container isolation
  for normal runs stays the Phase-5 item.
- 2026-07-03 — **Production verification (live VPS) + 3 follow-up fixes.**
  Ran `scripts/feature_check.py` against the live gateway (4/4 PASS) and drove
  real tool-calling runs. Key discovery: the initial wiring did NOT actually
  gate the primary live tool path. On the **Copilot-MAF-BYOK** path (the common
  runtime), platform tools (`web_search`, …) and the agent's OWN repo-baked
  tools are executed as **agent-framework function-tools**, NOT through the
  Copilot SDK's `on_permission_request` hook (that fires only for the SDK's
  built-in shell/file/fetch) — so the 5-site handler never saw them. Fixes:
  (1) `_gate_injected_tool` wraps every tool we inject; (2) `_inject_agent_tools`
  RE-WRAPS the agent's existing `_tools[*].func` too (repo-baked tools land there
  via the `GitHubCopilotAgent(tools=…)` ctor → `self._tools = normalize_tools`);
  (3) the gate logs EVERY decision (approve + deny), because it only logged
  denials before — which made a silent approval indistinguishable from "gate
  never ran" and blinded audit mode. **Verified live:** a `web_search` run now
  emits `permission.decision {tool:web_search, approved:true,
  reason:tool_read_only, surface:injected_tool, mode:audit}`. Also fixed E2
  `duration_ms` (was null — now derived from event stream-ids; live run shows
  ~9s) and set `LOG_FORMAT=json` on the VPS (logs are now JSON, run-correlated).
  Prod is in `AGENT_PERMISSION_MODE=audit` (log-only) pending review of the
  decision stream before flipping to `enforce`.

---

# B6 Phase 5 — Isolation for normal agent runs

> **Status: verified against code on 2026-08-03.** P5-a shipped · P5-b partly
> shipped · P5-c parked · P5-d not started. See §"What actually shipped" below —
> that table, not the prose, is the state of record. The near-term permission
> handler above is the *policy* layer inside the process; Phase 5 adds the
> *boundary*.

## What actually shipped (verified against code on 2026-08-03)

The prose in this section was written 2026-07-04 and went 30 days without a
reconciliation pass while four separate things landed. Everything below was
re-checked against the tree at `2ccff9e0` before being written here.

| Slice | State (2026-08-03) | Evidence |
|---|---|---|
| **P5-a — per-run credential scoping** (was "Tier 0") | ✅ **SHIPPED** | `_inject_integrations_to_env` now returns a **restore token** and `_restore_integration_env` tears it down at run end — `executor.py:4340-4389` (fn) / `:4392` (restore). Called + restored on all three run paths: `_run_sub_agent_streaming` (`:599` / `:843`), `run_agent_stream` (`:2335` / `:4053`), `_run_with_maf_agent` (`:4516`). Pinned by `tests/unit/test_integration_env_scoping.py` + `evals/trajectories/test_integration_env_scoping_trajectory.py` |
| **P5-b — container resource + capability ceilings** (was part of "Tier 1") | ✅ **SHIPPED 2026-07-27** | `mutation.py:700-722` and `copilot_sandbox.py:153-171` both pass `--cap-drop ALL`, `--cap-add DAC_OVERRIDE`, `--security-opt no-new-privileges`, `--memory`, `--cpus`, `--pids-limit`, all settings-overridable. Pinned by `tests/unit/test_mutation_sandbox_hardening.py` and `tests/unit/test_copilot_sandbox.py` |
| **P5-b — egress + read-only rootfs** | 🔲 **UNBUILT** | Neither `docker run` passes `--network`, `--read-only`, or any allowlist. This is **WS-3b** (§P5-b.2) |
| **P5-b — scoped gateway key for the sandbox** | 🔲 **unbuilt and undesigned** | `mutation.py:700-722` still passes `GATEWAY_API_KEY` straight through. TTL, issuance and revocation are all unanswered — **OWNER-GATE** (see §P5-b.3) |
| **Copilot-CLI containerization** (T2-*shaped*, but not T2) | ✅ **SHIPPED, wired at 2 call sites, ships OFF** | `orchestrator/copilot_sandbox.py` + `Dockerfile.copilot-sandbox`; call sites `code_session.py:109-120` (`code_task`) and `executor.py:1070-1080` (`_maybe_sandbox_session_workspace`, App Workshop app-builder). Gated on `settings.copilot_sandbox_scope` (`acb_common/settings.py:222`, default `""`), hard fallback to in-process on any spawn failure. It containerizes the **`copilot` CLI binary**, not the agent run — the host still owns orchestration, tools and permissions, so it is not T2 |
| **`isolation_tier()` derivation** | ⚠️ **SHIPPED AS A LOG LINE ONLY** | `manifest.py:273-287` computes T0/T1/T2 from the resolved surface; pinned by `tests/unit/test_agent_manifest.py:224-252`. Its only non-test consumer is `declarative.py:210`'s `_log.info("declarative.agent_built", …, tier=…)`, plus a registration warning at `manifest.py:370-374`. **Computed and thrown away** |
| **Tier record on `agent_run` + T2-run refusal** | 🔲 **UNBUILT** | No `tier` column exists — checked `infra/postgres/`; highest migration on disk is 142. Nothing refuses a run. This is **WS-3a** (§P5-a.2) |
| **P5-c (T2 proper)** | 🔲 **untouched, and now parked** | `loader.py:1300`'s in-process `spec.loader.exec_module` is what `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO‑7 and `competitive_hardening_2026-07.md` CH‑1 actually name, and neither 2026-07-27 pass touched it. See §P5-c |

**Cross-doc pointer (contract point 6).** The **build record** for the 2026-07-27 work is
not in this file — it lives in
[`competitive_hardening_2026-07.md`](competitive_hardening_2026-07.md)`:119-141` (the
`2026-07-27 — BO-7 progress, in two passes` log entry). That is a fourth doc describing
this board cell, alongside this spec, `agent_platform_hardening_2026-07.md` Part 1, and
`FOUNDATION_BUILDOUT_CHECKLIST.md` §BO‑7. **This spec is the owner**; the other three
should link here and add nothing. Recommended `work_plan.md` §4 row:
*"Isolation ladder (BO-7 / HH-6 / T0–T2) → owner **`permissions_sandbox_b6.md`**;
mirrors: hardening Part 1 (ladder definition only) · checklist §BO‑7 · competitive CH-1
(build log)."*

## The exposure, precisely (audited 2026-07-04 — **reconciled against code 2026-08-03**)

Everything about a normal agent run executes **in the single gateway/orchestrator
interpreter**, and that interpreter's `os.environ` holds **every decrypted
integration secret**. Concretely, from the recon:

1. **Shared ambient credentials — ✅ CLOSED by P5-a (2026-07-04).**
   `executor._inject_integrations_to_env` (**`executor.py:4340`** — the old
   `:4509` anchor is stale) exports this run's resolved credentials
   (`ZOHO_REFRESH_TOKEN`, `CLICKUP_API_TOKEN`, `SMTP_PASSWORD`,
   `APIFY_API_TOKEN`, `INSTANTLY_API_KEY`, the Gmail/Sheets SA-json paths, …)
   into `os.environ`. It is called on all three run paths — **`:599` sub-agent,
   `:2335` streaming, `:4516` batch** (the old `:1419 / :2769 / :4655` anchors
   are stale) — and it now **returns a restore token** that
   `_restore_integration_env` (`executor.py:4392`) consumes at teardown
   (`:843`, `:4053`). The pre-2026-07-04 behaviour, described below as the
   exposure, was write-once-never-clear: creds **accumulated globally** for the
   process lifetime and any agent could read any other integration's secret with
   `os.getenv(...)`. **Residual, unchanged and honest:** `os.environ` is
   process-global, so *concurrent* in-process runs still share the env for the
   overlap window. Only a real per-run boundary (P5-c) closes that.
2. **Arbitrary code in-process — open.** `loader._import_module_file`
   (**`loader.py:1287`**, the `exec_module` call at **`loader.py:1300`**; the old
   `:1240-1247` anchor is stale) `exec_module`s the agent repo's `agents.py` in
   the gateway interpreter; imported modules persist process-wide (cleanup only
   pops the run module + sys.path entries). **This is the line `FOUNDATION_BUILDOUT_CHECKLIST.md`
   §BO‑7 and CH‑1 actually name, and nothing has touched it.**
3. **Shared venv — open, but the install-time RCE is closed.**
   `_install_agent_deps` (**`loader.py:1137`**; the old `:1095` anchor is stale)
   and the runtime `install_dependency` tool (`dep_tools.py:79`) both
   `uv pip install --python sys.executable` — into the gateway's own
   interpreter. One agent's deps can still shadow/break another agent's or the
   gateway's. **What the 2026-07-04 text did not know:** since 2026-07-27
   `_install_agent_deps` defaults to **`--only-binary=:all:`**
   (`loader.py:1213-1215`, wheels only, escape hatch
   `settings.agent_deps_allow_source_builds`), which closes the
   arbitrary-code-at-install-time gap that ran *ahead* of any tool-call gate.
   `dep_tools.py:79` does **not** carry that guard — noted, not scoped here.
4. **Resource/capability ceilings — ✅ CLOSED 2026-07-27. Egress + rootfs still open.**
   > ⚠️ **The sentence that used to sit here was false and dangerous.** It read:
   > *"even the mutation container … runs with **zero** `--memory`/`--cpus`/
   > `--pids-limit`/`--network`/`--cap-drop`/`--read-only` flags (grep-confirmed)."*
   > That has been untrue since **2026-07-27**. An implementer trusting it would
   > most likely have re-added `--cap-drop ALL` to `mutation.py`, duplicating a
   > flag that is already there. **Struck.**

   Actual state, verified 2026-08-03. Both containers carry four of the six
   flags, settings-overridable:

   | Flag | `mutation.py:700-722` | `copilot_sandbox.py:153-171` |
   |---|---|---|
   | `--cap-drop ALL` | ✅ | ✅ |
   | `--cap-add DAC_OVERRIDE` | ✅ (root-in-container vs. host-owned bind mount) | ✅ (same reason) |
   | `--security-opt no-new-privileges` | ✅ | ✅ |
   | `--memory` | ✅ `settings.mutation_memory_limit`, default `2g` | ✅ `settings.copilot_sandbox_memory_limit`, default `768m` |
   | `--cpus` | ✅ `mutation_cpu_limit`, default `2` | ✅ `copilot_sandbox_cpu_limit`, default `1` |
   | `--pids-limit` | ✅ `mutation_pids_limit`, default `512` | ✅ `copilot_sandbox_pids_limit`, default `256` |
   | `--network` | 🔲 **absent** — default bridge, unrestricted egress | 🔲 **absent** |
   | `--read-only` | 🔲 **absent** — writable rootfs | 🔲 **absent** |

   The two remaining gaps are **WS-3b** (§P5-b.2). Note the constraint any
   `--network` work must respect: `mutation.py:716` passes
   `--add-host host.docker.internal:host-gateway` because the sandbox reaches the
   gateway `/v1` over the host, and `copilot_sandbox.py:163` publishes
   `-p 127.0.0.1::<port>` because the host drives the CLI over loopback TCP.
   `--network none` breaks both; the posture has to be an allowlist, not a cut.

The one clean seam: the **model call already goes over loopback HTTP** to the
gateway `/v1` (native MAF `OpenAIChatCompletionClient(base_url=…/v1)`;
Copilot-SDK BYOK force-routed to the same). So a sandbox doesn't need the
provider keys — it needs egress to the gateway `/v1` with a **scoped** key.

## Why not "container-per-run" as the first move

The obvious SOTA answer — run each agent in a `Dockerfile.mutation`-style
container — is the *destination*, but shipping it as step 1 is wrong here:

- **4GB VPS reality.** The box already runs systemd (`acb-gateway`) + Docker
  infra (`acb-postgres`, `acb-redis`). A cold container per run costs hundreds of
  MB + seconds of startup; naive container-per-run would OOM or serialize under
  any real concurrency. A production model needs a **warm pool** or subprocess
  tier — non-trivial infra.
- **The tool boundary is the hard part, not the container.** ~12 injected tools
  are **in-process closures** over gateway state (recon (c)): `call_agent`
  re-enters the executor; `query_history` opens a Postgres session; memory tools
  hit Mem0/Graphiti; `write_artifact`/`share_artifact` close over
  `_WRITE_ARTIFACT_CONTEXT` + the live SSE queue; `install_dependency` mutates
  `sys.executable`. Moving the run across a process boundary means **proxying
  every one of these back over RPC** — that's the bulk of the work and it's
  orthogonal to which isolation mechanism wraps it.
- **The mutation container is batch-only.** It communicates by parsing stdout
  sentinels after the process *exits* (`mutation.py:748-765`; the old `:665`
  anchor is stale); normal runs need the **live AG-UI SSE relay**
  (`orchestrator/stream_relay.py`). So even reusing the skeleton, we'd
  be building a new live host↔sandbox event channel.

So Phase 5 is **stepped** — ordered by (exposure removed) ÷ (infra cost), so each
step is independently shippable and de-risks the next.

## The Phase-5 plan — P5-a … P5-d

> **Naming (R2).** These were "Tier 0/1/2/3" until 2026-08-03 and are now
> **P5-a/b/c/d**, because "Tier n" already means isolation strength in
> `agent_platform_hardening_2026-07.md` §1.2 (**T0/T1/T2**) and the two ladders
> do not correspond. Mapping, for anyone reading an old link:
>
> | Old name | New name | T-ladder relation |
> |---|---|---|
> | Tier 0 | **P5-a** | none — it is credential hygiene inside T0/T1, not a tier |
> | Tier 1 | **P5-b** | none — it hardens the containers we already run; a T2 *prerequisite*, not a tier |
> | Tier 2 | **P5-c** | **is** the hardening doc's **T2** |
> | Tier 3 | **P5-d** | none — it is a permission-*policy* change unlocked by T2 |

### P5-a — Per-run credential scoping (kill the shared-env exposure) — ✅ **SHIPPED 2026-07-04** · AGENT-SAFE
The single highest-value slice, and it needs **no container at all** — it
directly closes exposure #1 above, which is the concrete "any agent reads any
secret" hole.

Replace the write-and-never-clear `_inject_integrations_to_env(os.environ)` with
a **scoped, per-run** materialization that is torn down when the run ends:

- Only export the credentials for **this run's** resolved integrations (the
  executor already has the per-run `integrations` dict — it's the argument).
- **Restore `os.environ` to its prior state when the run completes** (context
  manager / try-finally): capture the pre-existing value of each var, set ours,
  and on exit restore the captured value (or delete if it wasn't set). So creds
  for run A are gone before run B (or a concurrent idle agent) can read them.
- **Concurrency caveat, stated honestly:** `os.environ` is process-global, so
  under *concurrent* in-process runs this scoping is best-effort — two runs
  overlapping still share the env for the overlap window. This is a real limit of
  the in-process model and is exactly what P5-c (a real process/container
  boundary, each with its **own** env) fixes permanently. P5-a's win is
  removing the **permanent accumulation** (the steady-state where every secret
  ever used is always present) and scoping to the run's own declared
  integrations — a large, real reduction, not a complete fix. The residual
  concurrent-overlap window is logged as a known limit here so it isn't mistaken
  for closed.
- Prefer, where the tool supports it, passing creds **per-call** (the structured
  `state["integrations"]` dict the tools are *documented* to read —
  `integrations.py:8-11`) over the env at all; the env export exists only for
  subprocess skill scripts that call `os.getenv` directly. Audit which tools
  actually need the env vs. which can take the dict, and shrink the env surface
  to only the subprocess-callers.

This is a contained executor change with unit-test coverage and no infra
dependency — ships first.

**Shipped as described** — `executor.py:4340-4389` (token) + `:4392` (restore),
teardown at `:843` and `:4053`, pinned by `tests/unit/test_integration_env_scoping.py`
and `evals/trajectories/test_integration_env_scoping_trajectory.py`. The last
bullet (shrink the env surface to subprocess-callers only) was **not** done and is
not part of WS-3a; it stays as an unowned residual.

### P5-a.2 — **WS-3a · Record the derived tier, and refuse a run we cannot isolate** — 🔲 **AGENT-SAFE, dispatchable**

The tier is already derived and immediately discarded (see §"What actually
shipped"). This slice makes it a **record** and a **gate**. It builds no
container and changes no isolation mechanism — it makes the ladder observable
and makes the one posture we cannot honour refuse itself instead of proceeding
silently. Nothing here needs P5-c to exist.

**Scope.** `manifest.py` (no change expected), `declarative.py`, `executor.py`,
`gateway/run_trace.py`, one new migration, one new test file.
**Non-goals.** No container. No change to which tools any agent receives — the
tier is *derived from* the resolved surface, never the other way round. No
change to `AGENT_PERMISSION_MODE` behaviour.

**Done when — all five, each independently testable:**

1. **Derived on all three run paths, not just `declarative.py`.**
   `isolation_tier()` is resolved once per run and available to the run in each
   of the three executor entrypoints that already call
   `_inject_integrations_to_env` — `_run_sub_agent_streaming` (`executor.py:599`),
   `run_agent_stream` (`executor.py:2335`), `_run_with_maf_agent`
   (`executor.py:4516`). `declarative.py:210`'s existing log field stays and
   keeps reading the same function. *Test:* each of the three paths emits the
   tier for a manifest fixture whose expected tier is known (reuse the four
   fixtures already pinned in `tests/unit/test_agent_manifest.py:224-252`).
2. **Persisted on the `agent_run` trace.** A new nullable `text` column on
   `agent_run` (name it `isolation_tier`), added by **the next free migration
   number — determine it by listing `infra/postgres/` at build time; do not
   copy a number out of this document**. `gateway/run_trace.py::_persist_row`
   (~`:169`) writes it in both the INSERT and the `ON CONFLICT DO UPDATE`
   branch, and `build_run_trace_row` (`:67`) carries it.
   *Test:* `build_run_trace_row(...)` returns the tier in its row dict.
   **Known limit, state it in the PR rather than chasing it:** `agent_run` rows
   are written only from `chat_fold.py:467-478` (the streamed chat path), so
   batch `/agent/run` and sub-agent runs get the log line and the refusal but no
   row. Widening trace coverage is a separate, unowned item.
   **Recommended seam:** the streaming path already emits
   `{"type": "RUN_STARTED", "runId", "threadId"}` at `executor.py:2311`, and
   `run_trace` already derives fields from that same replayed event list
   (`_derive_status`, `:23`). Carrying the tier as one extra field on
   `RUN_STARTED` needs no new plumbing and is unit-testable against a synthetic
   event list. An explicit `record_run_trace(..., isolation_tier=…)` kwarg
   (`run_trace.py:220`) is an acceptable alternative; pick one and say which.
3. **A T2 run that is not covered by the sandbox is refused — before any tool
   injection.** When the resolved tier is `T2` and
   `settings.copilot_sandbox_scope` (`acb_common/settings.py:222`, default `""`)
   does not cover that run, the run raises a **named** error —
   `IsolationTierUnavailable` — carrying the agent slug, the derived tier, and
   the configured scope. It is raised **before** `_inject_agent_tools`
   (`_tool_injection.py:639`) runs, so an un-isolatable run never receives the
   shell tools that made it T2.
   *Test:* a T2 manifest with an empty `copilot_sandbox_scope` raises
   `IsolationTierUnavailable`; the same manifest with a covering scope does not;
   a T0 and a T1 manifest never raise regardless of scope.
4. **It ships OFF, and the switch is named.** Because *today* every unscoped
   agent derives T2 (`manifest.py:281-282` — an open scope means the shell tools
   are injected), enforcing the refusal on day one would refuse most real runs.
   So the refusal is behind an env switch (`ISOLATION_TIER_ENFORCE`, default
   off) and defaults to **log-and-proceed** with a `WARNING` naming the tier and
   the missing coverage — the same audit-then-enforce shape
   `AGENT_PERMISSION_MODE` already uses, and for the same reason.
   **Flipping it on is OWNER-GATE**; register it in `work_plan.md` §6 in the
   same change. *Test:* with the switch off, a T2/no-scope run proceeds and logs;
   with it on, the same run raises.
5. **Pinned in a named new test file:** `tests/unit/test_isolation_tier_record.py`,
   covering all of 1–4. Existing pins must stay green —
   `tests/unit/test_agent_manifest.py` in particular, because it asserts
   `resolve_tool_surface` ≡ `_resolve_injected_scope`, and that equivalence is
   what makes the derived tier trustworthy.

**Verification commands (WS-3a):**
```
uv run ruff check .
uv run python -m pytest tests/unit/test_isolation_tier_record.py \
  tests/unit/test_agent_manifest.py tests/unit/test_declarative_builder.py -q
```

### P5-b — Ceilings, egress and a scoped key on the containers we already run

Before generalizing the container, **harden the ones we already have** (they are
the template P5-c reuses). There are now **two**: `mutation.py`'s batch mutation
sandbox and `copilot_sandbox.py`'s Copilot-CLI sandbox.

#### P5-b.1 — Resource + capability ceilings — ✅ **SHIPPED 2026-07-27** · AGENT-SAFE
~~Add `--memory`, `--cpus`, `--pids-limit`, `--cap-drop=ALL` (+ re-add only what's
needed) … to the `docker run` in `mutation.py:626`.~~ **Done.** Both containers
carry `--cap-drop ALL` + `--cap-add DAC_OVERRIDE` +
`--security-opt no-new-privileges` + `--memory` / `--cpus` / `--pids-limit`, all
settings-overridable — `mutation.py:700-722`, `copilot_sandbox.py:153-171`. Do
**not** re-add these; see the struck sentence in exposure #4. Pinned by
`tests/unit/test_mutation_sandbox_hardening.py` and
`tests/unit/test_copilot_sandbox.py`.

#### P5-b.2 — **WS-3b · Read-only rootfs + a stated network posture** — 🔲 **AGENT-SAFE, dispatchable**

The two flags the 2026-07-27 pass did not add. Pure additions to two existing
`docker run` invocations; no new infrastructure, no new call site.

**Scope.** `apps/services/orchestrator/orchestrator/mutation.py`,
`apps/services/orchestrator/orchestrator/copilot_sandbox.py`,
`packages/acb_common/acb_common/settings.py`, the two existing test files.
**Non-goals.** No scoped gateway key (that is P5-b.3, OWNER-GATE). No egress
*proxy* — a proxy is P5-c infrastructure. No change to what either container runs.

**Done when — all four:**

1. **Both containers pass `--read-only` with a named writable mount.** The
   rootfs is read-only and the workspace is the declared exception:
   `mutation.py` keeps `-v {agent_dir}:/workspace/repo` writable and adds a
   `--tmpfs /tmp` (the `copilot` CLI, `git`, and `uv` all write there);
   `copilot_sandbox.py` keeps its two existing `-v` mounts
   (`{workspace}:{CONTAINER_WORKSPACE}` and the state dir) writable and adds the
   same `--tmpfs /tmp`. Both are settings-overridable
   (`mutation_readonly_rootfs` / `copilot_sandbox_readonly_rootfs`, **default
   `True`**) so a single env var reverts the posture without a deploy.
2. **Both containers pass an explicit `--network` posture with a stated
   default.** Default `bridge` — i.e. **today's behaviour, made explicit and
   overridable** (`mutation_network` / `copilot_sandbox_network`, default
   `"bridge"`). Deny-by-default is **not** in this slice, and the reason is
   recorded rather than assumed: `mutation.py:716` needs
   `host.docker.internal:host-gateway` to reach the gateway `/v1`, and
   `copilot_sandbox.py:163` publishes `-p 127.0.0.1::<port>` for host→container
   RPC, so `--network none` breaks both. The slice's value is that the posture
   becomes a **named, overridable, tested setting** an operator can narrow to a
   custom docker network — not that it is narrowed here.
3. **Assertions land in the existing test files.**
   `tests/unit/test_mutation_sandbox_hardening.py` and
   `tests/unit/test_copilot_sandbox.py` each gain: the flag is present with the
   default; the setting overrides it; the writable mount / tmpfs is present
   alongside `--read-only`; and the existing cap/limit assertions still pass
   unchanged.
4. **No behaviour change at defaults.** A run with untouched settings produces
   the same container behaviour as today apart from the read-only rootfs — which
   means the honest risk of this slice is *"something in the image writes outside
   the mounts and now fails"*. Both images must be exercised once before merge
   and the result stated in the PR.

**Verification commands (WS-3b):**
```
uv run ruff check .
uv run python -m pytest tests/unit/test_mutation_sandbox_hardening.py \
  tests/unit/test_copilot_sandbox.py tests/unit/test_code_session_sandbox.py \
  tests/unit/test_app_builder_sandbox.py -q
```

#### P5-b.3 — Scoped gateway key for the sandbox — 🔲 **OWNER-GATE · unbuilt and undesigned**
Give the sandbox a **scoped gateway key** (not the master key) with a short TTL /
run-scoped identity, so a leaked sandbox key can't act as the gateway. (Ties to
B5 on-behalf-of vs fixed-credential.) Today `mutation.py:700-722` passes
`GATEWAY_API_KEY` straight through.

**This has no acceptance and should not be given any by an agent.** Three
questions are open and every one of them is an owner decision, not an
implementation detail: what the TTL is, who issues the key (the gateway at spawn
time? a pre-provisioned service identity?), and how it is revoked mid-run. It
also touches credential issuance, which is in `work_plan.md` §6's gate list.
An agent asked to "finish P5-b" builds **P5-b.2 only** and refuses this by name.

### P5-c — Generalize the container to a live, streaming run sandbox — 🔲 **PARKED SUB-PROJECT** (owner decision 2026-08-03) · **OWNER-GATE to un-park**

> ⚠️ **Read the D16 update at the end of this box first (2026-08-09):** the parking
> survives but the premise below is dated and the un-park trigger changed — it is the
> **pooled cutover**, not "a second organisation".
>
> **Why this is parked, not cancelled.** Metorite is an **internal Fracktal
> tool**. The team uses it; there are no external tenants and no customer-authored
> agents. So the isolation ladder has to hold up to **trusted colleagues, not
> hostile users** — and against that threat model the failure modes that matter
> are mistakes and blast radius (a runaway loop on a 4GB box, an agent reading a
> credential outside its declared scope, a write in the wrong tree), all of which
> are addressed by P5-a's credential scoping, P5-b's ceilings, and WS-3a/WS-3b.
> None of them needs a container around a normal run.
>
> The design below is still the right destination and is kept intact. What it
> loses is its **schedule** and its old justification: it was previously gated on
> *"before the Agent Workshop opens to non-engineers"*, which assumed the Workshop
> would hand agent authorship to people outside the engineering team. It will not —
> the Workshop's users are colleagues who could already open a PR against this
> monorepo. See `agent_platform_hardening_2026-07.md` §1.5.
>
> **P5-c has no acceptance criteria, and none should be written for it** until
> either (a) a **second organisation** runs on this platform, or (b) agent
> authorship opens to someone **outside Fracktal**. At that point it is re-costed
> from scratch — the 2026-07-04 estimates below are a year stale by then.
> Un-parking it is an **owner decision**. An agent asked to "finish the isolation
> ladder" builds WS-3a and WS-3b and refuses P5-c by name.
>
> *[Update 2026-08-09 — D16: the parking survives, the trigger changed. P5-c/T2
> is now a precondition of the pooled cutover (`saas_multitenancy.md` §5.1,
> MT-0c-2); the internal-tool premise expires with the first external tenant.
> Acceptance remains unwritten by design until the owner un-parks.]*
>
> **Do not confuse P5-c with what shipped.** `copilot_sandbox.py` containerizes
> the **`copilot` CLI binary** and is wired at two call sites behind a scope
> setting that ships empty. The host still owns orchestration, tool execution and
> permission handling. That is T2-*shaped* reuse of the mutation container's
> hardening — it is **not** P5-c, and it does not isolate a normal agent run.

Design of record (unchanged, 2026-07-04) — lift a **normal** Copilot/MAF run into
the (now hardened) container:
- New `sandbox_runner.py` (generalize `mutation_runner.py`) that runs the agent
  turn and **streams AG-UI events live** to the host over a real channel
  (Redis Stream keyed by thread_id — reuse `stream_relay.py`'s contract directly,
  rather than post-exit stdout sentinels).
- A **tool-proxy RPC**: the in-process tool closures stay host-side; the sandbox
  calls them over the boundary (the host already owns Postgres/Redis/Mem0/the SSE
  queue). Model calls stay HTTP-to-gateway (already the pattern).
- **Per-agent venv/image** so dep installs can't collide (removes the
  `--python sys.executable` shared-venv risk).
- **Warm-pool** execution model for the 4GB box (a small pool of pre-started
  sandbox containers claimed per run), not cold-container-per-run.
This is genuinely multi-step infra and is scoped as its own sub-project; P5-a
+ P5-b remove the concrete standing exposures and de-risk it.

### P5-d — Default-deny tightening + intent-level auth — 🔲 **blocked on P5-c (parked)** · **OWNER-GATE**
Once P5-c gives real isolation, flip the near-term handler's *unknown →
approve-open-but-logged* to *default-deny* (the honest reason it's fail-open
today, per the near-term section, is that a hard deny on an in-process model that
already runs arbitrary code gives false assurance — a real boundary removes that
objection). Layer intent-level authorization over allow-everything.

> **Re-framed 2026-08-03 under the internal-tool threat model.** P5-d inherits
> P5-c's parking: it is explicitly conditioned on *"once P5-c gives real
> isolation"*, and P5-c is parked. **Do not build P5-d in the meantime**, and do
> not "partially" default-deny an in-process run to make progress — that is
> precisely the false assurance the paragraph above warns about, and against
> colleagues rather than attackers it buys nothing while breaking real work.
> *[See the D16 update note at §P5-c — trigger re-scoped 2026-08-09.]*
>
> The one piece of P5-d that is separable is the **near-term handler's mode**,
> which already exists: prod runs `AGENT_PERMISSION_MODE` in `audit` and moving
> it to enforcement is **OWNER-GATE** (`work_plan.md` §6). That flip does not
> need P5-c and does not need this section — it needs someone to read the
> decision stream. Everything else here (intent-level authorization over a
> default-deny surface) stays parked with P5-c and gets **no acceptance**.

## Grade movement — *re-scored 2026-08-03*
P5-a alone: B6 stays **B−** but closes the single worst concrete hole (shared
ambient secrets). **P5-a + P5-b.1 (both shipped) + WS-3a + WS-3b: B** — ceilings,
a recorded and enforced tier, a stated egress/rootfs posture, and no permanent
credential accumulation. The `sk-local`-class scoped key (P5-b.3) is the one
piece of the original "B" bundle still missing, and it is owner-gated.

**The A− line is now conditional, not scheduled.** The old text put **B+/A−** at
P5-c ("real isolation boundary for normal runs") and treated it as the
destination. Under the internal-tool threat model that grade is only *worth
buying* when the trust boundary moves — a second org, or authorship outside
Fracktal. Against colleagues, **B is the right resting grade**, and the module
map's "container isolation for normal runs" item should be read as *"open, and
deliberately parked"* rather than *"open, in progress"*.
*[See the D16 update note at §P5-c — trigger re-scoped 2026-08-09.]*

## Status (Phase 5)
- 2026-07-04 — Design from the B6 Phase-5 recon (mutation-container primitive +
  in-process/credential boundary analysis). Tiered plan authored. Implementing
  **Tier 0** (per-run credential scoping) first — the highest exposure-removed ÷
  infra-cost slice, no container dependency.
- 2026-07-04 — **Tier 0 (now P5-a) shipped.** `_inject_integrations_to_env`
  returns a restore token consumed by `_restore_integration_env` at run
  teardown; wired on all three run paths. Never logged at the time — recovered
  from code on 2026-08-03.
- 2026-07-27 — **Tier 1's ceilings (now P5-b.1) shipped**, plus two things this
  spec never mentioned. Recorded here on 2026-08-03 from
  `competitive_hardening_2026-07.md:119-141` and re-verified against code:
  (a) `--cap-drop ALL` / `--cap-add DAC_OVERRIDE` /
  `--security-opt no-new-privileges` / `--memory` / `--cpus` / `--pids-limit` on
  the mutation container; (b) the **dep-install RCE fix** —
  `_install_agent_deps` defaults to `--only-binary=:all:` (`loader.py:1213-1215`);
  (c) **Copilot-CLI containerization** (`copilot_sandbox.py` +
  `Dockerfile.copilot-sandbox`) wired at `code_session.py:109` and
  `executor.py:1070`, gated on `settings.copilot_sandbox_scope` and shipping OFF.
  Egress, read-only rootfs and the scoped key were **not** part of that pass.
- 2026-08-03 — **Truth pass (WS-3), verified against code at `2ccff9e0`.** No
  code changed; this is a documentation reconciliation. What changed here:
  1. **Struck a false, dangerous claim.** Exposure #4 asserted the mutation
     container ran with *"zero `--memory`/`--cpus`/`--pids-limit`/`--network`/
     `--cap-drop`/`--read-only` flags (grep-confirmed)"*. Untrue since
     2026-07-27; four of the six have been present for five weeks. An
     implementer dispatched on this row would most likely have re-added
     `--cap-drop ALL` to `mutation.py`. Replaced with a per-flag table naming
     both containers and the two flags that really are missing.
  2. **Renamed Tier 0/1/2/3 → P5-a/b/c/d** (R2) and adopted
     `agent_platform_hardening_2026-07.md` §1.2's **T0/T1/T2** as the single
     isolation ladder. The two were incompatible numberings inside one board
     cell (WS-3).
  3. **Fixed six stale anchors**: `executor.py:4509`→`:4340`; the three run-path
     anchors `:1419/:2769/:4655`→`:599/:2335/:4516`; `mutation.py:626`→
     `:700-722`; `loader.py:1240-1247`→`:1287`/`:1300`; `loader.py:1095`→`:1137`
     (plus the previously-unrecorded `--only-binary=:all:` guard at `:1213-1215`);
     `mutation.py:665`→`:748-765`; and the five near-term permission sites
     `~1190/~2572/~3023/~3796/~4297`→`632/2483/3011/3909/4442`.
  4. **Recorded what shipped and was never written down** — see the
     §"What actually shipped" table.
  5. **Wrote acceptance for exactly two slices**, WS-3a (§P5-a.2) and WS-3b
     (§P5-b.2). Both AGENT-SAFE, both dispatchable, neither needing a container.
  6. **Parked P5-c and P5-d** under the owner's 2026-08-03 internal-tool threat
     model, with the un-parking condition stated. Neither gets acceptance. *[See
     the D16 update note at §P5-c — trigger re-scoped 2026-08-09.]*
  7. **Struck WS-3's claim on `tool_scope` deny** — that is built and
     owner-gated under **WS-23** (`_tool_injection.py:101-117` + `:214-224`,
     spec `skills_scope_out.md` §4), not this row.
  **Still owed by the owner** (recorded, not actioned, because they are outside
  this doc): the `work_plan.md` §2 WS-3 title correction, a §4 single-owner row
  for the isolation ladder, `copilot_sandbox_scope` registration in §6, and the
  `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO‑7 correction (its stale
  `loader.py:1247` / `:1095` anchors, and its CH‑1 note recommending a flag set
  that has already been adopted).

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-3 — **Isolation ladder** (BO-7 / HH-6 — T0/T1/T2 per `agent_platform_hardening_2026-07.md` §1.2)

**State cell (as of the move):** 🟢 **WS-3a** (record + refuse, §P5-a.2) · 🟢 **WS-3b** (rootfs + network posture, §P5-b.2)

**Narrative (verbatim):** P5-a (per-run credential scoping, 2026-07-04) + P5-b.1 (cap/resource ceilings, 2026-07-27) shipped. **T2 / P5-c PARKED** under the internal-tool threat model (owner decision 2026-08-03, D10) — the ladder must hold against trusted colleagues, not hostile users; **un-parking is OWNER-GATE**, and no acceptance should be written for P5-c until it happens. P5-d is blocked behind it. **Two claims struck from the old title:** `tool_scope` deny belongs to **WS-23** (shipped there), and "T2 for non-first-party agents" named a distinction the code does not carry — no `first_party` field exists on any manifest, config or column; the phrase occurs only in comments and one test helper. **OWNER-GATE:** the `AGENT_PERMISSION_MODE` enforcement flip · P5-b.3's scoped gateway key (unbuilt *and* undesigned) · the new `ISOLATION_TIER_ENFORCE` flip WS-3a introduces.

**Corrections applied 2026-08-09:**
- T2/P5-c parking re-framed by D16 (2026-08-08): the un-park trigger is now "precondition of the §5.1 pooled cutover (customer 8–12)" per `saas_multitenancy.md` — not "a second org on this platform, or agent authorship from outside Fracktal". Acceptance still must not be written until the owner un-parks.
- MT-0b's migration 157 adds `organization.first_party`, retiring the row's "no `first_party` field exists" note.
