# Work Plan of Record — the dispatch board

**Status:** Active · **Date:** 2026-08-09 — **multi-tenancy consolidation pass** (§5
residual 7 is the change list): the D11/D10-premise purge across the corpus after
D15/D16, §2 compacted per **D18** with row narratives moved to owning specs' "Board
record (2026-08-09)" sections, **R5** (tenant-ready by construction) minted, **D17**
(Mem0 binding) + **D18** (priority of record · board format · MT-2/3 pricing inputs)
recorded, WS-29 updated with the H1 scratch-verify result and PR #404, and eighteen
stale-vs-merged row claims swept (branch protection, ledger, backups, deploys,
WS-13/26/27 states). **Prior pass 2026-08-03** (six-row truth pass: WS-1, WS-3, WS-8,
WS-11, WS-12, WS-21 swept to match their rewritten specs; D10 records two owner
calls. **Second pass the same day:** D11 + D12 record the tenancy boundary and
the visibility model from `specs/tenancy_and_visibility.md`; WS-14 unblocked;
WS-13 gains the verified "Centers are unreachable by anyone" finding; §2 gains
the three app-by-app exceptions. **Third pass the same day — WS-14 doc
remediation:** WS-14 was audited NO-GO on 7 of 7 contract points and is now
re-scoped onto four lettered bullets in `department_centers.md` §3 (C1 🟢 · C2
struck, ownerless · C3 🟢 narrow · C4 🔴 owner-decision); **WS-14a is minted** for
TV-1, which passed the contract but had no board row; six stale `rooms.py` anchors
corrected in D11, D12 and the WS-14 row; §4's shared-mailbox and per-Center-approvals
rows re-stated against measurement. **Repair round on the third pass:** **D13** is
registered (the project grant table — `agent-proposed, owner may overrule`, previously
discoverable only inside the WS-14 row); C1's acceptance gains a caller-reachable grant
creation path; and a factual claim this board carried twice is retracted — `actor` in
`pending_actions` **does** name the requesting human at two of its six writers, so §4's
row and §6's gate now rest on the measured shapes rather than on a false absolute. The
C4 verdict is unchanged. **2026-08-04 — WS-24 minted:** colleague onboarding
readiness gets a row, an owning spec (`specs/colleague_onboarding.md`) and an
executable gate (`scripts/onboarding_preflight.py`); the single member of record
and the five member/group write endpoints join §4 and §6; **D14** records that
`data:org:read` grants nothing — it has zero consumers — so `manager`'s
"org-wide visibility" is a name and the department-privacy question is really
about `admin:members:read`) · **Owner:** vjvarada
**Purpose:** the single sequencing document from which independent agents are
dispatched. **Navigation for agents: `project-docs/INDEX.md` is the
classification of record (D26) — build only from specs it lists ACTIVE.** Content lives in the owning specs; *this* doc owns ordering,
ownership, and the rules that make a spec executable without questions.

Built from a three-way audit (2026-07-31) of the foundation docs, the app
master plans, and the platform specs. The audit found the corpus rich but not
dispatchable: status drift (docs claiming "not built" for shipped work and
vice versa), the same work claimed by 2–6 specs with no single owner, and
broken anchors (stale migration numbers, pre-restructure file paths, colliding
phase IDs). §5 is the remediation backlog; §2 is the board.

**Authority.** For *what to build and how*, the owning spec wins. For *what
order and who owns it*, this doc wins — including over `project_plan.md` §6
sequencing for near-term work. When a mirror spec disagrees with the owner
named in §4, the mirror is stale by definition; fix the mirror.

---

## 1. The agent-ready spec contract

A workstream may be handed to an independent agent only when its owning spec
carries all seven. (Exemplars: `permissions_sandbox_b6.md` Tier 0 for tests +
decision table; `drawio_integration.md` for per-ticket "done when";
`task_manager_app.md` §9.3 for the runbook; `observability_e2.md` for
verification commands.)

1. **Status header** — dated, with "verified against code on <date>". A header
   that contradicts the body (WhatsApp, task-manager) is worse than none.
2. **Scope and non-goals** — explicit, like email master §1.
3. **Acceptance per item** — a "done when" an agent can test, not "owner call".
4. **Current file paths** — `apps/services/...` tree; anchors re-verified at
   dispatch, not trusted from authoring time.
5. **Verification commands** — the exact pytest/tsc/build/feature_check calls.
6. **Single owner** — one owning spec; every other doc that mentions the work
   links here and adds nothing.
7. **Gate labels** — every item marked **AGENT-SAFE** or **OWNER-GATE**
   (see §6). An agent must refuse OWNER-GATE work and say so.

**Standing rules** (bind all specs from today):
- **R1 — no absolute future migration numbers.** Write "next free number at
  build time". The audit found ~12 wrong citations (117/118/119/120/122/123/
  128/131/133/134/135 all point at unrelated shipped migrations).
- **R2 — no phase-ID reuse across docs.** "Phase 2" currently means three
  different things. New work uses the WS-n IDs below.
- **R3 — nomenclature per `department_centers.md` §1.** "Agent Workshop" not
  "Agent Creator" (5 spec sites violate this); "Agent Registry" for `/agents`;
  Center/module/group as defined there.
- **R4 — status changes propagate.** A PR that ships spec'd work updates the
  owning spec's status header in the same PR.
- **R5 — tenant-ready by construction** *(owner-directed 2026-08-09, D18; binds
  every PR while WS-29 is in flight)*. App work continues in parallel with the
  tenancy retrofit on these terms, each enforced by an existing test, not by
  prose: **(a)** every new persisted table is tenant-scoped — it must satisfy
  `tests/unit/test_tenant_coverage.py`'s source gate (covered by the generated
  RLS migration, or in `gen_tenant_migration.EXEMPT` with a reason a reviewer is
  expected to challenge); **(b)** no new database connection sites outside the
  seam — additions to `_SYNC_ENGINE_ALLOWED` / `_PSYCOPG_ALLOWED` need a cited
  reason in the PR; **(c)** new Redis keys go through the tenant-prefix wrapper
  (allow-list additions likewise); **(d)** session acquisition uses the current
  seam idiom only, so H2's conversion stays mechanical — do not invent new
  acquisition idioms; **(e)** never trust a tenant (or identity) from request
  input — `user_management_contract.md` R11/R3. The ratchet tests **bind `main`** (PR #404 merged 2026-08-09).
- **R6 — expand/contract migrations** *(D28, 2026-08-10; owning spec
  `specs/engineering_practice.md` §3).* **The deploy applies migrations BEFORE
  restarting services**, so old code always runs against new schema for a window.
  A migration must therefore be compatible with the code currently running: new
  columns **nullable with a default**; **no rename in place** (add, backfill,
  switch readers, drop in a LATER release); constraints over existing data land
  `NOT VALID` and validate in a guarded block (migration 148 is the reference).
  The tightening half is always a second, later migration. ⚠️ **We cannot roll
  back** — forward-only ladder, no blue/green — so recovery is roll-forward or
  restore, which is why §8's off-box backup and SHA-in-`/health` items are
  business risks rather than tech debt.
- **R7 — a rule names its fence, or it is advisory** *(D28; the generalisation of
  R5's "enforced by an existing test, not by prose").* Any PR introducing an
  architectural rule must name the test that makes breaking it fail, or label the
  rule advisory. Prose binds nobody: the next agent has not read that paragraph.
  Prefer **structural fences** (assert the invariant over the whole tree) to
  example tests — they are what defend against future agents.
- **R8 — SQL is verified against a real database** *(D28; `engineering_practice.md`
  §4).* Hermetic fakes agree with whatever SQL they are handed, which is how five
  live bugs shipped green (an unencodable `CAST(:param AS timestamptz)`; a fake
  matching `lower(col) = :param` against NULL; a `LEFT JOIN` whose `ON` could not
  see its table). Any change whose subject is a query, a migration or a predicate
  is run against a real Postgres before it is believed. **Verified-red-first**
  applies to every fence and every bug fix; **mutation testing** applies to money,
  auth, tenancy and outward writes.

---

## 2. The dispatch board

States: 🟢 ready to dispatch · 🟡 dispatchable after the named gate ·
🔴 blocked on owner/decision · ✅ done. "Docs" gate = the §5 fix for that spec.

### WS-0 · Documentation remediation — ✅ executed 2026-08-01 (residuals in §5)
Six-agent truth pass completed: Tier 1 (items 1–10), Tier 2 (11–17), and the
Tier 3 annotations are done, verified against code. Residual items listed at
the top of §5. Findings folded back into this doc: D7 gained the MAF-side MCP
gap; calendar P3 was found already shipped (with revised roll-over semantics).

### Can we go app by app? — yes, with three exceptions *(2026-08-03)*

The owner asked whether the foundation is complete enough to work app by app. It
is. **Three items are exceptions** — they are not app work, they do not get
better by being deferred behind app work, and one of them gets *worse* with every
app added. Recorded here because §2 is where a reader planning the next app
looks. Full statements live in `FOUNDATION_BUILDOUT_CHECKLIST.md`.

| # | Exception | Where it lives | Why it can't wait for "after the apps" |
|---|---|---|---|
| 1 | ~~**`main` has no branch protection**~~ — **CLOSED 2026-08-03** | WS-5 · checklist §BO-17 | Was `404 Branch not protected` with rulesets `[]` under both mechanisms, so every CI gate in the YAMLs was decorative. **Enabled 2026-08-03** (owner-authorised in-session): PRs required, `required_approving_review_count: 0`, **`enforce_admins: true`**, force-push and deletion blocked. Verified by reading the protection back. ⚠️ **`required_status_checks` is deliberately `null`**: `pr-check.yml` has `paths-ignore: ["**.md", "project-docs/**"]`, so a docs-only PR produces **no** check-runs — requiring those contexts would make every docs PR permanently unmergeable (this row's own PR included). Tightening path: add an always-runs sentinel job to `pr-check`, then require **that** one context. |
| 2 | ~~**No backup / restore path**~~ — **CLOSED in substance 2026-08-07** | checklist §BO-23 | Nightly `acb-backup.timer` **verified scheduled 2026-08-07** (after three same-day defects: #382 wrong script, #383 fork bomb, #384 mig-148 cast); a restore was rehearsed for real 2026-08-05 (`live=228 restored=228`); the migration **ledger is merged** (`5f025d80`, renumbered to 153), so a deploy stops replaying the whole ladder. Residue: `BACKUP_REMOTE` unset — off-box copy **deferred by owner decision 2026-08-05** (`backup_and_restore.md` §4.2); losing the disk, box or provider account still falls back to the weekly two-deep Hostinger image. Verify backups by deploy-log lines, never job conclusion. |
| 3 | ~~**DB engine sprawl**~~ **CLOSED 2026-08-06** | checklist §BO-10 | Measured 2026-08-03: **12 `create_async_engine(...)` call sites across 10 modules** (`acb_auth/access.py:69`; gateway `routes/{admin,apps,email,notes,tasks,whatsapp,workflows}/*core*.py`; `email_ingestion/{inbound,scheduler}.py` ×4), plus a 13th **sync** `create_engine` in `acb_graph/db.py:32`. Eight are module-level cached `_ENGINE` singletons and **none of them is disposed on shutdown** — the only `engine.dispose()` calls in the tree are the four `email_ingestion` per-call engines cleaning up after themselves. **This is the one that compounds: one engine per app, added by each app.** The next app should extend a shared seam, not add engine 13. **CLOSED 2026-08-06:** every async caller now resolves to ONE engine and pool in `packages/acb_common/acb_common/db.py` — not `acb_graph` (the gateway does not depend on it, and its engine is sync) and not `gateway/db.py` (which `acb_auth/access.py` cannot import, so a gateway-owned seam could never get below two pools in the gateway process). The six remaining route packages plus `acb_auth.access` were converted, each keeping its historical `get_db`/`_get_db`/`_get_session_factory` name as a re-export so ~50 call sites and every test monkeypatch are untouched; `gateway/db.py` is a re-export. `acb_auth`'s engine had never carried the 2026-08-06 connect/`idle in transaction` bounds — it does now. Pool ceiling 30 (tunable via `db_pool_size`/`db_max_overflow`), deliberately not the old ~165 sum, which exceeded a stock `max_connections` of 100 shared with Langfuse/LiteLLM/ingestion. `acb_audit.record()` is non-blocking on the loop (`to_thread` only when a loop is running; sync callers still inline) and `acb_audit.drain()` is awaited last in the gateway lifespan. Guarded by `tests/unit/test_db_engine_seam.py` + `tests/unit/test_audit_non_blocking.py`. Still open by design: `acb_graph/db.py`'s **sync** `create_engine` and `email_ingestion`'s per-run engines. |

**Row discipline (D18, 2026-08-09).** Rows below carry state, gates and pointers —
nothing else. The narrative that used to live in these cells (up to 29.5k characters
per row; §2 alone was ~77k tokens, unreadable in one pass by the dispatch loop it
serves) was moved verbatim into each owning spec's **"Board record (2026-08-09)"**
section, with that day's corrections applied and enumerated there. R4 binds a
shipping PR to update the row *and* the owning spec's header; **R5 (§1) binds every
PR to tenant-ready-by-construction while WS-29 is in flight.** Git history and the
owning specs are the archive; this file owns ordering, gates and states only.

### Substrate (foundation)

| WS | Workstream | State | Owning spec · record | Gates · next (verified) |
|---|---|---|---|---|
| WS-1 | **Action Broker truth + completion** (BO-1) | 🟢 | `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-1 · board record 2026-08-09 | Broker loop LIVE and writing; handlers register at SIX sites; `crm.zoho_*` handlers live and the Zoho sync loop is **running** (§6 WS-26 (a)). **BO-1a + BO-1b BUILT 2026-08-11** (branch `ws-1-broker-routing`, one PR): all six gated ClickUp action names have handlers behind an AST-derived fence, and a broker-QUEUED push writes `sync_state='awaiting_approval'` with no `provider_task_id` instead of a false `synced`. Open: **BO-1d** (the flip blocker, see below), then **BO-1c** the email-verb decision + handlers. 🔴 `ACTION_BROKER_ENFORCE` flip (§6) — ⚠️ **corrected 2026-08-11 (repair round 1): this row previously said "both blockers now clear", and the flip is NOT safe.** BO-1a and BO-1b cleared the *handler-routing* and *sync-state* blockers only. **BO-1d is now minted and open**: four callers never read the gate's pending marker — `routes/tasks/accounts.py:335` (`POST /tasks/accounts/{id}/projects`) and `:403` (`…/folders`) and `routes/tasks/planning.py:377` (`POST /tasks/plan/apply`, `target:"clickup"`) all index `created["id"]` unconditionally and hard-**500** under enforcement; `routes/tasks/items.py:790` (`_push_patch_upstream`) silently swallows a queued update, reporting local success with nothing upstream. Do not flip until BO-1d lands. Standing residuals either way: the var is set in **no** environment; a `pending_actions` row queued before a flip stays approvable after one (check `SELECT action, status FROM pending_actions` first); and no reconciliation of an approved write back onto its `gtd_items` row (BO-1b's own non-goal) — the next `/tasks/sync` pull inserts it as a second row. (2026-08-11) |
| WS-2 | **Secrets** (BO-8: rotate Zoho token, purge history, fail-closed) | 🔴 | checklist §BO-8 + `FOUNDATION_CONTINUATION.md` | OWNER-GATE end-to-end (force-push history purge, credential rotation). Standing P0 since 2026-07-11. WS-26e / WS-27g cutovers execute the Zoho / ClickUp revoke halves (§6 WS-26 (c), WS-27 (c)). |
| WS-3 | **Isolation ladder** (BO-7 · HH-6 · T0–T2) | 🟢 a+b · ⏸ T2 | `permissions_sandbox_b6.md` §P5 · board record 2026-08-09 | P5-a (credential scoping), P5-b.1 (ceilings), WS-3a (record+refuse), WS-3b (rootfs+network) shipped. **T2/P5-c re-framed by D16 (2026-08-08):** parked as a **precondition of the §5.1 pooled cutover** (customer 8–12) — no longer "until a second org appears"; acceptance stays unwritten until the owner un-parks (§6 first blockquote). P5-b.3 scoped gateway key: unbuilt *and undesigned*. MT-0b's `organization.first_party` (migration 157, scratch-applied) retires this row's old "no `first_party` field exists anywhere" note. 🔴 flips: `AGENT_PERMISSION_MODE`, `ISOLATION_TIER_ENFORCE` (§6). (2026-08-03 · re-framed 2026-08-09) |
| WS-4 | **Event-bus consumer + durable queue** (BO-20) | 🟢 a+f+b1 | checklist §BO-20 — **file at the REPO ROOT** · board record 2026-08-09 | §BO-20.0 answered: **Option A, in-process** (owner 2026-08-02). Built: BO-20a consumer (reviewed, four P2s repaired) · BO-20b slice 1 · BO-20f receiver parity (inert). Next, AGENT-SAFE in strict order: **BO-20b slice 2** (strict `dispatch_event` path + PEL/XAUTOCLAIM reclaim — the record pins eight traps; read it first) → BO-20c → (BO-20d, BO-20e). 🔴 `INGESTION_CONSUMER` flip (§6) + provisioning `ZOHO_WEBHOOK_SECRET`/`GMAIL_PUBSUB_TOKEN` on the box — ⚠️ D15 coda: those become **per-org** secrets at MT-1a+; one box-wide value cannot serve N tenants. (2026-08-03) |
| WS-5 | **CI gates real** (BO-17/BO-18) | 🟡 Docs | checklist §F · board record 2026-08-09 | Audited 2026-08-01 → NO-GO (§F has zero testable done-whens). ~~"main has no branch protection"~~ **struck 2026-08-09** — protection was ENABLED 2026-08-03 (exceptions row 1); the row had never been swept. Deploy still lints with non-blocking `ruff check .`. Ready slice: **BO-17a main-guard** (`correctness` on push-to-main, deliberately NOT in `needs:`) — AGENT-SAFE. 🔴 GitHub *settings* changes (required checks, `needs:` wiring, `skip_tests` removal). BO-18 → WS-2. (2026-08-01 · corrected 2026-08-09) |
| WS-6 | **Observability wiring + attribution** (BO-5 + D1) | 🟡 partial | `observability_e2.md` §7 · board record 2026-08-09 | WS-6a + WS-6c BUILT 2026-08-02, pending review — attribution reaches **logs + Redis only, nothing durable**. WS-6b/6d/6e **HELD NO-GO**: no mechanism carries run identity across the HTTP hop to `/v1` (contextvars don't cross it; `agent_run` rows are written at run boundary); do not dispatch until §7 names one. 🔴 WS-6f–i activation flips (§6). (2026-08-02) |
| WS-7 | **Memory activation + search** (BO-21 → BO-22) | 🔴 | checklist §C + `llm_caching_memory.md` | 🔴 OWNER-GATE `MEM0_ENABLED` / `GRAPHITI_ENABLED` prod flips (§6; cost + latent findings, `agent_platform_hardening` Part 5). `acb_search` (BO-22) after. ⚠️ WS-29 coda: Mem0 tenant binding is decided — **D17, conninfo option** — and the flip should land only with MT-1c's binding in place. |
| WS-24 | **Colleague onboarding readiness** *(minted 2026-08-04)* | 🔴 2 gates + 1 decision | `specs/colleague_onboarding.md` · board record 2026-08-09 | Every AGENT-SAFE item BUILT + MERGED + DEPLOYED (N1–N8; G4 closed 2026-08-04). ~~G3 backups~~ **closed**: BO-23 timer verified scheduled 2026-08-07, restore rehearsed 2026-08-05. Remaining: **G1** Caddy identity-header strip (§6 WS-24 (a)) · **G2** `GATEWAY_INTERNAL_TOKEN` split from `LITELLM_MASTER_KEY` (§6 WS-24 (b); rotation is a redeploy and delivery works again — see WS-25) · ~~**N5** owner decision~~ **N5 ANSWERED 2026-08-10 (D25.5): NOT a blocker for colleague #1; the D12 grant-table treatment on the nine `routes/notes` modules is owed before colleague #2 or any external member** ~~ports-open claim~~ closed 2026-08-05 (§6 identity item 2). ⚠️ D14 coda: `data:org:read` now has a consumer path (WS-27d) — re-verify the capability matrix before member #2. (2026-08-05 · corrected 2026-08-09) |
| WS-25 | **Deploy delivery path** *(minted 2026-08-05)* | 🟡 recovered — cause unverified | `specs/deploy_delivery_path.md` · board record 2026-08-09 | ~~🔴 BROKEN~~ **re-measured 2026-08-09**: deploys landing since 2026-08-06 (migs 144/145 applied on prod); six green runs on **2026-08-07 UTC** alone, the last = #400's log-verified deploy `31217978773` (2026-08-08 IST — `crm_app.md`'s dating; `c1eba71f` fixed the apply script git-resetting itself mid-read — the "six deploys reported success while shipping nothing" hole). Tip run (`b09093a8`, docs-only) failed **health-verify** ×3 rounds 21:21→22:16 UTC 2026-08-07 — box at `affe0647`, one docs-only commit behind, cause unresolved; re-measure before quoting either state. Still real: **D1** extract the 435-line `DEPLOY_SCRIPT` from `deploy.yml` env (two-stage bootstrap) · SHA-in-`/health` (highest-leverage verify fix) · failure visibility. ⚠️ D15 re-scope: delivery becomes placement-parameterised (`saas_multitenancy.md` §5.1 condition 3) — one pipeline, N targets, never per-customer scripts. 🔴 all execution owner-gated. (2026-08-09) |

### Platform

| WS | Workstream | State | Owning spec · record | Gates · next (verified) |
|---|---|---|---|---|
| WS-8 | **Agent architecture A0→C** | 🟡 | `agent_architecture.md` §12.2 (WS-8a…n) · board record 2026-08-09 | A0 `approve_all` half done 2026-07-26. ⚠️ **Read §12.1 before dispatching**: ~60% of Phases A+B exists as complete-but-unwired substrate (`manifest.py`, `declarative.py` — documented, tested, zero production callers); an uninformed implementer rebuilds it. **WS-8c** = the MAF-side MCP injection silent no-op (D7), AGENT-SAFE. WS-14 does **not** wait on Phase A (D3 amendment). (2026-08-03) |
| WS-9 | **Memory tiers 3b/3c/4** | 🟡 Docs | `memory_architecture.md` §9 · board record 2026-08-09 | 3a′ substrate shipped (migs 136–139). **Ownership settled 2026-08-09: the 3a′ remainder (`subject:` compartments) is WS-10's S1; this row owns 3b/3c/4 only.** Audited NO-GO — §9 carries acceptance for 3a′ alone. Ready when specced: **3c-0** correction PATCH slice (AGENT-SAFE; shape in the record). Not owner-gated. ⚠️ never run `tests/unit/` as a directory here — `test_memory_integration.py` hangs. (2026-08-02) |
| WS-10 | **Multiplayer remainder** — S1 `subject:` compartments · floor re-decision · backfill | 🟡 S1 | `docs/multiplayer/memory-clearance.md` §7/§7.1 · board record 2026-08-09 | Steer shipped; two verification/repair rounds closed 2026-08-02. The work: **S1 `subject:` compartments** (AGENT-SAFE once §7.1 accepted). ~~🔴 floor-control re-decision~~ **ANSWERED 2026-08-10 (D25.4): CUT — steer suffices; the floor-mode machinery is retired unbuilt** · 🔴 `prefs`/`user` backfill **APPLY** (§6; classifier + dry-run report are AGENT-SAFE and the whole mandate). ⚠️ WS-29 coda: `org:global` scope is deployment-global today and must become tenant-scoped — coordinate S1 with MT-1c/D17; do not mint a sixth scope shape (`saas_multitenancy.md` §1.9). (2026-08-02) |
| WS-11 | **Workflows Slice 3** (gallery, fan-in/join, loops) | 🟢 | `workflows_app.md` §8.3 · board record 2026-08-09 | Slice 3 = **8.3a** gallery · **8.3b** fan-in/join · **8.3c** loops (owner-approved, D10.2; R1 governs the node *catalog*, not control flow). 8.3b/8.3c each must **invert a pinned test** (`test_fan_in_rejected_v1`, `test_cycle_rejected`) — leave either standing and the ticket closes green having built nothing. Template *content* is an owner input; the report-digest template belongs to WS-15. Slice 4 after BO-20b2 → c → (d, e) + 🔴 `INGESTION_CONSUMER` flip; its sandbox-dependent parts follow MT-0c-2's trigger (D16) — the old bare "BO-7" dependency is restated. (2026-08-03) |
| WS-12 | **Framework uplift** | 🟡 Ph4 | `multi_agent_orchestration.md` **Phase 4 only** (D6) · board record 2026-08-09 | Ph0 shipped; Ph1 struck; Ph2–3 superseded (D6); Ph5 struck. One SDK major remains: `github-copilot-sdk 0.1.32 → 1.0.2` (`openai 2.38.0` already in-tree). ~~🔴 Phase 4.0 target choice~~ **ANSWERED 2026-08-10 (D25.6): minimal bump** — 4.1 evidence then the 4.x slices are dispatchable; 🔴 Phase 4.6 recorded human soak stands (§6). Phase 4.1 throwaway-venv resolution evidence is AGENT-SAFE and must never mutate `.venv`/`uv.lock`. (2026-08-10) |
| WS-23 | **Skills registry + per-agent toggles** *(added 2026-08-01)* | 🟡 built | `specs/skills_registry.md` · board record 2026-08-09 | S1–S4 shipped pending review: registry + measured catalog, per-agent toggles (intersection-only, core floor non-toggleable), scope-out proposal, index diet (full surface 19,259 → 12,644 tokens). The ≤2k target is **unreachable by trimming** — §7.5 progressive disclosure is designed, costed, and deliberately unbuilt. 🔴 `SKILLS_FAIL_CLOSED`, `SKILLS_INDEX_ONLY` flips (§6). (2026-08-01) |

### Product — Centers (`department_centers.md` §3 · combined board record 2026-08-09 there)

| WS | Workstream | State | Owning spec · record | Gates · next (verified) |
|---|---|---|---|---|
| WS-13 | **Centers B — groups become real** | 🟡 review | `department_centers.md` Phase B | Groups admin UI + six-group seed built 2026-08-01, **pending owner review**; `center.*` feature vocabulary shipped 2026-08-03. ~~People directory read view open~~ **closed by WS-28b** (2026-08-06). ~~"nav renders with no access filter" / "catalog-read was rejected"~~ **inverted by merged #389** (`747b65af` — the catalog, not a code mirror, decides). Residue: the owner review itself. (swept 2026-08-09) |
| WS-14 | **Centers C — scoping deepens** | 🟢 with ⚠️ | `department_centers.md` §3 C1–C4 | D12 answered the blocker (a project belongs to a team by an explicit `group:<slug>` grant). **C1 tasks team slice: ⚠️ RE-AUDIT before dispatch (flag added 2026-08-09)** — WS-27e's owner-directed one-store revision (D-PM-6: `pm_tasks` is THE task table; WS-27h retires `gtd_items`) may moot D13's `gtd_*`-local grant table; whichever way it lands, the subject grammar must not fork (§4, D13). C2 shared mailboxes: doc-action only, ownerless in fact (§4) · C3 team-instanced agents: narrow, columns intentionally unread · ~~🔴 C4~~ **C4 ANSWERED 2026-08-10 (D25.3): Center leads approve, fallback org admins — the "add the requesting-Center column" ticket is dispatchable**. (2026-08-10) |
| WS-14a | **Tenancy TV-1 — the three `org_group` slug-only joins** *(minted 2026-08-03)* | ✅ absorbed | `specs/tenancy_and_visibility.md` §2 → **WS-29 MT-1i** | **Absorbed by WS-29 as MT-1i (2026-08-08) — do not dispatch from this row.** Code shipped on the WS-29 branch. Severity re-framed: under D15 the three joins **leak across tenants**, not merely misbehave within one. The open criterion — the two-org DB-backed fixture run `passed`, never `skipped` (§2 done-when 3) — travels with MT-1i. (2026-08-09) |
| WS-15 | **Centers D — dashboards + Company Center** | 🟡 WS-13 review | `department_centers.md` Phase D | Center dashboards, personal dashboard, weekly digest workflows (double as `workflows_app.md` G1 metric), D4 org-memory fix. Blocked only on WS-13's owner review now that WS-28b shipped the directory. ⚠️ D21/D22/D23 scope colour recorded in Phase D: configurable per-department, multiple leadership rollups, Center-grouped standalone app; Dashboards is a package-included base slice, never an upsell line (D23). |
| WS-16 | **Centers E — AI budgets** | 🟡 WS-6 | `department_centers.md` Phase E | Per-member caps at the LLM choke points (D2, D8). The chain is real: needs WS-6's **durable** attribution, which is HELD at WS-6b — do not dispatch expecting Redis-only records to suffice. ⚠️ The credit gate lands on the same choke points — design once, serve both. **This is now WS-31 CP-7 (D32.8, 2026-08-12):** per-member caps are a policy against the org credit pool, not a sub-wallet; default on exhaustion is degrade-to-`tier-fast`. Build it there, not twice. |

### Multi-tenancy (SaaS) — `saas_multitenancy.md`

| WS | Workstream | State | Owning spec · record | Gates · next (verified) |
|---|---|---|---|---|
| WS-29 | **Multi-tenancy — turning Metorite into a product sold to other companies** | ◐ H1 done · H2 next | **`specs/saas_multitenancy.md`** (architecture; §11 tickets) · ⭐ **`specs/saas_multitenancy_handover.md`** (H1→H8 runbook — hand THIS to the executing agent) · `specs/saas_multitenancy_implementation.md` (shapes) · board record 2026-08-09 in the parent spec | **Phase 0 ✅** (MT-0a · 0b · 0c-1 · 0d, pending review) · **H1 ✅ CLOSED**: scratch-verified 2026-08-09, **PR #404 merged and migrations 157/158/159 CONFIRMED on prod the same day** (ledger line "157 already recorded"; box self-applied via pull timer). · MT-1: 1a schema ✅ (identity cutover = H6, open) · 1b generated ✅ · 1c seam + ratchets ✅ — **561 call sites across 138 files unconverted = H2, the long pole** · 1e wrapper ✅ (~58 key sites unconverted = H5) · 1i ✅ (two-org DB fixture owed) · ⚠️ **MT-3 ABSORBED by WS-31 (D32, 2026-08-12) — do not dispatch it from here;** MT-2/MT-4 are pulled forward into WS-31's scope too, and §3.1 is reversed (banner in the spec). **MT-1's tenancy retrofit (H2–H6) is unaffected and remains the long pole.** · **MT-2/MT-3/MT-4 owner inputs ALL ANSWERED — final pricing shape D23/D24 2026-08-10 (§2.4b Center packages; ladder 600/1200/1800/2400/3000)** — MT-2's scope includes `center_package` + `plan_catalog` + seat `source` + the one-assignment act; spec detailing may start on all three; the customer-facing billing surface is WS-30 (Subscription Console — not to be confused with D41's Customer Console, the operator-side engine) · 🔴 MT-0c-2 parked (D16; §6 first blockquote) · §5.1 cutover trigger **ADOPTED 2026-08-09** — owner checks monthly · ⚠️ **#399 (MERGED 2026-08-09) carried a second WS-29 thread** (`specs/multi_tenancy.md`, superseded for architecture — measured record only): migration **161** keys all 17 `pm_*` + a parent-consistency trigger, **162** makes `app_user` unique on `lower(email)`, S1-1 fixed a cross-tenant **write** into access control, S1-4 removed the process-global agent identity, plus a 14-finding leak audit. **It also found a defect in MT-1b:** the generator scoped `crm_contacts`/`crm_deals`/`crm_activities` by column name, but their `organization_id` references `crm_organizations` — phase 2 would have aborted mid-window. Gated at generation time now (`HOMONYM_BLOCKED`); those three tables carry **no isolation** pending a rename — 🔴 owner call. ◐ **H2 STARTED 2026-08-10**: central `bind_tenant` binding + `TenantScopeMiddleware` SHIPPED; `routes/projects` converted (84 sites; `agent_dispatch` H4-exempt by name); ratchets in `test_db_engine_seam.py` (projects=0, elsewhere frozen at 494, bank-your-progress rule). ⚠️ **The first live run found `tenant_session()` itself broken** — `SET LOCAL … = :param` is a Postgres syntax error (SET can't bind); fixed to `set_config(..., true)`, pinned + live-proven. See the handover's H2 box. **H2 ◐◐ NEARLY DONE same day (two waves of parallel slice-agents):** notes/whatsapp/email/tasks/workflows/apps/crm/people/admin all converted — baseline **494 → 111**, every remaining site classified in place (`# H4:` background / `# H4/H6:` service-identity, tenant source named), zero-remainder packages pinned parametrically, whatsapp/apps pinned at exact counts, live smokes per wave incl. a FORCE-RLS two-org isolation proof. ⚠️ **New gap, D31 (2026-08-11): there is no per-tenant restore, only a whole-cluster one (`saas_multitenancy.md` §6.6).** BO-23 restores the box, so serving one customer's recovery rolls every other customer back — a §6 cross-tenant defect from customer #2, *not* a customer-#1 gate. Not dispatchable yet: needs a ticket contract, and its filtered export must be driven by the same `discover_tables()` set the RLS policies use, i.e. after MT-1b promotion. **Next: H4 (explicit tenants for the 111 marked sites' jobs/consumers) and H5 (Redis), then H3.** (2026-08-11) · ◐ **H4 STARTED 2026-08-22:** tasks/calendar background jobs bound (WS-29 MT-1d) — the 5 scheduler + rollover sites moved from unbound `get_db()` to `tenant_session(org)` (single-org jobs bound from the loop; `start_background_sync`/`_run_rollover_sweep` became per-org sweeps enumerating the RLS-EXEMPT `organization` table), refusing `TenantUnbound` with no org; R8 GREEN-bound/RED-unbound/no-org-refuses fences on the phase-4 non-priv catalog in `test_h3_rls_promotion_rehearsal.py`, elsewhere ratchet 111→108. **Remaining H4:** the tasks/CRM broker handlers, ingestion scheduler/consumer, reconciler, orchestrator runs. DARK (byte-identical pre-flip) · 🔲 **MT-1j · Tenant-side organization provisioning MINTED 2026-08-19 (D43)** — the ticket four specs disclaimed to and nobody had written; the substrate that lets Fracktal exist as org #2. Six slices (parameterised role seed replacing five `WHERE slug='default'` seeds · un-literal the `_BOOTSTRAP_ORG_SLUG` owner path · organization+tenant_placement as one idempotent act · the Console↔tenant seam INCLUDING the measured hole that **`POST /orgs/provision` never writes `org_placement`** so a provisioned org resolves to nothing · key_store/model_config threading across 38+10 sites on an H2-style ratchet, never weakening `_resolve_org`'s fail-closed contract · the `ON CONFLICT (email)` 42P10 repair at `access.py:550`/`_common.py:599`). **🟢 build + R8-on-scratch · 🔴 EXECUTE against a real second org = §6 gate (H3's RLS promotion is a hard prerequisite — a second org before promotion has NO DB-level isolation).** Dispatch order (auditor-ruled): slice 6 first (reproduce the 42P10 red — it edits the same SQL string slice 2 rewrites), then 1+2+3 one branch, then 4 (done-when split into single-DB halves 2026-08-19), then 5 on its ratchet cadence. (2026-08-19) · ✅ **SLICE 6 BUILT 2026-08-19 — the premise held and was WORSE: the 42P10 fires at PLAN time** (verifier: fails at `EXPLAIN`, zero rows touched), so since migration 162 BOTH upserts failed on EVERY call — owner bootstrap was a total outage and every invite/sign-in approval raised. One-line repairs to `_BOOTSTRAP_OWNER_SQL` + `_PROVISION_MEMBER_SQL` (conflict target → `lower(email)`; S1-1 trailing fence byte-identical, now driven by a previously-unreachable mixed-case cross-org probe: rowcount 0, row stays org #1). NEW `test_app_user_upserts.py` (20 R8 tests, both shells, in the CI skip-guard) + a whitespace-proof bare-target ratchet (one argued allow-list entry: the Console's CITEXT plane). **Why nothing caught it: `test_owner_bootstrap.py` gates on `DATABASE_URL`, which no CI job sets — AND its own fixture carries the same bad upsert (4-passed-1-failed when actually pointed at a DB), making slice 2's "its tests stay green unedited" done-when unsatisfiable as written — recorded under slice 2 with both repair options.** Mutation-proven (revert → exactly 6 red; padded-whitespace plant → caught). (2026-08-19) · ✅ **SLICES 1+2+3 BUILT 2026-08-19 (batch 1b, one branch)** — migration **179** (three plpgsql callables: `provision_org_roles` / `provision_org_owner` / `provision_organization`; seeds nothing itself, replay-safe; **SECURITY INVOKER posture flagged in 179's header + the spec for H3 to resolve**), NEW `test_org_provisioning.py` (34 R8), the owner-bootstrap suite **re-gated onto `TENANT_LADDER_DATABASE_URL` and its fixture repaired** (argued deviation from slice 2's unsatisfiable "unedited" clause — the suite was 5-skips-forever with the slice-6 defect in its own fixture; M1 proves the re-gate has teeth), 178's seed split into seed-vs-callable (+2 fences). Verifier's own SQL: `provision_organization('fracktal', …)` → 1 org, 1 placement, 6 roles as a SET, admin holds `billing:purchase`, owner holds `*`, grant set EQUALS `default`'s role-for-role (20/19/11/2/7/1, set-diff empty both ways), re-run idempotent, `default` untouched, **cross-tenant owner move REFUSED and rolls the whole act back** (verifier's own mutation). 8 recorded mutations + 4 verifier mutations all bit; 156/0-skip; both shells; D36.3 fence (the literal is not re-pointed and reads no env). New board findings: `access.py:283` `_tables_missing` is a **process-global degraded-mode latch with no reset path** (a transient "relation does not exist" during a migration window disables access resolution for the process's life — needs its own ticket); the SECURITY INVOKER question travels with H3. ✅ **SLICE 4 OPERATOR ARM BUILT 2026-08-19** (branch `ws-29-mt1j-slice4-remediate`, same PR as the D46.6 remediation): required `deployment_label` on `POST /orgs/provision` + the `org_placement` write (`DO NOTHING`, `database_target` NULL, 404 unknown label / 409 different label / **400** missing field even with exactly one deployment seeded — ⚠️ **shipped as 422; became 400 when WS-31 CP-2c slice 1 landed 2026-08-19**, because a two-arm route cannot keep the field required at the MODEL, so the refusal moved into the handler and this fence was amended red-first in that same PR and re-mutated both ways) + the tenant seam `acb_common.provisioning.provision_local_organization` + the 4c projection clause; six CP-2a suites updated for the required field. **398 passed / 0 skipped** across the eight named suites on both real ladders (baseline 386); red-first 8 red (4a) and 5 red (4b/4c); six mutations, one of which found the item-3 fence blind to a `count(deployment)=1` fallback — that test now asserts BOTH arms in an emptied one-deployment world. ⚠️ Argued deviation: the seam takes `get_session_factory()`, not `get_db()` — a new `get_db()` site is red against `test_db_engine_seam.py`'s frozen H2 baseline of 111, and `tenant_session()` cannot bind a tenant this function is creating. **Independent verify: FAIL-then-repaired same day (discipline only — every functional clause re-derived PASS via the verifier's own SQL probes and its own minted deployment key; 5 replayed mutations all killed; fresh scratch DBs).** The four findings, all repaired in-round: the file banner still said "Slice 4 remains NOT BUILT" on the branch that built it (the CLAUDE.md §5 stale-mirror defect — both banner sites fixed); done-when 4b still demanded the `get_db()` idiom the shipped code argues against (clause amended in place, deviation preserved); two test docstrings taught the wrong idiom for the one seam decision the ratchet polices (fixed); the "stacked diff" concern was a stale-local-main artifact (GitHub PR #25 = 18 files/+1344, the true slice diff). **Two verifier observations RECORDED, not fixed:** (a) the 409 different-label check is read-then-write in one txn — two CONCURRENT provisions of a pre-existing UNPLACED org under different labels can both read no-placement and the loser 200s without moving or 409ing (needs a pre-slice-4 org + true concurrency; the Console is deployed nowhere; same shape as the rest of the route); (b) `get_session_factory()` is identity-fenced (`test_db_engine_seam.py:280-307`) but its SITES are not counted the way `H2_BASELINE_ELSEWHERE` counts `get_db()` sites — the deviation is sound here, and the un-ratcheted escape hatch is a standing board note. **Adversarial review: APPROVE, 3×P2 (none blocking), the reviewer's own live probes: refusal-order oracle DISPROVEN (operator auth resolves before body validation — a deployment key POSTing {} gets 401 not 422); 409 is on ids and its refusal is TOTAL (changed name/gstin rolled back, no audit row); a pre-slice-4 unplaced org self-repairs on re-provision.** P2s: (1) 🔴 `deployment_by_label` ignores `deployment.status` — provisioning onto a `retired`/`draining` box 200s (reviewer measured live) and the org lands unreachable (that box's key is gone) AND unmovable (no move route, 409 arm) — the fail-closed-outage shape re-entering by a different door; needs a one-line status predicate + fence, its own small ticket beside the placement-move ticket; (2) the `steps_done` 'placement' step is written into a column NOTHING reads (one INSERT, two test assertions, no production reader; partial arrays unreachable by construction) — the build box's resumability claim was struck at review, the resume path stays CP-2a's owed clause; (3) two anchor cites went stale INSIDE the PR (+13 shift from its own ProvisionRequest insert) — fixed at landing. Noted: `deployment_label: ""` is a 404 not a 422 (no min_length); the resolve suite's autouse `_parking` builds an undisposed engine per test (slow accumulation); `TestSliceFourTheSeamCaller` is the file's first COMMITTING class (exception to the rolls-back fixture contract, verified harmless — every row it leaves has a placement). **Remaining: slice 5 (key threading, ratchet cadence — MVP-relevant: a second org row breaks ALL LLM key reads until threaded).** (2026-08-19) · ◐ **SLICE 5 RATCHET ROUND 1 BUILT 2026-08-19** (branch `ws-29-mt1j-slice5-r1`): the ratchet (source gate, never skips) + the two-org R8 half (keys through the store's own `put` via 179's provisioning; A→A/B→B; untenanted read ""/write raises) + `settings.py`/`agent.py` converted to 0 untenanted. **Banked 11+10 → 9+1 — and the round's most important output is the BASELINE CORRECTION: the ticket's "38 sites" over-stated the blast radius ~3.5×** (AST-counted, verifier-reproduced: of 48 store-method calls, 33 are master-key encrypt/decrypt that never touch `_resolve_org`, 1 `_execute`, 3 process-global configure — only 11 resolve a tenant; the 38/10 greps stay as auditable ceilings). **H4 FINDING, owner-gated by name (§6 (f)): the live `/v1/chat/completions` path CANNOT be tenant-threaded** — `require_llm_api_auth` is a pure token check binding no identity (`current_tenant()` None by construction), and `configure_litellm` injects keys process-globally (org #2's save would overwrite org #1's for every caller — a recorded cross-tenant WRITE). Fix = a tenant-scoped API key, a credential-scope redesign. Four sites `# H4:`-marked, pinned at EXACT counts (a guessed-org "conversion" goes red). **⚠️ MVP consequence, verifier-measured on a 43-org simulation: with 2+ orgs and a restart, DB-stored provider keys load on NO path — completions survive ONLY via `_load_keys_from_env()`, so the box's `.env` MUST carry the provider LLM keys; and the lifespan's untenanted `put` raises BEFORE `configure_integrations()`, leaving non-LLM integrations unconfigured after every restart (fail-closed per D33 finding 3 — must not be "repaired"; the integrations casualty needs its own ticket).** Two vacuous fences found by mutation AND fixed in-round (operator-key-first seeding; the CI-guard matching a comment not the argument). Deferred: `integrations.py`'s 5 sites (round 2), the 33 encrypt/decrypt (no tenant dimension), the litellm/os.environ injection (same redesign as H4). (2026-08-19) · ◐ **SLICE 5 RATCHET ROUND 2 BUILT 2026-08-19** (branch `ws-29-mt1j-slice5-r2`): `routes/integrations.py`'s 5 sites threaded on round 1's idiom byte-for-byte (`organization_id=current_tenant()` from `gateway.db`), pinned at 0 in `CONVERTED_FILES`. **Banked 9+1 → 4+1, which now EQUALS `sum(H4_KEY_STORE_SITES)` + `sum(H4_BLOB_SITES)` — every **walker-visible** untenanted credential call in `apps/`+`packages/` is owner-gated by name, so slice 5 is at the FLOOR **of the set the ratchet measures** — ⚠️ *not* of the tree: `key_store.py` itself makes three untenanted `self.` calls the walker cannot see (`:420` `get`, `:433` `put` — a WRITE that raises and aborts `configure_integrations` mid-loop, `:472` `get_all`), same class and same owner act, itemised in the spec's round-2 box — absent the §6 (f) credential-scope act.** New terminal fence `test_every_remaining_untenanted_site_is_an_owner_gated_one` states that as SET equality, not a total (two totals can agree while naming different sites). **Pre-conversion behaviour measured, and the DELETE was the worst of the five:** `key_store.delete` with no tenant logs `delete_no_tenant` and *returns*, so `DELETE /integrations/keys` answered `{"ok": true, "deleted": true}` having deleted nothing once org #2 exists; `get_by_type` returns `{}` the same way (so `/integrations/status` renders "no keys stored"); configure's `put` raise was swallowed into `integrations.db_write_failed` while the route still reported success; `PUT /integrations/keys`'s raise surfaced as a 500. **Refused and marked in place, not repaired:** the `os.environ`/`_upsert_env_var` halves of configure/put/delete — one env var and one `.env` per deployment, so org #2's save overwrites org #1's for every caller and the delete's `pop` unsets it for every org (same class as `settings.py::_inject_env_into_litellm`; same redesign as H4). **R8 half extended to `credential_type="integration"`, and it earned its place:** dropping `organization_id` from `key_store.delete`'s or `get_by_type`'s `WHERE` leaves the ENTIRE structural half green — the call sites are correctly threaded and only the SQL underneath is wrong — and only the new two-org case goes red. Ratchet suite 22 → **25 tests, 0 skips**; §11 block run 156 → **181 passed, 0 skipped**; five mutations performed/measured/reverted (4 / 4 / 3 / 1 / 1 red). Remaining: the 4+1 H4 sites (owner act, not effort), `key_store.py`'s three uncounted internal `self.` calls (same owner act) and the 33 encrypt/decrypt (no tenant dimension). ⚠️ Also recorded, not fixed: an accessor **wrapper** (`routes/tasks/core.py:326`'s `_key_store()`, 6 importing modules / 15 call sites, all `encrypt`/`decrypt` today) would be invisible to the walker if it ever grew a credential read. (2026-08-19) · ⚠️ **SLICE 4 DISPATCH-AUDITED NO-GO 2026-08-19 → REMEDIATED the same day (docs only)** — seven blockers, all documentation, the decisive one: done-when 4a demanded an `org_placement` row while `ProvisionRequest` carries no deployment field, the `Operator` credential has no deployment identity and the Console ladder seeds **zero** `deployment` rows — unconstructible without an unmade design decision; 4b was self-declaredly vacuous; the tenant seam caller slice 3's box promised had no clause; CP-2c (D46, same day) proposed the auth answer slice 4 forbids. **Adjudicated as D46.6** (two arms, one per ticket: slice 4 = operator arm with required `deployment_label`, never-move semantics, sole-deployment heuristic forbidden by name, plus the tenant seam function `provision_local_organization`; CP-2c = deployment-key arm, capability growth stays §6 (f)/(h)); done-when re-split into 4a/4b/4c, each red-today; verification line repaired (the "end to end" promise struck, `test_deployment_resolve_cache.py` + `test_org_provisioning.py` added). **Dispatchable on re-audit GO.** (2026-08-19) · ✅ **SLICE 7 BUILT 2026-08-20** (branch `ws-29-mt1j-slice7`) — **the tenant-plane create-only guard 179 was missing** (found by CP-2c slice 2's re-audit): `provision_org_owner` (`179:222-229`) refused only email-belongs-elsewhere, NOT a fresh email claiming an existing slug → the tenant plane would make that email a co-owner of a stranger's org (the slice-1 Console P0's exact twin, latent — no production caller yet, but CP-2c slice 2 makes the seam the first writer for a user-supplied slug). Owned HERE (fixes 179's own invariant, D46.6). Built: migration **180** (`180_org_provisioning_create_only_guard.sql`, `CREATE OR REPLACE provision_org_owner`, forward-only R6, **179 byte-for-byte untouched**) adding the create-only guard (`WHERE lower(au.email) <> lower(v_email)`, mirroring `store.org_owned_by_other`; no-owner org completes, same owner idempotent case-insensitively) + **dedicated distinct SQLSTATEs** — email-elsewhere re-tagged `P1001` (was generic `P0001`), slug-owned-by-another `P1002` (custom class P1, NOT 23505 which the real unique constraints raise); the blank-arg and no-owner-role raises stay generic `P0001` and pass through raw. `provision_local_organization` now raises two DISTINCT catchable types (`OwnerBelongsElsewhere`/`SlugOwnedByAnother` under `ProvisioningRefused` — the translation slice 4 deferred), classifying on the driver's SQLSTATE ALONE (`_translate_refusal`), never message text. `access.org_owner_of(slug) -> owner_email | None` added beside `resolve_identity` (CP-2c pre-flight `SlugTaken`). **`test_org_provisioning.py` 54 passed/0 skipped, both shells**; red-first (180 off ladder → 179's unguarded body) reds the two hijack fences + both classification fences; 4 mutations all bit (guard revert; shared SQLSTATE; no-owner-role dedicated code; message-substring classifier). Structural invariants updated for R6 forward-only redefinition (owner path defined in 179+180; seeding callables stay singly-defined, D43-A). `test_an_address_already_in_another_tenant_is_refused` + `test_re_provisioning_grants_the_owner_role_exactly_once` stay green; related suites green (159). No change to 179/`deploy/`/`.env*`/`key_store.py`; R5 source gate green. 🟢 AGENT-SAFE (fixtures + real PG); execution vs a real org #2 stays H3-gated. **Hard prerequisite of CP-2c slice 2.** (2026-08-20) · ◐ **H3 REHEARSED ON SCRATCH 2026-08-22** (branch `ws-29-h3-rehearsal`, `pgvector/pgvector:pg16`): stood up a dedicated promoted catalog (full ladder + `generated/{01,02,03,04}` by hand, non-priv `acb_app` role, two orgs), RLS came up clean on **137/140** scoped tables. **Shipped:** `tests/unit/test_h3_rls_promotion_rehearsal.py` — the OWED MT-1i two-org isolation fixture (bound-A sees only A, unbound → 0, WITH-CHECK cross-tenant write refused) **+** the sign-in-brick characterization, **12 passed on real PG (10 DB-backed + 2 structural arm-checks), never skipped**; armed in `pr-check.yml`'s tenant-ladder skip-guard. `test_tenant_coverage.py::test_app_role_cannot_bypass_rls` **PASSES** as `acb_app`. **Brick CONFIRMED — sign-in does NOT survive phase 4:** the real `resolve_access`/`resolve_identity`/`ensure_owner_bootstrap` on an unbound `app_user` return no-access / `(None,None)` / a WITH-CHECK-refused write. Written up as an **OWNER DECISION** in `saas_multitenancy_handover.md` §H3.2 — **recommend Option B (H6 identity cutover before a clean phase-4 sign-in); Option A (app_user carve-out in `gen_tenant_migration`) prototyped on scratch as a bridge (un-bricks sign-in, but measured to make `app_user` cross-tenant readable)**. Gate finding §H3.3: `test_live_catalog_has_column_force_and_policy` needs a one-line fix (subtract `HOMONYM_BLOCKED`, which the generator already excludes) or it can never go green — proposed for owner ratification, NOT applied (classifier guards the fence). ⚠️ **Live promotion, the `EXEMPT`/app_user carve-out, and any H3↔H6 reorder ALL remain OWNER-GATE — nothing enacted; EXEMPT map + generated SQL + ordering untouched.** (2026-08-22) · ◐ **H6 SLICE 1 SHIPPED (dark) 2026-08-22** (branch `ws-29-h6-slice1`, stacked on the H3 rehearsal): the identity-shadow **dual-write** — `acb_auth.access.mirror_identity_membership` (best-effort, own session, mirroring `_record_signin_request`) called after both `app_user` write paths (`_BOOTSTRAP_OWNER_SQL` in `ensure_owner_bootstrap`, `_PROVISION_MEMBER_SQL` in `_common.provision_member`) — plus a **catch-up backfill** (migration **182**, re-runs 159's idempotent seed for every member added since). `org_membership` is **create-only** (`ON CONFLICT (organization_id, user_id) DO NOTHING`, no `SET organization_id`) so one email holds membership in TWO orgs and an identity is NEVER moved (§H6 done-when 2+3). **NO read moved; `app_user` reads + both upserts byte-identical** (the dual-write is additive, on a separate statement + session). Fence `tests/unit/test_h6_identity_shadow.py` (R8, in `pr-check.yml`'s skip guard). **Remaining H6 slices (the read cutover / done-when 1+4), the `IDENTITY_CUTOVER` flip, H3 promotion, the app_user carve-out and executing the backfill against prod ALL stay OWNER-GATE.** (2026-08-22) · ◐ **H6 SLICE 3a SHIPPED (dark) 2026-08-22, D48 ratified** (branch `ws-29-h6-slice3a-statusmirror`, stacked on slice 1): records D48 (§3) and builds the FORWARD status mirror slice 1 left open — `acb_auth.access.mirror_membership_status` (best-effort, own session, scoped UPDATE that never moves an identity) on `members.py`'s suspend/remove/reactivate + `provision_member`, plus reconcile migration **183** (`org_membership.status` ← `app_user.status`, `IS DISTINCT FROM`-guarded, idempotent). NO read moved; `app_user` writes byte-identical. Fence: `test_h6_identity_shadow.py` (R8). **Building the read cutover (slice 3b) DARK is AGENT-SAFE per D48; only the `IDENTITY_CUTOVER` flip, H3 phase-4 promotion, and running the 182 backfill on prod are OWNER-GATE.** · ✅ **H6 ORPHAN-CLOSURE SLICE SHIPPED (dark) 2026-08-22** (branch `ws-29-h6-orphan-closure`, off merged `origin/main`): closes all four §H6 CUTOVER-CHECKLIST orphans at the SOURCE — the prerequisite of slice 4 (RBAC re-key) + the flip, landing BEFORE both — because an active `org_membership` orphan (no `app_user`) becomes a wrong-ADMIT once RBAC stops gating on `app_user`, and reconcile 183 can never reach it (it joins the gone `app_user`). (1) **PURGE orphan** — `members.purge_member` now calls `acb_auth.access.purge_identity_shadow` post-commit: deletes ONLY the org's `org_membership` row (the active-shadow wrong-ADMIT source); it **NEVER** deletes the GLOBAL `user_identity` — deleting it on the human's LAST membership was a check-then-cascade cross-tenant erasure race (org A's purge CASCADE-wiping org B's concurrently-added membership), removed by construction (R7 fence `no-user_identity-delete-on-purge`); a membership-less identity is harmless (fail-closed: no org → no access) and re-used on re-join, its lifecycle/pruning deferred to slice 5's atomic mirror+prune. (2)+(3) **APPROVE-ROLLBACK + create-only over-count orphans** — both mirror calls moved OUT of `_common.provision_member` into its callers POST-COMMIT (`members.invite_member`, `access_requests.approve_access_request`), matching `update_member`/`remove_member`, so a concurrent-approve 409 (`_decide`) or a rolled-back caller txn commits NO active shadow. (4) **Un-pinned SQL** — `access._MIRROR_IDENTITY_SQL`/`_MIRROR_ORG_BY_SLUG_SQL` pinned byte-equal to `console_resolve`'s (a TEST import is not counted by `test_console_dependency_boundary`'s production importer cap). **SLICE ORDER CORRECTED: "atomic mirror + prune" was mis-filed as a "slice 5 (D48) terminal" item — it is THIS orphan-closure slice, landing BEFORE slice 4; §H6 + this row updated.** Fences: `test_h6_identity_shadow.py` `TestTheOrphanClosure` (shape + post-commit-ordering + constant-equality, no DB) + `TestThePurgeClosure` (R8, real PG) — 43/43 green on scratch; 154/154 across the four verify suites. NO read moved; `app_user` byte-identical; DARK. `IDENTITY_CUTOVER` flip / H3 promotion / prod backfill stay OWNER-GATE. (2026-08-22) · ✅ **H6 SLICE 3b — THE READ CUTOVER — SHIPPED (dark) 2026-08-22** (branch `ws-29-h6-slice3b-readcutover-v2`, off merged `origin/main`): the sign-in brick fix, behind new flag `IDENTITY_CUTOVER` (`acb_auth.access.identity_cutover_enabled()`, unset=OFF, fail-closed, `deps._refuse_llm_key_identity` env idiom; read server-side in `deps._with_resolved_access`, NOT the Next-side `CUSTOMER_CONSOLE_RESOLVE_ENABLED` — the two-phase resolve is entirely server-side in `acb_auth`). Flag ON → TWO-PHASE: the IDENTITY LEG (`resolve_identity` → new `_IDENTITY_LEG_SQL` over the RLS-EXEMPT `user_identity ⋈ org_membership ⋈ organization`, `status='active'`, the `console_resolve._READ_SQL` shape reused not forked) runs UNBOUND and `bind_tenant`s the resolved org BEFORE the ROLE LEG (`resolve_access`, bound, `app_user`-keyed, UNCHANGED — it stays the ACCESS authority, so a residual orphan is bind-with-no-access not a wrong-admit; RBAC re-key is slice 4). Flag OFF = byte-identical to today (same calls, order, SQL). Fences (R7/R8): `test_h3_rls_promotion_rehearsal.py::TestTheReadCutoverIdentityLeg` — flag-ON GREEN, flag-OFF RED (brick, EVEN with the shadow seeded), suspended-not-admitted, all through `resolve_identity`'s own call path as `acb_app` UNBOUND on the phase-4 catalog; `test_h6_identity_shadow.py::TestTheReadCutoverFlag` — DB-free flag switch (OFF→`app_user`, ON→exempt shadow) + default-OFF env idiom. **66/66 green across the two H-suites on scratch; 181/181 across the four verify suites, 0 skipped.** DEFERRED with reason (not the sign-in brick + RBAC-blocked): `org_owner_of`/`_HAS_OWNER_SQL`/`ensure_owner_bootstrap` (owner = an RBAC role in the FORCE-RLS'd `user_role`, no exempt-table equivalent — that IS slice 4) and `membership_of` (signup-path, active-only semantics belong with that path). NO read moves while OFF; `app_user` writes untouched; DARK. `IDENTITY_CUTOVER` flip / H3 phase-4 promotion / prod backfill stay OWNER-GATE. (2026-08-22) · 🔧 **H6 SLICE 3b REPAIR ROUND 2026-08-22** (same branch `ws-29-h6-slice3b-readcutover-v2`) — diff-review returned **P0 + 2×P2**, all closed DARK. **P0: the role leg was never actually RLS-bound.** `bind_tenant` sets only the `_TENANT` ContextVar; the GUC `app.tenant_id` phase-4 RLS keys on is applied ONLY by `tenant_session()`'s `set_config(..., true)` — but `resolve_access` read `app_user` on a RAW unbound session, so under phase-4 RLS + `IDENTITY_CUTOVER` ON EVERY member resolved `is_active=False` (total lockout — the brick the slice exists to fix, "working" only pre-phase-4 where the slice is pointless). Fix: ON+bound → `resolve_access` opens its read via `tenant_session()` (the ONE GUC seam, reused not forked, R5); OFF/unbound stays the raw session byte-identical. **The absent end-to-end fence let it through** — the old GREEN drove `resolve_identity` in isolation, never `resolve_access` under the bind. NEW `test_h3_rls_promotion_rehearsal.py::TestTheReadCutoverEndToEnd` (R8): the FULL `deps._with_resolved_access` two-phase path UNBOUND, flag ON → clean ACTIVE member is_active=True WITH real role (GREEN); mutation (role leg blind to the bind) → is_active=False (RED). **P2a: `_IDENTITY_LEG_SQL` `ORDER BY joined_at … LIMIT 1` could bind the WRONG org** for a multi-membership human (console memberships leave `joined_at` NULL → slug tiebreak) → now `LIMIT 2` + COUNT in `resolve_identity` (one binds; >1 with no host = deny/chooser `(None,None)`, MT-1f owns host selection); fenced R8 (two real memberships) + DB-free COUNT. **P2b: `/auth/me` `user_id` id-space divergence** (ON=`user_identity.id`, OFF=`app_user.id`) documented as an opaque per-human token, never an `app_user` FK (backend keys on email), at `resolve_identity`/`roles.UserContext.user_id`/`me.py`. **Write-brick note co-located into §H6** (`ensure_owner_bootstrap`'s startup INSERT into RLS-forced `app_user` is refused unbound under phase-4 — a still-open WRITE-brick on the slice-4 RBAC path, to handle before the flip). I001 nit fixed on `deps.py`. VERIFY: the four named suites **186 passed / 0 skipped** on scratch PG (`TENANT_LADDER_DATABASE_URL`); the H3 rehearsal suite **19 passed** incl. the 3 new fences. Touched code has 0 new `ruff` violations (pre-existing repo-wide RUF100/UP037 is a ruff-0.15.14 version drift). DARK; flip/H3/backfill stay OWNER-GATE. (2026-08-22) |4 RBAC RE-KEY EXPAND SHIPPED (dark) 2026-08-22** (branch `ws-29-h6-slice4-expand`, off merged `origin/main` @ aeef9399): the shared first PR of D48's RBAC re-key — migration **184** (`184_rbac_user_identity_rekey_expand.sql`) adds nullable `user_identity_id` to `user_role`/`user_permission_override`/`org_group_member`, BACKFILLS via the lower(email) bridge (RBAC.user_id → app_user → user_identity) and indexes it (R6 expand, `IS DISTINCT FROM ui.id`-guarded idempotent, FK `ON DELETE SET NULL` — the shadow nulls, never cascade-deletes a still-authoritative grant; RLS untouched, no new table so no `EXEMPT` row) + **DUAL-WRITE** on all five RBAC INSERT sites (`access.py` `_BOOTSTRAP_OWNER_SQL` by `lower(:email)`; `_common.set_roles`; `groups.add_group_member`'s `org_group_member` + center-access override; `members.set_member_overrides`) — same bridge, `app_user.id` stays authoritative, **no read moved**. New fence `tests/unit/test_h6_rbac_rekey.py` (structural + R8 on `TENANT_LADDER_DATABASE_URL`, in `pr-check.yml`'s ninth skip-guard slot): backfill bridges every row (NULL only where no identity), idempotent re-run = 0, and `set_roles`/bootstrap dual-write both columns to the same identity. §H6 authored: Slice-4 EXPAND done-when + the 15-site/5-module per-module READ-cutover block (5 TRICKY status/org reads flagged) + dependency order (EXPAND ∥ 3b; read-cutovers AFTER both land). The per-module READ cutovers, the CONTRACT drop of old `user_id`, the `IDENTITY_CUTOVER` flip, H3 promotion and prod backfill stay OWNER-GATE. (2026-08-22) · ✅ **SLICE 4 REPAIR (round 1) 2026-08-22** (branch `ws-29-h6-slice4-expand`): ws-verifier FAIL — the dual-write covered only the FIVE Python RBAC INSERTs and MISSED a SIXTH, live RBAC INSERT: `provision_org_owner` (the tenant-plane SQL, `180_...:153-155`) INSERTs the owner's `user_role` DURING provisioning, before any `user_identity` exists, so a freshly-provisioned owner had a NULL `user_role.user_identity_id` → NO roles once `IDENTITY_CUTOVER` flips the role leg (locked out of their own org, on a path the owner runs continuously). **Repaired via the Python mirror+bridge (NOT a SQL migration 185 — that would fork identity creation):** the provision caller `acb_common.provisioning.provision_local_organization` now reuses the ONE identity seam `acb_auth.access.mirror_identity_membership` for the owner (best-effort, own session, after the authoritative commit — no second `user_identity` writer), and that mirror now ALSO backfills the human's existing RBAC rows (`_bridge_rbac_to_identity`: scoped UPDATE per RBAC table via the lower(email) bridge, sets ONLY the still-NULL `user_identity_id`, idempotent, moves no grant). Because the mirror is called by invite/approve/bootstrap AND now provision, this GENERALIZES the timing fix — every RBAC-FIRST order (provisioned owner, bootstrap owner, any future) is bridged once the identity exists, in one place. `test_h6_rbac_rekey.py` extended: `TestTheProvisionOwnerBridgeMechanism` (structural), `TestTheProvisionOwnerIsBridged` + `TestRbacCreatedBeforeIdentityIsBridged` (R8, real PG). **186 passed, 0 skipped** across the four verify suites on scratch PG. §H6 corrected: the "exactly five, no sixth" write-site claim was wrong — sixth named, mirror-time bridge documented, property = "user_identity_id becomes correct for every RBAC row once the human's identity exists". Still DARK; no read moved, no RLS change, `app_user` byte-identical; no migration added (R1 moot); flip/CONTRACT/prod-backfill stay OWNER-GATE. (2026-08-22) · ✅ **SLICE 4 REPAIR (round 2) 2026-08-22** (branch `ws-29-h6-slice4-expand`): ws-verifier FAIL — the round-1 mirror-time bridge `_bridge_rbac_to_identity` UPDATEs the three RLS-FORCED RBAC tables on an UNBOUND session, so under real phase-4 RLS with the non-priv app role it matches **0 rows** (unset `app.tenant_id` → NULL → 0 visible) and silently no-ops (best-effort swallow) → the provisioned owner's `user_identity_id` stays NULL → lockout at cutover (SAME class as 3b's P0; re-verification missed it only because scratch `acb` has `rolbypassrls`). **Repaired:** the bridge now runs inside the ONE GUC seam `tenant_session()` (`SET LOCAL app.tenant_id`, no second GUC path) bound to the human's org (== `app_user.organization_id`), threaded from the mirror's already-resolved org (app_user is unreadable unbound — the H3 chicken-and-egg). The over-general "shadow tables are RLS-EXEMPT so no bind" R5 note is corrected (exempt covers the mirror's own `user_identity`/`org_membership` writes only, NOT the bridge's forced-table writes). Docstrings + migration-184 header + §H6 updated (bind requirement binds ANY H6 forced-table write path; cross-refs 179's "REVISIT AT H3"). **Closes the harness blind-spot** with a new R8 fence under the NON-PRIV `acb_app` role on the phase-4-promoted catalog (reusing `test_h3_rls_promotion_rehearsal.promoted`): `TestTheRealBridgeFunctionUnderForceRls` (real bridge, GREEN bound / RED if bind removed — the bypass-role tests are blind) + `TestTheBridgeSqlNeedsTheGucBindUnderForceRls` (bound GREEN=3 rows / unbound RED=0 rows→NULL); red-first confirmed by reverting the bind. **132 passed, 0 skipped** across the four verify suites on scratch PG. Still DARK; no read moved, no RLS change (generated/ untouched), `app_user` byte-identical; no migration added (R1 moot); flip/CONTRACT/H3-promotion/prod-backfill stay OWNER-GATE. (2026-08-22) · ◐ **H6 RLS-BIND HARDENING SHIPPED (dark) 2026-08-22** (branch `ws-29-h6-rls-bind-hardening`, off merged `origin/main` @ dba93340): the systematic RLS-binding audit's three agent-safe fixes for sites that read/write FORCE-RLS'd tables on an UNBOUND session (0 rows / refused writes under phase-4 — the class the 3b P0 + the slice-4 bridge already hit). (1) `resolve_session_access` — its participant/member fold (`chat_session_participant`+`org_group_member`+`app_user`, all FORCE-RLS'd) now reads via `tenant_session()` when `IDENTITY_CUTOVER` is ON **and** a tenant is bound (the request path — `deps` binds the resolved org); its two BACKGROUND callers (`executor._integration_authorizer`, `chat_fold._run_authority`) run with `current_tenant()` None → the raw unbound branch (never `TenantUnbound`, fail-closed to actor-only until H4 threads them). (2) `ensure_owner_bootstrap` — resolves the `default` org id from the EXEMPT `organization` table, then runs `_HAS_OWNER_SQL` + `_BOOTSTRAP_OWNER_SQL` inside `tenant_session(default_org_id)`, un-bricking first-owner bootstrap under phase-4 (the still-open write-brick §H6 flagged; the raw-SQL characterization stays RED by design). (3) THE RATCHET BLIND-SPOT FENCE — `test_db_engine_seam.py::test_no_new_unbound_factory_opens` pins every direct `get_session_factory()()`/`_get_session_factory()()` open in `apps/`+`packages/` to a reviewed per-file count (`_FACTORY_OPEN_ALLOW`: db.py=2, email.core=1, access.py=10, console_resolve=5, provisioning=3), the class the `get_db()` ratchet was blind to; a new unreviewed open goes RED + a non-vacuous detector test. R5 (reused `tenant_session`, no new GUC/connection seam); R7-named fences (`session-authority-bound-under-rls`, `owner-bootstrap-bound-under-rls`, the factory-open ratchet); R8 real PG under the NON-PRIV `acb_app` role on the phase-4-promoted catalog (reused `promoted()`). Fences: `test_h3_rls_promotion_rehearsal.py::TestResolveSessionAccessBindUnderForceRls` + `::TestEnsureOwnerBootstrapBindUnderForceRls` (bound GREEN / unbound-mutation RED). **3 mutations all bit** (each bind reverted → its GREEN goes RED; a planted factory open → ratchet RED). §H6 authored the PRE-FLIP RLS-BINDING CHECKLIST: the 3 fixes (done) + 3 OWNER-DECISION items NOT fixed here (new-org provisioning writes — `provision_org_owner`/`provision_local_organization` — need a migration or SECURITY DEFINER; `access_request` is FORCED but its writers are tenant-less → decide EXEMPT vs a bound writer; `reconcile_orphaned_runs` (`routes/workflows/service.py:628`) is a cross-tenant `workflow_runs` sweep needing a per-org loop or BYPASSRLS role) + the H4 dependency (~111 unbound `get_db()` background/service sites go dark at the flip unless H4 threads them). VERIFY: the five named suites **220 passed / 0 skipped** on scratch PG (`TENANT_LADDER_DATABASE_URL`); `test_owner_bootstrap.py` 8/8 (its fixture now pins the shared seam too, since bootstrap opens `tenant_session`). Touched code `ruff`-clean. DARK; no read moved, no RLS change (generated/ untouched), byte-identical with the flag off / session unbound; no migration added (R1 moot); the flip, H3 promotion, prod backfill + the 3 owner-decision items stay OWNER-GATE. (2026-08-22) **H6 PROVISION-RLS-BIND SHIPPED (dark) 2026-08-22** (branch `ws-29-provision-rls-bind`, off merged `origin/main` @ 098b805e): closes PRE-FLIP RLS-BINDING CHECKLIST item (b)1 (new-org provisioning writes) — was OWNER-DECISION, now FIXED (→ **2** owner-decision items remain). `provision_organization` creates the org row + `tenant_placement` (RLS-EXEMPT → land unbound), so the org EXISTS before the create act's FORCE-RLS'd writes (`org_role_permission` via `provision_org_roles`; `app_user` + `user_role` via `provision_org_owner`) — under phase-4 RLS on an UNBOUND session those refuse (WITH CHECK vs unset GUC), so provisioning a customer post-flip failed. **Migration 185** (`185_provision_org_rls_bind.sql`; highest was 184, re-check at merge per R1) CREATE OR REPLACEs `provision_organization` forward-only (R6; **179/180 byte-untouched**) to `PERFORM set_config('app.tenant_id', v_org_id::text, true)` right after the org id is known and BEFORE the forced writes — the ONE GUC seam (`SET LOCAL`, txn-scoped, reaches the PERFORMed sub-functions). **SECURITY INVOKER preserved** (SET LOCAL preferred over SECURITY DEFINER — no privilege escalation, the "REVISIT AT H3" note 179 flagged). No RLS policy change (`generated/` untouched); no new connection site (the bind is inside the existing function/txn, R5). R7-named fence `provision-under-force-rls`; R8 real PG under the NON-PRIV `acb_app` role on the phase-4-promoted catalog (reused `promoted()`): `test_h3_rls_promotion_rehearsal.py::TestProvisionOrgBindUnderForceRls` — GREEN the real 185 provisions unbound (org+placement+roles+owner all land), RED the no-set_config mutation refused (row-level-security violation, SQLSTATE 42501) + writes nothing, the owner app_user/user_role write bound-GREEN/unbound-RED, and 180's create-only guard (P1002) still holds under the bind. VERIFY: `test_org_provisioning.py` + `test_h3_rls_promotion_rehearsal.py` + `test_h6_rbac_rekey.py` **105 passed / 0 skipped** on scratch PG (`TENANT_LADDER_DATABASE_URL`). DARK; building + scratch-testing = AGENT-SAFE (D43-C), EXECUTING a real customer provision + the H3 flip stay OWNER-GATE. (2026-08-22) · **MT-1f SLICE 1 BUILT 2026-08-24, then WITHDRAWN by D51 the same day** — subdomain workspaces dropped entirely (owner directive); the host parser/flag/redirect REMOVED; the reserved-slug vocabulary + gate STAY (CP-2c 4a); the workspace chooser is **MT-1k** (slice A built in WS-35, B/C later). B1–B7 remain the record of the withdrawn design in `saas_multitenancy.md` §11 MT-1f. The gate is now **SPLIT**: code 🟢 AGENT-SAFE, and four runtime acts 🔴 OWNER-GATE (wildcard `*.metorite.com` DNS · DNS-01 wildcard cert + Caddy block · the **`AUTH_URL=https://app.metorite.com` pin, a HARD flip prerequisite** because `auth.ts:219` is `trustHost: true` with no pin anywhere · the flag flip) — §6. Carries the CP-2c live-defect fix: the signup slug gate now refuses the reserved labels (`api`/`app`/`www` were registrable), one vocabulary shared with the host parser. (2026-08-24) · 🔧 **MT-1f SLICE 1 REPAIR ROUND 1 — 2026-08-24** (same branch `ws-29-mt1f-slice1`; ws-verifier PASSED, diff-review returned **P1 + P2**, verifier logged 6 accuracy items — all closed). **P1: signed-out on a workspace host was a post-flip DEAD END.** Slice 1 left that caller on the workspace host's own `/signin`, and under ruling **B2** (Auth.js cookies host-only on the apex — the OAuth `state`/`pkce` checks too) plus **B3**'s `AUTH_URL` pin the callback returns to `app.` without the cookies `acme.` wrote → Auth.js `InvalidCheck`; with the pin absent it is instead an unregistered `redirect_uri`. And because B2 means **signed-out is the only state a workspace host can be in today**, every workspace hostname would have been a sign-in page that cannot sign anybody in from the moment of the flip. Fix = B5's existing design applied to one more state: the signed-out case **also 302s to `https://app.metorite.com<path+query>`**, still with **zero** gateway calls. **done-when 6 is AMENDED in `saas_multitenancy.md` §11** with the cookie/callback chain written out; it **strengthens B4** — the `Location` is built from a fixed host, so real and invented slugs are now byte-identical rather than identical-modulo-the-hostname-you-typed, and `proxyWorkspace.test.ts`'s old block (which blessed the broken 307 shape) is rewritten. Recorded consequences: a signed-out **API** call on a workspace host now 302s rather than 401s (one host, one answer), and `/api/auth/**` still passes through above the branch (unreachable from any served page, but a hand-typed URL still reaches a flow that cannot complete). **P2:** `SignUpForm.tsx` imported `RESERVED_LABELS` but hand-copied `SLUG_RE`; it now imports both and the cross-language pin gained an import/no-local-literal scan, so the shape rule has ONE home across three consumers. **Accuracy items folded in:** `auth.ts:163`→**`:389`** (the real exact-string flag site; `:163` is an OTP comment) in the spec clause, the `subdomain.ts` docstring and `subdomain.test.ts` — ⚠️ the verifier also named "the work_plan board entry", which does **not** carry that anchor (its only `auth.ts` citation is `:219` for `trustHost`, re-verified correct) · CP-2c done-when **8a** corrected twice: the check is **`currentIdentity()`**, not `await auth()` (the branch's own fence asserts NO `await auth()`), and its motivation is **defence in depth** — `proxy.ts` already 307'd signed-out `/signup`, so the form was never reachable · the `test_the_invisible_cases_are_indistinguishable` citation fixed · **"the only HOST reader" narrowed to the only REQUEST-host reader** and `nextUrl.hostname` added to the fence regex (`\b` could not match between `host` and `name`, so the alias evaded the sweep; client-side `window.location.host` in `whatsapp/lib/callAudio.ts` is deliberately out of scope) · **`WORKSPACE_BASE_DOMAIN` added to the §6 flip checklist as act (e)** — unset on a non-`metorite.com` apex it fails safe and silently · reserved set **extended additively 13→21** (`assets`, `auth`, `billing`, `dev`, `login`, `operator`, `staging`, `ws`) in both languages, safe only while signup is dark. VERIFY: `npx tsc --noEmit` clean and `npx vitest run` **2526 passed / 0 failed, 116 files** (baseline 2522; +4 net — one new fence-regex case and three from the rewritten signed-out block). Still DARK; all five runtime acts stay OWNER-GATE. (2026-08-24) |

| — | **Future modules roadmap** (KB · Marketing · Support · dashboards-colour · Builder/Workflows slicing rule) *(named 2026-08-09, D21)* | 🔴 not dispatchable | `specs/future_modules_roadmap.md` | No WS rows until each earns a §1-contract spec; Dashboards colour lands in WS-15's acceptance; the Builder/Workflows visibility-tier-at-creation rule binds reviews now (D12). |
| WS-30 | **Subscription Console — customer-facing billing surface** *(minted 2026-08-09)* | 🔴 MT-2 first · **SC-4/SC-5 specced 2026-08-13** | **`specs/subscription_console.md`** | Manage-only at launch: **Centers & add-ons panel · users × Centers seat grid (D23)** · credit monitor · seat writes under D19.3's hard cap · role presets (D24.5) · change-request flow (fulfilment 🔴 OWNER-GATE during silo phase). Sequencing: MT-2 tables → SC-1 → SC-2/SC-3 → SC-4 with MT-4. Business inputs answered (D19 + D23 + **D24** — framing closed); blockers: MT-2's substrate and the MT-2/MT-3 ticket contracts. (2026-08-10) · **SC-4g minted 2026-08-18 (D42)**: operator-issued discount codes are part of the checkout's launch contract — customer zero completes the whole purchase at ₹0 through the real rails (100% path writes identical records with NO provider order; capture leg rehearsed on test-mode keys; invoice shows the discount). (2026-08-18) · ⚠️ **SEQUENCING CORRECTED 2026-08-18 — the "MT-2 tables → SC-1 → …" chain above is STALE and must not be dispatched from**: D32.2 moved the substrate to WS-31 and it is BUILT (`plan_catalog`/`org_subscription`/`seat_grant`/`credit_ledger`, seeded D23/D24 ladder). The 2026-08-18 checkout audit found the REAL blocker was different: **the payment-provider seam had no ticket anywhere** (CP-8 is Operator Console — three docs mis-cited it as the Razorpay ticket) and the Console had no customer-authenticated write path (`cc_live_` is read-only by CP-3's design). Remediation (branch `ws-30-checkout-mint`, audited NO-GO → repaired → GO-NARROWED → nine blockers B1–B9 repaired → confirmed GO): **CP-9 "Payment provider seam (Razorpay)" minted in `customer_console.md`** (payment_order state machine, integer paise, org key gains exactly two value-inert writes + two reads — value moves ONLY on a signature-verified webhook [terminal-state rule is the money guard; Razorpay sends different event ids for one capture] or an operator-issued `cc_disc_` code [the code is the pre-authorization; the redeem→fulfil path is the ONE allow-listed carve-out, contents-pinned]; `can_pay` joins the lifecycle capabilities — capture never transitions org status, reinstatement stays the operator's §6(c) act); **SC-4a re-scoped to the seeded priced Center ladder** (credit packs deferred behind D19.2), **SC-4g completed** (split-key codes, redemption schema, pre-GST basis-point discount, `<reason>:<ref>` one-vocabulary references, ₹0-vs-paid equivalence fence with named exclusions, {unknown, wrong-org} = one non-oracle refusal); invoice DOCUMENT bounded behind SC-5b/5c (checkout completes without it, owner sees the sequencing note). New board findings: `GET /api/billing/summary` is reachable by any signed-in member while its docstring claims an admin gate (merged f1fcca4f, needs its own ticket); `/me/billing` returns no seats (SC-1a). **Dispatchable now: CP-9 substrate + SC-4g server-side (done-whens CP-9 1–17, SC-4g 1–8), fixtures/fake-provider only.** Held back: SC-4a UI + write proxies (billing:purchase capability), operator issue surface, invoice document, the four-item live-flip set (all owner-gated). (2026-08-18) · ⚠️ **SC-4a SURFACE AUDITED NO-GO 2026-08-19 → remediated (branch `ws-30-sc4a-surface-mint`) → confirmed GO-NARROWED.** The decisive finding: **the paid path has no browser→provider hand-off anywhere in the corpus** (CP-9 creates Razorpay Orders, no payment link, no key_id/order_id on the wire, `OrderView` strips provider ids by fence) — so **the LAUNCH checkout is narrowed in writing to the ₹0/discount path** (pick package → order → 100% `cc_disc_` code → fulfilled — exactly customer zero's journey, D42) and the paid hand-off is **SC-4h**, a named deferred joint WS-31/WS-30 clause (key_id exposure · order_id on the wire · the OrderView fence amendment; SC-4g clause 2's owner capture-rehearsal stays owed). Also fixed: done-when 5a rewritten over shipped fields; `billing:purchase` = **admin-only** seed via the 133 pattern, registered with `user_management_contract.md` §3 as an argued deviation, and **born unheld in every non-`default` org** — named as the SAME defect class as the recorded "new org gets zero roles" second-org blocker, owned by the org-provisioning ticket; the BFF gate fence is a two-entry explicit list with the open read-proxy excluded BY NAME (a sweep would go red on merged code); buyer identity discarded on the write path = named residual (WS-31 column ask). The remediation itself found **F-I: no route exposes the priced `plan_catalog` to a customer credential** — launch done-when 1 was unbuildable; the catalog read is `customer_console.md` §6 item (f), made buildable 2026-08-19 (route `GET /billing/catalog`, `can_pay` auth NOT `can_use_ai`, `price_paise` single money field, two fences). **Dispatch order (auditor-ruled, two slices): (1) WS-31 item (f) catalog read; (2) SC-4a narrowed surface (done-whens 1, 2, 5a, 6, 7, 8, 9 — migration 178-at-build seed + CAPABILITIES entry + two gated BFF write proxies + checkout UI under `purchaseEnabled`), GO the day slice 1 merges, re-anchor only.** (2026-08-19) · ✅ **SC-4a NARROWED SURFACE BUILT 2026-08-19** (branch `ws-30-sc4a-surface`): `billing:purchase` (permissions.py + **tenant migration 178**, seeded onto `default`'s admin only, manager excluded, R8-fenced by `test_billing_purchase_capability.py` — 10 tests on the tenant ladder, both-shell proven); four BFF proxies over the existing org key (basket rebuilt slug+quantity — no browser price reaches the wire, fenced; Console refusals relayed verbatim EXCEPT 503, which would name Razorpay env vars, and upstream 401 → 502 so a rotated key never reads as "sign in"); `Checkout.tsx` + pure `checkout.ts` (ladder from the catalog route — price-scan fence; 5a re-read discipline; the refusal partition with unknown≡wrong-org from one constant); all inside the `purchaseEnabled` arm (hoist fence). **Verify+review found 1 blocker + 2×P1 + 3×P2, all repaired same day red-first:** the tenant fence was red from Git Bash (resolve-vs-abspath drive case — normcase both sides, whole-path compare kept); **the BFF gated on the CALLER's capability while binding the DEPLOYMENT's key with no org comparison** — a second org's admin could have bought INTO the key's org once seeding parameterizes → `requirePurchaser` now compares the caller's `/auth/me` slug against the key's org via the Console's `whoami` (fail-closed 503, failure never cached, mismatch copy never names the key's org — verifier's 30-assertion harness + mutation proved 6 repo fences live); upstream-401 relay; focus effect; completeness sweep (a fifth billing route goes red); anchor 1438. Final: **tsc silent · 2327 vitest · 117 payments · 10 tenant · 0 skips · both shells.** Deferred honestly: DESIGN_SYSTEM §8 theme-switch pass is review-only (no DOM runner — owed at the owner's first hands-on look); summary read-proxy gap unchanged (excluded BY NAME, its case now executable); 404-discriminator coupling to Console copy recorded (CP-9 machine-readable code = the fix); SC-4h still the paid hand-off. **Fracktal's ₹0 purchase path is now CODE-COMPLETE end-to-end** (catalog → order → code → fulfilment → seats), pending: org-provisioning ticket (capability + roles in a non-default org), the owner's Razorpay test account (H-14), and the flip set. (2026-08-19) · **SC-1a seats PANEL BUILT 2026-08-20** (branch `ws-30-sc1a-seats-panel`) — the customer-facing seats block on `/settings/billing`, the second half of the post-signup MVP (after WS-31's `GET /me/seats` read, merged #37). Renders per-plan purchased/assigned/available/oversubscribed VERBATIM from the read (zero client arithmetic; pass-through proven by an inconsistent row rendering [2,5,9]); a NEW BFF hop `api/billing/seats/route.ts` mirroring `catalog` (server-side identity, no client-named tenant, R11); `oversubscribed` surfaced as a `Badge tone=warning`. DESIGN_SYSTEM primitives only, conformance green, theme-switch-ready; fence `settings/billing/lib/seats.test.ts`; tsc 0 / vitest 2351. Verify PASS, review APPROVE. Repaired a P2 in-slice: guarded the render against a malformed Console 2xx (`Array.isArray(seats?.plans)`) so a bad body cannot white-screen the whole billing page. **This carves the SC-1a SEATS BLOCK out of the "🔴 MT-2 first" gate** — the seats block ships now; only SC-1a's ENTITLEMENT half (active|trial|locked, upsell/savings) stays MT-2-deferred. P2 (PR body): the seats read is session-gated not admin-gated (inherited posture shared with sibling billing reads, its own ticket); no loading/error-vs-empty distinction on the panel until the Console is live. 🔴 OWNER: inert until the Console is deployed (D47); no flag flip. (2026-08-20) · **SC-2a manage-seats TRANSPORT BUILT 2026-08-21** (branch `ws-30-manage-seats`) — the gateway-tier path for the seat WRITE (§9 residual 7 RESOLVED to gateway tier). Browser → Next hops `app/api/billing/seats/{assign,release}` (proxyToGateway, NO deployment key, body `{member_email,plan_slug,source}` only) → gateway `routes/seats.py` (`POST /seats/{assign,release}`, session `actor_email` via get_current_user, `_FORBIDDEN_BODY_KEYS`→400, `is_wired`→503-not-silent) → two new `console_resolve` funcs (the ONE Console client; deployment key stays gateway-side, no `org_slug`) → the item-(h) `seat_admin` door. Load-bearing fence: the `console_resolve` importer allow-list grown **2→3** red-first. No migration; `gateway.test.ts:232` stays green (key never in Next); R11 (browser cannot write as another org/actor — dropped at Next, 400 at gateway, org derived Console-side). 59 gateway-side / vitest 2361 / tsc 0. Verify PASS, review APPROVE (no findings). 🔴 OWNER: granting `seat_admin` to the REAL gateway key (§8 gate 8) + live write (§8 gate 4) + deploy. **Next: SC-2b — the assign/release UI (needs the owner surface admin-gate call: reuse `billing:purchase` vs a dedicated `seats:admin`).** (2026-08-21) · **SC-2b manage-seats UI BUILT 2026-08-21** (branch `ws-30-sc2b-manage-ui`) — the members×seats management surface on `/settings/billing`, the LAST piece of manage-seats. Admin-gate resolved to **`billing:purchase`** (owner call; no `seats:admin` minted; a COURTESY gate — the real authz is Console-side via SC-2a's transport). NEW `api/billing/members/route.ts` read hop (verbatim mirror of the seats hop, no deployment key, R11) + `lib/manage.ts` view-model (roster + assign/release, ZERO client seat arithmetic — counts are the read's; `canAssign` reads the server's clamped `available`) + `ManagePanel`/`MemberRow` beside `SeatsPanel` (design-system primitives; on success refetches `GET /me/seats`; cap-409 `buy_more` verbatim; idempotent). Fences: `manage.test.ts` (20) + `members.test.ts` + the checkout-sweep EXCLUDED, all red-first; tsc 0 / vitest 2388 / conformance 36. Verify PASS, review APPROVE. **manage-seats is now COMPLETE end-to-end** (see seats + members → assign/release → the UI), all dark. P2 (PR body): a no-op write shows no UI feedback (prints nothing, NOT a false success) — fix with a per-member holdings column (deferred backend follow-up on §6 item (i)); the roster read is session-gated (any member can enumerate colleague email/role/status via the API — rides the existing "any signed-in member" hardening ticket, not cross-tenant). 🔴 OWNER: grant `seat_admin` to the real gateway key (§8 gate 8) + live write (§8 gate 4) + deploy. (2026-08-21) · **SC-2c MINTED + BUILT 2026-08-24 — the invite surface + the notification email** *(D49; branch `ws-30-invites`)*: 🟢 **AGENT-SAFE, dark** — `src/lib/inviteEmail.ts` (builder + `sendInviteEmail`, IMPORTING `resendSender`/`emailOtpFrom` from `emailOtp.ts`, never a second transport), `api/admin/members/invite/route.ts` (a shadow over the `/api/admin/[...path]` catch-all for this one path — the catch-all is NOT special-cased), and the `/settings/members` `InviteDialog` switched onto it with the "it does not send an email" copy struck. Gate: **`MEMBER_INVITE_EMAIL_ENABLED`**, default OFF, exact-string `"true"`, ANDed with a present `RESEND_API_KEY` (the `isEmailOtpConfigured` idiom) — the flag gates **the email only**; the Console member write is CP-2f's and is dark-by-reach. 🔴 OWNER: flipping that flag on a live deployment · inviting a REAL member into a live org (§6 below, `customer_console.md` §8 gate 4). Seven done-whens with named fences in `subscription_console.md` SC-2c. |
| WS-31 | **Customer Console — the subscription, seat and AI-metering engine** *(minted 2026-08-12 as "Platform Control Plane", D32 · renamed 2026-08-18, D41.1 — the path/env/spec mapping lives there; this row's history keeps pre-rename names)* | ◐ **CP-0 ✅ · CP-1 ✅ · CP-2a ✅ · CP-3 ✅ · CP-4 ✅ — MERGED in #442 (`4934eb8`) 2026-08-14, DEPLOYED** (`HEAD is now at 4934eb8c`, `Migrations complete (0 applied, 172 already recorded)` — correctly zero: `infra/platform/` is the Control Plane's own ladder and the deploy does not apply it, so **`platform_api` is on the box but inert**) | **`specs/customer_console.md`** | The central service `§0.9.2` always named, extracted from each deployment: org registry + placement · plan catalog/subscriptions/**seats purchased vs assigned vs available** · per-org `llm_api_key` · the **AI Router** (tier→model binding, rate card, `usage_event`, `credit_ledger`, balance gate) · Operator Console. **D32 reverses `saas_multitenancy.md` §3.1** (banner in place) on an argument §3.1 never weighed — under §5.1 silos, metering inside CC puts the rate card and the balance on the *customer's* box. **D15 untouched**: tenancy is still a ROW, deployment still a placement. **Absorbs MT-3 whole** and pulls MT-2/MT-4 forward of their §11.1 position — WS-30 becomes a *client* of this service, not a reader of CC-local tables; WS-16 Phase E is the same mechanism (per-member caps as a policy against the org pool). Model access reworked: **customers never see a model**, tiers are the only vocabulary, the picker leaves the product, a bare model id 400s instead of being coerced. Sequencing CP-1→CP-8; **CP-4 (Router pass-through, unpriced, flag OFF) before CP-6 sets a rate card** — price on measured burn, not estimates. 🔴 Gates: the `GATEWAY_INTERNAL_TOKEN`/`LITELLM_MASTER_KEY` split is a hard prerequisite for per-org keys and is the **owner's** redeploy; deploying the service, live Razorpay keys, real entitlement/credit edits and issuing a production `cc_live_` key are all OWNER-GATE. Open (commercial, non-blocking): AI-only SKU, trial credits, auto-top-up threshold (⚠️ **cap it below ₹15,000** — D33.4b). · ⚠️ **RE-ORDERED 2026-08-12 by D33** (`specs/saas_operations_doctrine.md`): **CP-0 runs first** — sign-in is pinned to one Microsoft Entra directory and auth **fails OPEN** when unconfigured (`workbench/control_plane/src/auth.ts`), so **today the product can onboard exactly one customer: us**, and a mis-provisioned box is open. Neither was covered by any existing ticket. **CP-2a** (signup + idempotent provisioning + GST fields at signup) added — no signup route exists in the app tree at all. Sequence is now CP-0 → CP-1 → CP-2 → CP-2a → **CP-2b** → CP-3 → CP-4 → CP-5 → CP-6 → CP-7 → CP-8 *(CP-2b inserted 2026-08-18; **CP-4b** is owed out of order — CP-6 shipped before it and it must land before the first Router caller)*. · ✅ **CP-0 BUILT 2026-08-12** (multi-directory providers + fail-closed posture in a pure `authPosture` module; 16 fences, **verified red-first** — restoring the old semantics fails exactly one test and only that one). · ✅ **CP-1 BUILT 2026-08-12**: `infra/platform/` is the Control Plane's **own** migration ladder (NOT `infra/postgres/`, which `apply_migrations.sh` replays into the tenant DB and `gen_tenant_migration.py` scans to demand RLS — both wrong for a deliberately cross-tenant plane), `apps/services/platform/`, **79 tests of which 34 run against a real Postgres 16 (R8)**. New engine site declared in `test_db_engine_seam.py::_ALLOWED_SYNC` with its reason (R5(b)) — the ratchet caught it, as designed. Service **fails closed** without `CONTROL_PLANE_OPERATOR_TOKEN`; its DSN has **no default** so it cannot silently reach the tenant database. Rate card seeded **UNPRICED** on purpose — CP-4's unpriced pass-through measures real burn before CP-6 sets prices. ✅ **CP-3 BUILT 2026-08-12 — but only after independent verification returned FAIL and it was rebuilt.** The first attempt put `/usage/record` under *organization-key* auth; the verifier measured, on a live database, that a negative `billed_credits` became a **positive** ledger delta (989 → 100,989 credits — a customer-reachable credit-minting endpoint) and that a globally-unique `request_id` let one tenant silently suppress another's charge while `recorded:false` leaked as a cross-tenant existence oracle. It also showed the log fence was satisfied by `getMessage()` alone and so missed both `extra={}` and `exc_info` leaks, and that **CI never set `CONTROL_PLANE_DATABASE_URL`, so every R8-gated fence was skipping while reporting green**. All fixed: three auth schemes (operator / internal / org key), the org key is **read-only**, the Router's internal token writes the meter, quantities floored in both the API model and CHECK constraints, idempotency scoped `(organization_id, request_id)` (migration 003), the log fence reads the whole record, and `pr-check.yml` now runs a Postgres service **plus a guard that fails the job if the platform suites skip**. ⚠️ **Standing lesson: a passing suite proved nothing here — the verifier is what found all of it.** · ✅ **CP-4 BUILT 2026-08-12** — the Router: `POST /v1/chat/completions`, org-key authenticated, tier resolved from `tier_binding` (a provider swap is one row, not a deploy to every customer), response returned **unchanged**, one `usage_event` per call, **unpriced on purpose** so CP-6 sets the card against measured burn. ⚠️ It does **not** reuse `acb_llm` as the ticket said: that package's key store reads the TENANT database, so reusing it would put our provider credentials on a customer's box — the Router carries its own encrypted `provider_credential` table (migration 004). Metering is best-effort and never fails the completion. 132 platform tests, 87 DB-gated. · ✅ **CP-2a BUILT 2026-08-13** — account management: the lifecycle is ONE state machine (`platform_api/lifecycle.py`) read by sign-in, the Router, seat writes and the console, because four copies drift and the permissive copy gives away product. **`suspended` keeps login working while locking features** (a customer who cannot log in cannot pay you) and **`deleted` is reachable only from `cancelled`**, so no operator action can destroy data without the export window — enforced by the transition graph, not by remembering. Provisioning now writes the trial `org_subscription` and a resumable `provisioning_run`. 172 platform tests. · ✅ **Where it runs is DECIDED — D47 (2026-08-19): the production VPS, own unit + env, webhook-only public surface, move trigger = second deployment; deploying it stays §6(a)** — `specs/customer_console_infrastructure.md` §5 item 4 (Firebase disqualified on technical grounds in §3: no partial unique indexes, no server-side SUM; the schema depends on both). · 🔴 **A LADDER DEFECT THAT WAS GREEN LOCALLY AND RED ONLY ON REAL POSTGRES, found by opening #442 and fixed in it (`d8f52e9`)**: the five R8 suites each transcribed their own tuple of `infra/platform/00N_*.sql`, and three stopped at 003, so `test_platform_api.py` built a schema two migrations behind the code and answered `column "client_ref" of relation "usage_event" does not exist` — **inside an assertion about billing**, several steps from the missing migration, which is what makes a mirror worse than no list (CLAUDE.md §5). It had been wrong since 004 landed, and the suites carrying the stale copy were the ones that would have caught it. `tests/unit/_platform_ladder.py` now READS the directory, sorted on the leading integer (`010` precedes `002` lexically), refusing an empty ladder and duplicate numbers (R1). Fence: `test_no_platform_suite_transcribes_the_ladder_by_hand`, globbed not listed — its own first draft carried a five-name tuple, i.e. the same mirror one level up — and proved by mutation. (2026-08-14) · ✅ **CP-2 was ALREADY BUILT — status drift corrected 2026-08-18**: the spec header still said "CP-2 spec only" while all five done-when clauses were met on main (anchors post-CP-6: `main.py:603` summary · `:635` seats · `:675` release; pinned in `test_customer_console_seats.py:59`, `test_customer_console_api.py:142`, `test_customer_console_sql.py:183/217`). Residual doc debt: nothing pins "no surface recomputes seats locally" or per-write audit rows. · ◐ **CP-6 MECHANISM BUILT 2026-08-18** (branch `ws-31-cp6-credit-gate`): rate-card resolution (`resolve_rate_card`, newest row in effect) → rating via the ONE pure `credits.rate_call` → ledger draw at metering; the 402 balance gate (`decide_spend`, absolute grace 100, none for trials) and the 403 per-run breaker (`decide_run_ceiling`, ceiling 500 over migration 003's partial index) pre-flight `/v1/chat/completions` — **both refusals behind `CUSTOMER_CONSOLE_SPEND_GATE`, OFF by default, now registered in §6 with its flip-order warning** (price the card first, by owner act, never via migration). **219 CC tests on a real Postgres 16, 0 skips (R8)**; the R7 no-balance-UPDATE fence proven by FIVE mutations (implementer 3, verifier 2 fresh ones incl. a .ts write and a scratch 006 column); adversarial review then caught the implementer's own test-narrowing and the whole-table unpriced-card fence was RESTORED red-first — it is now the tripwire holding two OPEN money invariants inert: (a) **BYOK zero-rating unbuilt** (`_rate_completion` cannot see which credential served the call; a BYOK org would be double-charged the day a card is priced), (b) **the gate pre-flights one CREDIT_QUANTUM, not the call's cost** — a single huge priced call can vault the grace floor, and concurrent draws are unsynchronised (bound: grace + N_inflight × max-call cost). Both must close before any real price (the fence goes red on a priced ladder either way). Known P2s carried: gate reads the org's whole ledger per call (fine dark; revisit before flip), two 403 body shapes coexist on the route (auth string vs breaker dict), `credits_required` shows the quantum probe for a zero-balance org. NO new migration (006 still free). **Next per the 2026-08-18 audit: mint CP-2b** — §5.2's sign-in resolve wiring has NO ticket and three undecided questions (which credential — the operator token is FORBIDDEN on tenant boxes per `main.py`'s own note; R11 shape — body slug is the forbidden pattern under tenant auth; unreachable-CC posture — guessing "admit" recreates D33.1). Also owed: CP-4b streaming ticket or CP-4 amendment (flag named in its done-when exists nowhere), CP-5 "CC" disambiguation, CP-2a step-kill test, CP-1 clause-3 rewording. (2026-08-18) · ✅ **THAT DOCUMENTATION SLICE IS DONE (2026-08-18, branch `ws-31-cp2b-mint` — specs + this board only, no product code).** **CP-2b MINTED** with all three undecided questions answered as agent-proposed defaults the owner may overrule (D16/D17 convention): **(a)** a FOURTH auth scheme, the **deployment key** `cc_depl_…` — issued per deployment, capability set exactly `{resolve}`, stored in a `deployment_key` sibling table (owner is a `deployment`, not an `organization`) reusing `keys.py`'s split-key pattern verbatim; the operator token is forbidden on a tenant box by `main.py:946`'s own note, and the org key cannot serve a POOLED box because the org is the *answer*; **(b)** the request carries the deployment-verified EMAIL and the key and **no org** — R11 at its strongest reading ("the caller makes no tenant claim"), with the existing operator+`org_slug` shape kept legal **for the operator scheme only** (that credential has no tenant to infer, so naming one is an operator act, not an override) and a deployment key presenting `org_slug` refused **400**, never ignored; **(c)** **fail closed, degrade bounded** — uncached + Console unreachable = refused with *temporarily unavailable* copy (never "access denied"; D33.1's lesson), cached proceeds within `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS` (proposed 900) and is refused past `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS` (proposed 86400). ⚠️ **Corrected 2026-08-18 — this row said "so `suspended`/`deleted` land within the TTL", which is FALSE:** the TTL bounds anything only while the Console is **reachable**; on a partitioned box the real bound is `MAX_STALENESS` (24h), a `deleted` org's user included. The honest bound is a **pair** — TTL when reachable, `MAX_STALENESS` when not — and a cached answer already carrying a **dead** state applies immediately at any freshness (`deleted` refuses sign-in; `suspended`/`cancelled` lock features with login open, via the one `lifecycle.capabilities_of()`), because a dead state in cache is a fact, not a stale hint. The isolation claim is stated so the pooled case cannot falsify it: **placement, not organization, is the boundary** — a deployment key resolves an email iff its membership org's current `org_placement.deployment_id` is that key's deployment, and the three invisible cases return one indistinguishable shape (no cross-org existence oracle — CP-3's `recorded:false` lesson applied ahead of a verifier). Cache maps onto migration 159's projection with two verified gotchas — 159's `org_membership` column is **`user_id`**, not `user_identity_id`, and its `user_identity.email` is `TEXT` + `lower(email)` index, not `CITEXT` — plus two new nullable columns (`org_membership.resolved_at`, `tenant_placement.cc_org_status`); **role is deliberately NOT projected** (a registry role is not a permission role — a second grant vocabulary is forbidden). Caller lands in the gateway identity seam (`acb_auth/access.py:249`), not the Next BFF, because the write is to the tenant DB. Ten testable done-whens, each naming its future fence; R8 mandated; migration numbers taken at build time (R1 — CC ladder is 001–005, tenant ladder tops at 176; neither number is written as a future absolute). **CP-4b MINTED** carrying CP-4's unmet streaming clause (`stream: true` = **501**, `main.py:818`), and **CP-4's done-when AMENDED**: its "behind a flag, default OFF" flag **exists nowhere** in the tree — ship-dark is real but comes from **absence of a caller**, `CUSTOMER_CONSOLE_SPEND_GATE` is the enforcement flag that does exist, and the Router-exposure flag arrives with the first caller ticket. Four smaller corrections landed dated, none silently: CP-6's §6 clause "~10%" → the shipped absolute `grace_credits = 100`; CP-5's three ambiguous "CC"s (meaning CommandCenter pre-D41, now colliding with Customer Console) expanded to "Metorite (the tenant deployment)"; **CP-1 clause 3 reworded** — it demanded CC tables in `gen_tenant_migration.EXEMPT`, but that generator scans `infra/postgres/` alone (`:63`/`:175`/`:200`) so the entries would print as stale AND push the 12-entry map at `test_tenant_coverage.py:77`'s ≤15 tripwire, i.e. degrade the security review it belongs to; **CP-2a's step-kill clause marked OWED, not struck** (only whole-run re-provision idempotence is tested, `test_customer_console_lifecycle.py:155`) with the future fence named. All **seven** §8 gates now registered in §6 (six were spec-only), including the new deployment-key gate and D42's discount-code gate. (2026-08-18) · ⚠️ **CP-2b was RE-AUDITED the same day: the mint PASSED but dispatch of the BUILD returned NO-GO on five blocking corrections. All five landed 2026-08-18 (same branch, docs only) and CP-2b is now DISPATCHABLE.** **C1** the freshness bound was self-contradictory and overstated (fixed above, plus the dead-state rule and org re-placement named as an `invalidate()` trigger — `acb_auth/access.py:73-98`). **C2 seat allocation was farmable:** the gateway trusts `X-User-Email` under the internal token by design (`deps.py:393` *"still trust Next.js"*, `:402-404` *"whoever holds the internal token can already assert any X-User-Email"*), so internal token + deployment key ⇒ resolve arbitrary addresses ⇒ burn a customer's cap to 409 and lock their staff out. New clause 11 fences resolve to **the sign-in path with a session-derived email only** (structural fence over the single caller site), and the **residual internal-token blast radius is recorded as a named accepted risk** rather than silently inherited — it predates this ticket and the `GATEWAY_INTERNAL_TOKEN` split (§6) is what narrows it. **C3 clause 1's fence could not pass:** `/health` is deliberately unauthenticated (`main.py:383`), so "parametrised over `app.routes`" was red on day one; the exempt set is now exact — `/health` **plus FastAPI's four auto-generated doc routes** (`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`, which are `starlette.routing.Route`, not `APIRoute`), the parametrisation is *every `APIRoute` except `/health`*, and the doc routes serving a cross-tenant service's full OpenAPI schema unauthenticated is logged as a **pre-existing finding, not fixed here**. **C4 the response schema was unspecified and §5.2 still documented the forbidden shape:** new clause 12 pins success (organizations list: id, slug, placement target, lifecycle status, seat outcome — **never `role`**, that is registry/billing vocabulary, D12), empty (`200 {"organizations": []}` and **no `identity_id`**, which would be the existence oracle clause 5 forbids), at-cap (409 + the shipped `buy_more` payload, `seats.py:135-146`), dead-org-placed-here (403, `main.py:542-547`), and states the operator shape is unchanged (`main.py:594-600`); §5.2's code block now shows **both** schemes. **C5 multi-org admit/refuse was undefined:** the Console returns the set and allocates nothing, and **the deployment REFUSES sign-in** with honest copy — fail closed rather than guess, since `resolve_identity` returns exactly one `organization_id` (`access.py:358`) so admitting requires a choice nobody made; the chooser stays a non-goal. **C6 (non-blocking) landed too:** migration 159's projection is named the **cache of record** with `acb_auth`'s 60s per-worker `_cache` (`access.py:37`) demoted to a read-through in front of it (a 24h ceiling is unenforceable in a per-process dict); `tenant_placement.cc_org_status` is **renamed and moved** to `organization.registry_status` (placement is placement, D15; and `cc_` now parses two ways post-D41) with both new columns noted as sitting on `gen_tenant_migration.EXEMPT` tables (`:72`,`:75`) so **zero** EXEMPT rows are added; the nullable-**without**-default choice is argued as a deviation from R6's letter (NULL means *never resolved*, a state fail-closed sign-in must see) rather than mis-cited as compliance; §7's R8 line now binds CP-2b and CP-4b and its stale "170" absolute is replaced by take-at-build-time. (2026-08-18) · ◐ **CP-2b CONSOLE HALF BUILT 2026-08-18** (branch `ws-31-cp2b-console-half`, commits `7e2fcb6c` + repair `ec4ce57e`): the FOURTH auth scheme (`cc_depl_<prefix>_<secret>`, capabilities exactly `{resolve}`, `keys.py` reused verbatim), migration **006** `deployment_key` (CC ladder now 001–006), and the two-arm `/registry/resolve` — operator arm keeps its `org_slug` shape, deployment arm takes `{email}` only with `org_slug` = **400 never ignored** (R11), org derived from `org_membership ⋈ org_placement` with **placement as the boundary**, the three invisible cases byte-identical `200 {"organizations":[]}` with no `identity_id`, dead-org-placed-here 403, multi-org = allocate nothing + refusal (chooser later), one shared allocation path (operator arm's inline duplicate DELETED, not mirrored). The clause-1 fence derives the open-route set from the new `AUTHENTICATING_DEPENDENCIES` registry. **Review found 2×P1, both fixed red-first and independently re-verified:** (P1-1) `_allocate_core_seat` ignored lifecycle — a suspended org gained seats on sign-in; `seats_locked` is now a REQUIRED kwarg (suspended/cancelled = report-never-write; verifier's own SQL probe: zero rows written, back-to-active allocates). (P1-2) seat-cap check-then-insert race — `store.lock_seat_capacity()` takes `pg_advisory_xact_lock(hashtext(org:plan))` BEFORE the count; verifier's own 2-thread race: 10/10 iterations `[200,409]`, and its MUTATION (lock → no-op) oversubscribed 9/10 — the lock is load-bearing. **45 new R8 tests (`test_customer_console_resolve.py`) + 219 existing, 0 skips, real PG 16; blocking ruff clean; `pr-check.yml` skip-guard hand-list 5→6.** New board findings (recorded in spec, NOT fixed): a removed member still resolves and burns a seat; `deployment.status` is ignored by its keys; the **operator** arm still passes `seats_locked=False` unconditionally (pinned by `test_customer_console_lifecycle.py:170` — wants its own ticket); `POST /billing/seats` does NOT take the capacity lock (same race class open); `seat_rows`' `effective_from <= now()` is clock-sensitive (scratch container steps backward ~0.7s/29s → spurious 409 with `buy_more.purchased == 0` fingerprint ~1-in-95; environment, not branch — verifier confirmed mechanically and by backdating). **OPEN: the deployment half** — gateway caller at the `acb_auth` identity seam, the two `CUSTOMER_CONSOLE_RESOLVE_*` settings, tenant projection columns (`org_membership.resolved_at`, `organization.registry_status`) via a tenant migration, clauses 6/7/11, multi-org refusal copy on the sign-in surface. (2026-08-18) · ◐ **DEPLOYMENT HALF: audited NO-GO → ticket remediated twice → re-audited GO, now DISPATCHABLE as one slice (2026-08-18, branch `ws-31-cp2b-deployment-remediate`, commits `6bbb4751` + `0319f13e`, spec §6(d)–(k)).** First audit found 7 blockers (B1–B7): the mandated `lifecycle.capabilities_of()` is unimportable from the tenant deployable (the root venv at `pyproject.toml:27-30` makes an import GREEN in pytest and WRONG in the deployable); the 159 projection is a self-declared shadow copy whose general read is WS-29 H6's; no tenant Postgres in CI (R8); the Next→gateway hop unfenced; clause 7 promised feature-locks the non-goals forbid; resolve client/env names/refusal-copy mechanism homeless; 4 stale anchors. Remediation answered all 7 (capabilities booleans ride the resolve RESPONSE, computed Console-side — the box never imports the Console nor branches on a lifecycle string, fenced by `test_console_dependency_boundary.py`; projection read scoped to THIS entry point's fallback only, `_ACCESS_SQL`/`resolve_identity` untouched, H6 named; artifact homes: `acb_auth/console_resolve.py` +httpx with ONE caller `gateway/routes/signin.py` — deliberately NOT behind `resolve_access`, which has six callers incl. `rooms.py:215`'s per-participant map = farmable seat burn; Auth.js `signIn` callback refuses via string-return, mechanism verified in `@auth/core` 0.41.2; `errorCopy.ts` gains `ConsoleUnavailable` + `WorkspaceChooserRequired`). Hostile re-audit then found 7 MORE (B-a..B-g), sharpest: exporting `DATABASE_URL` in CI turns ON two tests that fail by construction (superuser DSN = MT-1c's, un-promoted `04_policies.sql` = MT-1b's) → new suites key on **`TENANT_LADDER_DATABASE_URL`**, set in-process (safe: `conftest.py:16` snapshot + module-scope skipif, both verified); `capabilities.sign_in` can NEVER be false in a 200 body (the dead-org refusal is the 403) → §6(j) four-outcome table (200-admit / 403-dead caches `{"sign_in": false}` in a THIRD projection column `organization.registry_capabilities JSONB` / multi-org refuses caching nothing, residual named / empty = genuine AccessDenied); projection write had NO join key across the two databases → §6(k) slug key (`130:39` TEXT UNIQUE NOT NULL), no-local-row = skip+warn+admit-on-fresh-answer, row creation is provisioning's; CI image must be `pgvector/pgvector:pg16`; the existing `migrations` job (pr-check.yml:235-291) named with divergence argued; clause-11 residual restated **internal-token-ALONE**; the `auth.ts ⇄ lib/gateway.ts` import cycle named and the inline-bearer escape forbidden. Final verdict GO with 5 non-blocking nits carried to the build (2nd skip-guard grep for the new suite's own skip string; reset `db.py:46-47` singletons on fixture SETUP; §6(j) row ii should name its error code; strike lines 1615-17's superseded reading; `Awaitable` cite is :198). Build slice = Console-side capabilities commit + `console_resolve.py` + `routes/signin.py` + `signIn` callback/errorCopy/extended sweep + 4 settings fields + 3-column tenant migration (number at build) + `_tenant_ladder.py` + `test_deployment_resolve_cache.py` + `test_console_dependency_boundary.py` + 2nd CI Postgres. Live `cc_depl_` issuance & env writes stay OWNER-GATE §6(f). (2026-08-18) · ✅ **CP-2b DEPLOYMENT HALF BUILT 2026-08-18 — CP-2b is COMPLETE (both halves), dark behind `CUSTOMER_CONSOLE_RESOLVE_ENABLED` (unset = OFF, registered in §6(f))** (branch `ws-31-cp2b-deployment-half`, 14 commits): the wire is `signIn` callback (`auth.ts`) → `headersActingAs()` → NEW gateway route `POST /signin/resolve` (internal-bearer; the ONE holder of the deployment key) → NEW `acb_auth/console_resolve.py` (httpx, read-through cache, projection write) → the Console's deployment arm, which now carries `capabilities:{sign_in,write_seats,use_ai}` per admissible org from the one `capabilities_of()` — the box branches on outcomes+booleans, NEVER a lifecycle string (structural fence). Migration **177** (three nullable no-default columns on EXEMPT tables; next free 178). §6(j) gained rows v (seat-cap 409 refuses, caches nothing) and vi. **Verification was adversarial and it EARNED it — five defects found post-build, all repaired red-first same day:** (F1, verify-FAIL) the callback armed itself off `CUSTOMER_CONSOLE_URL` — a variable billing already sets — so merge would have live-armed the hop; → the dedicated flag, callback no longer reads that var (source-fenced). (P0, review) flag ON + half-provisioned gateway **silently admitted everyone** (seat cap = shipped no-op, zero log) → the route itself refuses when `is_wired()` false (`ConsoleUnavailable` + ERROR log; NEW `test_signin_resolve_route.py`, 7 tests — nothing had tested the route). (P1) Console 5xx/401 showed the "isn't authorized" copy and ignored fresh cache → ≥500/401/408/429 are outages (cached admits, uncached `ConsoleUnavailable`). (P1) a 403 wrote `{"sign_in":false}` onto the ORG row → **permanent lockout of every member**, unrecoverable → dead state bounded by the staleness ceiling, self-heals ≤24h on a 200 (follow-up recorded: move the refusal to `org_membership`). (P1) unprovisioned-slug return skipped `_forget_others` → an org MOVE left the old admission live 24h → forgets before returning. (P2) the age floor was fail-open for future stamps → signed age + measured 60s skew tolerance (comment softened to run-varying evidence per verifier N1). **Final counts, independently re-verified: 271 Console + 50 tenant (real PG on :5443, `TENANT_LADDER_DATABASE_URL` — deliberately NOT `DATABASE_URL`, which would arm 2 tests that fail by construction, MT-1b/1c's) + 11 route/boundary, 0 skips; vitest 2256; tsc + blocking ruff clean; verifier's own harnesses 26/26 + fence mutations bite.** Carried findings: internal-token-ALONE drives resolve with the box's key (accepted, narrowed by §8 gate 1 split); multi-org caches nothing (older admitted row survives outage ≤ceiling, named); org-row refusal blast radius inside the ceiling (colleagues of a 403'd person refused until heal — the `org_membership` follow-up removes it); `capabilities_of` maps unrecognised status strings to `deleted` (Console-side, own ticket); skip-guard tee swallows a failing (not skipping) suite's exit; 10 pre-existing Windows-local unit failures identical on main. **NEXT for WS-31:** Console deployment wiring (`customer_console_infrastructure.md` — where it runs is 🔴 owner decision), org provisioning + `key_store._resolve_org(None)` second-org fix, entitlement enforcement, CP-4b streaming; the sign-in wire goes live only by the owner's §6(f) acts (issue key, set env, flip flag). (2026-08-18) · ◐ **CP-9 "Payment provider seam (Razorpay)" MINTED 2026-08-18, spec only** — the ticket three docs mis-cited CP-8 for (all four citations fixed; CP-8 stays Operator Console + reconciliation). Sequence is now CP-0→…→CP-6→**CP-9**→CP-7→CP-8, and the checkout record lives in the WS-30 row (this remediation was audited twice; details there). Dispatchable: the substrate half + SC-4g server-side, fake-provider/fixtures only. (2026-08-18) · ✅ **CP-9 SUBSTRATE + SC-4g SERVER SIDE BUILT 2026-08-19** (branch `ws-31-cp9-substrate`): migration **007** (5 tables, integer paise, 13 arithmetic CHECKs all SQL-proven to refuse; next free 008); `payments.py` (state machine — all 25 pairs asserted vs an independently-transcribed graph; `RazorpayProvider` httpx/no-SDK; `FakeProvider` with the real HMAC; the ONE `fulfil()`); six routes; `can_pay` appended to lifecycle (capture NEVER transitions org status — reinstatement stays §6(c) operator work); `cc_disc_` codes; the ₹0 path (zero provider calls, same fulfilment records, field-by-field equivalence fence); {unknown, wrong-org} refusals byte-identical. **The gauntlet earned its keep three times:** verify-FAIL round 1 (clause-2 fence asserted nothing in 10/25 cases; the "harmless orphan" residual was FALSE in its paid direction — a capture on a replaced provider order keeps the money at a WARNING; the transitive walk's DEPTH was unfenced — depth-1 left all 105 green) → repaired, walk now self-tested on a synthetic 3-hop graph, `fulfil`'s writer set contents-pinned, CP-8 owns unattributed receipts. Then adversarial review found the **P0 everything else missed: a failed first payment attempt drove the order into terminal `failed`, so the customer's RETRY captured money with nothing granted, swallowed at INFO** — repaired red-first (a failed ATTEMPT no longer closes the ORDER — nothing drives `failed` now, structurally fenced; the refusal arm splits on status: capture-after-abandonment alerts at ERROR with amount + both provider ids) + 2 P2s (redeem-attempt log now fires on FAILING attempts — the §9.3(6) rate-limit metric; non-ASCII signature header 400s instead of an unauthenticated 500). Final: **115 payments + 392 across ten suites, 0 skips, real PG**; verifier re-reproduced the P0 pre-repair and re-verified all fixes with its own harnesses. **The org-key transitive value fence found CP-6's pre-existing metering draw as a second value edge — exempted BY NAME (`METERING_EXEMPTION`, count+contents fences), NOT narrowed; 🔴 owner input 6: ratify or overrule.** ⚠️ **Owner sequencing fact: the ₹0 flow 503s without the three `CUSTOMER_CONSOLE_RAZORPAY_*` vars — a test-mode Razorpay account is now a launch prerequisite (H-14).** Findings recorded not fixed: term-restart on mid-term repurchase (SC-5); unbounded order quantity (BIGINT overflow → 500); order-list N+1; capture-vs-redeem race verified serialized (FOR UPDATE both paths). Held back: SC-4a UI + `billing:purchase` proxies, operator code-issue surface, invoice doc (SC-5b/c), flip set. (2026-08-19) · ✅ **item (f) catalog read BUILT 2026-08-19** (branch `ws-31-catalog-read`): `GET /billing/catalog` on the `can_pay` door (suspended reads, deleted 403s — verifier proved the two doors have not converged), exactly `slug·name·kind·price_paise·sort_order` over `plan_catalog WHERE active`, paise via the ONE `payments.paise()`, org-invariant (byte-identical across orgs), no migration, auto-joined both the default-deny parametrisation (21→22 cases) and the org-key money walk. 117 payments tests + 395 block, 0 skips; combined verify+review (proportionate to a zero-input read-only route): PASS, no P0/P1, one P2 count-in-prose fixed at landing. Known reliance recorded: `paise()` raises on NULL — safe only while `plan_catalog.price_inr` stays NOT NULL (docstring names it). **SC-4a narrowed surface (slice 2) is GO on merge, re-anchor only.** (2026-08-19) · 🔲 **CP-2c (self-serve signup flow) + CP-2d (2FA/OTP, documented-deferred) MINTED 2026-08-19 (D46, owner directive — "let us first work on the entire sign-up flow")**: the form over CP-2a's ✅ API — `/signup` entry, Google-attested identity, GST at signup, ONE orchestration driving Console `/orgs/provision` AND migration 179's `provision_organization` (which has NO production caller today), first user = owner both planes, dark behind `SELF_SERVE_SIGNUP_ENABLED` (minted; existed nowhere). **Dispatch order: MT-1j slice 4 FIRST — ✅ its operator arm landed 2026-08-19, so CP-2c is unblocked** (CP-2c step (b) IS the Console↔tenant seam, `org_placement` hole included), then CP-2c, then WS-33's page ships its CTA. New gate §6(h)/§8 gate 8 (flag flip + deployment-key capability growth to `{resolve, provision}` — the proposed answer to D43-2's open sub-question). Named interaction amended in-ticket: CP-2b's empty-resolve = AccessDenied would strand signup once the resolve flag arms — under the signup flag it becomes a limbo session (AccessGate everywhere, `/signup` usable). Live set stays owner: Console deployed (§8 gate 2; WHERE = D47, the production VPS) · key issuance · both flags. (2026-08-19) · ⚠️ **CP-2c DISPATCH-AUDITED NO-GO 2026-08-19 → REMEDIATED the same day (branch `ws-31-cp2c-remediate`, docs only), five blockers all documentation:** B1 the deployment-key arm cannot exist without amending slice 4's mutation-proved required-label fence — ruled IN WRITING (D46.6 item-1 amendment: model → `str | None` on the ResolveRequest precedent, operator missing-label 422→400, the fence amended red-first in slice 1's PR); B2 the Console-provision client was homeless — ruled into `acb_auth/console_resolve.py` (the ONE client/env-read owner) with the single-importer AST fence growing to exactly two named importers; B3 done-when 7 cited a §6(j) row that did not exist — **row vii AUTHORED** (limbo = the signIn CALLBACK's decision under the signup flag, Next-side only, gateway byte-identical, caches nothing) and the flag's THREE readers/two env files named (the CP-2b F1 lesson); B4 refusal shapes ruled apart (outcomes = 200 admit:false with codes SignupDisabled/ConsoleUnavailable/AlreadyMember; shape violations = 400); B5 GSTIN got its structural regex (15-char, checksum deferred, argued) and registered-state-required its enforcement site (gateway route, 400 MissingState). **Auditor-ruled slice order now IN the ticket: (1) Console deployment-key arm → (2) gateway route + client → (3) callback limbo branch → (4) form UI — never one slice.** Scope reductions found: GST fields already thread (main.py:139-140 → store.py:75-94); `deployment_key.capabilities` has no CHECK so slice 1 needs NO Console migration; pr-check already provisions both Postgres services for the cross-plane kill test. (2026-08-19) · ✅ **CP-2c SLICE 1 BUILT 2026-08-19** (branch `ws-31-cp2c-slice1`) — the Console **deployment-key provision arm**, done-when 6 + 8's Console half. `POST /orgs/provision` is now two arms on the SAME `deployment_or_operator` dispatcher `/registry/resolve` takes (unchanged — it was already generic over the capability): `auth.PROVISION_CAPABILITY` + `_provision_dependency` + the `ProvisionCaller` alias, **registered in `AUTHENTICATING_DEPENDENCIES`** (the audit named that registration by name; removing it is mutation-proved to make the clause-1 fence report `{'/health', '/orgs/provision'}`). A `{resolve}`-only key is **403 and the refusal is LOGGED** — new event `deployment_key.capability_refused` at warning, carrying the capability and the key PREFIX, never the secret; the raise was silent before, so a key reaching for a capability it was not issued for was unobservable. A `{resolve, provision}` key provisions onto **itself** and the same key then resolves the org. **Carries the D46.6-item-1 amendment**: `ProvisionRequest.deployment_label` → `str | None = None` on the `ResolveRequest.org_slug` precedent, both arms' rules in the handler, operator missing-label **422→400**, deployment-arm presented label **400 never ignored** (R11, refused on shape before the value is read); slice 4's mutation-proved no-inference fence amended **red-first in the same PR** and re-mutated BOTH ways — the original `count(*)=1` fallback in `deployment_by_label` AND a *working* sole-deployment guess in the handler, which is the risk the amendment itself created; both red. **Zero migrations on either ladder** (`deployment_key.capabilities` is TEXT[] with no CHECK) and **zero new engine sites**. **409 passed / 0 skipped** across §7's ten Console suites on real Postgres 16 (baseline 402, +7 new R8 tests in `test_customer_console_lifecycle.py::TestTheDeploymentKeyProvisionArm`), 31/31 on the dependency-boundary + engine-seam fences, 89/89 on the two tenant-ladder suites; six mutations all killed; ruff parity with the branch point (identical findings by file and rule). CP-2b's parametrised clause-1 fence learned the capability-refusal shape (403 at `/orgs/provision`, not 401) — the property is unchanged, only the vocabulary. **Two agent-proposed defaults recorded in the build box for owner overrule (D16/D17 class):** the refusal-log event name/level/fields, and `control_audit.actor` distinguishing the arms (`deployment` vs `operator`) since `_audit`'s own contract is that calling every act "operator" misattributes the one class of write we most need to tell apart. **Inherited unchanged by name:** `resolve_deployment_key`/`deployment_by_label` still ignore `deployment.status` (WS-29 slice-4 reviewer's P2-1, its own ticket) — now reachable by one more door, which raises its priority without changing its owner. **Issuing or widening a REAL key stays §8 gate 7 — the code is built, the act is the owner's.** Slices 2/3/4 remain OPEN; nothing of the signup FORM exists. · ⚠️ **SLICE 2 DISPATCH-AUDITED NO-GO 2026-08-20 → REMEDIATED same day (docs only), three blockers one cluster** ("what the two planes do when an email or slug already exists"): the gateway `POST /signup/provision` orchestration is ruled **tenant-plane FIRST** — step 0 a read-only pre-flight (`access.membership_of(email)` → `AlreadyMember` naming the caller's own org, NOT the seat-burning resolve path), step 1 the tenant seam `provision_local_organization` whose one-email-one-org guard is the hard refusal (email-belongs-elsewhere / slug-taken → nothing written on the Console), step 2 the Console mirror which cannot permanently refuse a fresh tenant-born slug (only transient `ConsoleUnavailable`, resubmit converges). Closes: blocker 1 (`SlugTaken` = 4th outcome code, the slice-1 `409 "slug unavailable"` → browser); blocker 2 (AlreadyMember detection mechanism + org display-name source named); blocker 3 (two-plane atomicity). ⚠️ **RE-AUDIT 2026-08-20: NO-GO on that remediation's FALSE PREMISE — 179 does NOT refuse slug-owned-by-another, so tenant-first would HIJACK** (a fresh email claiming an existing slug is silently made co-owner — the slice-1 P0's tenant twin; `provision_org_owner` guards only email-belongs-elsewhere). Corrected: the tenant create-only guard is minted as **MT-1j slice 7** (WS-29; migration 180 + typed raises + `access.org_owner_of`), owned there because it fixes 179's own invariant (D46.6), and a HARD PREREQUISITE landing BEFORE CP-2c slice 2. Step 0 now does two pre-flight classifying reads (`membership_of`→AlreadyMember, `org_owner_of`→SlugTaken). **Dispatch: MT-1j slice 7 → CP-2c slice 2.** (2026-08-20) **Independent verify: FAIL→repaired (doc sweep only — 45/45 own-probe PASS, 7 mutations killed, 8 stale sentences swept). Adversarial review: 1 P0 + 1 P1 + 1 P2, all repaired before merge.** 🔴 **P0 the review caught that 409/0 AND the verify both missed — a LIVE privilege-escalation:** the deployment-key arm inherited slice 4's idempotent-on-slug re-provision, so a `provision` key POSTing an EXISTING slug with a different `owner_email` was made `owner`/`active` + given a seat in a STRANGER's org (200, measured live). Repaired at the CONSOLE door (R11 — slice 2 feeds it a user-supplied slug from an unauthenticated form): the arm now **creates only, never joins** (`store.org_owned_by_other`, CITEXT-safe; same-owner retry + ownerless-crash-resume still complete). P1: the 409/200 split was a global-slug existence oracle → collapsed to one bare `409 "slug unavailable"` (agent-proposed default, owner may overrule; residual "slug taken: yes/no" is inherent, flagged). P2: the ninth stale sentence swept. **444 passed / 0 skip after repair** (four new red-first security fences; supervisor re-ran the ownership-guard mutation — disabling it re-opens the hijack). (2026-08-20) · ◐ **CP-2c SLICE 2 BUILT 2026-08-20** (branch `ws-31-cp2c-slice2`) — the gateway `POST /signup/provision` route + the Console-provision client, done-whens 2–5. NEW `access.membership_of(email)` tenant read (beside slice 7's `org_owner_of`); NEW `gateway/routes/signup.py` mounted in `main.py`, mirroring `signin.py`'s posture (BFF-internal bearer, session-email-only, IDENTITY-ONLY — never binds a tenant, uses `get_current_user` not `_with_resolved_access`); two-plane orchestration **tenant plane FIRST** (0a `membership_of`→`AlreadyMember` naming only the caller's own org, 0b `org_owner_of`→`SlugTaken` naming nothing; step 1 the hard guard, its two typed raises the TOCTOU backstop→the same two codes, `OwnerBelongsElsewhere` re-reading `membership_of` to populate `org_name`; step 2 the Console deployment-key arm→`ConsoleUnavailable` on transient failure only, since a fresh tenant-born slug cannot be permanently refused). Flag `SELF_SERVE_SIGNUP_ENABLED` minted as a GATEWAY settings field (exact-`"true"`, default OFF→`SignupDisabled`, fail closed); GST 400s (`MissingState`/`InvalidGstin`) threaded to the Console org row. Console-provision client added to `acb_auth.console_resolve` (the ONE Console httpx client, sole reader of the deployment credential) and `test_console_dependency_boundary.py`'s allow-list grown ONE→TWO importers (`signin.py`+`signup.py`, reason restated); `errorCopy.ts` gains `SignupDisabled`/`AlreadyMember`/`SlugTaken` (`ConsoleUnavailable` reused). **Slug SHAPE validated at the route before step 0/1/2** (`MissingSlug`/`InvalidSlug` **400s**, DNS-label-safe `re.fullmatch` `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`, agent-proposed default D16/D17, forward-compatible with MT-1f `<slug>.metorite.com`) — **slice-2 P2 repaired at landing 2026-08-20**: a missing/blank slug had reached `provision_local_organization("")` → 179's generic `P0001` → the broad step-1 `except` → a FALSE **200 `ConsoleUnavailable`** ("retry" for a permanent shape error), and `"a b"` reached the cross-plane join key unvalidated; docstring residual line added (inherits signin.py's internal-token `X-User-Email` risk, BROADER because it CREATES an org+owner, gated dark). **39 route tests + 97 in the verify set, 0 skip on BOTH ladders** (cc :5442, tenant :5443); auth/tenancy clauses mutation-proved (drop flag→`SignupDisabled` red; trust body email→R11 400 red; wrong exception map→code red; drop slug format check→`"a b"` 200-admits red / drop blank check→missing-slug returns `ConsoleUnavailable`|`InvalidSlug` red; skip `org_owner_of`+uncaught step-1 raise→`SlugTaken` red); ruff parity; frontend tsc+vitest green. **Resolved the "resubmit converges on both planes" wrinkle at the PLANE level** — on a literal resubmit 0a short-circuits to `AlreadyMember`, so convergence is the idempotence of both provision ops on the slug (the org works dark meanwhile). New suite added to pr-check's skip-guard AND §7 in this PR. **Independent verify: PASS** — verifier's own 26-check probe on fresh DBs (R11 body-trust closed twice: refused on shape before any read AND owner is unconditionally the session email; DW5b step-2 provably unreachable on a permanent refusal, SQL-confirmed zero rows for the signer), 3 mutations replayed, the two-importer fence proven (a planted 3rd importer reddens it). **Verify findings (non-blocking, all recorded/fixed at landing):** (1) 🟡 DW5a is plane-level convergence, NOT route-level — a transient Console failure DURING signup leaves the tenant org **never mirrored to the Console** (0a short-circuits any resubmit to `AlreadyMember`); un-metered until reconciled, and **no reconciler exists in this slice** — named as **CP-2e (signup Console-mirror reconciler)**, a small dark-only follow-up (cannot bite until the Console is deployed + signup flipped + a race); (2) stale `provisioning.py` docstring ("nothing calls this yet") self-falsified by this caller → corrected; (3) header undercount 28/86→30/88 → fixed; (4) minted `InvalidBody` code for the R11 400 (spec named none) judged sound. **Slice 3 (`signIn` limbo) BUILT 2026-08-20 (repaired a P1); slice 4 (`/signup` form) OPEN; flipping the flag live is §8 gate 8; the code is built, the act is the owner's.** (2026-08-20) · **CP-2c SLICE 3 BUILT 2026-08-20** (branch `ws-31-cp2c-slice3`) — the Next-side `signIn` callback self-serve-signup **"limbo" branch**, done-when 7. Under `SELF_SERVE_SIGNUP_ENABLED === "true"` (a NEW Next-side env, SEPARATE from CP-2b's `CUSTOMER_CONSOLE_RESOLVE_ENABLED`) an org-less authenticated session is ADMITTED (`return true`, caches nothing) instead of row iv's refusal, so the ordinary redirect reaches `/signup`. **Adversarial review caught a P1 latent suspension-bypass** in the first build (keyed on `code === "AccessDenied"` ALONE — the gateway emits that for FOUR sources, so a SUSPENDED/non-paying org's member would have regained full access on re-signin, since registry suspension is enforced ONLY at the sign-in door and the per-request path is blind to it). **Repaired same-day** with a purely-additive gateway signal `ResolveDecision.signup_eligible` (True on the zero-org `console-empty` path ALONE, set directly not derived from `source`; R6 expand-safe — row iv still returns `code=AccessDenied`, CP-2b untouched); the callback re-keyed onto it. Fences: `signin.test.ts` source-pins (both flag positions + a negative pin that the guard no longer keys on the code) AND a **live-Postgres (R8)** `TestSignupEligibleIsZeroOrgOnly` proving zero-org→True, suspended/seat-cap→False (independently mutation-verified: a suspended return carrying True fails red, then restored hash-matched). tsc 0 / vitest 2330 / gateway pytest 101 passed · 2 skipped (unrelated Console-ladder DB). **Verify PASS, review APPROVE.** P2 non-blockers: the negative pin keys on the literal `answer?.code` (refactor-evadable); a comment/test label "suspended→refused" conflates with the real Console contract where only `deleted` 403s (security property holds regardless). Slice 4 (`/signup` form) BUILT 2026-08-20; CP-2e reconciler still OPEN; the live flag flip stays §8 gate 8. (2026-08-20) · **CP-2c SLICE 4 BUILT 2026-08-20** (branch `ws-31-cp2c-slice4`) — the dark `/signup` form UI + its `/api/signup` Next hop, done-when 1. **The full four-slice self-serve signup flow now exists in code (dark).** New `src/app/signup/page.tsx` (server, `force-dynamic`, redirects to `/signin` when `SELF_SERVE_SIGNUP_ENABLED !== "true"` — redirect not 404 — else renders the form), `SignUpForm.tsx` (org name · slug · registered-state `<Select>` · optional GSTIN; DESIGN_SYSTEM primitives only, layout byte-identical to `SignInForm`), `src/app/api/signup/route.ts` (the BFF hop — the browser cannot reach the session-email-only gateway route directly; forwards a hard-coded `{slug, display_name, registered_state, gstin}`, NO body spread, identity from the session via `requireIdentity`/`gatewayHeaders`, R11), and the `signup.test.ts` source-pin fence (both flag positions + the three errorCopy codes + the hop forbidden-key drop; red-first proven). Success path traced end-to-end: admit=true → `window.location.assign("/")` → proxy admits the session → the new `app_user` binds the org on per-call resolution → the owner enters the app bound to the new org (no `/signup` loop). tsc 0 / vitest 2339 (conformance 36/36) / eslint 0 / gateway pytest 33 passed (no Python changed). **Verify PASS, review APPROVE.** P2 non-blockers: gateway 400-shape codes fall to a raw error string (near-unreachable behind the form client-side guards); `AlreadyMember` copy lacks a sign-in button; error banner has no `aria-live` (matches existing `SignInForm`). Manual gates for the owner post-merge: the flag-off redirect / flag-on render firing, and the Fluent→Material→Graphite theme-switch on `/signup` + `/signin`. Remaining before go-live: **CP-2e** (Console-mirror reconciler) + the owner-gated set (deploy Console, issue `{resolve, provision}` key, flip `SELF_SERVE_SIGNUP_ENABLED`, WS-33 landing page). (2026-08-20) · **CP-2e BUILT 2026-08-20** (branch `ws-31-cp2e-reconciler`) — the signup Console-mirror reconciler, closing CP-2c done-when-5 dark window. Migration **181** (R6 expand: nullable `console_mirrored_at` / `gstin` / `billing_state` on `organization`, no default so `IS NULL` is the sweep selector). `reconcile()` in `console_resolve` sweeps `console_mirrored_at IS NULL AND first_party = false`, reconstructs `(slug, display_name, owner_email, gstin, billing_state)` from tenant data (owner via `org_owner_of`), re-drives the create-only-guarded `provision_org_on_console` (idempotent-on-slug), sets the marker on success; `is_wired()` False → logged no-op. Signup gains additive best-effort step-1.5 (persist GST tenant-side) + step-2.5 (marker on Console success). Management CLI `scripts/reconcile_console_mirror.py` (inert unwired, no scheduler). R8 fence `test_signup_reconciler.py`: 9 passed / 0 skips both ladders (6 genuine skips unwired); create-only + marker clauses mutation-proved (disable the guard → hijack red; drop the NULL predicate → sweep reddens). Verify PASS, review APPROVE. **P2 (PR body):** a RARE double-fault (step-1.5 AND step-2 both fail) → tenant GST NULL → the sweep first-provisions the Console org with NULL GST, frozen by the create-only conflict arm (`billing_state` drives IGST vs CGST/SGST — a tax gap, dark + unobservable). Closer = a follow-up: a Console-side GST-update path + a NULL-profile counter in `ReconcileSummary`. Design defaults (owner may overrule): sweep excludes `first_party = true`; GST persisted via a seam helper (not folded into 179). 🔴 OWNER: running the sweep + deploy + `{resolve, provision}` key (§6(h)/§8 gate 8) + flip. **The full signup system (CP-2c slices 1-4 + WS-33 landing + CP-2e reconciler) is now BUILT dark; only the owner-gated go-live set remains.** (2026-08-20) · **CP-9 done-when 19 / §6 item (g) BUILT 2026-08-20** (branch `ws-31-seats-mvp`) — the customer-authenticated seats read `GET /me/seats`, first slice of the post-signup in-app MVP (D46.4). On the `can_pay` door (a suspended org sees its seats to decide to pay; deleted 403s), org from `caller.organization_id` only — two-org isolation + exact-five-field wire (`plan_slug/purchased/assigned/available/oversubscribed`) both mutation-proved R8 (`test_customer_console_payments.py`, 120 passed / 0 skips). Reuses the ONE seat vocabulary via a NEW shared `_seat_grid(conn, org_id)` helper (extracted from `billing_summary`; both routes call it — no drift), no migration, no recompute, additive. Verify PASS, review APPROVE (2 P2s fixed in-slice: dup loop → shared helper + an empty-holdings fence). 🔴 OWNER still: seat WRITES `POST /billing/seats` + deploy + flags (§8 gate 4). **Next: the SC-1a seats PANEL (WS-30, GO on this merge).** Board follow-ups (owner/next PR): §6(c) gate-registry line numbers drifted ~+266 (gate correct, numbers stale — re-stamp); the WS-30 row needs the SC-1a seats-block carve-out. (2026-08-20) · **§6 item (h) BUILT 2026-08-21** (branch `ws-31-seat-assign`) — the first CUSTOMER-AUTHENTICATED seat WRITE (assign/release), the write-twin of the `GET /me/seats` read. A NEW deployment-key capability `seat_admin` (third beside resolve/provision, via the `deployment_or_operator` factory — NOT an org-key route, so the CP-3 org-key-read-only fence stays green), routes `POST /registry/seats` + `/registry/seats/release`. Console enforces in its own code: the capability; org+actor from `deployment_visible_orgs` (placement∩membership, never a body field — R11, `org_slug`→400); the actor `org_membership.role ∈ {owner,admin}`+active (a plain member 403s CONSOLE-side); target-must-be-a-member (NO identity minting, unlike the operator `/billing/seats`); refuse `core`; `lock_seat_capacity` before the count (closes a race the operator twin has); `available==0`→409 `buy_more` (no self-serve oversubscription); idempotent; release drops only `can_write_seats`. Reuses the operator seat composition, NO migration. **All four authz refusals mutation-proved RED** (cross-deployment, non-admin, mint-unknown-target, oversubscribe) — independently re-run + reverted byte-identical; 10 R8 fences (7 behavioural + 3 R11 shape) + structural; 130 passed / 0 skips. Verify PASS, review APPROVE. **P2 for the owner (PR body): the operator arm makes `/registry/seats` a SECOND operator seat-write beside `/billing/seats` (§5 second-way smell; both fail-closed, the new one stricter/never-mints) — decide: keep the two-scheme, or make `/registry/seats` deployment-key-only.** 🔴 OWNER: the LIVE seat write (§8 gate 4) + granting `seat_admin` to a real deployment key (§8 gate 8) + deploy + the WS-30 "manage seats" UI (owner picks the surface transport, §9 residual 7 — the deployment key is fenced out of the browser). The WS-30 SC-1a seats-block carve-out is now DONE (#38). (2026-08-21) · **§6 item (i) / done-when 20 BUILT 2026-08-21** (branch `ws-31-members-read`) — the customer member-list read `GET /me/members`, the sibling of `GET /me/seats`, feeding the SC-2b members×seats manage surface. On the `can_pay` door (suspended reads, deleted 403s), org from `caller.organization_id`; per-member `{email, role, status}` from a NEW `store.org_members` helper (reuses the ONE `org_membership ⋈ user_identity` join; no roster read existed to fork), `MembersView`/`MemberView` mirroring the seats pair. Two-org isolation + exact-triple-and-nothing-else, both mutation-proved R8 (test_customer_console_payments.py, 132 passed / 0 skips); no recompute, no migration, no id/PII leak on the wire. Verify PASS, review APPROVE (keep-no-filter: filtering to {invited,active} would diverge from the write path, which accepts any-status targets). P2 (PR body): the roster returns `removed`-status members too (by design — the SC-2b surface owns the render filter; rides item (g)'s "any signed-in member" BFF-hardening ticket; member emails are PII — owner ratifies the `can_pay`-vs-admin-gate door at flip-time). **Follow-up on ALREADY-MERGED item (h): `_seat_admin_target` does not refuse `removed`/`suspended` targets — latent (nothing writes those statuses yet), fix when member-management lands.** 🔴 OWNER: deploy + live `cc_live_` key. **Next: SC-2b — the members×seats manage UI (needs the owner admin-gate capability call: reuse `billing:purchase` vs `seats:admin`).** (2026-08-21) · ◐ **CP-2d MINTED + FIRST SLICE BUILT 2026-08-22** (owner directive, branch `cp-2d-email-otp`) — **passwordless email OTP via Resend**, the first non-OAuth identity path, shipped **DARK**. A new Auth.js v5 `Resend` provider is registered in `auth.ts` ONLY under `EMAIL_OTP_ENABLED === "true"` **AND** a present `RESEND_API_KEY` (the Google-conditional idiom, one notch stricter; the ONE gate `isEmailOtpConfigured` is read by both the provider and the `configuredProviders` seam, so the button and the provider cannot disagree). **NUMERIC OTP, not magic-link** (owner's ask): `generateVerificationToken`→6-digit code, `sendVerificationRequest`→emails it via an **injectable** Resend transport (mocked in tests, never the network). Rides the ONE NextAuth session (JWT strategy unchanged, no `Credentials` door); the verified email is Auth.js's `identifier`/`user.email`, never request input (R11). Pure half `src/lib/emailOtp.ts`; surface reuses the provider seam (email field via the shared `Input`). Zero migrations, no new engine/DB/Redis site (R5). tsc clean; vitest **2405/2405**; the exact-string dark gate shown **red-first**. 🛑 **HARDENED at review (2026-08-22): the provider registers ONLY when the Auth.js DB adapter is ALSO wired** (`isEmailOtpProviderReady` = env-configured AND `EMAIL_OTP_ADAPTER_READY`, `false` today) — NOT on the env flag alone. An email provider without an adapter makes `@auth/core` return `MissingAdapter` on EVERY `/api/auth/*` request, 500ing ALL sign-in (Google/Microsoft included), so gating on the flag alone would turn one documented owner flip into a **site-wide auth DoS**; the adapter guard makes the flag physically un-footgun-able (both the provider AND the button stay dark until slice-2). New fence `emailOtp.test.ts` *"the adapter guard makes the flag un-footgun-able"*, red-first. **What actually ARMS OTP is slice-2 (NOT a flag):** wire the Auth.js **DB adapter** into `NextAuth({ adapter })` (table+migration, R1 next-free number) in the same change that flips `EMAIL_OTP_ADAPTER_READY` true, + the numeric-code **entry page** — which **MUST rate-limit verify attempts per identifier** (a 6-digit code over ~10 min is brute-forceable; `@auth/core` does not rate-limit verification). 🔴 OWNER: `RESEND_API_KEY` (a secret, §6(f)'s class) + flipping `EMAIL_OTP_ENABLED=true` (an enforcement flip) — because of the adapter guard, either flip before slice-2 is a **no-op, not an outage**. TOTP/passkey step-up + SMS OTP stay anti-scope. (2026-08-22) · ✅ **CP-2d SLICE 2 BUILT 2026-08-23 — OTP ARMED (branch `ws-31-cp2d-slice2`), still DARK (flag+key unset everywhere).** Dispatch-audited **NO-GO** the same day purely for missing slice-2 acceptance; the owner answered the three blocking decisions (B1 token home · B2 pre-auth residual · B3 rate-limit numbers) and `customer_console.md` §CP-2d gained **done-when 6–16, each naming its fence (R7)** — that cured the NO-GO in the same PR (R4). ⚠️ **Wording defect corrected in the ticket AND here: the token table is NOT "tenant-scoped by R5" — it is RLS-EXEMPT.** The row is minted **pre-session** for an address that may belong to no org (that is the `console-empty` signup funnel), so there is no `organization_id` to stamp, and with **H3 phase-4 RLS now LIVE in production (2026-08-23, 140 policies / 140 forced, app on a non-priv role)** an org-scoped policy would have bricked OTP the day it shipped rather than "at promotion". Exempted in `gen_tenant_migration.EXEMPT` with its reason, beside `user_identity`/`org_membership` (R5(a)'s exempt-identity-table permission). **Migration 186** `auth_email_otp_token` — **PREFIXED** name and a `DO`-block guard that RAISES on a foreign relation of that name, because the Langfuse collision is real on the staging box's DB and `CREATE TABLE IF NOT EXISTS` on a clash is a silent no-op. **No DB in the Next tier (R5):** the adapter is a thin object calling three new BFF-internal gateway routes through the existing `headersActingAs()` seam — new R7 fence `emailOtpAdapter.test.ts` scans `package.json` and all of `src/` for a pg/postgres/`@auth/*-adapter` dependency or import. **`session: { strategy: "jwt" }` pinned in the same config that takes `adapter`** — without it `@auth/core@0.41.2` (`init.js:74`) silently switches ALL sign-in to database sessions and `assertConfig` does not catch it; fenced both ways. **Rate limit is SERVER-SIDE in the gateway** (the callback URL is directly reachable, so page-level limits limit nothing), transactional on the token row under an identifier advisory lock, **no new Redis surface**: 5 verify attempts / 10 min (exhaustion invalidates the outstanding token AND 429s the window), 3 sends / hour, applied to **every** caller including internal-bearer ones. **The `signIn` callback fires TWICE on the email path** (`send-token.js` passes `email.verificationRequest`) and only the **completion** leg resolves — a resolve on the send leg would let an anonymous visitor burn a paying customer's seat cap, the exact hole `routes/signin.py`'s docstring exists to prevent; the completion leg is byte-identically the Google funnel (`console-empty` → `signup_eligible` → limbo). Pre-auth residual **accepted and named** in the ticket the way signin.py/signup.py name theirs; the narrowing stays §4.3's `GATEWAY_INTERNAL_TOKEN` split (§8 gate 1), referenced not built. 🔴 OWNER unchanged (`RESEND_API_KEY`, the `EMAIL_OTP_ENABLED` flip) — **but they are no longer no-ops**: the pair now arms a live non-OAuth sign-in path. (2026-08-23) · ◐ **CP-8 SLICE 1 IN PROGRESS 2026-08-22 (`cp-8-operator-console`, DARK)** — the Operator Console's live customer-management surface. **Backend:** the §4.1a cross-org read `GET /orgs` (`store.cross_org_summary`, `OrgListView`) — Operator-only (a `cc_live_` customer key is 401), per company its plan/seat grid (the ONE `seat_counts` vocabulary, byte-identical to `GET /billing/summary`), credit balance, lifecycle + subscription status, trial expiry, MRR (paise, `payments.paise`, **zero unless the subscription is active** — agent-default D16/D17). `test_customer_console_operator_list.py` R8, 14/0 skips, on §7 + pr-check skip-guard (7→8). **App:** a SEPARATE Next.js app `workbench/operator_console/` (D35 — own build, NOT a workbench route; theming-exempt D35.4) — customers list + customer-detail with the ACTIONS (activate subscription, allocate/release seats, add AI credits, suspend/resume) wired through **server-side BFF routes** holding `CUSTOMER_CONSOLE_OPERATOR_TOKEN`+`CUSTOMER_CONSOLE_URL` (token never reaches the browser). Staff auth behind an **interim server-side secret** (`OPERATOR_CONSOLE_STAFF_SECRET`); the staff Entra directory (D35.3) is an OWNER dependency, not built. typecheck + vitest green (token-server-side, staff-gate, paise fences). **DEFERRED to later CP-8 slices:** payment-reconciliation queue (NULL-`order_id` + capture-after-terminal `payment_event`), the nightly seat/subscription drift job, invoice rendering. 🔴 **OWNER-GATE go-live:** deploy the app · wire hostname/Caddy route · set the operator-token env · stand up the staff Entra app. (2026-08-22) · ◐ **CP-8 SLICE 2 BUILT 2026-08-22 (`cp-8-provision-customer`, DARK)** — provision-a-new-customer, CREATE-ONLY. A "New customer" CTA + form on the operator console list (`workbench/operator_console/`) creates an org via the Console's **operator** provision arm: NEW BFF route `POST /api/operator/provision` MIRRORS `/api/operator/activate` (gates on staff via `proxyToConsole`, relays the Console `POST /orgs/provision` status + JSON VERBATIM through `provisionOrg` in `src/lib/console.ts`, which holds the operator token — never the browser). Body `{slug, name, owner_email, deployment_label, gstin?, billing_state?, core_seats}`. `deployment_label` is operator-NAMED, never inferred/hardcoded (D46.6 items 1 & 3): editable free-text prefilled from a NEW env `OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL` (box value `gateway`), required client-side; a wrong label is the Console's relayed 404. Relays 200 / 400 (missing label) / 404 (unknown label) / 409 (placed elsewhere) unchanged. typecheck + typecheck:lib + vitest green (verbatim-relay + token-not-bundled fences); the Console provision contract is R8-proven (`tests/unit/test_org_provisioning.py`), no new Python. Create-only by design — activate/seats/lifecycle already live on the customer-detail Actions, and provision already grants Core seats + a trial sub + placement, so the new org is immediately manageable. 🔴 **OWNER-GATE go-live:** deploy the app · set the operator-token env · set `OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL` on the box · provision a REAL customer through the finished UI. (2026-08-22) · **CP-2f MINTED + BUILT 2026-08-24 — the Console member-write door** *(D49; branch `ws-30-invites`)*: the gap the invite ticket's audit found — **the Console has no member-add door at all**; `POST /orgs/provision`'s founder INSERT is the only membership writer, so an invited colleague is invisible to `GET /me/members`, 404s at `_seat_admin_target`, and with resolve ARMED resolves `console-empty` into the self-serve signup funnel. 🟢 **AGENT-SAFE, dark by construction:** `POST /registry/members` on the deployment-key scheme behind a FOURTH capability **`member_admin`** (no route issues one, no migration carries the string — the `seat_admin` posture), org+actor DERIVED from `store.deployment_visible_orgs` (R11 — no `org_slug` on the wire), writing `org_membership` `status='invited'`, role left at the column default `member` (no cross-plane role mapping, D12); plus D50.3's `invited→active` promotion inside the deployment resolve arm. **NO MIGRATION** — `001_customer_console.sql:121-131` already CHECKs `status ∈ {invited,active,suspended,removed}`. Gateway `invite_member` calls it POST-COMMIT, best-effort, own session, exactly like the H6 mirror beside it; `test_console_dependency_boundary.py`'s importer allow-list grows **three→four**. 🔴 OWNER: granting `member_admin` to the REAL deployment key (§8 gate 8's capability-growth class) · deploying the Console (§8 gate 2) · a member write on the live box (§8 gate 4, and §6 below). · **CP-2g MINTED + BUILT + REPAIRED (verify FAIL + adversarial review, 2 repair rounds) 2026-08-24 — organization offboarding end to end** *(branch `ws-36-org-offboarding`; owner directive: delete organizations before the E2E)*: the lifecycle's `deleted` finally destroys — Console `POST /orgs/purge` (Operator scheme, refused outside `deleted` with a refusal that teaches the cancel→delete→purge path, strips personal data + live secrets, tombstone-renames the slug so the name is free for a fresh start, keeps the books, audits `org.purge`) + gateway operator door `DELETE /internal/operator/organizations/{slug}` (static bearer `GATEWAY_OPERATOR_TOKEN`, 503 ship-dark) driving `acb_auth.offboard`'s SINGLE cascade DELETE (33 `ON DELETE CASCADE` FKs, measured; `user_identity` survives — the after-state IS D51's org-less chooser) + the operator console's cancel/delete lifecycle edges and the type-the-slug DangerPanel, whose BFF runs authority-check → tenant → registry in a FENCED order (`purge/route.test.ts`; the operator-console vitest suite is now IN CI via the new `frontend-operator` job — it had tests and no CI). ⚠️ Found on the way: `crm_*.organization_id` references `crm_organizations`, NOT the tenant — the CRM family has no tenancy column at all yet (the MT-1j remainder), so per-tenant CRM purge is IMPOSSIBLE today; named + fenced (`TestTheExclusionCannotGoStale` goes red when threading lands). R8 suites `test_org_purge_console.py` / `test_org_purge_tenant.py` / `test_operator_door.py` added to the CI ladder list in the same PR. 🔴 OWNER: setting `GATEWAY_OPERATOR_TOKEN`/`GATEWAY_INTERNAL_URL` on the box · pressing the purge on a REAL organization (the §6 production one-off class made into a button). Repairs, headline: the door now demands TWO tokens on the REAL gateway app (round 1's P0: the app-level auth gate made the first draft a shipped no-op its bare-app suite could not see); already_absent stops for a human (`needs_accept_absent`); the kept books lose their addresses — `usage_event.user_email`, `control_audit.detail` AND round 2's find, `control_audit.actor`; slug matched byte-exactly; tombstones 409; every org-scoped Console table classified BY NAME with an information_schema fence; the honest boundary stated in the confirm copy (~119 un-threaded tables — the WS-29 H2 remainder — are unreachable by any per-tenant delete). · 🔧 **CP-2h MINTED 2026-08-24 · slice 1 of 5 BUILT ON BRANCH 2026-08-24 (not merged, no PR)** (owner instruction, same-day: customer-side seat management in the app — see `customer_console.md` §6 CP-2h, D-SEAT-1…7 recorded incl. the D-SEAT-7 release-consequences ruling: seats gate ENTRY never data, release ≠ removal, auto-reseat while capacity is free. Slice 1 = the D-SEAT-4 reroute, **built exactly as scoped**: Console deployment-key READ `POST /registry/seats/overview` beside the existing registry writes — same `seat_admin` capability, same `owner|admin` gate, `SeatsView`'s grid + `MembersView`'s roster COMPOSED, so no new SQL, no second seat vocabulary and **no migration** — → `acb_auth.seat_overview_on_console` on the shared `_post_seat_call` transport (the four-caller `console_resolve` boundary did not widen: `routes/seats.py` was already named) → gateway `GET /seats/overview` (session actor, `_unwired_read_refusal` 503) + the existing `/seats/assign|release` → workbench BFF `/api/org/seats` READ session hop — the writes reuse the existing gateway-backed `/api/billing/seats/{assign,release}` pair (repair round 1 below deleted the short-lived twins this sentence first shipped) → `SeatsTab.tsx`. Fences: `tests/unit/test_customer_console_seat_overview.py` (R8, 21 tests, 0 skipped, added to `pr-check.yml`'s hand-list and §7's verify block in the same change) · `test_seat_admin_proxy_route.py` (36) · `seatRoster.test.ts`. ⚠️ **The org-key path is NOT dead for seats generally** — `/settings/billing`'s manage-seats block and the Members-tab invite-with-seat still drive `/api/billing/seats*`; retiring those is a later slice, not this one. Slices 2-5 outstanding: the waiting card + Awaiting-seat, the blocked count, buy-more-files-a-request, remove-releases/suspend-does-not. Still DARK by construction — no live key holds `seat_admin` and nothing sets `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`. ⚠️ **Repair round 1 (2026-08-24, same branch, adversarial diff review):** the READ's refusals were drawing as an OUTAGE — a 403 (which is every admin except the founder, since no Console door ever writes `role='admin'`, §6 CP-2f) and the multi-org 409 both rendered “the seat plane did not answer”, i.e. a fabricated incident for the majority persona — now five testable states via `interpretOverviewRead(status, payload)` extracted into `settings/organization/lib/seatRoster.ts` (403 → a calm founder-only sentence, 409 → its own multi-org sentence, a `buy_more` 409 → `error` rather than a confident lie); the byte-identical twin write hops `/api/org/seats/{assign,release}` DELETED and the tab re-pointed at the already-gateway-backed `/api/billing/seats/{assign,release}` (one seam — CLAUDE.md §5); stale comments/names fixed; and done-when 1 downgraded to the honest claim — the tab works for a Console `owner|admin` whose email holds exactly ONE membership on the box. **Slice 2 now owns, by name:** thread the caller's SESSION org through gateway→Console and VALIDATE it against membership (the `requirePurchaser` §4 comparison idiom in `api/billing/_console.ts` is the precedent), which fixes together the permanent multi-org 409, the CROSS-TENANT switch-off (`store.deployment_visible_orgs` does not consult membership status, so an admin of org B inviting org A's owner's email makes that email multi-org and turns org A's whole seat surface off, from outside org A, with no UI saying why) and the registry-vs-tenant org mismatch (an actor whose registry org differs from the tenant org they are signed into reads/writes the WRONG org's seats — P2, wrong-tenant write, no unauthorized disclosure). Branch `ws31-cp2h-slice1`.) |

| WS-32 | **Organization Identity — the customer's own mark inside the product** *(minted 2026-08-14)* | ◐ **OI-1 ✅ · OI-3a ✅ — MERGED in #442 (`4934eb8`) 2026-08-14** · 🔴 **OI-2 BLOCKED (MT-1b)** · 🟡 OI-3b/OI-4/OI-5 open | **`specs/organization_identity.md`** | Settings → Organization: logo upload (OI-1) · tenant-scoping the store (OI-2) · first-paint branding (OI-3) · org display name (OI-4) · logo on invoices (OI-5, waits on D38's renderer). ⚠️ **Minted AFTER OI-1 was built, which is backwards and is recorded in the spec's banner.** The owner asked for the logo mid-session on the WS-31 branch; it shipped, and BOTH independent passes flagged that no ACTIVE spec owned it — so the verifier had no acceptance contract and had to check the implementer's own claims, the one thing that role exists to avoid. OI-1's acceptance criteria were written from shipped behaviour and are therefore weak evidence; the row stays ◐ until re-verified against §4. · 🔴 **OI-2 is a hard prerequisite for customer #2 and is BLOCKED on MT-1b** (not owner-gated — dispatchable the day promotion lands): `org_settings` has PK `key` alone, sits in `test_tenancy_boundary.py`'s BASELINE_UNSCOPED, and is written through a raw psycopg site allow-listed as "a binding site the RLS work must convert, not a permanent exemption". Before promotion tenant B's upload overwrites tenant A's logo; after promotion every org's logo silently vanishes. Inherited from the `appearance` neighbour, but branding is the first *content* in that table rather than a preference. · Format rules are magic-byte-sniffed, SVG refused by name (stored-XSS surface), 128 KiB / 32–2048px / aspect 0.5–8.0. · ✅ **The second Playwright seam is gone** — `scripts/theme-check*.mjs` (hardcoded POSIX browser path, outside `testDir`, invisible to the runner) folded into `e2e/org-branding.spec.ts`. Its first version's key assertion could not fail in the case it was written for and was reported as working — the R7 lesson, recorded in the spec §7. · 🔴 **THE FOLDED SPEC CANNOT RUN AND IS `test.fixme` IN FULL, 18 SKIPPED.** Two findings under it. (a) The e2e suite had not booted since CP-0 (`8f6eb79`): `next start` sets NODE_ENV=production, `authPosture` grants its dev bypass only when NODE_ENV is *not* production, so the proxy answered every page 503 and **every spec in `e2e/` timed out** — nothing noticed because nothing runs `e2e/` in CI. Fixed by serving the suite with `next dev`; ⚠️ these specs now exercise the DEV bundle, and restoring production coverage needs a considered test-auth posture (owner). (b) Under dev the app **does not hydrate** in Playwright — zero `/api/**` requests, a failing `webpack-hmr` handshake. ⚠️ **The partial pass was the dangerous part**: 9 of 18 reported green and every one was a fallback/outage case that an un-hydrated page renders anyway — they would pass with the client bundle deleted. ✅ **MEASURED 2026-08-14 — three of four hypotheses ELIMINATED**, recorded in the spec's banner so nobody repeats them: **not** Playwright's interception (zero routes registered gives the identical result), **not** the container's proxy (`--no-proxy-server` changes nothing), **not** a truncated RSC stream (`/chat` answers 200 in ~94ms, 95 KB, closes cleanly with its `__next_f` payload). The page loads 39 chunks with **zero failed requests** and no page errors; React creates a root (`window.next`, DevTools hook, 5 elements with `__reactEvents`) but **0 of 422 elements carry `__reactFiber`** and `localStorage` is empty, so not one mount effect ran. ⚠️ `__reactEvents` alone is NOT hydration — reading it as such briefly produced the opposite conclusion here, and only counting `__reactFiber` across the whole tree settled it. ► **One hypothesis left, with a mechanism**: the client opens `ws://…/_next/webpack-hmr` and the handshake fails `ERR_INVALID_HTTP_RESPONSE` while the server is Next 16.2.6 on **Turbopack** serving a `[turbopack]_browser_dev_hmr-client` chunk. Stated as a lead, not a diagnosis: a dead HMR socket does not normally block hydration. (2026-08-14) |

| WS-33 | **Marketing site — the public face on the apex (`metorite.com`)** *(minted 2026-08-19, D46.1)* | 🟡 page+fence BUILT 2026-08-20 · deploy owner-gated | **`specs/marketing_site.md`** | D41 reserved the apex for exactly this; now specced: ONE static page (`site/index.html`, no JS, no cookies, no external assets, < 100 KB) + the R7 fence `tests/unit/test_marketing_site.py` — both AGENT-SAFE. CTA hrefs pinned to `https://app.metorite.com/signup` (CP-2c's URL) and `https://app.metorite.com`. 🔴 OWNER: apex DNS A record (the D41.3 wildcard does NOT cover the apex) · the Caddy vhost block (travels as a ready-made patch — `deploy/` is plan-guard-gated) · the deploy. ⚠️ Sequencing: ships WITH or AFTER CP-2c, or the CTA 404s. Finding recorded in the spec: `Caddyfile:8` ACME email is still `ops@fracktal.in` (rebrand leftover — cert-expiry alerts go to the old domain). (2026-08-19) · **PAGE + FENCE BUILT 2026-08-20** (branch `ws-33-landing`) — `site/index.html` (13,280 B, self-contained: inline CSS, system fonts, inline SVG, `prefers-color-scheme` dark; hero + benefit cards + CTA band + footer; exact CTA hrefs, no `<script>`/cookies/external fetch) + the R7 fence `tests/unit/test_marketing_site.py` (7 tests, `encoding="utf-8"`, path from `__file__`; the foreign-origin check parses the URL host via `urlsplit` — rejects `.evil.com`/`@evil.com`/`//`/`http:` smuggling, proven red-first). Verify FAIL→repaired (origin check was prefix-only `startswith`; tightened to host-equality) → re-verify PASS, review APPROVE. The `/signup` CTA target now exists (CP-2c merged) so the CTA no longer 404s. Copy fix: hero CTA "Sign up free"→"Sign up" (paid SaaS; "No credit card to start" already carries the ₹0 nuance). P2 follow-ups (PR body): widen the fence scan beyond `href`/`src` (srcset / CSS `url()` / `@import` / `on*=`); confirm "self-heal" + "mirrors & orchestrates" copy against shipped capability before public. 🔴 OWNER still: apex DNS A record · Caddy apex vhost (patch travels in the PR body, `deploy/` plan-guard-gated) · deploy + live curl · the `ops@fracktal.in` ACME-email fix. (2026-08-20) |
| WS-34 | **Launch surface — what a customer sees on day one** *(minted 2026-08-24, D49)* | ◐ **LS-1…LS-10 BUILT 2026-08-24** on `claude/app-visibility-seat-management-jlvnhl` · 🔴 **LS-11 OWNER-GATE** · one manual gate open (theme switch, H-19) | **`specs/launch_surface.md`** | The owner directive of 2026-08-24, specced: flat ₹500/user/month + AI credits replacing the Center-package ladder (supersedes D23/D24 as the pricing shape of record) · the "Centers" nav section deleted while **every line of Center code, every `center.*` feature and every `group:<slug>` grant stays** · an eight-pane launch allowlist with everything else `preview` (routes intact, nav entry gone, `NEXT_PUBLIC_SHOW_PREVIEW_APPS` default OFF) · seats a customer admin drives from Organisation (invite → assign → release → **Unassigned** → reassign) with operator parity · and the three nav-resolution defects behind the reported "sometimes all the apps appear" flash (§8: `visibleSections(null)` returns EVERYTHING, `fetchAccess` maps a 502 to NO_ACCESS, and the home grid filters nothing at all). LS-1…LS-10 AGENT-SAFE; **LS-11 OWNER-GATE** (seats already held on a retired plan). |

### Apps

| WS | Workstream | State | Owning spec · record | Gates · next (verified) |
|---|---|---|---|---|
| WS-17 | **Email completion** | 🔴 owner calls | `email_app_master_plan.md` | Three owner decisions pending (kill-list batch, schedule-send go, contact-merge identity) + user-parked semantic search. §7 Tier-1 hardening is 🟢 AGENT-SAFE and gates the second account — ⚠️ a second mailbox connected 2026-08-05; re-verify §7's single-account premise at dispatch. |
| WS-18 | **Tasks Phase 3** (Weekly Review, Waiting-For, ~~Horizons~~) | 🟡 partial · ⚠️ **RE-BASED BY D53** | `task_manager_app.md` · board record 2026-08-09 | ⚠️ **2026-08-24 — D53 changes this row's substrate, so re-read every item before dispatching one.** Tasks is now the **personal lens over `pm_*`**, not an app with its own store; `gtd_items` is retired and **WS-39 owns the move**. Anything here that is written against `gtd_*` schema is a rewrite, not a build. ~~ClickUp write-back waits on BO-1~~ — **struck by D52**, there is no ClickUp. Waiting-For surfacing BUILT 2026-08-02, pending review (explicit-promise semantics settled). 🔴 Weekly Review NO-GO until the `gtd_reviews.summary` JSON contract + per-movement done-whens are written. Horizons: **WS-21 owns it** (§4) — DO-NOT-DISPATCH stands. 🔴 nudge **sending** (shared gate, §6) · ClickUp write-back waits on BO-1. EVAL-LOCKED: `propose()`/`propose_with_llm()`. ⚠️ WS-27e one-store: coordinate any `gtd_*` schema work with WS-27h's retirement plan. (2026-08-02) |
| WS-19 | **Notes + meeting bot** | 🟡 | `note_taker_app.md` + `meeting_bot_platform_plan.md` | Bot Phase 2 error codes 🟢 AGENT-SAFE. 🔴 bot Google account (§6) · share-to-chat needs a Slack integration that does not exist (scope call). ⚠️ D15 flag (2026-08-09): the bot plan's **ELv2 compliance argument reads "not a SaaS we resell"** (`meeting_bot_platform_plan.md`, Attendee is ELv2 not OSS) — re-evaluate before any external tenant uses bot features. |
| WS-20 | **WhatsApp activation + remainder** | 🟡 owner | `whatsapp_message_manager.md` §11 | Search UI 🟢 AGENT-SAFE; ~~OCR needs a vision-tier decision~~ **OCR tier ANSWERED 2026-08-10 (D25.6): fast/cheap vision via LiteLLM — the OCR ticket is dispatchable**; Odoo/Zoho-bound items bind to `crm` `entity_ref` per WS-26d instead — the linker (nothing writes `wa_contacts.entity_ref`) is owed by whoever takes them. 🔴 Meta env/app review · `WHATSAPP_ENRICHMENT` flip (§6). (2026-08-01) |
| WS-21 | **Calendar F2/F3** | 🟡 partial · ⚠️ **RE-HOMED BY D54** | `calendar_focus_os.md` §9 (+§5) + `calendar_timeboxing.md` §13 · board record 2026-08-09 | ⚠️ **2026-08-24 — D54 moves the SURFACE to its own `/calendar` app under Personal Center; WS-21 keeps the BEHAVIOUR** (F2/F3, Focus Shield, timeboxing, Horizons). **WS-39 owns the move.** 🔴 **Correction measured 2026-08-24: `gtd_time_blocks` and `calendar_accounts` DO NOT EXIST** — no `CREATE TABLE` for either anywhere in `infra/postgres/`. Both this row and `calendar_timeboxing.md` §13 P4 cite them as if built. What the calendar actually reads/writes is `gtd_items` **directly**, plus `gtd_settings` / `gtd_day_state` / `gtd_rollover_log` (D54.5) — so its task reads re-point to `pm_tasks` when D53 release (1) lands. P3 roll-over + ideal-week + packer-breaks all shipped (struck from scope 2026-08-03). `gtd_time_blocks` is **four slices S1–S4** — the "one non-breaking PR" claim was false (17 TS files + 3 gateway modules + skill + agent). Focus Shield is AGENT-SAFE (needs a design, not a credential). Owns Horizons (§4) — DO-NOT-DISPATCH, no acceptance. 🔴 external-sync OAuth credentials (§6) · shared nudge-send gate (§6). Never `pytest tests/unit -k calendar` (collection hangs). (2026-08-03) |
| WS-22 | **draw.io** | ⏸ PARKED | `drawio_integration.md` | **PARKED BY OWNER 2026-08-10 (D25.7)** — no agent time until a real need (proposal diagrams, KB visuals) pulls it back. The spec's acceptance structure keeps; anchors need re-verification at un-park. |
| WS-26 | **CRM app — native CRM + Zoho retirement** *(minted 2026-08-05)* | ✅ a–h + i-export MERGED · ✅ h2 + h-fence BUILT (branch, unmerged) · 🔲 i-bulk CONTRACT RECORDED 2026-08-11 (auditor's, verbatim; wants an auditor pass), not started · 🔴 i (merge · CSV import · saved views) NO-GO (docs) | `specs/crm_app.md` · board record 2026-08-09 | a + b + c + d (read · email · write) **merged + deployed** (d-write log-verified via deploy `31217978773`, 2026-08-08); f + g **merged to main** (#391, #397 — the old "on branch, NOT run against prod" wording is struck; f's stage repair still needs its 🔴 `?apply=true` run, §6 WS-26 (d)). **D5 d-autolead MERGED 2026-08-09 (#403; migration renumbered 158→163 at merge)** — remaining: 🔴 `CRM_AUTO_LEAD` flip (§6 WS-26 (b); clamp-anchor design, never reset-to-now). Zoho sync loop **ENABLED by the owner 2026-08-06** (§6 WS-26 (a)) — every "ships OFF / never run" sentence about it is struck. **h stage entry-requirements + rot badges BUILT 2026-08-11** (branch `claude/crm-command-center-tasks-i8l7n4`, migration **169** taken at build time per R1, re-check at merge — **MERGED 2026-08-11 in PR #425, `d471ae8`; 169 applied**) — `crm_deal_statuses` gains deal-only `required_fields TEXT[]` + nullable `max_dwell_days`, the gate is `pipeline.py::_require_entry_fields` beside the lost-reason refusal (entry-only, before the three effects, satisfiable by the same PATCH), rot is presentation-only, `LostReasonModal` absorbed into `MoveModal`. New cross-language fence `tests/unit/test_crm_stage_discipline_parity.py` reads the allowlist out of `board.ts`. Migration + `TEXT[]` round-trip verified against a real Postgres 16 (R8). ⚠️ Recorded gap, deliberate: **CREATE is not gated** — `POST /crm/deals` and the convert path write without entering the transition. **i AUDITED NO-GO 2026-08-11 — do not dispatch an implementer from it; the full per-item audit record is in `crm_app.md` §9 WS-26i and must not be re-derived.** All four items fail §1 point 3 (no done-when, no tests line) plus point 5 (no §10 block) and point 7 (unlabeled ⇒ reads AGENT-SAFE, yet four of six sub-behaviours write the LIVE tenant). Merge is doc-blocked on §7.1 merge semantics + a D-CRM number and is destructive upstream; bulk needs a `db`-taking patch seam extracted from `records.patch_record` first ~~and collides with WS-26h's raw `HTTPException(422)`~~ (**struck 2026-08-11: the 422 is a design note, not a second precondition** — a per-record catch narrowed to `status_code in (404, 422)` that re-raises everything else resolves it with **no change to WS-26h/h2 code or tests**; the surviving concern is volume, ~1,500 inserts for a 500-row lane move. Two further corrections to the same bullet in `crm_app.md` §9: the seam's anchors are `records.py:235` / session at `:247` at `a06fa6a`, and the precondition's reason is **N records without N sessions** — the `auto_lead.py:78-79` pool hazard — **not** "bulk needs one transaction": Projects' bulk is deliberately non-atomic, returning per-task `applied`/`skipped`/`failed`); CSV import has no precedent and no `UNIQUE` on `crm_leads.email` to dedupe against; saved views are pincered between migration 170 and the parked `crm_*` `organization_id` naming call (`multi_tenancy.md` §58-65). **The one clearable item is CSV EXPORT** — no Zoho write, no migration, no WS-26h collision, sibling `routes/projects/export.py` (WS-27ae); the auditor's ready-to-paste done-when block and its two measured traps are recorded in `crm_app.md` §9. **Both have now landed: the done-when block is in `crm_app.md` §9 and i-export is BUILT 2026-08-11** (same branch `claude/crm-command-center-tasks-i8l7n4`, **no migration**, read-only — `routes/crm/export.py`, four literal `GET /crm/export/<entity>.csv` paths with `export/` first so `/crm/leads/{record_id}` cannot shadow them; **MERGED 2026-08-11 in PR #426, `7255344`**). Both measured traps were real and are now fenced: `ListQuery.limit` is the `MAX_PAGE_SIZE = 100` page clamp and is never bound (`MAX_EXPORT_ROWS = 10_000`, over it is a 422 naming the real count, never a partial file), and the CRM BFF proxy's unconditional `NextResponse.json` turned a `text/csv` body into `{}` with a 200 until it gained Projects' pass-through arm. Two seams were PROMOTED rather than copied — `gateway/csv_export.py` (the formula guard, the BOM, the RFC-4180 writer) and `@/lib/export` (`filenameFromDisposition`/`saveCsv`) — with Projects converted to consume both. Fences: `tests/unit/test_crm_export.py` (47, every one measured red first; done-when 7's read-only rule asserted twice — no writer imported, every statement a `SELECT`) and `src/lib/export.test.ts`. ⚠️ **Repair round 1, 2026-08-11 — an independent verifier FAILED it and the reviewer asked for changes; three of the four defects were fences that passed while the thing they fence was broken** (full record: `crm_app.md` §9). **(1) DECISIVE:** both BFF proxies did `await res.text()` — a UTF-8 *decode*, which strips a leading BOM — so the BOM the gateway emits and `test_crm_export.py` asserts never reached the browser (measured node v22: `EF BB BF 4E 61 6D` in, `4E 61 6D 65` out) and done-when 5 was not met end to end; Excel on Windows renders the 3,993-row Zoho backfill mojibake. Both now read `res.arrayBuffer()`. **This fixes a LIVE Projects bug in the bargain: `api/projects/[...path]` has carried the same arm since WS-27ae, so `/projects/export/tasks.csv` serves BOM-less CSV. ⚠️ Evidenced exactly: WS-27ae is on `main` (`1de846a` ancestor via `ebf68f4`, PR #422) and `deploy` reported success on that SHA — **almost certainly live, not proven live**; a green job is not delivery evidence (non-negotiable 8).** Fixing only the CRM would have left the new shared fence documenting the broken shape as correct — the reach outside this ticket is deliberate and is called out here so it is not read as scope creep. **(2)** The done-when-1 parameter-parity fence compared the SHALLOW `route.dependant.query_params`; both list and export take filters via a class `Depends()`, so it was `set() - set()` on all four entities and adding a `city` filter to `records.ListParams` left all 46 cases green — it now recurses `dependant.dependencies` and asserts its own precondition. **(3)** `LIMIT :cap` bound at exactly `MAX_EXPORT_ROWS` could return exactly the cap after a concurrent insert (one transaction, but Postgres defaults to READ COMMITTED, so count and render take different snapshots) and ship a partial file with a 200 — it binds `cap + 1` and refuses on the RENDERED count, so complete-or-refused no longer depends on the count being current. **(4)** `X-Export-Rows` was a shipped no-op — the proxy forwarded two headers and this was not one; both proxies now forward it. `src/lib/export.test.ts` no longer greps for the defect it pinned (`toContain("await res.text()")`, commented "`res.text()` keeps the bytes") and instead RUNS both proxies end to end over a BOM'd body and compares bytes. **Both h and i-export are now MERGED (#425 `d471ae8`, #426 `7255344`) — every "not merged, not deployed" clause above is struck. The Projects BOM fix rides #426, so `/projects/export/tasks.csv` is repaired on the next deploy.** 🆕 **D-CRM-13 recorded 2026-08-11 (owner DELEGATED the call) + ticket WS-26h2 minted** — WS-26h's recorded CREATE gap is resolved by rule, not by reflex: **entry requirements gate the stage a caller CHOSE, never the stage the server DEFAULTED to.** The audit measured the inversion: `QuickCreateModal` sends no `status_id` and `ConvertDeal` has no such field, so every deal the PRODUCT creates lands in the default lane — the gap is reachable by direct API call only, while gating the defaulted path would 422 quick-create and every lead conversion the moment an owner gates the default lane (`admin.py` permits it unrestricted), and `organization_id` is requirable while NEITHER surface can supply it (`ConvertModal.tsx:126`), making those leads permanently unconvertible. ⚠️ **The audit also found a miscount that both `crm_app.md` and `pipeline.py`'s docstring carried: there are THREE ungated create paths, not two** — the third is `import_zoho.apply_record` → `core.upsert_by_zoho_id`, reached by the backfill route AND by `sync_zoho.pull_phase`, the **enabled** 600s loop, and it must stay ungated forever; a gate there starts refusing rows from the live upstream tenant on the next cycle. WS-26h2's done-when 8 is the structural fence (the existing one greps `apply_status_transition(` and would NOT fire on this change) and done-when 9 requires `test_crm_zoho_import.py`/`test_crm_zoho_sync.py` to pass with zero edits. **Gate label is a property of the SITE, not the ticket: AGENT-SAFE in `records._resolve_status`, OWNER-GATE (§6 WS-26 (a), running loop) anywhere `pull_phase` traverses.** **h2 is now BUILT 2026-08-11** on branch `claude/crm-command-center-tasks-i8l7n4` — **no migration**, one call in `records._resolve_status` under `if chosen:` and nowhere else, plus `pipeline.NO_EXISTING_RECORD` (a first-class "no stored row" shape; `None` is refused with a `TypeError` rather than read as one). The site is the one the label was stated against, so it stayed AGENT-SAFE. Done-when 9 held (neither Zoho test file edited). ⚠️ **18 mutants red across two rounds, and one is evidence rather than ceremony: the gate moved into `core.insert_row` keyed on `crm_deals` leaves both Zoho suites 136 GREEN** — the importer duplicates the statement instead of delegating — **so only the siting fence catches it.** ⚠️ **Repair round 1: done-when 8's PRESCRIBED fence was itself the defect** — "files containing the literal `_require_entry_fields(`" is a text match that stays green on the refactor it exists to stop (`import_zoho.apply_record` → `records._resolve_status`, which adds no call site and lands the gate on the enabled pull, `chosen` truthy because `apply_record` already sets `values["status_id"]`) and on an aliased import, while turning RED on a *comment* explaining why the path must stay ungated. Replaced by two AST fences — call sites, and transitive reachability from `sync_zoho.pull_phase` / `import_zoho.apply_module` — with the static, package-scoped limit stated; eight shapes pinned against synthetic packages. Done-when 6's `Decimal("0.00")` clause is unsatisfiable through `POST /crm/deals` (`DealIn.amount` is `float | None`) and was relabelled in the ticket rather than faked in the test. **Repair round 2 (re-verify PASSED):** the fence's self-test did not exercise `_crm_imports`' whole-tree walk — all eight fixtures imported at top level, so a `tree.body` mutant stayed 84 green — and that walk is load-bearing because **`core.py` cannot import `pipeline` at top level at all** (circular: `ImportError: cannot import name 'CLOSING_TYPES' from partially initialized module`), so the "one seam" mis-siting can only be written as a function-body import and `core.insert_row` is not reachable from the pull entry points. The fixture's import moved inside the function; the mutant now goes red. 🆕 **Banked board item (`crm_app.md` §9): WS-26h's own fence `test_the_zoho_pull_never_enters_the_stage_gate` carries the same text-match defect** — blind to an alias, red on a comment — but is a wart rather than an exposure, because the new reachability fence covers it transitively (`apply_status_transition` calls `_require_entry_fields` unconditionally; the residual is an aliased call from `broker_handlers.py` / `auto_lead.py`, neither reachable from the loop, and `CRM_AUTO_LEAD` is OFF). Riders: that coverage evaporates SILENTLY if the call is ever removed or relocated, and the shared AST helpers back BOTH fences, so one ~10-line follow-up fixes both once they are on `main`. ✅ **CLOSED 2026-08-11 as WS-26h-fence** (same branch, **test + docs only** — no `routes/crm/` change, no migration, no UI). `_GATE` became an **argument** rather than a module constant, so `_gate_call_files(package, gate)` / `_gate_reached_from(package, entries, gate)` answer for **two** gates through **one** mechanism (`_MOVE_GATE` = `("pipeline", "apply_status_transition")`, `_ENTRY_GATE` = `("pipeline", "_require_entry_fields")`); the helpers were **not** forked and no third mechanism was minted. WS-26h now carries the same two fences the entry gate has — `test_the_move_gate_is_called_from_exactly_two_files` (call sites) and `test_the_zoho_pull_never_enters_the_stage_gate` (**name kept**, now carrying the reachability claim its docstring always made), so both riders are discharged: the transitive coverage no longer has to hold. ⚠️ **Measured old vs new on the real package, applying and reverting each mutant — the last two are the whole point:** direct call in `import_zoho.py` red/red · **aliased** import **GREEN→red** · **indirect** route `import_zoho.apply_record` → `records.patch_record` **GREEN→red** (chain reported) · **comment** naming the call **RED→green** · baseline green/green. Synthetic self-test **8 → 15 cases**, both gates asserted on every case, and the move gate's `core.py` case uses a **function-body import** so it pins `_crm_imports`' whole-tree walk (repair round 2's F1 lesson, not re-opened). Suite **84 → 92**; four helper mutants red (`tree.body` → 2, `_resolved_call` losing imports → 15, `_gate_call_files` ignoring its gate arg → 10, `_gate_reached_from` ignoring it → 1). R8 does not bind: no SQL, no migration, no predicate. ⚠️ **Repair round 1 (2026-08-11): verifier PASSED, reviewer REQUEST-CHANGES on a P1 that was a REGRESSION against the deleted fence — RELATIVE IMPORTS.** `_crm_imports` gated on `node.module.startswith("gateway.routes.crm.")`, so `from .pipeline import …` (`node.module == "pipeline"`, `level=1`) matched nothing and `from . import pipeline` (`node.module is None`) was dropped by the `and node.module` guard. The substring scan caught ANY spelling; the first AST cut caught one. Measured old-vs-new on a copy of the real package: **six** shapes green-where-old-was-red (relative import · `from . import pipeline` · star import · `__init__` re-export · module-level call · a name bound to the package via `from .. import crm`), **and the decisive indirect route respelled `from .records import patch_record` was green on BOTH fences** — one line would have landed the transition on the enabled 600s pull with every fence green. Not hypothetical: `orchestrator` + `meeting_bot` already carry 19 relative imports and `pyproject.toml` selects no `TID` rules. All six now read, each pinned by its own synthetic case measured red first: suite **15 → 24 cases**, file **92 → 101**, **ten** helper mutants red. Two more fixed in the same round: reachability entered at `pull_phase` only, so a gate reached from the PUSH half (`push_records` / `apply_push_result` / `_settle` / `_fail`) reported `[]` while running every cycle — entry is now the cycle itself (`_sync_loop` / `run_cycle` / `_run_cycle_locked`), measured green-then-red; and the call-file docstring claimed FUNCTION-level siting that an assertion over `f"{module}.py"` does not have, corrected to claim only the file **deliberately**, since WS-26i-bulk done-when 1 moves the call to `apply_record_patch` inside `records.py`. ⚠️ **What stays blind, split by the ONE question that decides whether a hole is a REGRESSION against the substring scan this replaced — is the gate's own name written immediately before a `(`?** **(A) Name never written before a `(`, so the old scan was blind too — NOT regressions:** dispatch through a value (`_MOVE = apply_status_transition` then `_MOVE(…)`, `partial`, a cross-package callback); a registry or object holding the gate under ANOTHER name (`_REGISTRY["move"](…)`, `Registry.move(…)`); and `getattr(pipeline, "apply_status_transition")(…)` — **measured green on the old scan too**, because `")("` intervenes and the literal never appears. **(B) Name IS written before a `(`, so the old scan went RED — residual REGRESSIONS, exactly two, both left open deliberately:** a call qualified by something the graph cannot tie to a package module — `importlib.import_module("…pipeline").apply_status_transition(…)` and `_GATES.apply_status_transition(…)` where `_GATES` is a local object or class. ⚠️ **The reason recorded in repair round 1 for leaving them was FALSE and is corrected here:** it claimed closing them meant resolving unbound attributes against every top-level name, letting an innocent `db.close()` fabricate a chain — a reviewer disproved it by building the fix, and a narrow resolver reading only STRING CONSTANTS adds **no** edges to the real package, reddens both forms, and never looks at `db.close()`. **The real reason is reach, not risk:** `importlib.import_module` appears **once** in all of `apps/` + `packages/` (`orchestrator/declarative.py:100`, a plugin loader) and **zero** times in the gateway. Neither shape is the plausible refactor this fence targets; both are deliberate evasion, and a fence cannot be built against someone willing to edit the fence file. **(C)** An indirect route beginning at module level (module-level code is a call SITE but not an entry POINT). **(D)** Anything outside `routes/crm/*.py`. 🆕 **`WS-26i-bulk` recorded in `crm_app.md` §9 in the same pass — the spec-auditor's contract, verbatim** (`POST /crm/{entity}/bulk` + the `db`-taking patch seam): scope with explicit non-goals, nine done-whens, a **🟢 AGENT-SAFE (backend)** label whose reasoning is a property of the SITE (the seam extraction reaches no Zoho path — `import_zoho`/`sync_zoho`/`broker_handlers` do not import `records` at all, sit on the unbound `_get_db` seam, and `mark_dirty_on_update` lives BELOW the seam inside `core.update_row`, so §6 WS-26 (a) does not bind), a volume **warning rather than a gate** (`PUSH_BATCH_LIMIT = 500`, so a full 500-deal lane move fills one Deals push batch — intended under D-CRM-9), and a §10 block. **Bulk DELETE is explicitly OUT of scope** — it is the destructive verb and rides with duplicate-merge — and the multi-select UI is a separate ticket. ⚠️ **Authored by the auditor is not audited-as-written: it still wants a spec-auditor pass before dispatch**, because two of its criteria were keyed on the fence this same PR converted and carry dated correction notes. Done-when 1's anchor `test_crm_pipeline.py:1098` and test name are stale — the call-file assertion is now `test_the_move_gate_is_called_from_exactly_two_files` (`:1273`) and the name it cites is the reachability half (`:1301`) — but **the criterion survives and is stronger**, measured against a copy of the real package: extracting `patch_record`'s call into `routes/crm/bulk_seam.py` turns the call-file fence RED reporting `['bulk_seam.py', 'pipeline.py']` (`records.py` drops OUT, so the failure names the relocation) while the reachability fence stays GREEN, and being AST it now also catches an aliased call. Done-when 4's *"if reachability is wanted, parameterise `_GATE`"* is **already done** by this PR, so that claim costs a third constant rather than a helper change; its stated limit (`_tenant_session_functions()` answers for the seam's own body, not for its callees) is unchanged. Anchors re-measured at `a06fa6a` while pasting — all confirmed; one count made exact — **corrected in repair round 1**: 41 qualified `crm_records.patch_record(` call sites across `tests/unit/`, **32** of them in `test_crm_pipeline.py` (the 34 first recorded was `grep -c "patch_record("`, which counts prose and fixture strings, and has since moved to 37 because this ticket's own fixtures added more such text — the argument for the qualified convention). Two false "not merged" sentences in `crm_app.md` (WS-26h #425 `d471ae8`, WS-26i-export #426 `7255344`, both on `origin/main`) corrected — merged, deploy NOT independently verified. `pipeline.py`'s two-path docstring corrected in the same change (R4). R8 does not bind: no SQL, no migration, no predicate. Not merged, not deployed. The other four WS-26i items stay 🔴 NO-GO — do not widen into them. Also corrected this pass (R4): the spec header's "Nothing has ever written the Zoho tenant" was **false since 2026-08-06** and is now struck — the loop is RUNNING and every native write reaches the live tenant within one 600s cycle. · **e** cutover + retirement 🔴 (§6 WS-26 (c)). ⚠️ D15 coda: built single-Zoho-tenant by design; per-org credentials (migration 158) + per-org sync flags arrive with MT-1/MT-2, and D-CRM-3's org-wide read becomes org-scoped **by RLS**, not by hand-written predicates. (2026-08-08) |
| WS-27 | **Projects app — native PM** ~~+ ClickUp retirement~~ *(minted 2026-08-05)* | ⚠️ **AMENDED BY D52 (2026-08-24)** · ✅ a–t merged · **u–z MERGED to `main`** · aa–ag + S1–S6 merged · ab/ac/ae merged (#422) · ✅ **Wave 1 (al/am/bd) merged** · ✅ **Wave 2 slices MERGED + DEPLOYED 2026-08-12: ak-1 Modal (#429), ak-3 Toast (#430), be trgm index (#431, mig 170)** · 🔴 **bf theme sweep NOT BUILT (owed, gates the visual pass)** · 🟡 ak-2 Tooltip / ak-5 Skeleton NO-GO as written · c/g/h gated | `specs/project_management_app.md` §9.7 (sequencing) · board record 2026-08-09 | a b d e f i j k l m n **merged to main** (#390, #393, #394, #398 + fixes — the board's "BUILT on branch" wording is struck). ~~Open defect: **§11.12** — WS-27j's `notifications.deliverable` probes `project_clause`~~ ✅ **FIXED on #399** (assignees without a project grant were judged undeliverable, so assignment notified nobody). 🟡 **c** two-way sync waits on WS-1's BO-1a + BO-1b; 🔴 push enable (§6 WS-27 (b)) · 🔴 **g** cutover + retirement incl. the root-`AGENTS.md` constraint-8 amendment — ships in the g PR, never before (§6 WS-27 (c)) · **h** `gtd_items` retirement after e; the data move is 🔴. ~~Remaining letters: recurring, dependency UI, calendar view, search.~~ ✅ **the §11.2 ClickUp-parity backlog is CLOSED** — o recurrence · p dependencies+subtasks · q calendar · r ⌘K search · s shared task card · t timeline, all on **PR #399** with D-PM-11/D-PM-12 recorded. **Second reference studied 2026-08-09: `makeplane/plane` v1.4.1 (⚠️ AGPL-3.0 — patterns only, never code)** → `specs/plane_pm_research_2026-08.md` + spec §11.19: 12 shipped decisions validated, beyond-parity queue P-1…P-31 minted → **minted as dispatchable tickets WS-27u–z (spec §9.1)**: u intake/triage · v watchers+mention-diff · w read-path/history hardening · x spreadsheet+shown-fields · y board upgrades · z lifecycle policy (🟡 per-project, default off) + a deferred small basket, 2 owner questions ANSWERED same day → **D-PM-13** (project docs live in the knowledge base — creator-owned, grant-shared; PM links, never owns) · **D-PM-14** (public boards deferred). ✅ **WS-27u–z ALL BUILT 2026-08-10 on the restarted branch** (#399 merged; branch restarted from main per the merged-PR rule) — migrations **164** intake · **165** watchers · **166** lifecycle · **167** seed status colours · **168** delta-sync/tombstones; plus the **Tasks↔Projects continuity backport** (shared chips/cursor/QuickAdd/flash promoted to `src/lib`+`src/components`, both apps consume one implementation; remaining gaps recorded in HANDOVER). z's sweeper is wired as a `pm_lifecycle` workflow node — the scheduled workflow itself is an owner authoring step on the live box (workflows are DB rows, never files). ⚠️ granting `feature:projects`/`data:org:read` is §6 WS-27 (d) — D14's zero-consumer measurement is retired by this row. ✅ **Tenancy alignment AUDITED 2026-08-10 (D27): substantially aligned** — all 19 `pm_*` tables keyed, seam clean, no request-input tenant, no homonym. Fixed in the same pass: the generated RLS set was **stale AND carried a phantom `ALTER TABLE if`** (2 Projects tables would have promoted with no policy; the phantom would have aborted the window) — regenerated, generator taught to strip comments, and a new fence `test_the_generated_set_on_disk_matches_the_tables_that_exist` pins both. Two non-mechanical conversion sites recorded where the tenancy agents read them: `core.resolve_organization_id` (handover H2) · `run_lifecycle_sweep` (MT-1d). ➡️ **Next queue minted 2026-08-10 as spec §9.2** (after H2 landed on main; none needs a migration): ✅ **aa BUILT 2026-08-10** — the two tenancy residues Projects owned: `run_lifecycle_sweep` takes a required `organization_id` and refuses without one (MT-1d's named site + D27 (2) struck), and `pm.task.assigned` now carries the task's own org so `agent_dispatch` binds explicitly instead of running unbound. `routes/projects` holds **no H2 exemption at all** and sits at **0** unbound sites (`H2_BASELINE_ELSEWHERE` unchanged at 111 — the sweeper trades its unbound session for the resolver, which must stay unbound). Proven two-org on real Postgres: `tests/live/live_ws27aa.py`, 23 checks, mutation-measured. Two spec clauses struck as unbuildable/wrong and recorded in §9.2: the refusal cannot be written to the task timeline (that is tenant data — it is a WARNING log line), and MT-1d's "per-tenant loop" would be one tenant's workflow acting for all. Projects is no longer what gates phase 4. · **ab** view ergonomics (peek escalation, dirty-view affordances, palette action registry) · **ac** calendar week layout + per-day quick-add + honest overflow · **ad** Tasks↔Projects continuity round 2 (shared selection anchor, board-chrome convergence, cursor/quick-add on the flat lists) · **ae** export/delta-sync/small columns — BUILT 2026-08-10, both halves. ✅ **aa + ad + af + ag BUILT and MERGED to `main` 2026-08-10** (ab/ac were deferred behind the UI work, then BUILT 2026-08-10 — see below). **The owner's UI directive drove the wave**: *"the Kanban board and its lists, colour schemes, task cards… fix the UI for the shared components"*, then *"the UI for projects [must] match the theming configuration used in the Metorite… ensuring future development considers it."* Landed: one shared status vocabulary (`src/lib/statusAccent.ts`) + `TaskCardShell`/`StatusChip` consumed by both apps — **`pm_task_statuses.color` had been stored since migration 146 and rendered nowhere**, so every Projects lane drew the same grey; a themed categorical ramp `--cat-1…8` across all four manifests (owner-ruled) retiring `/tasks`' raw-palette debt; `/projects` joined the house shell and **gained a mobile UI, which it had never had** (no `useViewMode`, no `AppShell` branch). Fences: conformance rule 5 (raw palette classes, baselines that only go down), `test_category_and_keyword_agree`, and `test_seed_status_colours_match_the_shared_vocabulary`, which reads the TypeScript rather than mirroring it. ⚠️ **Two things this wave could NOT do and did not claim**: no browser is runnable in the build environment, so the **phone-viewport pass and the four-theme sweep are owed at review** — and this tree has **no structural or layout test at all**, so cross-app continuity is fenced by nothing but that look. 🔴 One contradiction needs an owner ruling: the shared card title is `text-[13px]` (ad's choice) against the house scale af established (spec §9.2 tail). ✅ **ab + ac + ae ALL BUILT 2026-08-10** and merged onto `claude/paca-research-task-management-a1f6zd` (PR #422, green): ab peek/dirty-views/palette-registry (§11.25) · ac calendar week + exact overflow (§11.24) · ae filtered CSV export (§11.26) + delta-sync feed with tombstones, migration **168** (§11.27). ~~**The dispatchable Projects queue is drained** — only c (blocked on BO-1a/b), g (cutover) and h remain~~ — **struck 2026-08-11: that was true of the a–ae queue for about six hours.** The same row goes on to mint WS-27ah–bd; §9.7 sequences them into four waves. What survives of the original claim: of the *lettered* queue, only c (blocked on BO-1a/b), g (cutover) and h remain, and h is the bridge to the Tasks derivation. 🆕 **SLICES S1–S6 belong to this row and were never listed here** (added 2026-08-10): S1 tenancy-residue UI · S2/S5 the task panel · S3 selection+bulk parity (which REVERSED a recorded WS-27ad decision by owner ruling) · S4 Projects-side conformance · S6 card pills. All merged to `main`; as-builts at spec §11.21–§11.23 and inside §9.2. A reader of this row alone could not have known they existed. 🔴 **WHAT THE PLANE MINT DROPPED** (traceability audit, 2026-08-10 — the reason this line exists): minting WS-27u–z from the Plane research silently lost **P-8** (child category distribution), **P-22** (timeline zoom presets / edge-drag dates / hover-to-place), **the inbox half of P-20** (two-pane notifications, mark-read-on-open, snooze — only the split unread badge shipped, marked "P-20 part"), and **the whole of research §4 item 14** ("small wins"), which never received a P-number at all and so was never eligible for minting. **P-9**'s adoption trigger names a ticket that does not exist (`161_projects_tenancy.sql` defers to "the ticket that onboards the second tenant"), so `clickup_id` stays globally unique and two orgs cannot import the same ClickUp workspace. Research §6's "restricted grant level" for contractors/clients is prose with no P-number, no D-number and no home. These are minted as **WS-27ah/ai/aj** (see spec §9.3). 🆕 **PACA READ TO THE SAME DEPTH, 2026-08-10** — four agents over `apps/web`, `services/api`, the agent/MCP/realtime layer and e2e. Minted as spec **§9.5** (WS-27au…bd + D-PM-19/20). **Paca is Apache-2.0** (plain — reuse is legally available with attribution), which is a DIFFERENT rule from Plane's AGPL; both are stated in **§9.0**, which also pins each repo to a commit SHA. **28 defects found in our own Paca record** (`paca_pm_research` §10–§11). The two that misled us most: we credited their migrations with "independently converging" on our idempotency discipline — **they have no ledger at all**, and one migration drops a column in the same file that backfills it; and we credited them with the presentation-vs-query-constraints split, when **their saved filter config is never read by the server**. We hold that property; they do not. ⭐ **THE CROSS-REFERENCE IS THE PAYOFF** — available only now that both repos have been read the same way: (1) **Paca ships Base UI too** (17 of 24 primitives), as does Plane's `propel` — two independent products chose the same substrate for exactly the set WS-27ak enumerates, so **D-PM-15 is now evidenced, not argued** (riders: Base UI has no Combobox, so that item is a build either way; and Paca carries a SECOND primitive library that arrived via a vendored registry — the failure this decision exists to prevent, observed); (2) **Paca is ahead on exactly one axis and it is ours** — its agent surface has no Plane counterpart; (3) on everything Plane covers **Paca is the weaker reference** (no multi-select, no bulk edit, no keyboard cursor in 61k lines, native HTML5 drag only), so importing from it there would be a regression. 🔴 **D-PM-19 — the agent autonomy gate.** **NEITHER reference has any approval or human-in-the-loop primitive for agents.** Paca's autonomy is its permission set exercised unilaterally; its "you MUST invoke a skill first" rule is a paragraph in a prompt with nothing enforcing it. So neither is prior art and **we design it**, at the tool layer — the UI contract, usefully, is an MIT library. Plus **D-PM-20** optimistic concurrency: neither has any, and with agents writing concurrently with humans, last-write-wins is a data-loss design that a revert affordance makes worse. 📎 **REFERENCE LINKS**: spec **§9.6** carries **66 permalinks** into the two pinned commits, each mechanically verified to exist with a real line range (two candidates failed that check and were fixed before inclusion). Fenced by `tests/unit/test_reference_links.py` — which found a real defect on its first run and was then mutation-measured; it SKIPS loudly rather than passing quietly when the read-only clone is absent, because that is the only check proving a path is real. 🆕 **SECOND-PASS PLANE READ, 2026-08-10** — owner: *"read the entire code and lift the features and the specifications from them."* Six agents read the whole monorepo (~3,000 TS files + the API): `apps/web` · API/data model · `packages/editor`+`apps/live` · `packages/ui`+`propel` · `apps/space`+`admin`+i18n · the traceability audit. Minted as spec **§9.4** (WS-27ak…at + four owner decisions + a banked list). **The finding that reframes it: almost none of Plane's interaction quality is Plane's** — propel wraps **Base UI** (MIT), the editor is **TipTap/ProseMirror** (MIT, and *we already ship TipTap v3*), collab is **Yjs/Hocuspocus** (MIT), palette **cmdk**, dates **react-day-picker**, DnD **pragmatic-drag-and-drop**. The AGPL wall blocks their glue — which we would rewrite for DESIGN_SYSTEM conformance anyway — so **the substrate is a shopping list, not a wall.** 🔴 **A SECURITY FIX CAME OUT OF IT**: reading their GHSA-4w5x-wc9w-f47x (a join write scoped at one end) found the same shape in `views.set_positions`, which validated the VIEW then wrote every caller-supplied `task_id` unchecked — **fixed 2026-08-10** (`15172bd2`), mutation-measured; the remaining `task_ids[]` endpoints are audited under WS-27as. ⚠️ Its first fence used `projects_user()`, which holds `*` incl. `data:org:read`, so it passed with or without the fix — **every scoping test uses `member_user`**. ✅ **D-PM-15 ANSWERED 2026-08-11: Base UI** — chosen over Radix and over building on `floating-ui`, on the cross-reference evidence (Plane's `propel` and Paca's 17-of-24 both chose it independently for exactly the WS-27ak set). **Unblocks Wave 2's WS-27ak in the order §9.7.2 fixed: Modal → Tooltip → Toast → Skeleton.** Three conditions ride with it, each already observed failing somewhere: every primitive gets a Metorite wrapper in `src/components/ui/` and call sites import ours (R7 — the conformance suite gains an import-restriction rule or it is advisory); the one-substrate rule binds **vendored registries** too (Paca carries `radix-ui` reaching exactly one file, inherited from a vendored registry — the back-door failure this decision exists to prevent); and **Base UI has no Combobox**, so WS-27ak item 4 was always a build, which is why WS-27bc sequences ahead of this decision rather than behind it. ⚠️ Licences are verified at install time — every licence claim in §9.4 comes from a manifest, not from a package. 🔴 **Three owner decisions still owed**: ~~D-PM-15~~ · **D-PM-16** org-wide vocabularies — *the most expensive item if wrong*, because dropping NOT NULL later is trivial but merging twelve projects' duplicate "Bug" tags is a judgement-call migration nobody can automate; **record it even if the answer is no** · **D-PM-17** the i18n *discipline* (5,181 keys/locale is their number for a smaller surface than ours; the extraction half is nearly free to prevent and unreviewable to retrofit) · **D-PM-18** RTL as logical CSS properties, free today, a full-surface sweep later. Also corrected in the research doc: the "per-board kill switch" we credited Plane with **does not exist** (`is_disabled` is read nowhere), the public roster refusal was incomplete (actor identity leaks twice more), and §5's refusal **bundled rich text with the collab server** — they are separable, `apps/live` is not required for an editor, and only the collab half survives. 🆕 **THE 21 MINTED TICKETS ARE NOW SEQUENCED BY IMPACT, NOT BY FINDING** — owner directive 2026-08-11: *"the features … which will make the most impact in the UI/UX … and can bring us to a state of usability as soon as possible."* §9.3–§9.5 had ranked by which research pass surfaced a thing; that is an archaeology order, not a build order. Recorded as spec **§9.7**, which owns sequencing only and edits **no ticket's scope**. ⚠️ **The re-measurement against `ebf68f4e` moved the order and corrected §9.4's own counts**, which had been written from a description of the tree rather than a count of it: **zero focus traps exist anywhere** (all 7 grep hits are the *word* "inert" in prose) against **69** hand-rolled `fixed inset-0` overlays · **there is no toast system at all** — no `useToast`, no `<Toaster>`, no `ToastProvider`, so a mutation today confirms itself inline or not at all, which promoted Toast above Skeleton inside WS-27ak · native `title=` tooltips in **125** files (§9.4 said ~157) · `animate-pulse` improvised in **26** (said ~20) · **no headless-primitive library in `package.json` at all** (no Radix/Base UI/Headless UI/cmdk/sonner/floating-ui — so WS-27ab's palette is hand-rolled too) · **zero `<Link>`/`<a href>` on any Projects task card** (124 `onClick`, 0 `role="button"`), so cmd/ctrl/middle-click cannot open a task in a new tab anywhere in `/projects` · and one item is **cheaper** than minted: `/projects` has zero `onContextMenu`, but `TaskCardShell` already *accepts* the prop and `/tasks` already ships `ContextMenu.tsx`, so WS-27bd(5) is wiring, not building. **The four waves**: **1 — papercuts** (al · am · bd) 🟢 nothing blocks it, **DISPATCHED 2026-08-11** · **2 — primitives** (ak Modal→Tooltip→Toast→Skeleton, + bc in parallel since Base UI has no Combobox) 🟡 **D-PM-15 is the ONLY gate in the whole sequence** · **3 — "does it lose my work"** (an autosave incl. save-on-unmount-if-dirty · at the living gallery, which is what stops waves 1–2 drifting apart) 🟢 · **4 — modern feel** (ao rich comments on the TipTap v3 we already ship · ah progress segments/timeline zoom/pins/recents). **Explicitly NOT on this path, recorded so they do not read as forgotten**: ap · aq · ai · az · ba · bb · the whole agent queue au–ay. 🔴 **The deferral has a real cost and it is stated, not buried** — four items are cheap now and expensive later: **D-PM-20**'s `updated_at` PATCH precondition (breaks every client at once if added after the delta-sync clients exist, and 168's `updated_at` already serves the feed so ONE semantic must cover both) · **WS-27ar**'s single generic pins table (else four `is_pinned` columns and a unifying migration) · **ap/ba**'s stored filter grammar (the saved-view corpus only grows). Recommendation put to the owner: take D-PM-20 + ar's table shape alongside Wave 1; ap/ba after Wave 3. 🔴 **WAVE 1 WAS AUDITED BEFORE DISPATCH AND CAME BACK MUCH SMALLER — the audit is the finding, not the build.** All three tickets returned **GO-NARROWED**, and **five of fourteen enumerated done-whens across them describe work that already exists or has no target in this tree**: al(6) selection self-heal is **shipped** (`projects/page.tsx:722-733` → `src/lib/selection.ts:105 prune`; the shipped comment even uses the ticket's own "select forty, narrow to three" example) · bd(3) clipboard honesty is **already true at all eight `writeText` sites**, /projects' own carrying a comment explaining why · al(3) lazy tooltips have **no target** (zero `Tooltip` components exist — moved into WS-27ak(2), Wave 2, where the dependency is visible) · al(4) selected-first ordering names no multi-select, and the nearest candidate is a permanently-visible chip strip with no "open" moment · bd(4) names no banner. Two more were re-scoped rather than struck: **bd(5) is a PROMOTION, not a build** — a generic ContextMenu already exists at `app/tasks/components/ContextMenu.tsx` wired at five sites, plus a second in email, so the likeliest outcome of the ticket as written was a **third** implementation, i.e. the CLAUDE.md §5 defect authored by the ticket meant to prevent it; and **am(2)'s "one HOC per layout" was struck as unenumerable** — it names no surface set, and an item that cannot be enumerated cannot be closed. 🔴 **al(5) produced the one genuine defect this audit found and one refusal.** The defect: `app/projects/lib/mywork.ts:64 isOverdue` has **no completion check at all**, so a finished task with a past due date renders overdue in MyWork — diverging from both the shared `taskCard.ts:190` and the SQL at `filters.py:182`, which is the strictest of the four (it excludes done *and* cancelled). The ticket's "seven predicates" is wrong: **three TypeScript + one SQL**, one of the three (`tasks/lib/waiting.ts:68`) deliberately different under a documented contract. The refusal: **"today counts as due" is under-specified and is now an owner question**, because adopting it inverts two deliberately-pinned assertions carrying their reasoning in comments (`taskCard.test.ts:174`, `mywork.test.ts:35-42` — *"Pins `<` rather than `<=`"*) **and** changes SQL, dragging R8 and R6 into a ticket labelled "logic-only, no dependency"; our store is timestamp-granular and the reference is date-granular, so it is a semantic choice, not a bug fix. ⚠️ **Two figures in §9.7.1 were MINE and were wrong**, corrected in place with the method named: native `title=` is **157** files (`title="` alone is 125 — §9.4's original figure was right and my narrower grep was not), and `/projects` has **122** `onClick` lines, with `role="button"` **not** absent since `TaskCardShell.tsx:74` emits it for every card — the real gap is the missing `<a href>`. ⚠️ **A fence limit that binds every wave from here**: `vitest.config.ts` is `environment: "node"` with `include: ["src/**/*.test.ts"]`, so **`.tsx` tests are not collected and no jsdom/happy-dom/@testing-library is installed — React rendering cannot be unit-tested in this tree at all.** Every rendering done-when is review-only until that substrate decision is taken deliberately, and no papercut ticket may take it. ✅ **WAVE 1 BUILT, VERIFIED, REVIEWED — on `claude/paca-research-task-management-a1f6zd`, NOT merged to `main`, NOT deployed.** Shipped: **al** `ControlLink` on the list/table task **title cell** (an `<a>` cannot wrap a `<tr>`) — the app's first `<a href>` on a task, so cmd/ctrl/shift/middle-click finally open a tab · the `data-prevent-outside-click` walker (⚠️ **its motivating case does not exist yet** — nothing in /projects portals a picker out of a dropdown; built for Wave 2's Modal/Combobox and recorded as such rather than implied) · MyWork's overdue predicate folded onto the shared one. **am** `LayoutBoundary` around all six /projects canvases — **the tree had ZERO error boundaries**, so one malformed group shape blanked the app; Retry re-mounts by bumping a key; `EmptyState` gains a disabled-not-hidden third arm. **bd** the ContextMenu **promoted** to `src/components/` behind a re-export shim (/tasks' five call sites unedited) and wired to /projects cards off a new `lib/taskMenu.ts`; per-row pending/error in `RelationsBlock`. Verifier **PASS**: 84 files / 1866 tests / exit 0, all three al refusals confirmed by diff (`grep '^+.*<='` empty; `filters.py` and `waiting.ts` absent from `--name-status`), merge losslessness checked mechanically per file, and it specifically confirmed **nobody faked a fence** (no `.test.tsx`, no jsdom, no `.only`/`.skip`, `vitest.config.ts` untouched). 🔴 **The adversarial reviewer's P1 was REFUTED by direct browser evidence, and the method is the point**: it argued that Enter on a now-focusable task title escapes the canvas keydown handler (true — `stepCursor` returns null at `cursor < 0` and `TaskList.tsx:171` returns *before* `preventDefault`) and therefore performs a full-document GET that reboots the SPA and discards filters/selection/view mode. It does not. Reproduced in real Chromium with React-style **root delegation**: the default action of Enter on an anchor is *to dispatch a click*, which `ControlLink` intercepts (`button=0 detail=0` → `preventDefault`) — **zero navigations, URL unchanged**. A repair round for a non-bug was avoided by running it rather than reasoning about it, and this is the first time a UI behaviour claim in this repo was settled by a browser instead of by argument. **Three review findings WERE real and are fixed**: `ControlLink` had no themed focus ring (~200 new focus stops drawing Chrome's default outline) — fixed by extending the **existing** `.cc-control:focus-visible` declaration with a `.cc-link` selector rather than minting a second ring, and deliberately **not** by applying `.cc-control`, which would have imposed control label weight/tracking/transform on every task title; the anchor's native draggability stole click-drag text selection (`draggable={false}`); and `mywork.ts`/`mywork.test.ts` still carried the retracted "rendered overdue in My Work" claim the spec had already corrected. Two more findings closed from the verifier: the empty-`positions` write (the context menu's "Change status" sends `positions: []` on every use) now has `test_an_empty_position_list_is_a_no_op_and_never_wipes`, mutation-measured — adding an authoritative DELETE turns it red on the *survival* assertion, which is the half that matters since `written == 0` alone passes a wipe-then-write-nothing; and `LayoutBoundary`'s own rationale was wrong (React unmounts the subtree below a boundary *before* rendering the fallback, so the key bump is belt-and-braces — the bump that earns its place is `page.tsx`'s `canvasKey`). 🔴 **ONE DEVIATION NEEDS OWNER SIGN-OFF, and it is not an agent's to close**: §9.5.2's done-when said the menu's items derive from `app/projects/lib/commands.ts`. That registry holds `go.*`/`view.*`/`panel.*`/`project.*`/`help.shortcuts` and **nothing task-scoped**, so satisfying it literally yields a card menu reading *"Widen the task panel · Custom fields · Import from ClickUp"*. The implementer built a second registry at task scope and **rewrote the acceptance criterion**, fencing disjointness in both directions instead. The argument is sound and documented in three places — but an implementer editing its own acceptance is a reviewer/owner call. The literal alternative (extend `commands.ts` to task scope) also moves the `g`/`v` sequences and the printed `?` sheet, so it is a ticket, not a side effect. ⚠️ **Still owed and NOT fakeable in this environment**: the four-theme sweep (Fluent/Material/Graphite) on the error fallback, the disabled CTA and the /projects context menu · the tab-order change from ~200 new anchor focus stops · and **`emptyStateCopy`'s third arm needs TWO edits, not one** — both call sites discard `copy.action` unconditionally, so passing `canCreate: false` alone would ship the hidden CTA the arm exists to prevent while its unit test stays green (§11.29). ✅ **THREE DECISIONS TAKEN 2026-08-11 (owner-delegated: "make the decisions as per what you recommend"), recorded in spec §8 as D-PM-21/22/23 so none is re-asked.** **D-PM-21 — UI behaviour is verified in a REAL BROWSER (Playwright), narrowly; jsdom is REFUSED.** jsdom looks cheaper and buys the wrong half: it has **no layout engine**, so scroll-lock/scrollbar compensation, collision-aware positioning, viewport flip and real Tab order — the behaviours most likely to be silently wrong in Wave 2 — are unverifiable under it, and it would mint a second test environment that still cannot answer the question. Playwright is switching on infrastructure that already exists and is idle (`playwright.config.ts`, six `e2e/*.spec.ts`, Chromium at `/opt/pw-browsers`). ⚠️ Installed browser is **chromium-1194** while the packaged Playwright wants 1223, so a spec may need an explicit `executablePath`; **never run `npx playwright install`**. Scope is deliberately narrow — one spec per primitive, asserting only what no other method can see (focus in on open, Tab wrapping at both ends, background genuinely `inert`, Escape caught at the dialog not on `document`, focus returning to the opener never `<body>`, scroll locked without the page shifting). NOT a broad e2e suite; a rotting suite teaches people to ignore red. **The evidence is the same day's P1**: a plausible, well-argued and WRONG finding cost minutes to refute in Chromium and would have cost a repair round otherwise. **R7: advisory** — nothing can test the existence of a spec file until a WS-27ak slice lands one. **D-PM-22 — overdue stays timestamp-granular (`due_at < now()`); today does NOT count as due.** The upstream's date-granular semantic is REFUSED and recorded so it is not re-proposed: adopting it inverts two assertions that deliberately pin `<` over `<=` **and** carries their reasoning in comments, and changes SQL, dragging R8/R6 into a "logic-only" ticket. What IS required and is now true: all four predicates agree a finished task is never overdue. **D-PM-23 — `taskMenu.ts` is a second registry at a different scope, and that is correct; WS-27bd's deviation is ACCEPTED.** The original criterion was unsatisfiable — `COMMANDS` holds nothing task-scoped, so a menu resolving to it reads "Widen the task panel · Custom fields · Import from ClickUp". Condition, already fenced: the two registries stay **disjoint in both directions**. Extending `commands.ts` to task scope stays possible but is a ticket, not a side effect — it moves the `g`/`v` sequences and the generated `?` sheet. 📌 **Process point recorded deliberately**: an implementer rewrote its own acceptance criterion. The reasoning was right and disclosed in three places rather than buried — that is the behaviour we want — but the decision to change acceptance is a reviewer/owner call, because **acceptance a builder can silently edit is not acceptance.** ~~🔴 **Owner question still open from al(5): does "today count as due"?**~~ ✅ **ANSWERED — D-PM-22, no.** Adopting it inverts two deliberately-pinned assertions carrying their reasoning in comments AND changes SQL, dragging R8/R6 into a "logic-only" ticket; our store is timestamp-granular, the reference is date-granular. As-builts: **§11.28** al · **§11.29** am · **§11.30** bd. 🔴 **WAVE 2 AUDITED 2026-08-11 — and the audits corrected TWO OF MY OWN DECISION RECORDS, hours after I wrote them.** **(1) D-PM-15's rider 3 was FALSE.** I recorded *"Base UI has no Combobox"* — verified against the registry, **`@base-ui/react@1.7.0` ships `combobox`**, plus `tooltip`, `toast`, `dialog`, `drawer`, `context-menu`, `alert-dialog`, `select` and `popover`; only **`skeleton` is genuinely absent**. The false claim came from reading the `@base-ui-components` line inside the *pinned* Plane/Paca clones — stale by a package rename plus seven minor versions, i.e. the same measurement-from-someone-else's-manifest failure this board keeps recording. 🔴 **And the name that research implies is DEPRECATED**: `@base-ui-components/react` says *"Package was renamed to @base-ui/react"* and its `latest` is stuck at `1.0.0-rc.0`, so installing the name the evidence points at ships an RC of a renamed package. The correct pin is **`@base-ui/react@^1.7.0`**, MIT **read from the package itself** (`npm pack` + extract), five runtime deps all MIT, React peer satisfied at 19.2.4, `date-fns` peer optional. Next/Turbopack interop is **unverified** and recorded as unknown. **(2) D-PM-21 understated itself, and the understatement had spread.** ✅ **Playwright RUNS HERE — verified twice independently, `e2e/theming.spec.ts` → 20 passed, exit 0.** `playwright.config.ts:22-27` already reads `PLAYWRIGHT_EXECUTABLE_PATH` (it landed in #424), so **no config edit and no `npx playwright install`** — but the two env vars are **required, not optional** (`browsers.json` pins Chromium **1223**; `/opt/pw-browsers` has **1194**), and ⚠️ **`npm run test:e2e` sets neither and therefore fails today.** 🔴 This retires a claim I repeated in every Wave 1 as-built and several reports: *"the four-theme sweep is owed at review — no browser runs in this environment."* **A browser runs here**, and `e2e/theming.spec.ts` already asserts Fluent/Material/Graphite control personality. What is genuinely unfenced is *cross-app layout continuity*, not "anything in a browser" — the weaker claim was repeated until it became received truth, which is how a tree acquires a false constraint. ✅ **WS-27ak — GO-NARROWED to item (1) Modal.** The six target dialogs are not a guess: a previous author already enumerated them at `app/projects/page.tsx:968-974`'s `overlayOpen` (ShortcutsSheet · SearchPalette · ImportClickUp · FieldManager · TagManager · LifecyclePolicy), each owning its own `fixed inset-0`, so **`page.tsx` — the hottest file in the tree, edited by both of today's merges — is not touched**. Re-measured, five of the ticket's numbers were wrong: "69 `fixed inset-0`" is **70 files / 95 occurrences**, of which only **60 across 48 files are dialogs** (21 are empty dismiss-scrims, 12 drawers, 2 prose); "seven hand-rolled copies" derives from nothing — `/projects` has **six**; `src/components/ui/` **already exists**, so this extends a home rather than minting one. ✅ Zero focus traps and zero `inert=` attributes **confirmed**. 🔴 **One done-when was factually impossible**: *"Escape captured at the dialog, not on `document`"* — Base UI binds `keydown` on `ownerDocument`, so it is restated as the observable it meant (one Escape closes one surface; `page.tsx:1039` must not double-fire — it returns early on `overlayOpen`, and a wrapper that takes over open state silently breaks that suppression). 🔴 **A Modal already exists** at `email/components/automation/ui.tsx:15` with five consumers — the WS-27bd(5) ContextMenu situation repeating, and building the ticket as written authors a second one. ⚠️ Two costs: conformance **rule 8** trips the rule-count fence, so the slice necessarily edits the root **`CLAUDE.md`** (all three docs say "seven"); and `DESIGN_SYSTEM.md` has **no overlay/z-index section at all** while the tree carries `z-40…z-95` and six different backdrop colours, `bg-black/60` passing all seven rules today because `PALETTE_CLASS` omits `black`/`white`. **The free harness is the argument for Modal first**: driven in Chromium with zero mocking, `?` opens ShortcutsSheet with `activeElement: BODY`, focus escaping after 6 Tabs, `[inert]: 0`, and focus returning to an arbitrary anchor. 🔴 **Items (2) Tooltip and (5) Skeleton are NO-GO as written** — their done-when is a count of the problem, not a definition of done. 🔴 **WS-27bc — NO-GO; the blocker is documentation, so the doc fix IS the ticket.** No "Done when" at all. A third is **already shipped** (`MIN_QUERY = 2` in `search.py:72` and `projects/lib/search.ts:27`, consumed by `TriageRail`), and `email/RecipientInput.tsx` is a working hand-rolled long-list picker with combobox/listbox/option roles — a fourth implementation would be the §5 defect the ticket meant to prevent. **Its central justification is FALSE**: the minimum does not stop *"a leading-wildcard scan"* — `'%ab%'` is exactly as unindexable as `'%a%'` and `pg_trgm` appears **zero** times in `infra/`; it bounds the result set, which is what the gateway's own docstring says. Its 300ms contradicts the shipped 180ms, against **seven** ad-hoc debounce copies and no shared helper. And it wants scroll-pagination + server search + an unfiltered fallback in one surface, but `/projects/search` is **capped-not-paged by a recorded decision** with no unfiltered mode while `/projects/tasks` is paged with no minimum — two endpoints, incompatible contracts, and reopening that is a decision, not an edit. Only **scroll-pagination is genuinely unbuilt**. Re-sequenced **behind** WS-27ak. 🆕 **WS-27be MINTED from the bc audit — bigger than the ticket that found it.** `idx_pm_tasks_fts` (migration 146) is a `to_tsvector` GIN index and **`ILIKE` cannot use it**, so every `/projects/search` is a seq scan + two joins + a full sort while an index that looks like it covers the case sits unused; and `filters.py:202` gives the LIST endpoint's `q` **no minimum at all**, so `GET /projects/tasks?q=a` fires unbounded — the exact query WS-27bc claimed to prevent, at the one endpoint where nothing prevents it. ⚠️ R8: a plan question, so it must be `EXPLAIN`ed against real Postgres — a green unit test proves nothing. ✅ **WS-27ak SLICE 1 (Modal) BUILT + REPAIRED 2026-08-11 — on `claude/paca-research-task-management-a1f6zd`, NOT merged, NOT deployed.** `src/components/ui/Modal.tsx` wraps **`@base-ui/react@1.7.0`** (MIT, verified from the package; **not** the deprecated `@base-ui-components/react`), and **all six `/projects` dialogs** moved onto it — the set a previous author had already enumerated at `page.tsx:968-974`'s `overlayOpen`, so **`page.tsx` is byte-identical to `main`** and the window-Escape suppression it owns cannot have regressed. **This is the first primitive in the tree with a real focus trap**; before it, 70 files carried a `fixed inset-0` and **zero** trapped focus. **Verified independently**: `tsc` 0 · vitest **85 files / 1916 tests** (baseline 1910; +4 conformance rule 8, +2 the repair's new structural fence) · `next build` 0 (which also settles D-PM-15's "Turbopack interop unverified") · **Playwright `e2e/modal.spec.ts` 10 passed** — the first slice fenced in a real browser under D-PM-21. The verifier re-derived the baseline in a detached worktree rather than trusting the write-up, and re-applied the `overlayOpen` mutation itself (wrapper owns open state → the `g`-sequence test goes red at `modal.spec.ts:177`). 🔴 **ONE ACCEPTANCE CRITERION IS NOT MET AND IS A BOARD CALL: "the background is `inert`, not merely covered."** `@base-ui/react@1.7.0` cannot deliver it — `FloatingFocusManager:339` passes `ariaHidden`, `markOthers` defaults `inert: false`, and nothing in the package ever passes `inert: true`; the only real `inert` is on the portal's own `InternalBackdrop` while **closed**. Measured in Chromium: `[inert] = 0`, background carries `aria-hidden="true"` + a `data-base-ui-inert` marker. So **screen readers cannot reach the background and Tab cannot leave the dialog, but Ctrl+F still finds the page behind the scrim.** Closing it means an upstream change or the wrapper re-implementing `markOthers` — a parallel seam CLAUDE.md §5 forbids. The implementer stopped and wrote it down rather than reporting the criterion met. ⚠️ **TWO ITEMS IN THE DISPATCH BRIEF WERE WRONG AND ONLY BUILDING FOUND THEM**: `outsidePressEvent="intentional"` is **not a prop** in 1.7.0 (it is computed, and resolves to `intentional` whenever a backdrop exists — so the fence pins the backdrop, not a prop that does not exist); and `markOthers` does not set `inert`, as above. Both came from the audit; both were verified against the installed package. 🔴 **REVIEW FOUND A REAL P1 WITH A REACHABLE PATH, now fixed**: the wrapper's documented `finalFocus` fallback **did not exist** — Base UI drops a disconnected opener and then focuses nothing, leaving `<body>`, the one outcome the docstring forbade. Reachable via a dialog this slice converted: **ImportClickUp's only trigger is the empty-state button**, which the tree refetch replaces *while the dialog is still open*. Repair round 1 implements the fallback in the wrapper (explicit ref → captured opener if still connected → `<main>` landmark with a borrowed `tabIndex`) and says plainly what happens when none exists, instead of promising a guarantee. Also fixed: `max-h-full` was baked into the popup base so every call site's height was a dead knob (the importer rendered ~10% taller than designed). 🔴 **THE RECORD LIED ABOUT THE CODE IN FOUR PLACES, INCLUDING THE AUTO-LOADED CONTRACT.** `Modal.tsx`'s own header and `DESIGN_SYSTEM.md` §4a both asserted the `inert` property this very slice disproved; `DESIGN_SYSTEM.md` additionally named **conformance rule 8 as the fence against a hand-rolled scrim, which it does not catch** (rule 8 only forbids importing the substrate; a `fixed inset-0` div imports nothing — an R7 violation). Repair round 1 corrects all four, adds a **real** structural fence (none of the six converted dialogs may contain `fixed inset-0`, plus a companion that they still import `Modal`), and labels the un-fenceable half advisory. ⚠️ **And one of the implementer's own mutation records did not reproduce** — the verifier removed `Dialog.Backdrop`, rebuilt, and got 8 passed, because `DialogPortal` renders an internal backdrop whenever `modal === true`. The assertion *is* a genuine discriminator (it goes red under backdrop-removed **plus** `modal="trap-focus"`), so it was a false ledger entry, not a dead fence; the record now carries the reproducible measurement. ⚠️ **ENVIRONMENT HAZARD, fixed and worth keeping**: a stray untracked `zzprobe.config.ts` left in the shared checkout by an agent failed type-check and took `npx next build` down — and **a failed build leaves `.next` without a BUILD_ID, which stops Playwright's webServer starting at all**, i.e. one scratch file silently disables the entire browser fence. Removed; build back to 0. Agents get their own worktree and must clean up. Separately, `next build` twice failed on a Google-font fetch and passed on retry with no code change — a network transient in this environment, not attributable. ⚠️ **Still owed**: the four-theme sweep (Fluent → Material → Graphite) on `/projects` and a neighbour — nothing in this tree tests layout, and the description-wrap and max-height changes are exactly what that pass would catch. Items **(2) Tooltip** and **(5) Skeleton** remain **NO-GO**: their done-when is a count of the problem, not a definition of done. The second `Modal` at `email/components/automation/ui.tsx:15` (five consumers) is recorded for retirement, untouched. 🔴 **SESSION ENDED 2026-08-11 ON A WEEKLY API LIMIT (resets 2026-08-13 04:00 UTC), MID-WAVE. Read this whole note before touching WS-27ak/be/bf — nothing below is verified.**
| **WS-39** | **Projects · Tasks · Calendar — the re-plumbing** *(minted 2026-08-24)* | ✅ **S1 BUILT + REPAIRED r1 2026-08-25** · ✅ **S2 BUILT** (gate corrected r1) · ✅ **S3a-server + server-2 BUILT** (migs 187/188, R8-verified) · ✅ **S3a-client slices 1+2+3 BUILT 2026-08-25** (spine, browser day planner, and every BROWSERLESS surface — agent planner, day-summary, agent apply and the nightly sweep; flags `NEXT_PUBLIC_TASKS_LENS` + `TASKS_LENS`, both default OFF, mismatch reported on `/version`; R8 10/10 and 15/15) · ✅ **slice 4 BUILT** (client connector excision) · 🟢 **slice 5 next** — the CRUD + AI tail (H-33) · 🔴 **H-34**: owner adds both vars to `.env.example` · ✅ **S3b + S3c BUILT 2026-08-26** (migs **189/190**, R8-verified two-org on real Postgres — 37 + 22 checks; both ship **INERT**, the move is a function the migration defines and never calls) · 🔴 **running** either stays owner-gated (§6 (f), H-29) · all on PR #91 (review verdict MERGE-AFTER-FIXES; fixes applied, see the repair-round record below) | `project_management_app.md` **§12** + `task_manager_app.md` **§13** + `calendar_focus_os.md` **§10** · **D52 · D53 · D54** | Owner directive 2026-08-24, three decisions in one breath: kill ClickUp, make Tasks the personal lens over Projects, lift Calendar into its own app. **Five slices, sequenced below — the order is load-bearing, not a preference.** 🔴 Slices **S3b** and **S3c** (the `gtd_*` backfill and drop against a live database) are **OWNER-GATE**, registered in §6 by D53.5. |

🆕 **S3b + S3c BUILT 2026-08-26 — migrations 189 and 190, and they ship INERT.**

The half that was always agent-safe is done: §6 (f) gates *running* the move
against a real database, not building it. What landed is the whole mechanism,
proven, with the gate still standing.

**The shape that keeps the gate intact.** 189 **defines** `gtd_backfill_to_pm()`
and never calls it; 190 defines a guard that refuses unless two independent
conditions hold. Applying both moves no rows and drops no table. That is
deliberate and it is the point: the deploy ladder runs migrations **unattended,
before services restart** (R6), so a migration that called the backfill would
execute an owner gate from a `git push`. `test_gtd_backfill.py::
test_189_defines_the_backfill_but_never_calls_it` is the fence, verified red.

**S3c needs BOTH an armed row and a clean data check**, because "the data looks
migrated" is also what a half-finished move looks like, and "somebody armed it"
is also what an over-confident afternoon looks like. One is a fact, the other is
a human taking responsibility; a `DROP TABLE` we cannot roll back should need both.

🔴 **Three defects this found, two of them in its own work:**
1. **A soft-deleted task would have been RESURRECTED.** `gtd_items.deleted_at` is
   an undoable soft delete ("vanishes from every view", `items.py:389`); the first
   draft carried those rows over as ordinary tasks, which would have refilled every
   member's list with what they had thrown away — irreversibly. Now
   `deleted_at → archived_at`: hidden, not destroyed, and it still satisfies S3c.
2. **S3c could not drop `gtd_items` at all.** S3b's own `gtd_backfill_plan` view
   reads it, so Postgres refused. Measured, not predicted — the first S3c run died
   on it. Left unfound it would have surfaced while ARMED and mid-cutover.
3. **A claimed property that was false.** The S3b suite advertised itself as
   re-runnable and was not: teardown matched `user_id LIKE '%s3btest%'`, which
   cannot reach the literal `'anonymous'`. Only the mutation run exposed it.

📌 **Investigated and NOT a defect.** `uq_pm_projects_personal_owner` is unique
on `lower(personal_owner)` with no `organization_id`, which reads like a
multi-tenancy hole — a second org cannot give a personal project to an email that
already has one. It is consistent: `app_user.email` is globally unique
(`app_user_email_lower_key`), so D-MT-1(a) holds and one email is one person in
one org. Recorded because the index looks wrong on its own, and the next reader
will re-derive the same alarm. 189 nonetheless carries an explicit tenant
predicate and **stops with a named exception** rather than a duplicate-key error
if the invariant is ever false — a migration bypasses RLS, so it cannot lean on
the reasoning `_load_personal_project` leans on.

📌 **Scope, stated rather than assumed.** S3c drops **only** `gtd_items` and
`gtd_waiting` — the two tables S3b replaced. The local project tree
(`gtd_projects`/`spaces`/`folders`/`contexts`/`attachments`) waits on **slice 5**,
because slice 4 established that family is a PORT and not a deletion. `gtd_horizons`
(WS-21), `gtd_reviews` (WS-18), the five `gtd_people*` tables and D53.6's three
Calendar tables all survive, each pinned by name.

📌 **Judgement call for review.** A LOCAL `gtd_project` becomes a **sub-project**
of the owner's personal project rather than being flattened away. Privacy is
unchanged (a grant naming one address) but it now appears in that member's
`/projects` list, where a `gtd_project` never did. The alternative loses the
member's own structure irreversibly, which R6 makes the worse bet.

📌 **Finding for the board, not fixed here:** `infra/postgres/schema.generated.sql`
is stale — it does not know 188's columns, and 187/188 did not regenerate it either.
The README requires a refresh alongside each migration. Regenerating now would mix
20+ migrations of unrelated drift into this PR.

🆕 **WS-39 MINTED 2026-08-24 (owner-directed).** The directive, in the owner's
words: *"remove all the ClickUp integrations… the projects app becomes the ClickUp
clone itself"* · *"the tasks app is just the personal view of the company-wide
projects… and in addition the tasks app has personal tasks that are not configured
in the projects app"* · *"remove calendar from the tasks app and make it into an app
by itself under personal center."* Recorded as **D52/D53/D54**; this row sequences
them. Three scoping questions were put to the owner before minting so none is
re-asked: **both** ClickUp importers go (not just the live-API one), `pm_*` is the
single store (not two stores split by privacy), and Calendar is **personal-scope**
(not personal + Center on day one).

**The five slices, in order. The order is the deliverable.**

| # | Slice | Gate | Why it sits here |
|---|---|---|---|
| **S1** | ✅ **BUILT 2026-08-24 · REPAIRED 2026-08-25 (r1).** **ClickUp excision** — delete the ingestion source, `skill-clickup-sync`, the `ClickUpProvider` arm, the poll scheduler, both importers, the frontend `ImportClickUp.tsx`, the catalog/OAuth/env entries, and the `scripts/clickup_sync.py` one-off. Columns STAY (D52.3); **so do the `task_accounts` ROWS**, which is what r1 had to answer for — the scheduler now skips them and the dead connect/sync UI is gone. | 🟢 AGENT-SAFE | Nothing else depends on it landing first, and it is the only slice that is pure deletion — so it is the one that can be verified by "does anything still import this". Doing it first stops the other four having to preserve a dead arm. |
| **S2** | ✅ **BUILT 2026-08-24.** **The Calendar app** — the surface leaves `/tasks` for `/calendar`, a ninth `live` pane in Personal Center; `nav.test.ts` 8→9; `launch_surface.md` §2 updated. Still reading `gtd_items`. ⚠️ Two amendments measured in the build: there was **no `/tasks/calendar` route** (it was a `ViewKey`, so this is an extraction, not a rename), and the gate stays **`feature:tasks`** — a new slug ships the app dark to everyone (D54.1). | 🟢 AGENT-SAFE | Deliberately BEFORE the store move. It is a route + registry change with no data semantics, so it lands and is reviewable on its own; folding it into S3 would put a nav change and a store change in one diff. |
| **S3a-server** | ✅ **BUILT 2026-08-24.** The store learns to hold a block — migration **187** puts `scheduled_start`/`_end`, `flexible`, `is_hard_date`, `actual_start`/`_end` on **`pm_task_personal`** (D53.7), plus `GET /projects/my/calendar` on a half-open window and merged-pair PATCH validation. | 🟢 AGENT-SAFE | Had to come first: the Calendar lens cannot leave `gtd_items` until `pm_*` can answer "when am I doing this". R8 caught **two real 500s** a hermetic suite could not see — the new instants missing from `TIMESTAMP_COLUMNS`, and the window params fed through a COLUMN-keyed coercer. |
| **S3a-server-2** | ✅ **BUILT 2026-08-25.** The overlay learns the LAST per-member fields — migration **188** adds `important`, `leveraged`, `deep_work`, `kept_mine`, `sort_key` and the Waiting-For quartet `waiting_on`/`delegated_at`/`expected_by`/`last_nudged_at` to `pm_task_personal` (**D53.8**), wires them through `PersonalIn` / `_apply_overlay` / `_MY_TASKS_SQL`, and adds a merged-pair since-when check. | 🟢 AGENT-SAFE | **Had to come before S3a-client, not with it.** H-33 said so in terms: re-pointing the UI without these columns stops persisting the matrix flags, loses Waiting-For entirely and resets every manual drag order — silently, on a 200. R8 caught three more a hermetic suite could not see: `clarified_at` already existed (147) and was WRITTEN-but-never-projected; the fake's `ON CONFLICT DO UPDATE` dropped NULLs so "clear a field" passed while doing nothing, for **every** overlay column; and `_upsert_personal`'s hand-rolled binds refuse a bare dict on the table's first jsonb column. |
| **S3a-client** *(slice 1)* | ✅ **BUILT 2026-08-25.** **The spine re-points, behind a flag.** `NEXT_PUBLIC_TASKS_LENS` (default **OFF**) + `app/tasks/lib/lens.ts` — a `GtdItem` adapter over `/projects/my/*` — and eight functions in `tasks/lib/api.ts` branching to it: `fetchItems`, `apiCapture`, `apiPatchItem`, `apiArchiveItem`, `apiDeleteItem`, `apiRestoreItem`, `apiPurgeItem`, `apiDelegateItem`. Server addendum: `GET /projects/my/tasks/{id}`, `include_archived`, `CaptureIn.notes`, and the three facts the projection was dropping. ✅ **Slice 2 BUILT 2026-08-25** — the day planner (`TaskSource`; four routes under `/projects/my/calendar/`). ✅ **Slice 3 BUILT 2026-08-25** — the browserless surfaces + the `TASKS_LENS` gateway flag. ✅ **Slice 4 BUILT 2026-08-25** — the client-side connector excision. 🟢 **Slice 5 next** — the CRUD and AI tail (H-33). | 🟢 AGENT-SAFE | Expand half of R6: new readers, old tables intact, nothing dropped. **The flag is not optional and cannot be flipped yet** — `gtd_items` still holds every existing task and S3b has not run, so an early flip does not degrade the app, it EMPTIES it, on a 200. |
| **S3b** | **Backfill** — surviving `gtd_items` rows move into their owners' personal `pm_project`s; overlay columns map 1:1 onto `pm_task_personal` (D53.2). | 🔴 **OWNER-GATE** | A data move against live customer data — the WS-29 cutover class. AGENT-SAFE to **build and R8-test against scratch databases**; running it is the owner's act. |
| **S3c** | **Drop** — `gtd_items` + satellites dropped; the ClickUp columns D52.3 preserved go with them. `gtd_settings`/`gtd_day_state`/`gtd_rollover_log` **survive** (D53.6). | 🔴 **OWNER-GATE** | Contract half of R6, a release later. We cannot roll back. |

⚠️ **Two traps this row exists to stop the next agent walking into.**
**(1)** `routes/projects/personal.py` already implements S3a's server side — every
route, plus the overlay table. An agent who reads "make Tasks a lens over Projects"
and starts designing endpoints is about to build a second one. Read that file first;
D-PM-6 got there in 2026-08-06.
**(2)** The calendar has **no store of its own** (D54.5, measured). Two specs cite
`gtd_time_blocks` and `calendar_accounts`; neither table exists. Any plan that starts
"move the calendar's tables" is built on a table that was never created.

🆕 **S3a-CLIENT SLICE 1 — 2026-08-25.** The slice was **narrowed at audit**, and
the measurement is why: `tasks/lib/api.ts` reaches **30+ `/items*` endpoints**,
so "the UIs re-point" is not one PR. Slice 1 takes the spine — list, capture,
patch, complete, defer, archive, delete/restore/purge, delegate — and leaves the
AI, subtask, plan, bulk and workspace tail for slice 2 (H-33 enumerates it).

**Three server gaps were found by reading the projection rather than the spec,
and each would have shipped as a silent wrong answer:**
1. **`is_mine` was computed and thrown away.** `_MY_TASKS_SQL` has had the
   `EXISTS` since WS-27e, but `row_to_dict(row, TaskModel)` copies only model
   fields and `TaskModel` has no such field. The Tasks client reads
   `raw.is_mine ?? true` — so **every task another member owns would have
   rendered as the caller's own**.
2. **`workflow_stage`** — the status *id* was on the wire; the NAME, the only
   part a human reads off a board column, was not.
3. **`subtask_count`** — the roll-up badge had no source.
Plus `assignees`, which `/my/inbox` never projected at all (fixed by calling the
existing `attach_assignees` seam, not by inventing a join).

**The archived filter stopped being a literal and became a REQUIRED bind.** The
Archive view had no source; making `:archived` optional would have let a
forgetful caller leak filed rows into a live list silently. With no default,
SQLAlchemy raises `StatementError` on the first request — that IS the R7 fence,
and there is no test to forget because the query cannot run. **R8, 10/10 against
tenant-scratch** (`tests/live/live_ws39_s3a_client.py`, which imports the
module's own `_MY_TASKS_SQL` rather than restating it): checks 3 and 4 prove the
`CAST(… AS boolean)` refuses a bogus value and that omitting the bind raises.

**Two design calls a reviewer should look at, both deliberate:**
- **`disposition: "DONE"` is lifted out of the overlay patch and routed through
  `POST /tasks/{id}/complete`.** The store marks a task done by patching the
  disposition, which under the lens would set MY view to done and leave the task
  open on the company board — the exact drift one store exists to prevent, and
  the opposite of §13.5 criterion 4.
- **The Tasks soft-delete maps to the TRASH disposition, not to `DELETE
  /projects/tasks/{id}`.** That route is a HARD delete that promotes subtasks;
  mapping "Delete (undo available)" onto it would make Undo a button that cannot
  work. `apiPurgeItem` is what reaches the hard delete.
- ⚠️ **And one real behaviour change: archiving from `/tasks` now inherits the
  projects guard** — an OPEN task cannot be archived (P-3). It could before,
  because the row was personal; it is not personal any more.

**Fences (R7), each verified RED by mutation before being trusted:**
`lens.test.ts` reads `types.ts` itself and refuses any `GtdItem` field that is
neither mapped nor listed in `UNMAPPED` with a reason (§13.4a's warning is exact
— a field with no home writes a 200 and disappears); it refuses a STALE
`UNMAPPED` entry and one naming a field `GtdItem` no longer has; and it reads
`api.ts` to check all eight spine functions consult `lensEnabled()`, bounding
each function by the next top-level `export` rather than a character window —
the first draft used 900 chars and false-positived on `apiPatchItem`, whose
inline patch type is 35 lines. Server-side, the existing shape-parity test was
**tightened from "differ by exactly `is_triaged`" to EXACT equality across three
readers**, because one client mapper is only safe if every reader agrees.
§13.5's own whole-tree grep for `/api/tasks/items` belongs to slice 2: asserting
it now would be a fence that is red on purpose, which is a fence people learn to
ignore.

🆕 **S3a-CLIENT SLICE 2 — 2026-08-25. The day planner plans the one store.**

The audit found that `/calendar` needed almost no work and one thing urgently.
**It reads its tasks from the shared task store** (`CalendarView` takes `items`,
`applySchedule`, `updateItem` off `useTaskStore`, not off a list endpoint), so
the grid, the rail and every schedule edit followed the lens the moment slice 1
landed — with **no change in `app/calendar/` at all.**

⚠️ **Except "Plan my day", which is computed SERVER-side and read `gtd_items`.**
Under the flag the UI would show `pm_*` tasks while the planner ranked and packed
the retiring store, and — since the backfill has not run — the plan would come
back **empty**. A 200, no error, no log line. That is the whole reason this slice
went before the CRUD tail.

**The planner is parameterised, not duplicated.** `TaskSource` answers the six
reads it performs; `GTD_SOURCE` carries the old WHERE clauses character for
character, `LENS_SOURCE` is `pm_*`. **The packer, the LLM ranker, the horizon
parser, the capacity arithmetic and the eviction rules do not change** — that is
where the behaviour lives, and a second copy diverges on the first bug fix only
one of them gets. Four new routes (`POST /projects/my/calendar/{plan,replan,
rollover}`, `GET …/estimate-stats`) serve the lens; **the store is chosen by
which ROUTE the client calls**, which is why the cutover still needs exactly ONE
flag. A server-side flag would be a second one, and two flags that must agree are
a mismatch waiting to be found by a user. None of the four writes: they return
proposals the client applies through the overlay PATCH slice 1 already routed.

⚠️ **The semantic that nearly broke it.** `gtd_items.disposition` is stored and
NOT NULL; `pm_task_personal` has **no row at all** until a member triages, so the
disposition is DERIVED (`derive_disposition` — an assigned, open task is `NEXT`).
A planner filtering the stored column would have found **nothing to plan for any
member whose board is untriaged, which is every member on day one** — again, a
200 with an empty plan. So the lens queries **prune with the stated disposition
and decide with the effective one**: `(p.disposition IS NULL OR = 'NEXT')` keeps
untriaged rows for Python to rule on, and `derive_disposition` is CALLED, never
restated in SQL. A SQL copy of it is a mirror, and mirrors go stale and then lie.

**R8, 12/12 against PostgreSQL 16** (`tests/live/live_ws39_s3a_client2.py`,
calling `LENS_SOURCE`'s own methods). The prune can only be wrong in one
direction — dropping a row it should have kept — so it is driven from both sides:
check 1, an untriaged assigned task IS a candidate; check 3, so is a **backlog**
task the member filed as `NEXT`. Also measured live: `scheduled_today` keeps the
fixed meeting, `carry_forward` sweeps only the prior day, `estimate_ratio` reads
the overlay (2.0 on a two-hour actual against a one-hour block), and moving the
member's `app_user` row to another tenant empties their plan.

**Two structural fences, both verified RED by mutation**
(`tests/unit/test_calendar_task_source.py`): the lens row is checked against the
names the planner ACTUALLY reads — extracted by parsing `_candidate_brief` and
`_replan_core` rather than from a list somebody maintains — because a missing
attribute does not raise, it answers `None` and the day gets planned slightly
wrong forever; and `planning.py` may not re-implement any packer symbol or write
its own `FROM pm_tasks`, so "which tasks are mine" keeps one definition
(`MY_TASKS_FROM`, factored out of `_MY_TASKS_SQL` in this slice and re-verified
against the live database).

🔴 **Still on the old store, deliberately: the AGENT planner** (`/calendar/
{plan,replan,rollover}-today`, `/calendar/day-summary`). No browser means no flag
to read, and a server-side one is the second flag this design avoids. Slice 3;
H-33 carries it, the endpoints carry the argument, and
`test_the_agent_planner_is_still_on_the_old_store` fails the day somebody routes
it without reading either. Until then an agent asked to plan a day on a lens
deployment plans the retiring store and reports an empty day.

📌 Also measured and worth having: `apiCalendarRange` has **no callers** — the
range endpoint the Calendar was assumed to use is dead code, because the surface
reads the store instead. A deletion candidate for a later slice, not this one.

🆕 **S3a-CLIENT SLICE 3 — 2026-08-25. Every browserless surface learns its store.**

Slice 2 left one thing open by name: the agent planner, which has no browser and
so cannot pick a store by picking a route. **Sizing it found two more, and both
are worse than the one that was known** — because both WRITE:

| Surface | | Why it was missed |
|---|---|---|
| `/calendar/{plan,replan,rollover}-today` · `/calendar/day-summary` | read | named in slice 2 |
| the agent **apply** path (`_apply_plan_blocks`) | **write** | slice 2 argued "the browser applies through the overlay PATCH", which was true |
| the **nightly roll-over sweep** | **write** | unattended, per tenant, on a timer — nobody clicks it |

⚠️ The sweep is the one that mattered. Left pinned it would have gone on
releasing blocks in the retiring store after the cutover — **every night, for
every customer**, while the members' real leftovers were never released at all.
Nothing would have failed. The lesson is small and exact: *"the client already
does it"* is not the same claim as *"nothing else does it."*

**The decision: `TASKS_LENS`, a gateway env flag** read at call time (the idiom
`projects/core.org_vocabularies_enabled` already uses) and consulted through ONE
function, `agent_source()`. Three alternatives were weighed and rejected —
deriving it from the data (magic, and wrong for exactly the member who has no
`pm_*` rows yet), asking the browser (the assistant has no browser), and serving
the browser's flag from the gateway (makes `lensEnabled()` async at synchronous
call sites and fails OPEN to the old store if the fetch has not landed).

⚠️ **So the cutover now has TWO flags, and that is deliberate.** What makes it
tolerable is not the count — it is that a **disagreement is observable**:
`/version` reports `tasks_lens`, unauthenticated, so "is this box on the lens?"
is one `curl` from a laptop with no box access. That is the same standard §3.8
sets for the deployed SHA. An invisible mismatch is what to refuse; a second
variable in the same `.env`, checkable by evidence, is not.
`docs/TASKS_LENS.md` is the pair's write-up; 🔴 **H-34** asks the owner to add
them to `.env.example` (`.env*` is §6-gated, so an agent cannot).

**The write half of the seam.** `TaskSource` gained `apply_blocks(place, clear)`.
The lens implementation is an **UPSERT, not an UPDATE** — a member can be handed
a task they have never opened and have the assistant schedule it the same day, so
there may be no overlay row; an UPDATE would report success and write nothing. It
routes through `_upsert_personal` rather than hand-rolled SQL, so the agent's
writes use the same binding and coercion path as the browser's PATCH (where
187/188's timestamp and jsonb handling lives). Ownership needs no `AND user_id`
guard any more: the key `(task_id, member_email)` does that job.

**R8 — 15/15** against PostgreSQL 16 (`live_ws39_s3a_client2.py`, extended).
Check 13 is the write path's whole point: `apply_blocks` CREATES the overlay row
it needs, asserted against a task that provably had none. 15: clearing MY block
on a shared task leaves the other assignee's standing.

🔴 **A FENCE THAT PASSED A DELIBERATE REGRESSION, caught by mutation.**
`test_every_browserless_surface_asks_which_store` first read
`if "agent_source()" not in body and "src." not in body`. Pinning the nightly
sweep back with `src = GTD_SOURCE` left every `src.` call in place, so the fence
saw a source variable and approved — **the mutation ran green.** It now requires
`agent_source()` AND refuses any surface naming `GTD_SOURCE`/`LENS_SOURCE`
directly, and the re-mutation fails as it should. Same class as the export-BOM
fence this repo already records: *a fence that holds a bug still is worse than
none*, and the only thing that surfaces one is mutating the code and watching
the test not fail.

📌 Also fixed while sizing: `_replan_core`'s `_parse_iso` is fed a `datetime` by
the lens source (identity `to_item`) and an ISO string by the GTD source
(`_row_to_item`) — verified both shapes parse, rather than assumed.
`gtd_rollover_log.item_id` is a bare `UUID NOT NULL` with **no** foreign key, so
the surviving log (D53.6) accepts a `pm_tasks` id — checked in the DDL, because
an FK there would have made the sweep 500 on its first lens night.

🆕 **S3a-CLIENT SLICE 4 — 2026-08-25. The client-side connector excision.**

S1 deleted the connector; this deletes the browser that still talked to it.
Eleven API functions, ten store actions, three store fields and the UI they fed.
Pure removal, and it went before the CRUD port for S1's own reason: `apiOrganize`
and the delegate flow still took `account_id` / `project_id` / `status` for a
provider that does not exist, so porting them first would have meant porting
parameters on their way to being deleted.

🔴 **A CORRECTION THIS SLICE HAD TO MAKE TO ITS OWN HANDOFF NOTE, BEFORE ACTING
ON IT.** H-33 filed `/hierarchy` · `/spaces` · `/folders` · `/local-projects`
under *"DELETION, not porting — D52 leaves them with no destination."* **That is
wrong.** `routes/tasks/hierarchy.py` is the **LOCAL** Space→Folder→Project tree —
its own header says "SYNCED projects are NOT here" — and it writes `gtd_spaces` /
`gtd_folders` / `gtd_projects`, whose destination under D53 is `pm_projects`
(which already nests via `parent_project_id`). Acting on the note would have
**deleted the Tasks app's ability to organise projects at all**. The note grouped
them with the connector because they sit next to it in the route table, which is
the wrong axis. H-33 is re-cut and the fence carries the reasoning so the next
reader cannot re-make the mistake from the list alone.

**What went, and the line drawn.** S1's repair round settled it: *delete the
controls that cannot succeed, keep the read-only provenance.* So the accounts
API, `POST /tasks/sync`, `POST /items/{id}/push`, the workspace/folder/list
writes, the account-derived store state and `apiCalendarRange` (which had **no
callers at all**) are gone — while a row imported before the retirement keeps its
source badge, its deep link and its last-known status, now READ-ONLY.

**Three things this fixed that were live wrongness, not dead weight:**
1. 🔴 **The assistant was telling people to click a button S1 deleted.**
   `taskAssistantPersona` had an `else` arm — the only one that could still fire
   — reading *"The user can connect ClickUp from Tasks → Connect workspace."*
   A stale persona is not inert; it is the model confidently giving instructions
   nobody can follow.
2. 🔴 **The Waiting-For flow had no working entry point.** `DelegateDialog`
   required an account before `canSubmit` could be true, and there are no
   accounts — so the feature migration **188** exists to hold could not be
   reached from the UI. It is re-cut to who / what / since-when, which is what
   it was always about.
3. 🔴 **`POST /items/{id}/delegate` builds a provider first**, so the flag-OFF
   path would have started 400-ing the moment the dialog worked. The old-store
   branch is a PATCH now (assignee + WAITING) — an unreachable endpoint was
   about to become a reachable failure.

**Two judgement calls a reviewer should look at.** `DelegatePopup`'s two modes
collapsed to one: picking a person now always means *delegate* (WAITING), where
a SYNCED task used to offer multi-select ASSIGNMENT instead. Assignment is not
lost — the task detail and `/projects` both still do it — but the component is
called Delegate and Waiting-For is the product. And the read-only "Workspaces"
list in the sidebar is deleted, superseding S1 repair round 1's compromise: that
round kept the account ROWS visible because D52 speaks only of COLUMNS, which
was fair; this slice deletes the client that read them, so the section could only
ever render empty.

**Fence (R7), verified RED by mutation:**
`app/tasks/lib/connectorExcision.test.ts` sweeps the WHOLE `tasks/` and
`calendar/` trees for seventeen retired symbols and three endpoint shapes, with a
blind-walk guard (>40 files, and `taskStore.ts` + `ClarifyPanel.tsx` present by
name) so an empty sweep cannot pass. Comments are STRIPPED before the sweep, on
purpose: a note explaining what was removed is exactly what should survive a
deletion, and banning the word would push the next reader to delete the history
with the code. It is the client half of
`test_no_task_provider_connectors.py`, and it defends the different failure that
round recorded in one sentence — **"deleting code does not delete rows or the UI
pointing at them."**

📌 The theme conformance ratchet fired (`SOLID_BUTTON_DEBT` 29 → 28) because the
deletion IMPROVED a baselined count. Lowering it is part of the change, per
`workbench/control_plane/AGENTS.md`: a file that got better must not keep its old
budget for the next regression to spend.

📌 **Deliberately left standing:** the Clarify destination picker still renders,
with exactly one option. Deleting a picker is a product decision this slice did
not take, and it costs nothing to leave until the CRUD port reshapes that panel
anyway. The gateway's `/tasks/accounts*` routes also stay — S3c drops them with
the store they read.

🔧 **REPAIR ROUND 1 — 2026-08-25, PR #91 (adversarial review: MERGE-AFTER-FIXES).**
The review's one sentence is the lesson: **deleting code does not delete rows or the
UI pointing at them.** Four P1s fixed on the branch — (1) the tasks scheduler now
skips any `task_accounts` row whose provider is absent from `providers._CONNECTORS`,
at the SELECTION seam and reading the SAME registry `build_provider` consults, so a
surviving `provider='clickup'` row no longer error-loops every 300 s and paints a
permanent "Sync failed" badge (fence: `test_h3_rls_promotion_rehearsal.py::
TestSchedulerBindUnderForceRls::test_a_retired_provider_account_launches_no_loop`,
measured red first; the class's green cases now register a STUB connector, because a
clickup-seeded "green" would have asserted the opposite of its own claim);
(2) the sidebar's "Connect workspace…" button, the per-account Sync button and
`WorkspacesModal` are DELETED — S1's own principle applied to its largest surviving
instance — leaving the account rows visible read-only with one muted line
("Workspace sync is retired; imported items remain"), since D52 speaks only of
COLUMNS and is silent on the rows; (3) `launch_surface.md` §2's `/calendar` gate
corrected `feature:calendar` → **`feature:tasks`** (D54.1; acting on the spec would
have minted a grant nobody holds and shipped the app dark) and its "lifted out of
`/tasks/calendar`" phrase struck (D54.5: that route never existed); (4) the ClickUp
credential-revocation handoff **renumbered H-27 → H-32** (main took H-27 for the e2e
entry via PR #47) and its Check repaired — it queried a table that does not exist
(`integration_credentials`; migration 11 adds COLUMNS to `provider_keys`) and would
have read `0` while the real tokens sat in `task_accounts.credentials_encrypted`.
🔴 **AND A P0 THE REVIEW MISSED, FOUND WHILE VERIFYING: PR #91's CI HAD BEEN RED
SINCE THE DAY OF THE EXCISION — two jobs, both S1 fallout, neither reported by the
branch's own write-ups.** (1) **Unit tests** — `tests/unit/test_tasks_archive_upstream.py`
and `tests/unit/test_tasks_gtd.py` still `from gateway.routes.tasks.providers import
ClickUpProvider`, a class S1 deleted, so both raised **ImportError at COLLECTION**;
CI runs `pytest tests/unit/ -x`, so the run died on the first of them and reported
**nothing else about the whole suite**. (2) **Harness trajectory evals** — the S1
re-cut of `test_workflow_engine_trajectory.py` moved the `write` node from
`clickup.create_task` to `http.request` and updated the fixture but left one
assertion carrying the OLD connector's `{list_id, name}` arg shape. Both are fixed
in this round: the two modules are re-cut (the ClickUp-connector cases deleted, not
skipped, per §12.6 criterion 7; `_queueing_provider` moved onto a
`BaseTaskProvider` stub so BO-1b's "a queued push writes `awaiting_approval`, never
`synced`" survives), and the eval assertion now matches the node it fires.
🔴 **A THIRD red, invisible behind the collection error**: with the suite
uncollectable, nothing had run `test_workflows_as_tool.py::
test_destructive_registry_names_clickup_write`, which asserted
`"clickup.create_task" in destructive_action_names()` — the exact claim D52
falsified. Re-cut to fence the helper's DERIVATION (does it read
`spec.destructive`?) by registering one of each and watching the answer follow,
which is what needs a fence and what outlives any tool; the claim about current
CONTENTS is the new tripwire's. Its three sibling gate tests were never affected —
`validate_graph` takes `destructive_actions` as an ARGUMENT, so their action name
is a string the validator never resolves (renamed off the retired tool anyway).
**Fixing the collection error then exposed THREE more reds that `-x` had been
hiding behind it**, each fixed and pushed in turn:
**(3)** `test_workflows_as_tool.py::test_destructive_registry_names_clickup_write`
asserted `"clickup.create_task" in destructive_action_names()` — the very claim
D52 falsified. Re-cut to fence the helper's DERIVATION (register one of each, watch
the answer follow) rather than the catalog's contents.
**(4)** `test_workflow_fixtures.py[lead_triage_high_priority]` still expected the
old `{list_id, name, description}` args — **and its `variables` block still
supplied a dead `list_id` while the re-cut node reads `{{vars.endpoint}}`, which
nothing defined.** The engine leaves an unresolvable template LITERAL, so the run
was posting to the string `{{vars.endpoint}}` and the fixture would have pinned
that as correct the moment its args were merely updated. Both halves fixed.
**(5)** `test_projects_calendar.py::test_the_week_layout_did_not_grow_its_own_endpoint`
is an EXACT-LIST assertion over calendar routes and has been red since
**S3a-server** added `GET /projects/my/calendar` on 2026-08-24 — nobody saw it,
because collection died several files earlier. Re-cut to allow the deliberate
personal lens (a different question over a different store) while keeping what it
actually guards: no LAYOUT-specific `/week` endpoint re-deriving the team window,
and the list stays exact so a third route must argue for itself.
⚠️ **This one also caught the LOCAL verification out**: CLAUDE.md's hazard note
says never run `pytest tests/unit -k calendar`, so the repair round's own full-suite
runs deselected `calendar` — and deselected the failure with it. Naming the file
(`pytest tests/unit/test_projects_calendar.py`) completes fine and reproduces it.
⚠️ **The lesson is about the EVIDENCE, not the code:** S1's commit message reported
"580 passed" over hand-picked suites, and a green *subset* said nothing about a
suite that could not be collected at all. `-x` then hid three more failures behind
the first, and a `-k` filter hid the fourth. Non-negotiable 8 — verify by evidence,
never by a green job — has a mirror image: **a job you did not look at, and a test
your filter deselected, are not evidence either.**

P2s in the same round: the runtime `_AGENT_REGISTRY` and `_WEBHOOK_ROUTES` clickup
entries removed (the catalog JSON was cleaned in S1, the runtime copy was not, so the
picker still advertised the integration); `personal.py`'s naive-vs-aware comparison
fixed via `_as_utc` — a `datetime-local` edge drag was a **500**, one line below the
422 that exists to stop exactly that — and the same normalization applied to the
calendar window; `/my/calendar` gained `/my/inbox`'s DONE/TRASH filter and its
`include_done` parameter (a trashed task was still occupying an hour of the week);
the two R7 fences citing the deleted `test_task_broker_handlers.py` relabelled
**advisory** with the surviving fence named; the claimed destructive-set tripwire
**written** (`test_no_task_provider_connectors.py::
test_the_workflow_registry_has_no_destructive_tool` — the S1 commit described it and
it did not exist); `TriggerPanel` now renders a stored `source:"clickup"` as a
disabled "(retired) clickup" option instead of a blank select that silently rewrote
the trigger on first save. ⚠️ **Deliberately NOT done, so it is not mistaken for
done:** the `disconnectAccount` / `refreshAccountSchema` store actions and the
`/tasks/accounts` gateway routes behind them survive — they are S3a's to delete with
the GTD store, and half-removing them here rewrites code the next slice deletes.

✅ **ALL THREE OF THE 2026-08-11 WAVE-2 BRANCHES ARE NOW MERGED TO `main` AND DEPLOYED (2026-08-12).** PRs #429 (Modal, slice 1), #430 (Toast, slice 2) and #431 (WS-27be, migration 170) all landed on `2b11f43a`; prod carries it and **migration 170 is in the ledger** (the deploy's recorded count went 167→168 as `migration_files` went 169→170 — box-side evidence, not a job conclusion). PR queue CLEAR.
🔴 **The record below is KEPT because what it warned about was right.** Three agents were dispatched in parallel on 2026-08-11, all three died on the same API-limit error, none finished, none self-verified; two left real work, opened as DRAFT PRs precisely so nobody would mistake them for GO. **Both drafts were independently verified on 2026-08-12 before merge, and verification found real defects in one of them** — which is the argument for the draft convention, not against it.
- **`ws-27ak-toast`** → PR #430, **MERGED after repair**. `tsc` 0 · `vitest` **88 files / 1983 tests** · `next build` 0 · **`e2e/toast.spec.ts` 6/6** with `e2e/modal.spec.ts` 10/10 as the control. 🔴 **Two of the six browser tests were red and BOTH were the test's fault, not the component's**: `[role="alert"]` counted Next's route announcer (`#__next-route-announcer__`, in a **shadow root** — Playwright pierces open shadow roots, `document.querySelectorAll` does not, so every count was off by one), and `getByRole` cannot see a high-priority toast's controls (`priority: "high"` is what makes an error assertive AND what makes Base UI stamp `aria-hidden="true"` on the visible toast until the viewport is focused, `ToastRoot.mjs:418`; F6 is the reader's way in). ⚠️ **And a THIRD defect the tests did not report: the keyed-re-fire fence was DEAD** — it fired the second toast from `Retry`, whose handler closes the toast before re-running the closure, so with `toastIdFor` stubbed to `undefined` it still passed. Re-pointed at a second press of "Mark all read"; under the same mutation that is 2 toasts mid-flight, 2 settled, test red. Recorded in `specs/project_management_app.md` §11.32.
- **`ws-27be-trgm-search-index`** → PR #431, **MERGED**. ✅ **R8 satisfied — the check the dying agent never ran was run on 2026-08-12**: ladder replayed 01→169 into a throwaway DB, 60k seeded rows, **23 checks green, exit 0** — `Filter:` → `Index Cond:`, ~302 ms → 0.41 ms, tenant isolation holds through the new index path, migration idempotent, `idx_pm_tasks_fts` retained per R6. **R1 re-checked at merge: 170 was still the next free number.** ⚠️ Still owed, unchanged: the `idx_pm_tasks_fts` drop, and the product call on raising `MIN_QUERY` to 3 (a trigram index cannot serve a 2-character pattern, so the shortest ACCEPTED query is the longest UNSERVABLE one).
- ~~**`ws-27bf-theme-sweep`** — died before writing a single file. Zero salvage; **still fully owed**, and it is the highest-leverage item in this queue.~~ 🔴 **WS-27bf IS STRUCK 2026-08-13 (owner-ruled) — it was never a ticket.** It appears nowhere in the owning spec: two lines of board prose and no §1 contract, no "Done when", no acceptance. The work it named is already specced as **WS-27at** (the living design-system gallery, §9.4.2), so it was a duplicate mint under a Wave-2 name, and a board row naming a ticket with no spec entry is not dispatchable — the same verdict WS-27bc drew on its documentation. **WS-27at is the ticket; use that name.** ⚠️ The gap it pointed at is also partly closed now: WS-27bg slice 2 landed `e2e/project-state.spec.ts`, so the Fluent → Material → Graphite sweep is a real test for the Projects tree rather than a manual step every slice owed and several skipped.
⚠️ **Environment traps that cost real time on 2026-08-12, both worth knowing before the next dispatch**: (1) `playwright.config.ts` runs `next start`, which serves a **prebuilt** `.next` — a stale one 404s every route and fails all 10 modal tests for reasons that have nothing to do with the diff, so `npx next build` comes first and a known-green spec is the control. (2) The `mt-scratch` Docker DB on :5433 is **NOT current** (no `pm_projects.organization_id`, empty ledger); an R8 run needs the ladder replayed into a throwaway database inside `acb-postgres`.
➡️ **Next, in order**: (1) **build the theme sweep from scratch** (WS-27bf — nothing exists, and it gates the visual pass every slice here owes), (2) then whichever of Tooltip/Skeleton — acceptance criteria are written for both in spec §9.4.2 — paired with the DOM-test-substrate question, since every rendering done-when in Wave 2 is review-only without it. ⚠️ **Owed and NOT closed by the merges**: the four-theme visual pass (Fluent → Material → Graphite) on `/projects` and a neighbour, on live `main`. Modal and Toast shipped **un-flagged** — they replace existing surfaces rather than adding new behaviour, so there is no flag to flip back if a theme looks wrong. (2026-08-12) ⚠️ **Live bug found and fixed 2026-08-11 by the WS-26i-export repair (MERGED in PR #426, `7255344`; deploy not independently verified):** `src/app/api/projects/[...path]/route.ts` did `await res.text()` — a UTF-8 decode, which strips a leading BOM — so **`GET /projects/export/tasks.csv` has shipped BOM-less since WS-27ae** and Excel on Windows renders a task titled "Café" as "CafÃ©". Measured on node v22 through the real handler: `EF BB BF 4E 61 6D` in, `4E 61 6D 65` out. The proxy now reads `res.arrayBuffer()` and also forwards `X-Export-Rows` (set since WS-27ae, unreachable until now). Fence: `src/lib/export.test.ts` RUNS both proxies over a BOM'd body and compares bytes — it previously asserted `toContain("await res.text()")` and pinned the defect. `specs/project_management_app.md` §11.26 carries the correction.
🆕 **WS-27bg MINTED 2026-08-13 (owner-directed) and it PREEMPTS the queue above** — project run
state, the coloured indicator, and archive: *"each project should show one colored indicator in
front of it — green for ongoing, red for stopped, orange for paused"*, plus **queued**, plus
*"the ability to archive an entire project and its tasks."* Spec **§9.8**; three decisions taken
before minting so none is re-asked: **D-PM-25** (run state and archived are **two axes** — 146
shipped `status` with an `'archived'` value **and** an `archived_at` column, storing one fact
twice), **D-PM-26** (project state **derives** onto tasks and never cascades a write — D-PM-12's
ruling applied to a second surface; the five costs are enumerated and measurable), **D-PM-27**
(the hue map, **owner-ruled against the shipped vocabulary** — green means "running" on the tree
and "finished" on the board beside it; the owner was shown that cost and chose it, so an agent
finding it inconsistent cites the decision and stops). 🔴 **The audit's real finding is that the
feature is half-built and entirely invisible**: `pm_projects.status` has been CHECKed, defaulted,
indexed, API-validated on create AND patch, returned by the tree read and declared in the
frontend type **since migration 146** — and `ProjectTree.tsx` contains the string `status` **zero
times**. `archived_at` exists and is **never written**; there is no archive endpoint, and the only
removal path is an unrecoverable `DELETE` cascade. There is **no project-editing UI at all** (one
`patchProject` call site, one `createProject`; a project cannot be renamed). This is the **second
instance** of the exact failure `statusAccent.ts`'s header records about `pm_task_statuses.color`
— stored since 146, exposed on the API, rendered nowhere — same table, sibling column. 🔴 **Three
automation paths corrupt data the day `on_hold` starts meaning something, none visible from a
mock**: `run_lifecycle_sweep` (`automation.py:298`) has **no project-status predicate**, so a
paused project with a `close_after_months` policy gets its backlog auto-cancelled as
`system:workflow:<id>` through the ordinary transition — indistinguishable from a human doing it;
recurrence keeps spawning; agent dispatch keeps dispatching. All three take **one** predicate, or
the ticket authors the §5 defect three times. Sliced §9.8.3 (axis + endpoints + guard, carries the
migration and an **R8** obligation) · §9.8.4 (indicator + the control surface that does not exist
yet) · §9.8.5 (overdue suppression across **four** predicates — three TS + one SQL, unified only
one wave ago — My Work, and calendar/timeline **honest overflow** per WS-27ac rather than silent
drops). ✅ **SLICE 1 BUILT 2026-08-13 on `claude/projects-app-development-01lepg`** — since MERGED
(#437) and **DEPLOYED** (as-built §11.35) — **migration 171** widens the CHECK to the union (R6 expand;
`archived` retained for the deploy window, its removal a named later release) and adds
**`archived_root_id`**, which is what makes archive reversible: archiving stamps the subtree with
the id of the project the user actually archived, unarchiving clears exactly those rows, so a
subproject archived on its own **survives** its parent's restore, and restoring a swept-in child
is refused with the ancestor named. The write path validates against **RUN_STATES**, not
`PROJECT_STATUSES`, so nobody can PATCH `status='archived'` and recreate the defect D-PM-25
removes. 🔴 **The predicted sweeper bug was real and is fixed**: `run_lifecycle_sweep` had no
project-status predicate, so a paused project under a close policy would have had its backlog
cancelled by `system:workflow:<id>` through the ordinary transition — indistinguishable from a
person. Recurrence and agent dispatch had the same hole; all three now consult ONE
`is_runnable`. ⚠️ The recurrence skip deliberately does **not** stamp `recurrence_spawned_at` —
copying the ended-series path would kill the series at the moment somebody paused the project.
**R8 satisfied**: ladder replayed 01→171 into a throwaway Postgres 16, `tests/live/live_ws27bg.py`
**27 checks green** driving the real endpoint functions, **every automation check run twice**
(paused must not fire, active must) because a guard that refuses everything passes a one-sided
test, and **five mutants each caught** — including replacing the subtree CTE with `id = :pid`,
which turns four checks red and is the only thing that catches a quietly-non-recursive walk. The
backfill was verified **on rows that actually exist** (a database built to 170, seeded with two
`archived` projects) rather than asserted empty: both moved, and the one with a real 2024 filing
date kept it instead of being overwritten by `now()`. The new read `EXISTS` plans as a **join,
never a per-row SubPlan** — a claim about shape, not duration, per WS-27be. Also re-run green
**under FORCE ROW LEVEL SECURITY** with the generated phase-4 set applied (not required today;
worth knowing before it is). Hermetic: 17 new tests incl. the mirror test that **reads the CHECK
out of the migration**, plus 5999 passed on the broad suite. 🔴 **Owed and NOT fakeable from
here**: the count of `status='archived'` rows on prod is **owner-gated reach (§6) and is asked of
the owner at review** — the migration is correct at any population, but an agent claiming "this
affects no rows" would be reporting a guess as a measurement.
🆕 **THE TASK-CARD DATA AUDIT, 2026-08-13 (owner-directed), and it minted WS-27bh + D-PM-28/29.**
Owner framing: *"the Tasks app is a sort of an extension of the Projects app — the slice for a
single user"*, plus a local GTD inbox. ✅ **That framing is already the recorded architecture and
is more built than the board said**: D-PM-6 (one row, the personal view is a lens),
`pm_projects.personal_owner` (mig 147 — a personal project is an ordinary project granted to one
member) and **`pm_task_personal`**, which already carries disposition · next_action · context ·
energy · time_estimate · two-minute · defer_until · clarified_at keyed `(task_id, member_email)`.
🔴 **What the audit found is a RENDERING gap, not a data gap**: `pm_task_types` is seeded per
project with a name, an **icon** and a **colour**, gained `is_epic` at WS-27ae, and reaches the
frontend **only as a board grouping key** — *a bug and a feature render identically on every
surface*. Same for `recurrence_id` (a repeating task is indistinguishable from a one-off outside
its own editor) and `source` (`/tasks` ships `SourceBadge` in six components; Projects has
nothing, though "assignment IS dispatch" means agent-created tasks exist). **This is the FOURTH
instance of one failure** — after `pm_task_statuses.color`, `estimate_mins` and WS-27bg's
`pm_projects.status` — and the pattern is that each was stored correctly and reachable by the
API, so every test passed and the only symptom was a UI plainer than its data. 🔴 **And Projects
has no concept of urgency at all**: only binary overdue, so a task due in three hours renders
exactly like one due in three months, which is the single biggest reason `/tasks` cards read
richer. ✅ **D-PM-28 (owner-ruled) — importance is SHARED and ALREADY EXISTS, urgency is DERIVED,
only `leveraged` is personal.** This **overrides §7.5.1's row** sending `important` to
`pm_task_personal` (recorded as an override, not edited quietly). **No boolean `important` is
added**: `pm_tasks.importance` is a four-level scale, strictly richer than the boolean, so adding
one beside it would be the §5 defect inside the decision meant to prevent it — the ruling costs
**one** new column (`leveraged`), not three. 🔴 **A live conflict this surfaced**:
`importance = 3` is labelled **"Urgent"** today, so shipping derived urgency beside it puts two
disagreeing urgencies on one card; it is relabelled **"Critical"** in the same slice.
`/tasks` adopts the four-level control (a boolean is lossy in the write direction) and
`urgent_window_hours` becomes **org-level, not per-user** — a personal window would have two
people see different urgency on one task. ✅ **D-PM-29 — Projects is the MASTER schema; `/tasks`
reproduces it and may only ADD** (owner: *"exactly the same as the product management field so as
to prevent confusion. Any changes made on the personal tasks should also reflect on the product
management task"*). A field's default home is `pm_tasks`; personal placement is the exception and
must earn itself against §7.5.1's Ana-and-Ben test; a write from the personal app is a write to
the same row (already true by construction — the decision is what stops a future ticket
re-introducing a sync). ⚠️ Timeboxing and `deep_work` stay personal on that same test;
**`actual_start`/`actual_end` are flagged as a genuine owner question**, not resolved by an agent
reading a rule. 🆕 **WS-27bh minted (spec §9.9)** — draw the four facts the card already knows:
task type · derived urgency (+ the Critical relabel) · recurring indicator · source badge. **No
migration, no new column, no API shape change.** ⚠️ Its brief carries the promote-don't-author
warning by name: `/tasks`' `SourceBadge` must be read first, or this ticket authors the fourth
copy of something — the WS-27bd(5) ContextMenu situation.
✅ **WS-27bg SLICE 2 BUILT 2026-08-13** (as-built §11.36) — frontend only, no migration, no API
change. `PROJECT_STATES` lands in `src/lib/statusAccent.ts` as the closed lookup D-PM-27
requires; `effectiveState` derives a node's state from its ancestors **on the way down the
render and writes nothing** (D-PM-26); the indicator draws **in front of every project row**,
replacing the folder glyph that carried no information in a tree where every row is a project,
with inherited states at half emphasis. A right-click menu on the tree (the promoted
`ContextMenu`) gives the run-state picker and Archive/Unarchive on the promise-bound toast —
**the first project-editing control this app has ever had**. 🔴 **Narrowed with reasons, not
silently**: Delete is deliberately NOT added (it is an unrecoverable cascade that has never had
a control, so "archive is the default affordance and delete is harder to reach" is satisfied
most strongly by leaving it unreachable); the bulk-close-on-Stop offer is deferred to its own
slice because a modal + bulk call + a count shown before agreement is not a menu item; **rename
was still owed at this point and is now BUILT** — `ws-27bg-project-rename` (**PR #47**, built
2026-08-21, rebased 2026-08-25, **not merged and not deployed**) adds inline rename on the tree
row; frontend only, no migration and no API change, because `PATCH /projects/nodes/{id}` already
accepted `name` and already tracked it, so the rename lands on the project timeline. ⚠️ Its
8 browser cases (`e2e/project-rename.spec.ts`) are **not CI-wired** — H-27. Re-verified on the
rebase 2026-08-25: `tsc` **0** · vitest **122 files / 2589 tests** green.
✅ `tsc` 0 · `next build` 0 · vitest **89
files / 2007 tests** (baseline 1983) · conformance green with **no new baseline entries**.
🔴 **THE FOUR-THEME SWEEP IS NOW A TEST, NOT A PROMISE** — `e2e/project-state.spec.ts`, **10
cases green**, drives a routed tree in real Chromium under **Fluent + Material + Graphite** and
asserts five states resolve to five DISTINCT COMPUTED COLOURS, none transparent, inherited at
reduced opacity, and five distinct **glyph paths** so the state survives a reader who cannot
separate the hues. Every slice since WS-27am owed this pass and several skipped it; this is the
first Projects surface where it is fenced. **Mutation-measured, four mutants each caught** —
routing the map through `resolveHue` (the D-PM-27 trap), a shared glyph, dropping the
inheritance derivation, and **making `on_hold` green, which turns the browser spec red in all
three themes** and is what proves the sweep discriminates rather than merely passes. ⚠️ The unit
tests assert a hue NAME; only the browser proves two names paint differently — which is exactly
the failure `statusAccent`'s own header records, a colour stored correctly for months while
every lane drew the same grey.
✅ **PR #437 MERGED to `main` 2026-08-13** (`a0af10f`) — slices 1+2, migration **171**, all six
checks green including **Migration ladder replay**. Owner-directed merge; branch restarted from
the new head. ✅ **DEPLOYED** — corrected 2026-08-14 from the deploy log rather than from
the merge: `171_projects_run_state.sql ... ok`, `Migrations complete (1 applied, 168
already recorded)`. **This row said "NOT deployed" for a day while migration 171 was
live on the box** — a stale negative is worse than silence here, because the next slice
plans its expand/contract around it. The `status='archived'` row count on prod is still
owed (§6 owner-gated reach).
🔴 **A DEFECT IN THE MERGED CODE, found by re-reading slice 1 before starting slice 3, fixed
2026-08-13** (as-built §11.37): **the three guards each consulted a DIFFERENT ROW.** The sweep
read the **root** then acted on the whole subtree by `root_project_id`; recurrence and dispatch
read the task's **immediate** project. The subtree in between was governed by nobody, and it
failed **both** ways — a task in an *active subproject* under a *paused root* still spawned
successors and still dispatched agents **while the tree drew that subproject as "Paused —
inherited"** (the product said paused and the automation ran), and a stale task in a *paused
subproject* under an *active root* was swept and closed anyway. Reproduced on a real database
before a fix was written. Fixed by `core.is_runnable_with_ancestors` — one `WITH RECURSIVE` walk
reduced by `bool_and`, the server's copy of the `effectiveState` rule slice 2's UI already used —
consumed by both guards, with the same predicate as a `NOT EXISTS` on the sweep's candidate query
so **every task earns its own verdict from its own chain**. Still derives, still writes nothing.
🔴 **AND THE NEW FENCE WAS DEAD ON ITS FIRST VERSION — only a mutation found it.** All four
checks passed *with the recursion deleted*, because the fixtures sat directly in the root, so
pausing the root paused their own immediate project and the walk was never exercised. Moved into
the grandchild (and the series re-armed), the mutation now turns both checks red. **Second time
in this workstream a fence looked like coverage and was not** — the Toast slice's keyed-re-fire
assertion was the first, and both were found by breaking the code on purpose. ⚠️ A smaller test
defect the same run: a fixture reused across checks was read before being reset, so one
assertion measured an earlier check's leftovers. ✅ `live_ws27bg.py` now **31 checks green**;
`tests/unit` **5999 passed**. ⚠️ The hermetic fake was taught the chain query explicitly —
falling through would have answered `None` → False → *"nothing is ever runnable"*, silently
disabling recurrence and dispatch across the whole suite while every assertion still read as a
real refusal.
✅ **TWO OWNER DECISIONS TAKEN 2026-08-13, recorded so neither is re-asked.** **D-PM-30 —
`actual_start`/`actual_end` are SHARED on `pm_tasks`**, which **overrides §7.5.1's row** sending
them to `pm_task_personal`. It follows D-PM-29 rather than contradicting it: the default home is
`pm_tasks` and *"how long did this task take"* is a property of the work. Projects has
`estimate_mins` and **no actuals at all**, so it cannot answer "did this take as long as we
said?" — the question a PM tool exists for. ⚠️ Per-person time tracking is a **timesheet**, a
separate concern this does not foreclose; timeboxing and `deep_work` stay personal, unmoved.
**D-PM-31 — the search minimum is 3, not 2.** The open state was the worst of the three: a
2-character query is ACCEPTED and physically **unservable** by a trigram index, so the shortest
query allowed was the longest one answered by a sequential scan (127 ms at 60k rows, measured).
One constant governs both endpoints since WS-27be moved it to `filters.py`. 🔜 **Queued by owner
selection the same day**: D-PM-20 (the `updated_at` PATCH precondition), WS-27ar (one generic
pins table), and **D-PM-16** (org-wide vocabularies — the expensive one, framed for a ruling
below rather than built). (2026-08-13)
✅ **WS-27bi MINTED 2026-08-13 and its R8 GATE MEASURED 2026-08-14** (spec §9.10, §9.10.1).
D-PM-20 answered: `If-Match: <updated_at>` → **412 with the current row**; **no `version`
column** (a second monotonic fact about one row is the §5 defect); **an absent header still
succeeds** — advisory now, mandatory later, R6's expand/contract applied to an API. The worry
that stalled this ("168's `updated_at` also serves the delta cursor, so one semantic must cover
both") **dissolves on audit**: there are exactly **two** `touch=False` sites, both stamping
`recurrence_spawned_at`, and they are right for *both* consumers at once — a bookkeeping stamp
should neither wake a delta client nor invalidate a human's pending edit. **Measured, not
assumed**: parse-then-bind compares exactly for ordinary, trailing-zero and zero microseconds.
Two fences moved by the measurement — **a naive (offset-stripped) token silently compares
`True`** (asyncpg reinterprets it in the session zone; it only *looks* right on a UTC box, so it
must be rejected with 400), and **`::text` disagrees with the JSON encoder** on trailing zeros
(`.1` vs `.100000`), so the token is never rendered by `::text` and never string-compared.
🔴 **FINDING FOR THE BOARD, NOT THIS TICKET — `updated_at = now()` CAN MOVE BACKWARDS.**
Reproduced on a real database: `now()` is the **transaction-start** timestamp, so a transaction
opening 201 ms earlier and committing *later* overwrote a newer row with an **earlier** stamp.
**Harmless to the precondition** — an exact comparison still differs, so the client still gets
its 412 — which is precisely why it does not expand WS-27bi. **It is a real gap in migration
168's keyset cursor**: a delta client whose cursor has passed the newer value is never handed
the row stamped behind it, and that change leaves the stream silently. Pre-existing and
already-merged, so per CLAUDE.md §5 it is recorded, not refactored; the fix (`clock_timestamp()`
or a sequence) changes the delta feed's contract and **owes its own row and its own decision**.
✅ **D-PM-16 RULED BY OWNER 2026-08-14 — adopt the nullable project scope** (spec §2457), and
**WS-27bj minted** for it (§9.11). `project_id` becomes nullable on `pm_task_types`,
`pm_custom_fields` and `pm_tags`; `NULL` means org-wide, with paired partial uniques replacing
each table's whole-table `UNIQUE`. This was the section's most expensive item precisely because
the cost is asymmetric: dropping NOT NULL later is trivial, **merging the duplicate "Bug" /
"urgent" / "Client" rows twelve root projects will each have accumulated is a judgement-call
migration nobody can automate**, so the ruling's value is that it stops that merge ever becoming
necessary. ✅ **No R5 gap, and it is what makes the ruling cheap** — all three tables already
carry `organization_id NOT NULL` from **migration 161**, so a `project_id IS NULL` row is
org-wide **within one tenant, never global**. Had tenancy been reached only through
`project_id → pm_projects`, nulling it would have produced untenanted rows readable by every
tenant. ⚠️ All three are **ROOT**-scoped already (their own headers say so), so the new axis is
root-local vs org-wide — there is no third level. 🔴 **Shadowing had to be ruled, not left to
taste**: `pm_tasks.tags` stores tag **display text**, not a key, so an org-wide `bug` and a
root-local `bug` are the *same tag* on every task while being two rows with two colours —
**most specific wins**, fenced by a test. **R6 expand-only** (old writers unaffected, old
readers simply do not see org-wide rows); **ship dark on the create affordance, not the read
union**. ✅ **WS-27bi BUILT 2026-08-14** — `core.parse_precondition` /
`core.require_precondition` (one seam, not inline) wired into `PATCH /projects/tasks/{id}` via
`If-Match`, checked **after** the visibility load so an unreadable task still answers 404 and
never leaks its existence through a 412 (R5). **Both measured traps are fenced and
mutation-proved**: assuming UTC for a naive token turns 2 checks red, string comparison turns 2
red, never enforcing turns 7 red. ⚠️ The string-comparison mutant still passed **12 of 16** —
including the ordinary-microsecond match — which is exactly why the measurement was required:
that mutant is invisible to any test someone would think to write. A third fence asserts
`touch=False` appears **twice, both in `recurrence.py`**, so a future third site has to come and
argue itself against both consumers. 🔴 **Two process findings.** (1) `Header(None, ...)` is
resolved by FastAPI **only through the HTTP layer**, so the package's many direct endpoint calls
receive the sentinel object rather than `None` — the first wiring turned **18 existing tests into
400s**, and the guard is `isinstance(str)`, now regression-tested. (2) **`ruff check --fix`
deleted `core.py`'s `_tenant_session` re-export** — unused *within* the module, imported by name
from every sibling — and took **25 test modules** down with it. The repo's own CI comment warns
that a blanket `--fix` is destructive here and CI runs it report-only; the line now carries a
comment saying so. ✅ `tests/unit` **6015 passed** (baseline 5999), blocking lint clean.
✅ **WS-27bj SLICE 1 (the schema) BUILT 2026-08-14 — migration `175_projects_org_vocabularies.sql`**
(number taken at build time, **re-check at merge, R1**). `project_id` is nullable on all three
vocabulary tables; each table's whole-table `UNIQUE` is replaced by a **pair** of partial uniques
— `(project_id, <identity>) WHERE project_id IS NOT NULL` and `(organization_id, <identity>)
WHERE project_id IS NULL` — on each table's **existing** identity (`lower(name)` for tags,
`field_key` for custom fields, `name` for types), inventing no new normalisation. ✅ **R8 — all
four cases proved on a real database, and the migration applied twice to prove idempotency**:
two org-wide duplicates rejected (`uq_pm_tags_org_name`, catching `R8DUP` vs `r8dup`), two
root-local duplicates rejected, **org-wide + root-local of the same name ACCEPTED** (the
shadowing case D-PM-16 requires — a whole-table unique left in place would have forbidden it),
and the same name under a second tenant accepted. ⚠️ **The first run of that check was a false
pass and the output is why it was caught**: a bad seed (`organization` has `display_name`, not
`name`) made every case fail on a **foreign key**, so cases 1 and 2 "errored" exactly as intended
for entirely the wrong reason. A pass/fail count would have read green. 🔴 **Slice 1 is
schema-only and is safe to merge alone** — nothing yet writes a `project_id IS NULL` row and
nothing yet reads one, which is "ship dark" landing as a schema rather than a flag. **Still
owed: the read path** — `org-wide ∪ root-local` with **root-local shadowing** (a correctness
rule for tags, since `pm_tasks.tags` stores display text, so two registry rows describe one tag
and the union must yield one colour), plus the flagged create affordance. `tests/unit` **6029
passed** (baseline 6015). (2026-08-14)<br>✅ **WS-27bj SLICE 2 (the read path) BUILT 2026-08-14 —
spec §9.11.1.** One seam in `core.py` (`vocabulary_scope` · `shadowed` · `org_wide_exists` ·
`refuse_org_wide_write` · `org_vocabularies_enabled`) and three readers on it. The union lands on
`load_definitions` rather than the list endpoint because that seam has **four** callers — list,
create's duplicate check, export columns, and `apply_values`' validation — and a field that
listed but whose values were refused as an unknown key would be the worst of both. Create is
gated on the flag (`PROJECTS_ORG_VOCABULARIES`, default OFF, read at call time) **and**
`admin:settings:manage`: an org-wide row lands in every project in the organization, including
ones the writer cannot see. ✅ **R8 on a real database, with the probe GENERATED from the live
clause builders** (retyping the SQL would prove the retyping): union correct, B's project cannot
see A's org-wide rows, the usage count reads 2 (A's tasks) rather than 3 or 0, `org_wide_exists`
is cross-tenant-false and case-insensitive, and an unknown `:root` returns nothing — fails
closed. 🔴 **Two of fourteen mutants survived the first pass, and both were the hermetic-fake
blindness R8 exists for**: reverting the usage-count correlation to `g.project_id` — which
reports every org-wide tag as used by **0 tasks** — kept all 57 tests green because the mirror
computed the count itself; and dropping the explicit tenant from an org-wide INSERT kept them
green because the fake filled it from its own default, where 161's trigger "does NOT invent a
tenant" and `NOT NULL` would refuse. Both mirrors were fixed to read the statement, then both
mutants died. ⚠️ The live probe had already shown the right behaviour in both cases — what was
missing was a test that goes **red** when someone undoes it, which is the difference between
verified and fenced. 🔴 **Board finding: `TagRow` is declared TWICE on the frontend**
(`projects/lib/tags.ts` and `projects/lib/api.ts`, with `page.tsx` passing rows between them);
both were widened to keep them assignable, but collapsing two public wire types is its own change
(CLAUDE.md §5). Also noted: the tag/field caps now count the **effective** set. Still owed: the
admin surface for managing org-wide vocabularies — which is also what would let one be edited or
retired. `tests/unit` **6090 passed** (baseline 6029); frontend `tsc` clean, **2007** vitest
passed.
✅ **MERGED to `main` 2026-08-14 as #439 (`aa0d7e3`)** — WS-27bg's guard unification, WS-27bi's If-Match precondition and WS-27bj's vocabularies, all six checks green (`tests/unit` **6540 passed**). ✅ **DEPLOYED and verified BY EVIDENCE, not by a green job** (CLAUDE.md §3.8): the box reports `HEAD is now at aa0d7e30`, the ledger line `175_projects_org_vocabularies.sql ... ok` / `Migrations complete (1 applied, 172 already recorded)`, and `Deploy verified healthy on round 1`. ⚠️ **This row previously said "NOT deployed" and that was FALSE** — written from the merge without reading the deploy run, which is the precise habit §3.8 exists to stop; corrected on the next pass when the log was actually opened. ⚠️ One transient in that run: `Job for caddy.service failed.` followed by `Caddy is active` and a healthy verify — a reload blip that self-recovered, recorded because a silent one is how a real Caddy failure would look.
🔴 **AN R1 MIGRATION COLLISION THAT MERGED CLEAN — the FOURTH in two weeks (R1's own header in `CLAUDE.md` already counts three), and the first that git could not show anyone.** This branch's `172_projects_org_vocabularies.sql` and `main`'s `172_people_profile.sql` (via #438) both claimed **172**. Different filenames in different subtrees, so there is no textual conflict: the merge succeeded silently with two migrations at one number, and `sort -V` would then order them by filename. Renumbered to **175**, behind 172/173/174; CI's ladder replay is the check that proves it. ⚠️ **The finding is NOT that the R1 guard is blind — measured, it is not.** `test_migration_prefixes.py` globs the directory, and with both 172 files present it goes red (verified by putting the duplicate back). The gap is *when it runs*: `actions/checkout` on a `pull_request` event checks out `refs/pull/N/merge`, which GitHub **does not compute for a conflicted PR** — #439 sat `dirty` and reported `check_runs: 0`, no jobs at all. So the guard was never given a tree containing both files. **A conflicted PR runs NO checks, which means the window where a cross-branch collision is most likely is exactly the window where nothing is watching**, and the collision surfaces only once someone resolves the conflict — by which point they are hand-editing the tree that hides it. What DID hold is the vocabularies suite finding its migration by **content** (`"org-wide vocabularies"`) rather than by number, written that way on purpose so a renumber that changed nothing cannot turn it red — a pattern worth copying to every migration-mirroring suite. (2026-08-14) |
| WS-28 | **People Center — directory, org chart, assignment seam** *(minted 2026-08-06)* | ✅ a+b+b-write+g+g-2+p+q+k+j1+j2+h+d+e+j3+m+l+c | `specs/people_center_app.md` · board record 2026-08-09 | a (key shape, mig 148 + quarantine table) · b (directory + person page, mig 149, five-place registration) · b-write (create/edit UI restored; found three ways mig 148 had broken the write routes) — built 2026-08-06/07; **closes WS-13's directory item**. ✅ **c BUILT 2026-08-15** — the org chart (`GET /people/chart` flat + `/people/chart` page): the tree and BOTH cycle guards live in the client where the recursion is — `buildTree` terminates on any input (a loop severs its smallest-id member into a flagged root) and `wouldCycle` refuses a re-parent BEFORE the request, visited-set-bounded so bad data cannot hang the guard that exists to prevent bad data. Alumni off the chart, so a manager who left resolves to no-manager and the orphan is a visible ROOT (the same fact m lists as `manager_alumni`). Center overlay via `org_group`⋈`app_user` on lowered email, tints through the `--cat` ramp, mismatch stated precisely (department names an existing group slug the person is not in). Re-parent = drag behind `can_manage`, human-confirmed, through the ORDINARY person PATCH — no new write path; the node model is fenced to exactly the ten directory-tier fields. **Adversarial review (both diffs) found 10, all fixed and re-measured live**, the worst: 🔴 the legend's `org_group` read had NO tenant predicate — `org_group` is EXEMPT from generated RLS, so it listed every customer's groups and fabricated mismatch warnings from another tenant's slugs (predicate now explicit + fails closed, proven with a second org on the scratch cluster); coverage missed every array-only skill (importer bypasses `gtd_person_skills` — union both sources; the importer backfill is a standing finding); the landing numbered roots from the CAPPED list; a NULL status told three different stories on one screen; the chart drag used the tasks-app door. 6 hermetic + 16 vitest, 30 live checks (`live_ws28ml.py`) · ✅ **d BUILT 2026-08-14** — capability search (`/people/search`): three signals (structured skills weighted by level x recency · the newest CV's matching line, quoted · cosine on the capability embedding, best-effort), each on the result with its own points. **Outside the eval lock by construction**: no LLM ranking prompt exists — arithmetic over named constants — and fences grep the module for model calls and writes (D-PC-13: no INSERT/UPDATE/DELETE) and the client for `.sort(` (the server's order IS the ranking). Gated whole-surface on `admin:members:read` (§4.2). 18 hermetic + 10 vitest, 12 live checks (`live_ws28d.py`) · ✅ **e BUILT 2026-08-15** — the Projects seams: the assignee picker is directory-backed (`GET /projects/assignees` — people AND agents, one list, two headings, D-PM-4), offers directory-only people saying "no login — cannot see the task" BEFORE assigning (D-PC-12), and carries §6.1's warning line (away · overloaded · engagement ends before due) — shown, never enforced, and free text still commits since the server accepts any non-empty string. "Assign work" (person page) and "Assign to…" (search) route through the ORDINARY task-create flow via a visible, dismissible `?assignee=` chip, applied through the same assignees PUT the panel uses. HR half follows the caller's grant with `hr_visible`. 10 hermetic + 5 vitest, 8 live checks (`live_ws28e.py`). Also fixed: `live_ws28k` pinned Monday 2026-08-10 and went stale at midnight — now date-relative; ✅ **j3 BUILT 2026-08-15** — the rebalancing suggestions (`/people/dashboard/suggestions` + the Rebalancing section): candidates per at-risk task ranked by §5.5's `score_skills` (asserted BY IDENTITY — one ranker) × spare × availability, all three numbers on the row; the idle↔behind join (pickups = viewer-scoped unassigned tasks matching skills + at-risk tasks where they are a candidate); the confirmed assign goes through the Projects app's ordinary assignees PUT with existing assignees riding along. ⚠️ Measured on the first weekend run: "spare this week" is zero for the whole roster every Saturday, so the helper window is the RISK HORIZON (`spare_hours_horizon`, today→+14d) — a suggester keyed to the calendar week is a Monday-to-Friday feature. No-overlap candidates dropped not ranked-last; away ×0.25 shown, never erased; caps reported via `truncated`. 11 hermetic + 5 live checks; 🔴 f seats/roles writes (§6 WS-24 (d) analogue). **SCOPE WIDENED 2026-08-13** (owner directive — the person record, self-service editing, the remaining sub-apps, and what the AI may read/do): spec rewritten to v2 with §3 the field inventory, §4 the access model (three read tiers · three write classes · one self predicate `lower(email)`, safe only because 148 made that unique), §5 the sub-app roster and §12 D-PC-1…14. ✅ **g BUILT 2026-08-13** (mig `172_people_profile.sql` — 20 nullable columns, expand-half only, no CHECK over live data; `routes/people/fields.py` the ONE field-class authority with a **structural fence** that discovers every `gtd_people` column from the migrations and fails an unclassified one; `GET /people/me` with three distinct states; `PATCH /people/{id}` authorized by class and refusing **by name**; self-service CV upload; `/people/me` + `ProfilePanels` rendering from the server's `editable_fields`; 137 hermetic + 20 vitest + **29 live checks on a real Postgres 16 with the full ladder applied**). Found on the way: `create_person` silently dropped every profile field it accepted, and the self door was about to return the admin projection. **REVIEWED 2026-08-13 against the owner's PM-perspective checklist** — five gaps closed in the spec and one **defect fixed**: ✅ **g-2 BUILT** — `/people/me` was behind `feature:people` (`is_default false`), so an ordinary colleague **could not open their own profile**, the one surface it exists for. Now an ungated `routes/people/selfservice.py` whose paths carry **no person id at all** (structural, not a check), the directory still gated, `UNGATED_ROUTERS` minted in the enforcement registry so *deliberately open* and *unchecked* stop looking alike, and "My profile" added to the **Personal Center** nav. Spec gains: §3.1a the **display image** (server re-encodes to 256×256 WebP, upload bytes discarded — kills size drift, crop bypass and the SVG/polyglot class at once, D-PC-17) · §3.4a the **work schedule** (org policy in `org_settings` + person override + one effective-schedule function; **D-PC-16 settles a seam collision WS-28g created** — `gtd_people.working_hours` vs the calendar's `gtd_settings.day_start_hour` from migrations 77/97: different questions, People→Calendar seeded once, never mirrored) · **D-PC-18** contracted hours become derived · §5.7 rewritten as the **people-management dashboard** (projects per person, tasks with deadlines, behind/at-risk/overloaded/idle/on-track as arithmetic over dates, department rollup, and idle↔behind rebalancing suggestions that propose and never assign). ✅ **p BUILT 2026-08-13** — the work schedule, **no migration**: org policy in `org_settings`, person override in the column P-3 shipped, ONE `gateway/work_schedule.py` computing the effective schedule; `contracted_hours_per_week` now DERIVED (D-PC-18) and the typed capacity flagged rather than corrected; the calendar SEEDS its day window from it as a **read-time default** and a structural fence sweeps the tasks package for any write back (D-PC-16); admin-gated policy editor that **previews whose hours move before saving**. The live run found what the hermetic suite could not: hours existed only inside shifts, so anyone on no shift had none and the seed silently fell back to migration 77's defaults. ✅ **q BUILT 2026-08-13** — the display image (mig 173): every upload is decoded, cropped square, resized to exactly 256x256 and **re-encoded**, and the stored value is the server's output — so "no random image sizes" is a shape the data cannot take rather than a rule to enforce (D-PC-17). Three things measured rather than assumed: **MuPDF renders SVG**, so the bytes are sniffed before the decoder sees them; the crop must be **fractional**, because a 1000x400 pixel image opens as a 750x300 *point* page; and `Matrix(scale,scale)` rounds outward, so `Rect.torect` is what makes 256 exact. JPEG not WebP — the same guarantee without adding Pillow to the gateway's deploy path. New tickets 🟡 **j** the dashboard, split j1/j2/j3 — ✅ **j1 BUILT 2026-08-14**, **no migration**: it is a READ over the Projects tables (`pm_tasks`/`pm_task_assignees`/`pm_projects`/`pm_activities`) joined to the People record on `lower(email)`, with the classification in ONE pure module (`gateway/workload.py`) so j2, j3 and §5.9 project it instead of counting again. Four aggregates keyed by `lower(assignee)` plus one absence query serve the whole page — built the obvious way an eighty-person roster is three hundred round trips. **D-PC-19** at-risk is CUMULATIVE and carries overdue work (per-task arithmetic calls three 12h tasks due Tuesday fine when there are 16h before it) · **D-PC-20** partial names the viewer's scope and the hidden delta is NEVER computed, because computing it means running the query without the scope · **D-PC-21** one pill by precedence with every flag beside it. Gated on `admin:members:read` on top of `feature:people` (§4.2's oracle rule over a whole surface); an assignee with no roster row still appears, which covers agents, alumni and unknown addresses with one mechanism. ⚠️ **Two fences were corrected, both self-inflicted by writing a fence over raw source**: the no-ranking grep fired on the docstrings explaining the refusal (it now strips prose and carries its own fence of four guilty lines), and the route-order check read a router whose order, inside pytest, is the test session's rather than the app's — it now probes a fresh process and matches on path AND method, since `PATCH /people/{person_id}` in front of `GET /people/dashboard` is not a collision. ⚠️ **The grant closure is applied to the `data:org:read` caller too**, not short-circuited to `TRUE`: `project_clause` answers the TENANT subquery for that caller (WS-29b) and the shortcut would make this endpoint's tenant boundary depend on a FORCE-RLS enforcement flip that is the owner's act. Measured on the scratch cluster (which has no FORCE RLS): with the shortcut a second organization's task lands on the row — 3→4 open tasks, 86h→185h. **`/people/{id}/work` still takes that shortcut** — a finding, not a pattern to copy. 48 hermetic + 30 vitest cases and 28 live checks (`live_ws28j.py`), whose scoped-viewer assertion first passed for the wrong reason — a viewer with no `app_user` row binds `vis_org = NULL` and sees nothing at all. ✅ **j2 BUILT 2026-08-14** — the department rollup, **no new query**: `gateway.workload.rollup` is handed `[r.model_dump() for r in rows]`, the exact payload the client receives, so the §5.9 guarantee is a mechanism rather than a promise — it cannot disagree with the table beneath it and cannot read a field the caller does not have. The fence asserts IDENTITY (`org['contracted_hours'] == sum(rows)`) and a second one greps the module for `db.execute`/`SELECT`/`await`, since the cheapest way to introduce a second count is a convenience `db` parameter. **Strain is a SHARE** (three behind out of four is not three out of forty); the **spread names both people and is stated in HOURS**, because a bare percentage gap is a score with two names attached, and it is computed only over rows whose hours mean something or a person with nothing estimated arrives at the bottom of it as free; agents are excluded from headcount and the exclusion is REPORTED (`org.agents`), a silent one being how a total quietly stops adding up. 16 hermetic + 8 vitest cases and 8 more live checks. 🟡 j3 suggestions · ✅ **h BUILT 2026-08-14** — structured skills & credentials (mig 176): `gtd_person_skills` (level · years · last-used · evidence) + `gtd_person_credentials`, both tenant-scoped day one; **D-PC-6 as a mechanism** — `gateway/person_skills.py` is a leaf outside both route packages (People routes AND the tasks-side résumé ingest write it) and every writer ends in ONE `project()` that rewrites `skills[]`/`skills_source` in the same transaction, so the four live array consumers (GIN, `_match_capability`, clarify, directory filters) keep reading truth. The flat `PATCH {skills}` now RECONCILES the table — retained skills keep their level, so the older door cannot strip what the newer one set; the résumé merge is add-only and writes credentials (LLM half, degrades to none). Measured: batch inserts share `now()`, so projection order is deterministic-alphabetical, not insertion; the parser's boundary regex eats a trailing period (`solidworks.`). **Found+fixed: every hand-typed skill rendered as parser-inferred** — backend writes `manual`, the UI's `skillOrigin` tested the literal `stated` which nothing ever wrote, and its test had pinned the wrong word, holding the defect in place. 28 hermetic + 14 vitest cases, 21 live checks (`live_ws28h.py`) · **j** workload & activity · ✅ **k BUILT 2026-08-13** — availability (mig 174): `working_hours_between(schedule, from, to, absences)` is the function the dashboard's "at risk" calls, and a **structural fence** greps the migration for `approv`/`balance`/`accrual`/`entitlement` so this cannot drift into leave management one reasonable-looking column at a time (D-PC-7). Self-writable, delete scoped `AND person_id` (the control, not belt-and-braces), two read tiers — the bare "away until" is DIRECTORY tier and resolved for the whole page in ONE query, the spans and hours are HR. The tenancy ratchet caught the new table and was right to: `organization_id` declared on day one, defaulting from the session GUC and failing closed when unbound (verified live). ⚠️ `REFERENCES` must precede `DEFAULT` — the ratchet matches the two with no comma between, and `current_setting('app.tenant_id', true)` has one. D-PC-15's fence refined to the invariant it protects rather than worked around · ✅ **m BUILT 2026-08-15** — skills coverage & data quality (`/people/quality`, no migration): bus factor of one · title terms nobody declares · declared-never-on-a-task — the last decided by the §5.5 ranker's OWN boundary (`skill_pattern`, asserted by identity), viewer-scoped through the dashboard's `_scope` (D-PC-20) and stating its basis (`tasks_scanned`/`tasks_partial`/`scope_partial`; an EMPTY scan proves nothing rather than declaring everything unused). 148's quarantine paid off: `email_conflict` listed distinct from no-email; bad statuses are hermetic-only because the live ladder has the CHECK VALIDATED (measured: seeding one is refused). Every list alphabetical IN PYTHON (a fake skips ORDER BY), pre-cap totals in `counts`, `_PERFORMANCE`+never-writes fences. 20 hermetic + 8 vitest, 20 live checks (`live_ws28ml.py`) · ✅ **l BUILT 2026-08-15** — the Center landing (`/people/overview`, `centers.ts` "People dashboard" flipped live): §5.9's "projection, not new arithmetic" as IDENTITY — the load half is `get_dashboard`'s own rollup verbatim (its `away` names ARE "who is away this week"), the quality half is m's `collect`, and the module's ONE query is the headcount GROUP BY (dept × status, alumni INCLUDED — the workload dashboard excludes them by design and a headcount that did would say the company never loses anybody). Found: a literal NUL byte in a template-literal separator — `sourceHygiene.test.ts` caught what would have made ripgrep go binary and every source fence silently half-read the file. 8 hermetic + 5 vitest; 🟡 **n** the AI seams (ranking EVAL-LOCKED, **sending is the §6 outbound-nudge OWNER-GATE — this row may not flip it**). ⚠️ `schema.generated.sql` regeneration is **due**: stale since ~migration 113, and 148 reached prod ~2026-08-07 (after the #384 cast fix). (2026-08-13) |
---

## 3. Decisions recorded (D1–D14: 2026-07-31→08-04 · D15/D16: 2026-08-08 · D17–D21: 2026-08-09 · D22–D30: 2026-08-10 · D31: 2026-08-11 · D32–D35: 2026-08-12 · D36–D38: 2026-08-13 · D39: 2026-08-14 · D40: 2026-08-17 · D41/D42: 2026-08-18 · D43: 2026-08-19 · D44/D45: 2026-08-19, **in flight on `governance-d45-owner-grants`** — the numbers are TAKEN, do not reuse them · D46/D47: 2026-08-19 · D48: 2026-08-22 · **D49: 2026-08-24** · **D50: 2026-08-24** · **D51: 2026-08-24** · **D52–D54: 2026-08-24**)

Resolutions for the cross-doc conflicts the audit surfaced. D1–D8, **D13**, **D14**,
**D16**, **D17** and **D43** are **proposed defaults, adopted unless the owner objects**
(`agent-proposed, owner may overrule`); D9, D10, D11, D12, D15, **D18** and **D32**
are owner calls, taken and dated; **D34 and D35** are owner calls of 2026-08-12 (D35
agent-recommended, owner-accepted). ⚠️ Two entries below are superseded and kept
as records: **D11** (re-taken by D15) and **D10 part 1's planning premise** (re-scoped
by D15/D16) — read their banners before citing either.

- **D52 — ClickUp is retired outright, not cut over. Projects IS the
  project-management system of record.** *(owner call, 2026-08-24; owning spec
  `specs/project_management_app.md`, board **WS-39**. Verbatim intent: "remove all
  the ClickUp integrations. The projects app becomes the ClickUp clone itself, and
  we don't have to have an external integration. We have a full-blown project
  management app within Metorite itself.")*
  - **D52.1 — the staged retirement is cancelled, and its gates die with it.**
    WS-27 **c** (two-way sync) is **unbuilt by decision**, not deferred — it was
    blocked on BO-1a/BO-1b and is now cancelled. WS-27 **g** loses its cutover
    half: there is no parity sign-off against an external system because there is
    no external system. What survives of **g** is the retirement half, and it ships
    now instead of behind a migration that will never be scheduled. In §6, WS-27
    gate **(a)** (running either import endpoint against the production workspace)
    and gate **(b)** (enabling the outbound push) are **dead** — an agent cannot be
    asked to refuse an endpoint that does not exist. Gate **(c)** narrows to the two
    acts that are still the owner's: **revoking the ClickUp tokens** wherever they
    are stored, and any **drop of ClickUp-sourced columns on a live database**.
  - **D52.2 — both importers go.** `POST /projects/import/clickup` (live API) and
    `POST /projects/import/from-tasks` (the local `gtd_*` mirror) are deleted. The
    owner was offered keeping the second as a one-shot and declined. This does not
    strand D53's data: the `gtd_*` → `pm_*` move is a **backfill migration** run
    once under R6, not an HTTP endpoint. An importer kept "just in case" is a live
    credential dependency plus a second write path into `pm_tasks`, which is the
    CLAUDE.md §5 second-implementation defect wearing a migration's clothes.
  - **D52.3 — what is NOT deleted, and why.** The `clickup_id` / `clickup_kind` /
    `clickup_snapshot` / `clickup_synced_at` columns on `pm_projects`/`pm_tasks`,
    and the `clickup_*` columns on the `gtd_*` and `acb_graph` tables, **stay**.
    R6 forbids dropping in the same release that stops writing them — the deploy
    applies migrations before restarting services, so old code would meet a dropped
    column. They stop being written and stop being read here; a **later, separate**
    migration drops them. ⚠️ `pm_projects.clickup_id` and `pm_tasks.clickup_id` are
    still GLOBALLY UNIQUE (migration 161 §424) and that is now pointless rather than
    wrong — fold the widening into whichever release drops them.
  - **D52.4 — root `AGENTS.md` constraint 8 is amended in THIS PR.** "Source systems
    are authoritative — Metorite is a read-mostly mirror" was slated to be amended in
    the WS-27g PR, which will now never exist. It stays true of Zoho and Gmail. It is
    **false of project management from today**, and a constraint that is false in a
    file every agent loads is worse than no constraint.
  - **D52.5 — why outright rather than staged.** The staged plan existed to keep an
    operating company running on ClickUp while Metorite caught up. §11.2's parity
    backlog CLOSED on 2026-08-09, and u–z, aa–ag, S1–S6 and ah–bj have landed since.
    The app is not catching up any more. Past that point the connector buys nothing
    and costs a token, a webhook receiver, a poll scheduler, a rate limiter and a
    three-way merge — five things that can break in a product nobody is using them for.

- **D53 — One task store. Tasks is the personal lens over Projects; `gtd_*` is
  retired.** *(owner call, 2026-08-24; owning specs `specs/task_manager_app.md` +
  `specs/project_management_app.md`, board **WS-39**. Verbatim intent: "The projects
  app and the tasks app are tightly coupled to each other. The tasks app is just the
  personal view of the company-wide projects. And in addition, the tasks app has
  personal tasks that are not configured in the projects app because they are specific
  only to the person.")*
  - **D53.1 — `pm_*` is the store of record for every task**, personal or shared.
    `gtd_items` and its satellites are retired.
  - **D53.2 — this is not new architecture; it is finishing WS-27e.**
    `routes/projects/personal.py` has implemented exactly this shape since D-PM-6:
    `/projects/my/inbox`, `/projects/my/project`, `/projects/my/tasks`,
    `PATCH /projects/tasks/{id}/personal`, with the per-member **overlay** on
    `pm_task_personal`. Measured 2026-08-24: that table already carries
    `disposition`, `next_action`, `context`, `energy`, `time_estimate_mins`,
    `is_two_minute`, `defer_until` and `clarified_at` — the GTD overlay, column for
    column. What never happened is the second half: the `/tasks` UI still calls
    `/api/tasks/*` and reads `gtd_items`. D53 says finish it.
  - **D53.3 — a personal task is a PRIVATE task in the member's personal project**,
    not a row in a second table. "I don't want this on the company board" is answered
    by `visibility = private` on a task in the member's own `pm_project` — a grant
    question D12's visibility model already answers — and never by a separate store.
    A second store would re-create the coupling this decision exists to remove.
  - **D53.4 — promotion is a MOVE, not a copy.** Publishing a personal task to a real
    project is an update of `project_id` + `visibility`. No sync, no id mapping, no
    reconciliation, because there was never a second row. Completing it in Tasks
    completes it in Projects because it *is* the same row — that property is the
    entire point, and it is the one a two-store design cannot buy at any price.
  - **D53.5 — retirement is expand/contract (R6), in three releases.**
    (1) the `/tasks` UI moves onto `/projects/my/*` and `gtd_*` writes stop;
    (2) a backfill migration moves surviving `gtd_items` rows into their owners'
    personal projects; (3) a later migration drops the `gtd_*` tables.
    **(2) and (3) against a live database are OWNER-GATE** — the same data-move class
    as WS-29's pooled cutover, registered in §6 by this decision.
  - **D53.6 — what `gtd_*` KEEPS past (3).** `gtd_settings`, `gtd_day_state` and
    `gtd_rollover_log` are per-member **calendar** state, not task rows. They belong
    to D54's app and are NOT part of this retirement. Named here so a later sweep does
    not take them with the rest.
  - **D53.7 — the SCHEDULED BLOCK is per-member; the DEADLINE is per-task.**
    *(Taken 2026-08-24 while building S3a; migration **187**.)* `scheduled_start`,
    `scheduled_end`, `flexible`, `is_hard_date`, `actual_start`, `actual_end` land
    on **`pm_task_personal`**, not on `pm_tasks`. This is D53.5's overlay argument
    one axis over, and it is the same fact rather than an echo: two people assigned
    one task hold different *dispositions* because they are doing different things
    with it, and for exactly that reason they hold different *calendars* — each
    blocks their own Tuesday. A `scheduled_start` column on the task would let
    whoever writes last delete the other's afternoon, silently, with neither told.
    **`due_at` is the opposite case and stays on the task**: one date, shared by
    everyone assigned, and a fact about the work rather than about anyone's week.
    ⚠️ `flexible` is **nullable, not `NOT NULL DEFAULT true`** — NULL means "never
    stated", which the reader resolves as flexible but which is deliberately not the
    same as a member pinning it. The Ideal Week packer (WS-21) needs that
    difference: "never stated" is what it is allowed to fill in.
    Verified two-member on real Postgres — `tests/live/live_ws39_s3a.sql`, 8 checks.
  - **D53.8 — the MATRIX, the RANK and WAITING-FOR are per-member too.**
    *(Taken 2026-08-25 building S3a-server-2; migration **188**.)* `important`,
    `leveraged`, `deep_work`, `kept_mine`, `sort_key`, `waiting_on`,
    `delegated_at`, `expected_by` and `last_nudged_at` land on
    **`pm_task_personal`**. Third application of D53.5's argument, unchanged:
    each is a fact about how ONE PERSON is holding the task. "I am waiting on
    Priya" is true for the delegator and false for the doer of the very same
    row; `sort_key` is my drag order in my own list. This closes H-33's
    prerequisite — **every `GtdItem` field now has a `pm_*` home**, so
    S3a-client can be written without silent data loss.
    ⚠️ **`important` is NOT `pm_tasks.importance`, and the names invite the
    mistake.** `importance` is an INTEGER the Projects UI labels **"Priority"** —
    shared, per-task, seen by everyone assigned. `GtdItem.important` is the
    BOOLEAN axis of the Eisenhower matrix, paired with an `urgent` that is
    DERIVED from `due_at` and never stored. Mapping one onto the other (say
    `important = importance >= 3`) would invent a threshold no decision records,
    publish one member's private triage to the whole task, and change what the
    Projects Priority column means. Two facts, two homes; `importance` is left
    alone.
    ⚠️ **Waiting-For collapses to four columns rather than porting
    `gtd_waiting`.** That table allows many rows per item with a `resolved`
    flag — a delegation HISTORY — but **every reader in the tree filters
    `resolved = false`** (measured: `routes/tasks/core.py:479` and
    `routes/tasks/ai.py:1645`, the only two), and `GtdItem` exposes one of each
    field rather than a list. So nothing displayed is lost. Resolved history is
    deliberately **not** carried; "how many times have I chased this" would be a
    new feature with a new home, not a restoration.
    ⚠️ `clarified_at` is **not** in 188 — it has existed since 147. It was
    WRITTEN on every triage and projected by nothing, so it had been collecting
    a real value no caller could read. A wiring gap, fixed without a migration.
    Verified on real Postgres — `tests/live/live_ws39_s3a_server2.sql`, 9 checks,
    including the partial Waiting index being CHOSEN rather than merely present.

- **D54 — Calendar is its own app under Personal Center.** *(owner call, 2026-08-24;
  owning spec `specs/calendar_focus_os.md`, board **WS-39**. Verbatim intent: "it
  might make sense to also remove calendar from the tasks app and make it into an app
  by itself under personal center.")*
  - **D54.1 — the surface moves.** The calendar becomes **`/calendar`**, a
    top-level pane in the **Personal Center** section of `src/lib/nav.ts`.
    ⚠️ **AMENDED 2026-08-24 while building S2, on two measurements:**
    **(a)** there was never a `/tasks/calendar` route — the calendar was a
    **view mode** (`selectedView === "calendar"`, a `ViewKey` in the shared task
    store), so this is a genuine extraction and there is nothing to redirect;
    **(b)** the gate stays **`feature:tasks`**, not a new `feature:calendar`.
    A new slug is a grant nobody holds: it would ship the app **dark to every
    existing member**, and un-darkening it is an owner-gated role write (§6, the
    WS-24 (d) class) on top of a migration that has to reach a box first — and
    H-1 records `main` as many migrations ahead of every box. The calendar lived
    *inside* Tasks, so every `feature:tasks` holder already had it; riding that
    grant keeps reachability exactly as it is. Minting `feature:calendar` is a
    later act that must ship **with** its grant migration, never before it.
  - **D54.2 — it ships `live`, not `preview`, and the fence moves with it.** The
    calendar is reachable today *inside* a live app, so classifying the extracted
    pane `preview` would withdraw a shipped capability. `launch_surface.md` §2's live
    set therefore goes from **eight rows to nine**, and `nav.test.ts`'s count fence is
    updated in the same PR that adds the pane. That fence exists precisely so adding
    a pane without deciding its launch status fails — so this is a deliberate edit,
    not a test that needed fixing.
  - **D54.3 — "Personal Center" is D49's section label, not a Center projection.**
    D49 withdrew the Centers *surface*; `lib/centers.ts`, the `center.*` features and
    the `group:<slug>` grant vocabulary are untouched by this decision.
  - **D54.4 — scope is personal.** My time blocks, my scheduled work, my connected
    external calendars. Center-wide and company calendars are **explicitly out of
    scope**: they need a model for whose blocks are legible to whom, and that is a
    separate owner decision, not an extension of this one.
  - **D54.5 — ⚠️ the calendar has no store of its own, and that changes the ordering.**
    Measured 2026-08-24: `routes/tasks/calendar.py` reads and writes **`gtd_items`
    directly** (scheduling is fields on the task row) plus `gtd_settings`,
    `gtd_day_state` and `gtd_rollover_log`. There is no `gtd_time_blocks` table and no
    `calendar_accounts` table — WS-21's S1–S4 and the OAuth work are unbuilt, and two
    specs cite those tables as if they exist. So Calendar is a **third lens on the
    same rows**, not a separate system: Projects is the company board, Tasks is my
    list, Calendar is my time. Its own three tables survive D53; its **task reads
    re-point from `gtd_items` to `pm_tasks` when D53's release (1) lands**, which is
    why D54 is sequenced with D53 rather than before it.
  - **D54.6 — WS-21 keeps ownership of calendar BEHAVIOUR** (F2/F3, Focus Shield,
    timeboxing, and Horizons per §4). D54 changes where the surface lives and who
    renders it, not who owns the semantics. External-sync OAuth stays OWNER-GATE.

- **D49 — Flat pricing, one seat, and a launch surface: Centers leave the product,
  ₹500/user/month + AI credits replaces the package ladder, and every app not
  finished is held back rather than shipped half-built.** *(owner call, 2026-08-24;
  owning spec `specs/launch_surface.md`, WS-34.)*
  Four parts, taken together because they are one decision about what we sell on
  day one.
  **(1) Pricing is flat.** ₹500 per user per month, carrying everything that is
  live, plus AI credits metered as before. This **supersedes D23 and D24** —
  `saas_multitenancy.md` §2.4b's Center packages, the two org-wide add-ons, the
  ₹1,800 all-Centers seat and ₹3,000 Complete are retired as customer objects;
  §2.4b keeps its banner as the decision record and prices nothing. **`core`
  stays the one seat slug** (membership IS the seat, D19.3) and is repriced
  rather than replaced: a second slug would immediately raise "which seat does
  this member really hold", which is the CLAUDE.md §5 second-implementation
  defect. **Explicitly NOT superseded:** D19.3's hard cap (assignment still 409s
  with `buy_more` and never auto-upgrades), D32.5's three counts, the entitlement
  seam and the 402-vs-403 partition, and the credit ledger with its
  unpriced-until-measured fence. A flat plan makes the module intersect
  trivially total for a seated member; it does not delete the seam, because
  deleting it is a one-way door the first time we sell a tier again.
  **(2) Centers leave the surface, not the tree.** The "Centers" nav section is
  deleted and the Center landing pages are unlinked; `lib/centers.ts`, the
  `/centers/<slug>` routes, the `center.*` feature rows (migration 140) and the
  `group:<slug>` slice grants (D12) are **all kept, unchanged**. The concept
  stops being a *destination* and stays the *scoping primitive* — deleting the
  registry would take the group vocabulary with it and break the live Projects
  grant model. **D22's Center roster is not repealed**; WS-13/14/15/16 are
  **parked, not cancelled**, and `department_centers.md` stays ACTIVE as the
  design record with a banner. Personal Center survives **as a nav section
  label** — a category of apps mapped to the signed-in person, which was never a
  departmental projection anyway.
  **(3) The launch surface is an allowlist, and it is written down.** Eight panes
  go live (`launch_surface.md` §2); every other pane is marked `preview` and
  disappears from navigation while its **routes, API, tests and board row stay
  exactly as they are**. `preview` is NOT a permission — a member holding
  `feature:email` still does not see Email, because access answers *may you* and
  launch status answers *are we selling it yet*; conflating them would mean
  revoking grants to hide an app and re-granting them to promote it, i.e. a
  migration per launch decision. One escape hatch, `NEXT_PUBLIC_SHOW_PREVIEW_APPS`,
  default OFF, which restores the nav entries and changes no authorization.
  **(4) Seats are a thing a customer admin does, from the app.** Invite → assign →
  release → **Unassigned** → reassign, all four in Organisation, with the same
  four available to an operator per customer. *Unassigned* is a first-class state:
  a released member stays on the roster, stays `active`, and keeps an enabled
  Assign control — which is why the seat surface reads the **gateway's** member
  roster (everybody) rather than only the Console's seat rows (only those who
  ever held one). The invite is **never** rolled back by a failed assign: two
  systems, two transactions, and a member who exists without a seat is precisely
  the state the surface exists to show.
  **Rejected alternatives.** (a) *Delete the Center code.* It is the group
  vocabulary D12's grants are built on; the directive says remove the concept
  from the application, not delete the work. (b) *Delete or 404 the unfinished
  apps' routes.* The directive says they remain in the application, and a route
  that 404s is a deleted app with extra steps. (c) *Hide apps by revoking
  features.* See (3) — it makes a product decision into a data migration and
  makes `/access` lie about why a pane is missing. (d) *A new `flat` plan slug
  beside `core`.* See (1).
  **Owner-gated consequence, refused by name:** anything touching an organization
  that already holds seats on a plan this decision retires — repricing,
  converting, refunding, proration (`launch_surface.md` LS-11, §6 money-on-a-live-
  system).
- **D50 — Member invites: a NOTIFICATION email, a Console membership written at invite
  time, and auto-activation at first resolve.** *(owner-ratified, 2026-08-24. Three
  clauses, one design; specced as `customer_console.md` CP-2f + `subscription_console.md`
  SC-2c, board WS-30/WS-31.)*
  - **D50.1 — no accept-token, ever.** The invite email is a **notification**
    (*"you've been added to &lt;org&gt; on Metorite — sign in with this address at
    `https://app.metorite.com/signin`"*). Identity is proven at sign-in by the IdP
    (Google / email OTP, CP-2d), so there is no token, no query parameter and no secret
    in the mail. A token would be a **second identity system** beside the one
    `user_management_contract.md` already binds, and a mail-borne bearer is a credential
    in a mailbox we do not control. Consequence, stated rather than implied: the email is
    pure copy, so losing it costs the recipient nothing but a nudge — the membership is
    already written.
  - **D50.2 — the invite writes the Console-plane membership IMMEDIATELY,
    `status='invited'`.** `POST /admin/members` mirrors the member into the Customer
    Console's `org_membership` at invite time, so the seats grid sees the person and a
    seat is assignable **before** their first sign-in. **Core-seat burn is unchanged and
    still happens at first resolve (D19.3 / D32.5)** — a membership row is not a seat.
    This is the load-bearing clause: without it an invited member is invisible to
    `GET /me/members`, 404s at `_seat_admin_target`, and — with resolve ARMED — resolves
    `console-empty` and is funnelled into creating **their own** organization instead of
    joining their employer's.
  - **D50.3 — first sign-in auto-activates an `invited` member, guarded to `invited`
    ONLY.** On a successful deployment-arm resolve, a Console membership whose status is
    exactly `invited` is promoted to `active` (and `joined_at` stamped
    `COALESCE(joined_at, now())`). **Never** `suspended`, `removed`, or any other status
    — the guard is the `AND status = 'invited'` in the UPDATE's own `WHERE`, not an `if`
    a later edit can widen. Scope, named honestly: this closes the **registry** half of
    `colleague_onboarding.md` N6b. The **tenant** plane's `app_user.status` is untouched
    by this decision — activation there is still §2 Step 1b (`PATCH /admin/members/
    {email}`), and folding it in is a change to the live auth path with its own ticket.

- **D51 — Subdomain workspaces are WITHDRAWN; the organization is explicit in the UI;
  the multi-org workspace choice is a session claim (MT-1k).** *(owner directive,
  2026-08-24: "Drop subdomains entirely; they can cause future complications. Ensure
  the UI clearly indicates which organization a user is signing into. Since a user's
  email may belong to multiple organizations, provide a way to select and switch
  organizations during login." D50 is the member-invites decision, in flight on
  `ws-30-invites`.)*
  - **D51.1 — one door.** `app.<domain>` is the only product hostname. MT-1f's
    slice-1 code (host parser, proxy branch, flag) is REMOVED, not left dark —
    dark code nobody will flip is future complications by another name. What
    survives is the slug **vocabulary**: `RESERVED_LABELS`/`SLUG_RE` in
    `lib/subdomain.ts` + the gateway slug gate (CP-2c 4a) — a slug is a public
    identifier regardless of DNS. The browser tier now reads NO request hostname
    at all (`subdomain.test.ts`'s zero-reader sweep — the old sole-reader fence,
    inverted).
  - **D51.2 — the organization is explicit in the UI.** A logo-less org's NAME is
    the sidebar caption (`lockup(branding, fallback, orgName)`); the org-less
    signed-in state is an explicit JOIN-vs-CREATE chooser (AccessGate's org-less
    arm — it used to show the suspended-member copy and route nowhere); `/signup`
    says loudly it creates a NEW organization and points invited people back to
    sign-in; `/signin` carries the fork line. Built as WS-35 slice A.
  - **D51.3 — multi-org is a SESSION-CLAIM choice, later.** One email in several
    organizations, selectable at sign-in and switchable in-session — **MT-1k**,
    slices B (session claim + gateway per-request bind from the claim; the claim
    only ever selects among the identity's own memberships, R11) and C (the
    tenant-plane enabler: per-org `app_user` uniqueness — today `app_user.email`
    is globally UNIQUE — plus the H6 re-key's completion). Owner-noted for later
    development; not launch-blocking (first customers' employees are single-org).

- **D48 — H6 identity cutover re-keys RBAC onto `user_identity`, resolved in TWO phases,
  with status mirrored FORWARD.** *(owner-ratified, 2026-08-22. ⚠️ AMENDED by D51,
  2026-08-24: D48's "multi-org active-org = per-request bound org (host = workspace
  choice)" loses its selector — subdomains are withdrawn, so the workspace choice is
  a SESSION CLAIM chosen in UI at sign-in (MT-1k). The claim may only SELECT among
  the identity's own memberships, never grant — the rule survives its mechanism.)*
  H6 re-keys RBAC (`user_role`, `user_permission_override`, `org_group_member`) from
  `app_user.id` → `user_identity.id`, resolved in TWO phases: identity+org+status from the
  RLS-EXEMPT `user_identity` ⋈ `org_membership` on the UNBOUND session (feeds
  `bind_tenant`), then roles/permissions from the RLS-forced RBAC tables on the BOUND
  session within the active org. **`app_user`-as-view REJECTED** (can't preserve the id
  linkage without re-keying anyway; can't express multi-org role-sets; unfences the ~18
  bound RBAC reads; needs `INSTEAD OF` triggers). **Status is mirrored FORWARD into
  `org_membership.status`** (reconcile-from-`app_user` is impossible post-RLS — the
  identity leg reads unbound while `app_user` is RLS-forced). Multi-org active-org = the
  per-request BOUND org (host = workspace-choice). Ships dark behind `IDENTITY_CUTOVER`
  (default OFF); H6 must land dark BEFORE H3 phase-4 promotion. R6 expand/contract.
  *(Slice 3a — this change, WS-29 — records this decision and builds the forward status
  mirroring the read-cutover (slice 3b) depends on: `acb_auth.access.mirror_membership_status`
  on `members.py`'s suspend/remove/reactivate + `provision_member`, and reconcile migration
  183. Still dark: no reader consumes `org_membership.status` yet.)*
  *(Superseded-in-part 2026-08-22 by the H6 ORPHAN-CLOSURE slice, WS-29: the invite/approve
  mirror moved OUT of `provision_member` to its callers POST-COMMIT — `members.invite_member`
  and `access_requests.approve_access_request` — so a rolled-back caller txn commits no shadow
  orphan; and purge now deletes the shadow via `acb_auth.access.purge_identity_shadow`. Decision
  unchanged; only the call site moved. See `saas_multitenancy_handover.md` §H6.)*

- **D47 — The Customer Console SERVICE runs on the production VPS, for now.** *(owner
  call, 2026-08-19: "ok, let's just use the same VPS for the customer console as well."
  Answers `customer_console_infrastructure.md` §5 item 4 AGAINST that document's own
  default — which explicitly invited the disagreement.)*
  1. Own systemd unit + own env file beside `acb-gateway`/`acb-workbench`; the data
     plane is unchanged (D34's Supabase Console project) — a box loss loses no Console
     data, and CP-2b's fail-soft cache covers sign-in through a Console outage ≤24h
     while purchases fail closed.
  2. **Public surface: the Razorpay webhook path ONLY.** Gateway and BFF calls target
     `127.0.0.1` and never leave the box.
  3. **Obligation on the way in:** the deploy pipeline gains the
     `infra/customer_console/` ladder-apply step — closing the recorded "platform_api
     is on the box but inert" gap. Until then the unit's schema changes never land.
  4. **Named move trigger:** the day a second deployment exists, the service leaves
     the box; relocation is re-pointing `CUSTOMER_CONSOLE_URL` (data unmoved).
  5. Deploying the unit, its env file, and its Caddy block remain §6(a) — owner act or
     a dated D45 grant. Artifacts travel as ready-made blocks (plan-guard forbids
     agent writes under `deploy/`).

- **D46 — Self-serve signup is the next build: the apex site, the signup flow, then the
  in-app growth MVP; 2FA/email-OTP documented-deferred.** *(owner directive, 2026-08-19:
  "let us first work on the entire sign-up flow and build that out … Build a basic website
  on metorite.com where users can sign up … They have to set up an organisation, and the
  first user becomes the admin and can then log into the application. Eventually … two-factor
  authentication and email OTP … Not made it by MVP, but documented … Purchasing extra
  seats and then inviting users to fill those seats, buying AI credits are all then done in
  their app, which I want to see an MVP for after building the signup workflow." The
  ticket-mapping below is agent-proposed, owner may overrule.)*
  1. **The apex marketing site is WS-33** (`specs/marketing_site.md`, minted with this
     decision) — D41's "apex reserved for a future marketing site" clause, now exercised.
     One static page, CTA into the signup flow. Apex DNS record + Caddy vhost + deploy stay
     owner acts.
  2. **The signup flow is CP-2c** (`customer_console.md`, minted with this decision) — the
     form over CP-2a's ✅ API, Google-attested identity, GST fields at signup (doctrine
     §3.1), both provisioning planes driven through ONE orchestration (Console
     `/orgs/provision` + migration 179's `provision_organization`), first user = owner on
     both. Dark behind `SELF_SERVE_SIGNUP_ENABLED` (minted there; existed nowhere).
     **Hard dependency: MT-1j slice 4** — CP-2c's step (b) IS the Console↔tenant seam,
     `org_placement` hole included. D36.1 stands: Fracktal onboards through this same flow,
     no first-party bypass.
  3. **2FA + email OTP are CP-2d, DOCUMENTED-DEFERRED** — not MVP by the owner's words;
     the stub records the eventual shape (IdP-attested identity means no password system
     exists to add a factor to; OTP arrives only with a non-OAuth identity ticket) so
     nothing builds against a guess. No board row until minted.
  4. **The in-app growth MVP (seats · invites · AI credits) runs AFTER the signup
     workflow** and is mostly existing tickets: SC-4a's ₹0 checkout is code-complete dark,
     SC-4h is the paid hand-off (needs H-14's Razorpay account), member invites are live
     (`POST /admin/members`), and **credit packs remain blocked on §9 input 4** (the pack
     ladder is an owner price call) — the owner's ask re-sequences these, it does not
     re-litigate D19.2/D23/D24 pricing.
  5. **Sequencing consequence:** CP-2c's chain (MT-1j slice 4 → CP-2c → WS-33 deploy)
     runs ahead of MT-1f (subdomains) and the remaining MT-1j slice-5 ratchet rounds.
     The owner's §6 Fracktal go-live sequence (H3 → provision → Console deploy → flips)
     is unchanged and stays the gatekeeper of anything LIVE.
  6. **The deployment-identity adjudication** *(added 2026-08-19, same day, after the
     slice-4 dispatch audit returned NO-GO on exactly this: nothing said where
     `org_placement.deployment_id` comes from, and CP-2c proposed the auth answer slice
     4 forbade its implementer from taking — one seam, two owners, three answers.
     `agent-proposed, owner may overrule`.)* `POST /orgs/provision` gets **two arms,
     one per ticket**: **MT-1j slice 4** ships the **operator arm** — a required
     `deployment_label` names the deployment explicitly (breaking API change, free
     while the Console is deployed nowhere); provisioning **never moves** a placement
     (same label no-ops, different label 409 — moves are a future operator ticket);
     the sole-deployment `count(*)=1` heuristic is **forbidden by name** (it would be
     a fourth copy of the sole-org guess MT-1j retires). **CP-2c** builds the
     **deployment-key arm** — the key IS the deployment identity, carries no label
     (400 if presented, the R11 rule), and the `{resolve, provision}` capability
     growth stays the owner's §6 (f)/(h) act at issuance. Slice 4 is thereby decoupled
     from D43-2's open sub-question; slice 4 also owns the tenant-side seam function
     (`provision_local_organization`, the one wrapper over migration 179) and CP-2c
     may not invoke the SQL directly.
     ⚠️ **Amended 2026-08-19 (CP-2c's dispatch audit, blocker B1 — same class, same
     day):** the two-arm route cannot keep `deployment_label` required AT THE MODEL,
     so when CP-2c slice 1 lands the model becomes `str | None = None` on the
     `ResolveRequest.org_slug` precedent, both arms' rules move to the handler, and
     the operator arm's missing-label answer moves **422 → 400** — slice 4's
     mutation-proved no-inference fence is amended in that same PR, red-first. Ruled
     in writing in both owning specs; an implementer changes neither silently.

- **D43 — Tenant-side organization provisioning gets its own ticket (MT-1j), and the three
  defaults that shape it.** *(`agent-proposed, owner may overrule`, 2026-08-19 — the D16/D17
  class. Owning spec: `specs/saas_multitenancy.md` §11 **MT-1j**, minted the same day.)*
  **The finding first:** four specs and one migration disclaimed per-org role seeding,
  owner bootstrap and local `organization`-row creation to *"the org-provisioning ticket"*
  (`saas_multitenancy_implementation.md` §7.1/§8 · `customer_console.md` CP-2b §6(k) ·
  `subscription_console.md` `:196`/`:987` · `user_management_contract.md` §3 ·
  `178_billing_purchase_permission.sql:39-48`) — **and that ticket did not exist**, while
  **D36.1** already asserts customer zero onboards through `/orgs/provision`. Each
  individual refusal was correct; the chain had no terminus.
  1. **Seed per-org roles with ONE SQL function, not a role-template table.**
     `provision_org_roles(org_id)` replays migrations 130/131/133/178's grants as data.
     One seeding doctrine — 178's own comment refuses *"a second seeding doctrine"* and
     assumes the successor parameterises the existing one. A template table is more
     general and buys nothing until customers edit system roles, which no ticket asks for.
  2. **Provisioning runs PULL, not push** — the box asks the Console which organizations it
     should host, symmetric with CP-2b's fail-closed resolve, which already carries the
     deployment key. Push would give the Console a write credential into every tenant
     database. ⚠️ **Open sub-question, deliberately not answered by an agent:** if pull
     needs more than the deployment key's `{resolve}` capability, that is a
     credential-scope change and belongs to §6 gate (f).
  3. **H3 (the RLS promotion) is a hard prerequisite for EXECUTING MT-1j against a real
     second organization — and blocks neither building nor R8-testing it.** Provisioning
     org #2 before promotion yields a tenant with **no database-level isolation**:
     `infra/postgres/generated/04_policies.sql` has never been replayed and
     `tests/unit/test_tenancy_boundary.py:126`'s `BASELINE_UNSCOPED` names **114** tables
     carrying no `organization_id` (measured 2026-08-19). A scratch database applies the
     generated phases in-fixture, so the build is unblocked today. **Registered as an
     execution gate in §6.**
  4. **What this decision does NOT do.** It does not move MT-2's one-assignment act
     (D23.2), does not build CP-2a's signup form, does not re-point
     `_BOOTSTRAP_ORG_SLUG` (**D36.3** keeps it as the fresh-box path), and does not
     weaken `key_store._resolve_org` — item 3 of the D33 findings below records that
     convenience as one that **must NOT be "fixed"**, and MT-1j slice 5 threads the
     tenant through the call sites instead.

- **D42 — Customer zero goes through the REAL purchase rails at ₹0: discount codes are a launch feature of the checkout.**
  *(owner directive, 2026-08-18: "ensure that we have some ability to put some
  discount coupon so that [Fracktal] does not have to pay anything, but still
  goes through the payment onboarding process … so that we actually can test it
  out like the production.")*
  1. The checkout (WS-30 SC-4a / **CP-9**) ships WITH operator-issued discount
     codes. The contract is **`subscription_console.md` SC-4g** — minted with
     this decision; do not build a purchase slice that treats codes as a
     later add-on. *(Ticket re-pointed 2026-08-18: this read "CP-8", which is
     the Operator Console. The payment seam had no ticket body anywhere and is
     now `specs/customer_console.md` **CP-9**. SC-4g was **completed** the same
     day — code storage via `keys.py`'s split-key pattern, the two tables,
     percent-in-basis-points against the **pre-GST** base, the named
     `credit_ledger` reason/ref vocabulary, and the honest note that clause 2's
     test-mode rehearsal is not in an agent's reach.)*
  2. The two-leg reality is recorded there and binds: Razorpay cannot capture
     ₹0, so the 100% coupon path completes **without a provider order** while
     writing the identical records (referenced to the redemption id), and the
     capture leg is rehearsed with **test-mode keys**. Together they cover the
     whole flow; neither alone does. A coupon that fakes a capture, or a test
     that skips the invoice, defeats the owner's stated purpose.
  3. Gates unchanged: issuing a code against a live org is owner-gated (the
     SC-4e adjustment gate class); live Razorpay keys stay owner-gated (§6).

- **D41 — “Customer Console” is the name; both IdPs at launch; slug subdomains ride MT-1f; the box is the wiped VPS.**
  *(owner calls, 2026-08-18: rename direction given — "something more
  descriptive about the job that system is actually doing" — concrete name
  recommended and accepted; IdPs and VPS stated directly.)*
  1. **Rename.** The WS-31 engine (D32) is now the **Customer Console** — the
     operator-side system that registers customer companies and manages their
     subscriptions, seats, keys, credits and provisioning. Mapping, applied
     2026-08-18: package `platform_api` → `customer_console`
     (`apps/services/customer_console/`) · ladder `infra/platform/` →
     `infra/customer_console/` (`001_control_plane.sql` →
     `001_customer_console.sql`) · env `CONTROL_PLANE_*` →
     `CUSTOMER_CONSOLE_*` (`_DATABASE_URL` · `_OPERATOR_TOKEN` ·
     `_INTERNAL_TOKEN` · `_ENCRYPTION_KEY` · the workbench billing proxy's
     `_URL`/`_ORG_KEY`) · specs `platform_control_plane.md` →
     `customer_console.md`, `control_plane_infrastructure.md` →
     `customer_console_infrastructure.md` · tests `test_platform_*` →
     `test_customer_console_*` (`_platform_ladder` →
     `_customer_console_ladder`). Pre-rename records below (D32, D33, D34,
     D40, board history) keep the old names — read them through this mapping;
     they are records, not anchors. Two deliberate non-renames: ⚠️
     **`workbench/control_plane/` is NOT this system** — it is the workbench
     UI's historical directory name; renaming it is pure churn across CI and
     design-system anchors and would be its own mechanical PR, if ever. And
     **WS-30 keeps the name Subscription Console** (the *customer-facing*
     billing surface); "Customer Console" always means the operator-side
     platform system.
  2. **Sign-in launches with BOTH Google and Microsoft** (extends D40.4's
     "Google first"). The code is already provider-driven
     (`workbench/control_plane/src/auth.ts`; issuer defaults to
     `organizations`, i.e. any customer's directory). The Microsoft half is
     an owner act: a **multi-tenant** Entra app registration with redirect
     `https://app.metorite.com/api/auth/callback/microsoft-entra-id`, then
     `AUTH_MICROSOFT_ENTRA_ID_ID`/`_SECRET` in the box `.env` with
     `_TENANT` left **unset** (setting a tenant GUID pins sign-in to one
     directory — the exact defect CP-0 removed).
  3. **Per-tenant hostnames (`<slug>.metorite.com`) are MT-1f, unchanged**
     (`saas_multitenancy.md` §MT-1f): the subdomain is a *lookup*; the tenant
     claim rides the authenticated session; sign-in stays on the fixed host
     `app.metorite.com` because OAuth redirect URIs cannot be wildcards. DNS
     gets a **wildcard `*.metorite.com` A record now** so MT-1f needs no DNS
     act later. Wildcard TLS is a Caddy change under `deploy/` (owner-gated)
     when MT-1f ships: on-demand TLS gated by an `ask` endpoint backed by the
     org registry, or a DNS-01 wildcard cert — decided then, not now.
  4. **The production box is the existing VPS, wiped.** The old CommandCenter
     deployment on it is torn down, not migrated; Fracktal re-onboards as a
     customer through the product path (D36). Wipe and SSH-credential
     rotation (H-3) are one owner session.

- **D40 — Production identity & topology for the Metorite fork.**
  *(owner calls, 2026-08-17: control-plane placement picked directly; domain
  layout and IdP explicitly delegated — "help me make these decisions for me" —
  recommended and accepted in-session.)*
  1. **Hostnames:** `app.metorite.com` (workbench) + `api.metorite.com`
     (gateway). The apex is reserved for a future marketing site and should
     redirect to `app.` until one exists. Rationale: the app's hostname is
     effectively immovable once OAuth redirect URIs, `NEXTAUTH_URL` and
     bookmarks pin it, so the apex stays free from day one, and per-tenant
     slug subdomains (MT-1f) coexist under the same apex when built.
  2. **Tenant database:** the owner's Supabase project, reached through the
     session-mode pooler. The complete ops delta and its traps live in
     `docs/EXTERNAL_POSTGRES.md`.
  3. **Control plane:** a SECOND Supabase project — never the tenant DB. Both
     planes define an `organization` table with different shapes
     (`infra/customer_console/001_customer_console.sql` — pre-D41:
     `infra/platform/001_control_plane.sql` — vs
     `infra/postgres/130_org_access_control.sql`); they cannot share a schema.
  4. **Sign-in:** Google first (Fracktal is a Google Workspace shop; identity
     is keyed by email, not provider, so the provider switch is transparent
     to `app_user` rows). The signin surface renders whichever providers are
     configured, so Microsoft Entra returns by configuration alone. An Entra
     app registration, when used, must itself be multi-tenant — Azure
     configuration, owner act (`saas_operations_doctrine.md` §8).

- **D39 — Pending work crosses sessions in the REPO, not in the owner's head.**
  *(owner-directed 2026-08-14: "I don't want to remember anything… create a
  system so old sessions can put information into and remove information from
  the system prompt as needed.")* `project-docs/HANDOFF.md` is a **queue of
  actions**, injected at session start by `.claude/hooks/session-handoff.mjs`
  and maintained by `/handoff`. Sessions append when they create an obligation
  and **delete** when its Check passes.
  ⚠️ **The design constraint that shapes everything else: this must not become a
  second board.** §2 is the only current-state authority, and a mutable queue
  that also described state would be the CLAUDE.md §5 defect in its purest
  form — two descriptions of one truth, and the *stale* one trusted precisely
  because it is the one loaded into every prompt. So an entry may never restate
  state; it carries a **Check**, a cheap read-only command whose output says
  whether the entry is still real. A stale entry then costs one command and gets
  deleted, and it can never be quietly believed, because believing it requires
  running something that would have contradicted it. Two Checks were wrong in
  the first draft and both were caught by running them — which is the mechanism
  working, not an argument against it.
  **Why a hook rather than a line in CLAUDE.md:** CLAUDE.md is the router, edited
  when the architecture changes; a queue changes every session, and a stale entry
  inside a stable document is indistinguishable from doctrine. Separate file,
  separate lifetime, separate trust level. The hook does **not** execute Checks —
  running commands out of a markdown file at session start would be an
  arbitrary-execution seam pointed at the one file every session loads — and it
  fails open and silent, so a missing or malformed queue can never stop a session
  starting. **R7 fence:** `.claude/hooks/session-handoff.test.mjs`, blocking in
  CI beside `plan-guard`'s, covering the silent failures (no file, garbage file,
  content below `# DONE` resurfacing as live work, `[OWNER]` surviving into the
  injected text).

- **D38 — Billing is a surface we own, and the tax invoice is OURS to issue.**
  *(owner-directed 2026-08-13: admins must be able to download bills for AI usage
  AND subscriptions, monthly, see all of them, and manage their billing. Owning
  spec `subscription_console.md` **SC-5**.)*
  1. ⚠️ **Corrects a wrong assumption carried since WS-30 was written.** Its
     non-goals called invoicing and tax *"the processor's job"*. **That does not
     survive Indian GST:** Razorpay collects money and issues a *payment
     receipt*; a **tax invoice** is the obligation of the **supplier of record**,
     which is us. It must carry OUR GSTIN, our serial, the SAC code, the place of
     supply and the CGST+SGST vs IGST split — none of which a processor can
     assert on our behalf. Left as-is, a year of invoices turns out
     non-compliant all at once.
  2. **One billing home, two document types.** The admin sees one chronological
     list; underneath, a seat subscription (recurring, monthly) and a credit pack
     (discrete prepaid sale) are **separate documents**, because they are
     separate taxable events. Merging them would mean delaying the credit invoice
     to the cycle boundary or back-dating the subscription — both wrong.
  3. **Three rules that are law, not preference, and all three constrain the
     schema:** invoice serials are **gapless and sequential per financial year**
     (April–March) and allocated at *issue* not at *attempt*, so a failed payment
     consumes no number; **an issued invoice is never edited**; corrections are
     **credit notes** referencing the original. SC-4e's ledger adjustment and a
     credit note are two halves of one act.
  4. **Download is the deliverable, not a list view.** Per-invoice PDF plus a
     period export, all history, forever, with **no processor round-trip** —
     which is also what keeps it working if we ever change processor.
  5. **The billing profile is admin-editable**: legal name, GSTIN, billing
     address and **state** (it decides the tax split, so it is not cosmetic),
     a billing contact distinct from the login, and a PO/cost-centre reference
     that mid-size finance teams require. A GSTIN edit is a tax-relevant act and
     lands an audit row; a state change affects the NEXT invoice, never a past one.
  6. **E-invoicing (IRN) is designed for, not built.** Mandatory above ₹5 crore
     turnover; SC-5b's field set is deliberately the IRN field set, so compliance
     later is registration plus a QR code rather than re-modelling invoices.

- **D37 — The AI-credit product: how customers buy, and how they find out.**
  *(owner-answered 2026-08-13 in session; owning spec `subscription_console.md`
  SC-4, which was previously a one-sentence placeholder. The credit ENGINE was
  well specced — ledger, rating, rollover, gate, member caps; the credit
  PRODUCT was not, and this closes that.)*
  1. **Fixed packs, self-serve** via Razorpay — not a free-text amount. Every
     pack is priced **under ₹15,000** so a repeat purchase never trips the RBI
     e-mandate AFA threshold, which would otherwise demand an OTP from a
     customer who is already dry mid-workflow (D33.4b). **Auto-top-up ships OFF**
     and opt-in, reversing §3.3's "default for paid plans": an auto-charge that
     silently fails at the cap is worse than one that never existed.
  2. **Alerts are delivered, not merely rendered.** Email to billing admins at
     80% / zero / failed top-up, an in-app banner while the condition holds, and
     **the affected member is told why their own call was refused and who to
     ask** — without figures. The 80% state was previously specced only as
     something the console draws, which requires the admin to already be
     looking. Once per cycle, not per call. *Operator-side push was considered
     and declined: CP-8's console already shows burn across all customers.*
  3. **Runway, not just balance.** "About N days left at your current rate" is
     the number that prompts a purchase; a balance alone is not.
  4. **Adjustments are ledger rows, distinguishable forever.** Goodwill credits
     after a runaway agent get `reason='adjustment'` plus a mandatory note —
     never an edit, and never indistinguishable from a purchase. 🔴 OWNER-GATE
     against a live org.
  5. **A monthly usage statement** the customer can export, read from
     `usage_rollup` so it survives the 90-day raw retention.
  6. **GST on prepaid credits — working assumption: TAXED AT PURCHASE**
     *(owner, 2026-08-13: "GST will probably apply".)* Credits are read as a
     **single-purpose voucher**: the supply is identifiable at issue — one
     supplier, one service, one SAC, one rate — which is the condition that puts
     the tax point at issue rather than redemption. **This unblocks SC-4a**,
     which may now issue a tax invoice.
     Two consequences built in rather than discovered: **(a) consumption is not
     a taxable event**, so SC-4f's usage statement is *informational* — no
     serial, no tax line, and it says so on its face, or the same supply is
     taxed twice; **(b) expiring or unused credits need no adjustment**, since
     tax was discharged at purchase and D32.6 already makes credits
     non-refundable in cash. That the two rules agree is a point in favour of
     the reading.
     ⚠️ **Still confirm with a CA before the first invoice goes out.** The design
     is robust either way: `invoice.tax_point` (`purchase | redemption`) is
     recorded **per document** rather than assumed in code, so a reversal is a
     policy flip plus credit notes — not a re-model. **"Probably" is not
     settled, and never appears in customer-facing copy.**

- **D36 — Fracktal Works is customer zero, and gets no special path.**
  *(owner-directed 2026-08-13: "Fracktal Works itself becomes another
  organization that is signed up as a tenant.")*
  1. **Fracktal signs up through the same provisioning flow as any customer** —
     same `/orgs/provision`, same trial, same seat cap, same credit ledger, same
     lifecycle. It is an `organization` row, not a hard-coded identity.
  2. ⚠️ **Therefore: no first-party bypass, anywhere.** No `if org == 'fracktal'`,
     no "internal" flag that skips the seat cap or the balance gate, no
     `EXECUTIVE_EMAILS` shortcut into a customer-shaped surface. Every such
     branch is a code path that only WE exercise, which means the path our
     customers use is the one nobody tests. Recorded as a rule because the
     temptation arrives disguised as convenience, usually at 6pm before a demo.
  3. **The `default` bootstrap org stays** (`acb_auth/access.py`
     `_BOOTSTRAP_ORG_SLUG`) as the first-run path for a **fresh box**, and is
     not Fracktal's tenancy. D33.5's finding is unchanged: provisioning a
     *customer* organization never routes through that constant.
  4. **This is the dogfooding argument, and it is the point.** If seat
     exhaustion, a suspended-state lockout or a credit soft-block is unpleasant,
     we feel it first and on our own operations — which is the only reliable way
     these get fixed before a customer meets them.
  5. **Consequence for the test deployment:** the acceptance bar for "the
     platform works" is *two* organizations provisioned through the same API,
     with data isolation demonstrated between them — not one org that happens
     to be us. MT-1i's two-org fixture is the tenant-side half of the same
     proof.

- **D35 — Two consoles, two deployables: the customer's inside the product, ours
  outside it.** *(owner-accepted 2026-08-12 — agent-recommended in session, owner
  replied "everything looks good"; sub-parts 3 and 4 are the ones a future agent
  would otherwise "fix". Owning specs: `subscription_console.md` (WS-30) and
  `saas_multitenancy.md` §4.1a.)*
  1. **Subscription Console — inside Metorite**, at `/settings/billing` in
     the workbench, admin-gated, one org, tenant-scoped. Unchanged from
     `subscription_console.md`; restated here so both halves sit in one place.
  2. **Operator Console — a SEPARATE Next.js app** in this monorepo, deployed at
     its own hostname. Chosen over a gated route tree inside the existing
     workbench because *"they share tables and must never share routes"* is then
     enforced **structurally** — the surfaces are different applications — rather
     than by a guard that is one misconfiguration away from serving cross-customer
     data into a tenant's UI. **R7: the fence is the deployment boundary itself,
     not a test.**
  3. ⚠️ **The Operator Console SHOULD pin one Microsoft Entra directory — ours.**
     This is the exact assumption D33.1 recorded as fatal, and it is **correct
     here**: the console is staff-only, and every user of it *is* in our
     directory. **Do not "fix" this to multi-directory** — that would widen a
     cross-customer surface to identities we do not control. D33.1 binds the
     *customer* product; this is its inverse and the distinction is the point.
  4. ⚠️ **The Operator Console is EXEMPT from the theming engine**
     (`workbench/control_plane/AGENTS.md`'s eight rules and its conformance
     suite). "One product, one look" exists for surfaces **customers** see; this
     is an internal tool for a handful of staff. Exempt structurally — it is a
     different app, so the conformance suite does not scan it — which is why the
     exemption must be *written down* rather than merely observed.
  5. **Neither console is on the critical path to customer #1.** Provisioning,
     seat assignment, credit grants and balance reads all work through the
     Control Plane API today (CP-1). The console makes that pleasant; it does not
     make it possible — which is why the **Operator** console is CP-8 and
     deliberately last. **Revenue order is enforcement, then checkout:** CP-3 →
     CP-4 → CP-6 makes credits real and sellable against a hand-issued invoice
     (D19.4's manage-only launch posture), and **CP-9's** self-serve checkout
     follows once manual invoicing is the bottleneck rather than the plan.
     Shipping checkout before enforcement would mean taking money for something
     we cannot yet limit.
     > ⚠️ **Corrected 2026-08-18.** This clause said *"CP-8's self-serve
     > checkout"*. CP-8 is the Operator Console and reconciliation and always
     > was; the checkout's payment seam had **no ticket anywhere** until it was
     > minted as **CP-9** (`specs/customer_console.md` §6). Two consequences of
     > this clause that a dispatching agent must read together: the ordering
     > binds the **flip to live checkout**, not the dark build — CP-6's
     > mechanism is already BUILT with its refusals OFF behind
     > `CUSTOMER_CONSOLE_SPEND_GATE` — so CP-9 and WS-30 SC-4a/SC-4g are
     > dispatchable now, and the four-item flip set is in `subscription_console.md`
     > SC-4a.

- **D34 — Supabase is the Control Plane's database AND its authenticator.**
  *(owner call, 2026-08-12: "Let's use Supabase for auth configuration." Owning
  spec: **`specs/customer_console_infrastructure.md`** *(pre-D41:
  `control_plane_infrastructure.md`)*, whose §4 recommended exactly
  this and whose §3 disqualified Firebase on technical grounds.)*
  1. **Managed Postgres, Mumbai (`ap-south-1`)** for DPDP residency (§2 R-f).
     CP-1's migrations apply **unchanged** — Supabase *is* Postgres, which is
     why this decision was safe to take late and stays reversible by `pg_dump`.
  2. **Supabase Auth is the authenticator** — Google, Microsoft and email/OTP for
     customers on no directory at all. This is the half of CP-0 that was never
     code: the providers are wired, but *operating* per-customer SSO is work we
     now buy instead of build.
  3. **NextAuth keeps issuing the Metorite session; Supabase Auth becomes
     one upstream provider inside it** — *not* a replacement. Chosen over
     swapping NextAuth out because it (a) preserves **D32.4 exactly** (the
     Control Plane owns the registry, CC issues sessions), (b) keeps CP-0's
     conditional-provider wiring and its 16 fences rather than discarding them,
     and (c) does not cut the **live auth path** of a running system in the same
     quarter as everything else here.
  4. **Supabase Auth authenticates; it never decides entitlement.** Seats,
     membership and credits stay the Control Plane's authority. A signed-in
     person with no seat is still refused at `/registry/resolve` — sign-in and
     *admission* are two questions and Supabase answers only the first.
  5. ⚠️ **Do NOT adopt Supabase's client-direct + RLS idiom.** That is the
     *tenant* plane's model and explicitly wrong for this plane, which is
     cross-tenant by design (§0.9.2). Our FastAPI service stays in front; no
     browser talks to Supabase directly.
  6. 🔴 **OWNER-GATE:** creating the project, configuring providers, and holding
     the keys. Everything above is buildable against fixtures without them.

- **D33 — The personal-brain assumptions that do not survive, and the two nobody had
  found.** *(owner-directed 2026-08-12 — "we might have made a lot of decisions
  initially that are not scalable anymore… create documentation to overwrite some of
  the initial architecture decisions"; audit `agent-taken` and measured against the
  working tree the same day. Owning spec: **`specs/saas_operations_doctrine.md`** —
  §2 the eight capability domains, §3 the Indian commercial/legal layer, §4 the
  twelve-finding audit, §5 the gap table.)*
  1. **Two findings are NEW, in one file, and both block customer #1 —
     not customer #2.** `workbench/control_plane/src/auth.ts`: **(a)** sign-in is
     pinned to **one Microsoft Entra directory** (*"the tenant-level app registration
     ensures only users in the Fracktal Microsoft 365 directory can sign in"*) — a
     paying customer's staff are not in our directory, so **today the product can
     onboard exactly one customer: us**; **(b)** auth **fails OPEN** when unconfigured
     (*"if no AUTH_MICROSOFT_ENTRA_ID_ID is set, middleware allows all traffic"*) —
     a mis-provisioned production box is wide open and reads as working. **No existing
     ticket covered either.** Now **CP-0**, ordered ahead of the whole WS-31 sequence.
  2. **There is no signup.** No `signup`/`register`/`onboard` route exists in the app
     tree; the only way in is `ensure_owner_bootstrap()` promoting an
     `EXECUTIVE_EMAILS` address (`acb_auth/access.py:581`). Invite-only is a product
     decision; *no signup at all* is a missing product. Now **CP-2a**, with
     provisioning required to be **idempotent and resumable**.
  3. **Not every old convenience is a defect, and one must NOT be "fixed".**
     `ProviderKeyStore._resolve_org` resolving "the sole organization" and **raising
     once a second exists** (`acb_llm/key_store.py:124`) is the correct pattern — it
     **fails closed and names its successor**. Recorded so a future agent does not
     helpfully replace it with a silent default. `colleague_onboarding.md` likewise
     stays as-is: it is the **internal staff** path and must not be merged with the
     customer path, because a customer is not a colleague — no shared directory, no
     shared threat model.
  4. **Three build-order changes with legal, not architectural, reasons**
     (`saas_operations_doctrine.md` §3, sources dated in §9 — re-check before relying):
     **(a)** capture **GSTIN + registered state at signup**, in the org-creating slice
     — SaaS is an 18% service supply and B2B invoices need both; **(b)** the **RBI
     E-mandate Framework 2026** clears recurring debits without OTP only to
     **₹15,000/transaction**, which makes ~25 seats at ₹600 a real product boundary,
     makes annual billing a friction escape rather than just a discount, and means
     **auto-top-up above the cap will fail exactly when a customer is out of credits
     mid-workflow** — so cap the default top-up below it and treat a failed mandate as
     the §3.3 soft-block path; **(c)** **model the DPDP consent record now**, while the
     tables are empty — *model training / product improvement / benchmarking are named
     secondary purposes needing their own consent*, which for an AI product is the
     sharp edge (Consent Manager obligations Nov 2026, full compliance May 2027).
  5. **Two capability domains have NO owner** and need one before customer #1:
     **compliance** (§3.3, and it carries a date) and **per-tenant restore** (D31 /
     `saas_multitenancy.md` §6.6 — a whole-cluster restore rolls every other customer
     back). Naming them here is not the same as ticketing them; both still need a
     seven-point contract before dispatch.
  6. **The audit is the record; do not re-run it from the same starting point.**
     Findings 6, 7, 9, 10 and 12 were already tracked (MT-1i, §6.3, D31, D32.7, D10) and
     are listed in §4 only so one table answers "what did we assume as a personal app".

- **D32 — The platform engine: eight calls taken 2026-08-12** *(owner-directed in
  session — "Metorite is evolving to a SaaS platform… I want to rework the way
  we set up model access"; the sub-calls marked `agent-taken` were delegated
  explicitly: "you can take the decisions accordingly". Owning spec:
  **`specs/customer_console.md`** *(pre-D41: `platform_control_plane.md`)* (WS-31).)*
  1. **⚠️ `saas_multitenancy.md` §3.1 is REVERSED.** Its blockquote —
     *"Do not reintroduce a separate proxy. The gateway's /v1 already IS the
     proxy"* — no longer binds. **The reason it gave still binds**: Metorite
     must not grow a second tenancy boundary, and it does not, because the Router
     sits OUTSIDE CC as a supplier and never resolves a CC session. What §3.1 did
     not weigh is **§5.1's silo rollout**: with one deployment per customer,
     "meter inside Metorite" puts the rate card, the margin and the credit
     balance on the customer's own box, N times over. A meter the metered party
     hosts is a suggestion. §3.1 carries a banner pointing here.
  2. **The Control Plane is ONE CENTRAL SERVICE, built at full scope now** —
     org registry, subscriptions, seats, AI metering and routing together, not AI
     alone. This is `§0.9.2`'s Control plane extracted from each deployment rather
     than a new concept, and it pulls MT-2/MT-4 forward of their board position.
     *(Owner chose full scope over the narrower agent recommendation; the size
     was flagged and accepted.)*
  3. **D15 is untouched.** Tenancy stays a ROW (`organization_id` + FORCE RLS at
     the seam); the deployment stays a PLACEMENT. Silo-vs-pooled remains a
     `tenant_placement` row. The Control Plane is what makes that indirection
     worth having — it can move a customer between placements; N sovereign
     deployments cannot.
  4. **Identity: the Control Plane owns the registry; Metorite keeps issuing
     sessions** (`agent-taken`). Global `user_identity`/`org_membership` authority
     moves central; NextAuth Google SSO and the session cookie stay exactly where
     they are, resolving against the registry on sign-in and caching the answer
     into migration 159's (currently inert) tables as a projection. **No
     authentication server is built and nothing on the live auth path is cut
     over.** Cost accepted: deprovisioning is cache-TTL fast, not instant. The
     full-IdP option is not foreclosed — the registry table is the same either way.
  5. **Seat semantics, defined once** (`agent-taken`): purchased =
     `SUM(seat_grant)`, assigned = live `COUNT(seat_assignment)`, available =
     the difference. **Membership is the Core seat (D19.3, unchanged)** and a seat
     is consumed at **first successful sign-in resolution**, not at invitation —
     an invited person who never signs in costs nothing. D19.3's hard cap is
     carried verbatim: over-assignment is a **409 with a buy-more payload**, never
     an auto-upgrade.
  6. **Credits roll over indefinitely while the subscription is active**
     (`agent-taken`, owner-stated intent "credits that roll over monthly").
     Balance is a ledger sum, so rollover is the default and expiry is what would
     need machinery. Not refundable in cash; expire only at `cancelled` + export
     window. No purchase-lot tracking until a grant with an expiry exists.
     **D19.2 is carried unchanged** — ₹10 credit, fractional draw at provider
     cost × 2, INR-native rate card, LLM + per-minute STT metered, embeddings and
     WhatsApp per-number fees absorbed into package prices.
  7. **Model access is reworked: customers never see a model** (`agent-taken`).
     Tiers are the only vocabulary outside the Router; `_TIER_ALIAS_MAP`
     (`acb_llm/client.py:86`) becomes a cache of a Router-published **tier
     capabilities document**, not truth. The customer-facing provider/model/tier
     picker is **removed from the product** (it was correct for a personal
     application where the operator was the user). A bare model id is **rejected
     400, not coerced** — silent coercion (`_model_resolution.py:35`) hides a
     misconfigured agent behind a bill. Consequence: adding `tier-vision` /
     `tier-embed` / `tier-voice` later is a data change, so **this decision
     deliberately does not decide them**.
  8. **Per-member metering and WS-16 Phase E are ONE mechanism**, as the board
     already required ("design once, serve both"). Credits are an **org pool**;
     a per-member cap is a policy against the pool, never a sub-wallet, so
     unallocated headroom is never stranded. Default on exhaustion is **degrade to
     `tier-fast`**, not block — which is itself an argument for keeping tiers.
  > 🔴 **Unchanged prerequisite:** per-org keys are meaningless until
  > `GATEWAY_INTERNAL_TOKEN` is split from `LITELLM_MASTER_KEY` (§6, measured
  > 2026-08-05 as byte-identical). Owner's act, via redeploy.

- **D31 — D15 re-tested against Odoo, Salesforce and SAP; it stands. One gap found.**
  *(owner-raised 2026-08-11 — "Odoo is also a large complex app with multiple sub-apps
  for one company; is shared-database still ideal?"; agent-answered,
  `agent-proposed, owner may overrule`.)* Full record in **`saas_multitenancy.md`
  §0.9.9**; do not re-run the comparison from the same starting point. **The rule it
  yields: what forces a database per customer is not customization, it is
  customization implemented as DDL.** Odoo adds a custom field by issuing real DDL and
  keeps modules/views/rules as rows in the tenant database, so its tenants cannot share
  one — a fact about Odoo's implementation, not about RLS (Odoo's own `ir.rule` is
  row-level filtering, trusted to separate legal entities inside one database).
  Salesforce/ServiceNow/NetSuite run the deepest per-tenant extensibility in the
  industry **pooled**; SAP's answer to the same pressure was to constrain *how*
  extension is expressed (key-user + side-by-side on stable APIs), not to silo. We are
  already on the metadata side and shipped it: `155_projects_custom_fields.sql` —
  definitions as rows, values as JSONB + GIN — and apps as rows (migration 114).
  **Recorded against ourselves:** SAP CAP runs §7 item 1c's schema-per-tenant in
  production, and it survives rejection here only because *we* ship continuously
  against a 160+ file ladder applied before restart with no rollback — so §7 item 1c
  now carries that as the first rejection to re-open if our cadence changes.
  **The one genuine finding: there is no per-tenant restore — only a whole-cluster one
  (§6.6).** Due before customer #2, not before customer #1 (with one tenant they are the
  same operation). **Caution carried:** keep custom fields typed and narrow; a general
  EAV/metadata platform is a priced feature with its own spec, not a pattern to spread.
- **D30 — `CLAUDE.md` at the repo root is the always-loaded briefing.**
  *(owner-directed 2026-08-10 — "any cloud instance must understand the
  development philosophy, the work plan and the architecture at all times".)*
  Claude Code auto-loads `CLAUDE.md` into **every** session including cloud and
  headless ones; the repo had none, and the 16 KB root `AGENTS.md` is not
  guaranteed to be in context at session start — so a cloud instance began
  blind. `CLAUDE.md` is a **router plus the minimum that must be known before
  the first tool call**, and deliberately duplicates nothing: where truth lives
  and in what order (INDEX → §1 rules → §2 board → §6 gates → §3 decisions →
  engineering practice → owning spec); the architecture in one screen (Centers
  are projections · apps have two access paths and one permission model ·
  modules are billing atoms and Center packages are what customers buy · tenancy
  is a row and we are multi-tenant from customer #1 · visibility is
  private→Center→org); the non-negotiables (never commit on main, refuse
  owner-gated work by name, R5/R6/R7/R8/R1, verify delivery by evidence); the
  development method (one narrowed slice, audit → implement → verify → review →
  PR → stop, verifier ≠ implementer, adversarial reviewer, re-verify anchors,
  extend seams never fork them, short branches); what NOT to do (re-litigate
  D1–D29, refactor to conform, build a second way to do an existing thing, mark
  done from the write-up); and the environment traps that waste the most time.
  **Rule: `CLAUDE.md` stays a router.** Anything that grows into content belongs
  in an owning spec with a pointer here — a briefing nobody finishes reading is
  a briefing nobody follows.
- **D29 — The agent harness is tracked in git; derived indexes are not.**
  *(owner-raised 2026-08-10 — "cloud instances should use the full development
  philosophy"; `agent-proposed, owner may overrule`.)* `.claude/` was a **blanket
  gitignore**, so every checkout that was not the owner's laptop — a cloud Claude
  instance, a fresh clone, a headless run, an agent's own worktree — ran with
  **no `plan-guard`** (i.e. no §6 owner-gate enforcement at all), no
  supervisor-worker agent definitions and no `/next-ticket`. A safety control was
  travelling by accident. **Now tracked:** `.claude/AGENTS.md`, `agents/`,
  `commands/`, `hooks/` (`plan-guard.mjs` + test, `rtk-bash.sh`),
  `settings.json` — scanned, no secrets. **Still ignored:**
  `settings.local.json` (per-machine), `worktrees/` (ephemeral checkouts),
  `scheduled_tasks.lock`, `skill-observations/`, `skills/` (third-party bundles —
  tooling, not doctrine). **Derived artifacts stay ignored and are rebuilt:** the
  CodeGraph index (`.codegraph/`) would be large, churn every commit and conflict
  every merge; its config (`.mcp.json`, `codegraph.json`) is already tracked, so a
  fresh environment installs the tool and builds its own. General rule: **anything
  derivable from source is rebuilt on the new machine; only sources of truth are
  tracked.** Fence (R7): `node .claude/hooks/plan-guard.test.mjs` now runs
  **blocking** in `pr-check`. Recorded in `engineering_practice.md` §5.1.
- **D28 — The development doctrine is written down and three of its rules
  bind.** *(owner-requested 2026-08-10 — "document all of this for our
  development process"; the doctrine itself is `agent-proposed, owner may
  overrule`.)* Owning spec: **`specs/engineering_practice.md`** (CONTRACTS &
  DOCTRINE in `INDEX.md`). Two premises: **(i)** our failures are *delivery and
  integration* failures, not coding failures — the evidence table in §0.1 is
  seven measured incidents from one fortnight, none of which a unit test could
  see; **(ii)** *a rule binds an agent only when a test refuses to break it*,
  which R5 already said and R7 now generalises. Recorded as binding: **R6**
  expand/contract migrations (the deploy applies migrations before restarting
  services, so old code always meets new schema), **R7** name-the-fence, **R8**
  real-database verification for SQL plus verified-red-first and mutation
  testing. Recorded as doctrine, not enforced: deploy≠release with **ring order
  us → one friendly customer → all** (the silo phase makes rings free), seam-based
  work partition with short branches and a **3–4 in-flight cap** (long branches
  are the single root cause behind the renumber trap, the cross-PR fences and the
  duplicated tenancy design), the adversarial-reviewer rule, and the
  customer-grade definition of done (delivery verified by evidence, never by a
  green job). §8 lists five items to close before customer #1, each pointed at an
  existing board row — SHA-in-`/health` (WS-25), off-box backup (BO-23,
  owner-deferred and **due for revisit**), migration rehearsal against a
  prod-shaped restore (WS-5, new), RLS promotion (MT-1b), lifecycle-sweep tenant
  binding (MT-1d).
- **D27 — The WS-27 × WS-29 alignment audit.** *(agent-run 2026-08-10 at the
  owner's request; findings fixed or recorded the same day.)* **Verdict:
  substantially aligned** — the Projects app's 19 `pm_*` tables are all
  tenant-keyed, `routes/projects/` uses the one shared seam with no Redis and
  no route reading a tenant from request input (R5 a–e hold), and no `pm_*`
  column repeats the CRM `organization_id` homonym. **Three gaps, all closed or
  ticketed:** (1) **the generated RLS set was stale and contained a phantom** —
  `pm_intake`/`pm_task_watchers` had no policy in any phase file (they would
  have promoted tenant-keyed but cross-tenant readable), and
  `04_policies.sql` carried `ALTER TABLE if ENABLE ROW LEVEL SECURITY`, a
  statement that cannot run, in a script promoted **by hand against production**;
  root cause was the generator regexing `CREATE TABLE` out of comment prose.
  Fixed: comments stripped, phantom names now RAISE, set regenerated (135
  tables), and a new CI fence compares the committed artifact against
  `discover_tables()` — nothing did before. (2) ~~**`run_lifecycle_sweep` sweeps
  every tenant's projects with no predicate on a scheduled path H2 never
  reaches**~~ ✅ **CLOSED 2026-08-10 by WS-27aa** — the sweep takes a required
  `organization_id` (blank → `TenantUnbound`, before any statement) and filters
  roots on it; `_pm_lifecycle_sweeper` resolves that tenant from the workflow
  **owner** through `app_user` (the `workflows` table has no `organization_id`
  until H3 phase 1) and binds `tenant_session(org)`. MT-1d's own "it needs a
  per-tenant loop" is struck: the loop is over *workflows*, one per tenant —
  a loop inside the sweep would be the unbounded-job shape H4 forbids. Proven
  two-org against a real Postgres (`tests/live/live_ws27aa.py`, 23 checks). (3) **`resolve_organization_id` resolves the tenant from
  inside the session and encodes one-person-one-org** — recorded on **H2/H6**,
  which also gained the measurement that 21 `app_user`-derived org reads across
  6 modules sit behind H6's one-line "app_user reads are gone" criterion. Also
  swept: migration 161's header now covers the generated **phase 3** (duplicate
  FK + the 17 indexes it argues against), `intake.py`'s recursive descent
  repeats the tenant predicate like `core.py`'s, and
  `project_management_app.md` — which had cited **no tenancy decision at all** —
  now cites D15 and records D23's placement of Projects inside Center packages.
- **D26 — The docs consolidation: `ai-company-brain/` → `project-docs/`, and an
  ACTIVE/DEFERRED classification of record.** *(owner-directed 2026-08-10 —
  "retire what is not in the immediate work plan, make active vs deferred
  clear, simplify so agents focus on required code".)* The folder renamed
  (261 tracked files swept, CI path filters included); **`project-docs/INDEX.md`
  is the classification of record** — ACTIVE (dispatch) · CONTRACTS & DOCTRINE ·
  REFERENCE · DEFERRED/HISTORICAL (19 files banner-marked, dispatching
  nothing). Standing rule: a new spec enters INDEX in its creating PR, in
  exactly one section; leaving ACTIVE takes a banner in the same PR. The
  `docs/` root stays engineering reference (one ACTIVE owning spec lives
  there: `docs/multiplayer/memory-clearance.md`). #399's docs classified:
  `multi_tenancy.md` superseded-measured-record, leak audit = MT-1i reference,
  branch HANDOVER historical, plane research reference-only.
- **D25 — Seven standing gates answered in one round (owner, 2026-08-10).**
  1. **Identity/IdP (MT-1a):** homegrown auth through the silo phase; an
     external IdP (WorkOS/Clerk/Keycloak) becomes a precondition of the §5.1
     pooled cutover **or** the first enterprise SSO ask, whichever first.
  2. **Version skew (§1.4b): meaning B only** — one codebase, one schema,
     always latest; orgs differ by per-org feature flags + release channel
     (`org_feature_flag`). A/C/D are not supported and no spec may assume them.
  3. **WS-14 C4 approvals routing:** **Center leads approve their Center's
     actions** — approver role on the `org_group`, proposals carry a
     requesting-Center column, fallback to org admins when unset. The
     "answer Q2, then add a column" ticket is now dispatchable.
  4. **WS-10 floor control: CUT.** Steer suffices; the five floor modes, turn
     queue, observer lane, handoff-with-note and HITL floor-holder routing are
     retired unbuilt. WS-10's remaining mandate is the S1 `subject:` slice
     (+ the still-gated prefs/user backfill APPLY).
  5. **WS-24 N5:** the nine unscoped `routes/notes` modules do **not** block
     colleague #1; the D12 grant-table treatment is owed **before colleague #2
     or any external member**. · **D14(ii) closed:** `manager` keeping the
     `/admin` read surface is acceptable — department privacy protects work
     content, not the org chart.
  6. **WS-12 Phase 4.0: minimal bump** (SDK 0.1.32 → 1.0.2, breaking-fixes
     only). Phase 4.1 evidence → 4.x now dispatchable; the 4.6 human soak
     remains owner-gated. · **WS-20 OCR: fast/cheap vision tier** via LiteLLM;
     per-call escalation later; Meta env/app review remains its own gate.
  7. **WS-22 draw.io: ⏸ PARKED by owner** (officially; re-enters when a real
     need pulls it). · **Guest access to Centers: deferred** until the first
     real guest; the standing rule holds — no external sharing before that
     design exists.
- **D24 — The customer-framing round: §8 item 5 CLOSED (owner, 2026-08-10).**
  1. **The ₹600 headline stays** — Core is the advertised base; the agent's
     "Workspace ₹1,200 default" framing was considered and REJECTED.
  2. **Slices-only Centers stay ₹300**, pitched as "a full team workspace"
     (projects, knowledge, dashboards for that department), never as a filter.
  3. **An all-Centers seat exists: ₹1,800/user** — every Center package, NO
     add-ons (Builder/Workflows stack on top). The multi-hat relief.
  4. **Complete reprices ₹3,600 → ₹3,000** — the all-Centers seat made ₹3,600
     dominated (full stack = ₹3,200); Complete = Core + all Centers + both
     add-ons at ₹3,000, keeping D20.5's all-GA + price-protection promise at
     the new number. Ladder of record: 600 · 1,200 · 1,800 · 2,400
     (Core + all-Centers) · 3,000 (Complete).
  5. **Role presets ship in SC-2's launch scope** ("Sales rep", "Field staff",
     "Founder" generate the users × Centers grid). Also standing: a
     typical-month credit anchor on the pricing page; internal vocabulary
     (atoms/slices/modules) never appears customer-facing.
- **D23 — Pricing is CENTER-SHAPED: Center packages are the sales object,
  modules stay the billing atoms.** *(owner-directed 2026-08-10, four knobs
  answered in session; supersedes D19's module-first customer framing and
  **D20's Team/Business tiers** — Complete survives, recast below. D19's credit
  model (₹10 fractional unit), seat rules (hard cap · member = Core seat ·
  processor proration) and Complete's price-protection promise (D20.5) all
  carry over unchanged.)*
  1. **Three layers.** **Core ₹600/member, mandatory** (basic AI chat — the
     front door stays lit and burns credits — personal tasks, calendar, people
     directory, personal dashboard, approvals, admin plane). **Center packages,
     per user per Center**: each bundles the Center's own apps **plus that
     Center's slice of the base cross-cutting set (Projects, Knowledge Base,
     Dashboards)** — app-bearing Centers **₹600** (Personal = Email + WhatsApp +
     Meetings, **optional per user, never mandatory**; Sales = CRM incl. CPQ;
     Marketing; Finance; Support), slices-only Centers **₹300** (R&D,
     Operations, People until its HR apps ship). **Org-wide add-ons, per user,
     light up in all the user's Centers**: Builder (App + Agent, incl. custom
     agents) **₹500** · Workflows **₹300** (deliberately an upsell — the
     highest-leverage credit driver). **Company Center is free for leadership**
     — a projection of data the org already bought.
  2. **The unification that kills the admin complexity: one assignment act.**
     Granting a user a Center package = the billing seat + the `org_group`
     membership + the module entitlements + the D12 slice grants, one row;
     unassignment reverses all of it. The console's seat surface becomes a
     **users × Centers grid** (+ an add-ons column) — the customer's org chart
     IS the invoice. WS-30's SC-2 inherits this shape.
  3. **Union semantics.** A multi-Center user holds each shared module once
     (never billed twice for a module) but pays per Center, because each
     package buys a new *slice of team data*, not a re-purchase. Enforcement is
     untouched: MT-2 modules at the seam + D12 grants; schema delta is one
     `center_package(center_slug, module_slugs[], price)` catalog beside
     `plan_catalog`, and seat `source` gains `'center'`.
  4. **Bundles.** Team and Business (D20) are retired — Center packages replace
     them. **Complete recast** *(price agent-proposed, owner may overrule)*:
     all Centers + both add-ons + Core, ~~₹3,600~~ **₹3,000/user/month (amended
     by D24.4 — the all-Centers seat made ₹3,600 dominated)**, keeping D20.5
     verbatim — every GA Center and
     add-on, always, price-protected per contract term, wildcard row.
  5. **Worked seats (as amended by D24):** shop-floor member ₹600 · desk worker
     (Core + Personal) ₹1,200 · sales rep (+ Sales) ₹1,800 · Core + all-Centers
     seat ₹2,400 · everything (Complete) ₹3,000.
- **D22 — The Center roster of record, and four architecture calls.**
  *(owner statement + question round, 2026-08-10; owning spec
  `department_centers.md` §5.)* The full shape: **Personal Center** (per-user
  private workspace — Email/WhatsApp/Meetings/Tasks/Calendar — **NOT a
  department**, no `org_group`, exists for every member automatically; the §2
  registration checklist needs a caller-scoped variant, never a fake group) ·
  **seven department Centers** — Sales, Marketing, Finance, **R&D (new)**,
  People, Operations, **Support (new)** — answering that spec's open questions
  2 and 3; **R&D and Operations launch slices-only** (cross-cutting apps
  scoped to their teams, unique apps deferred) · **Company Center kept** as the
  leadership surface (WS-15; multiple configurable rollups per D21, in-app
  all-slices filters as explicit org-tier grants). **Sales' products, price
  books, brochures and proposal generator are CRM-module features — sold only
  inside the Sales Center package (₹600, D23), never separate SKUs** —
  `crm_app.md` gains them as future phases. The admin/IT
  plane (Appearance, Membership/roles, Live activity, Integrations, Approvals,
  Agent Registry, AI credits) is Core capability surface, not a Center.
  **Amended 2026-08-10 (owner): dual access paths** — every cross-cutting app is
  also a standalone top-level app whose primary grouping is **by Center**: the
  caller sees the Centers they hold access to, each containing its slice (e.g.
  Projects opens to Center groups, each with its projects and tasks). Both paths
  resolve through the same D12 grants — the app view is the union of the
  caller's Center slices, never a second permission model
  (`department_centers.md` §5).
- **D21 — The future module roster is named.** *(owner-directed 2026-08-09;
  roadmap `agent-drafted, owner may amend`.)* Five directions beyond the D19
  catalog, recorded in **`specs/future_modules_roadmap.md`** so future specs
  start from what exists: **Knowledge Base** (graph-based org memory surface —
  new; builds on Mem0/D17, the OFF-by-design Graphiti, notes, acb_graph) ·
  **Marketing** (social + sites + ads insights — new; the Center exists, the
  module does not) · **Customer Support & Success** (tickets + customer pages +
  AI resolution — new; declares a real dependency on Knowledge Base) ·
  **Department Dashboards** (⚠️ NOT new — this is WS-15's mandate; the owner's
  statement adds scope colour: per-department *configurable* dashboards and
  *multiple* leadership rollups — carry into WS-15 acceptance) · **Builder +
  Workflows as cross-department substrate** (⚠️ NOT new — the slicing IS the D12
  doctrine; the one addition is a standing review rule: Builder apps and workflow
  definitions declare a visibility tier at creation). SKU posture *(rewritten
  2026-08-10 for D23 — no a-la-carte list, no D20 tiers)*: a future module joins
  a Center package (Marketing → Marketing Center, Support → Support Center, KB →
  the base slices in every package) or, exceptionally, becomes a new org-wide
  add-on; the spec-time owner call is placement, not a price. Sequencing:
  nothing here precedes WS-29/WS-30 or in-flight
  apps; expected order Dashboards → KB → Support → Marketing. Nothing in the
  roadmap is dispatchable until it earns an owning spec per §1 and a WS row.
- **D20 — Plan tiers: four bundles beside the a-la-carte list.** *(⚠️ **Team and
  Business SUPERSEDED by D23** (2026-08-10, Center packages); Complete survives
  recast at ₹3,600 all-in — cite D23 for the pricing shape, this record for
  Complete's all-GA + price-protection promise (part 5), which D23 keeps
  verbatim. Original record: owner-directed
  2026-08-09 — "bundle modules into three to four plan tiers"; the specific
  packaging is `agent-proposed, owner may overrule`.)* Tiers are **per-user
  assignable plans** (Microsoft-365 style: a user holds one plan, a-la-carte
  module seats stack on top), so they reuse D19.3's seat machinery — a plan seat
  expands to its module seats; no org-level plan concept exists. The ladder,
  chosen for clean ₹600 steps and standard bundle discounts:
  **Core ₹600** (the D19 base alone) · **Team ₹1,200** = Core + Projects +
  Meetings + Workflows (₹1,500 a-la-carte, 20% off — "run your team's work") ·
  **Business ₹1,800** = Team + CRM + Email + WhatsApp (₹2,400 a-la-carte, 25%
  off — the customer-facing layer, the expected default landing tier) ·
  **Complete ₹2,400** = Business + Finance + Builder (₹3,200 a-la-carte, 25%
  off — the Zoho-One-style everything play). Schema consequence for MT-2:
  `plan_catalog(slug, module_slugs[], price_per_seat_month)` beside
  `module_catalog`; `user_module_seat` gains a `source ∈ (plan, alacarte)` so
  unbundling is computable. **Included-monthly-credits per tier is deliberately
  NOT decided** — launch default is none (credits sold separately); bundling
  credits into tiers is an owner knob left open. **Amended 2026-08-09 (owner,
  final review round): Complete = every GA module, always, with price protection
  for existing subscribers within their contract term** — encoded as a wildcard
  `plan_catalog` row, never a hand-maintained list (§2.4a rule 5). Recorded in
  `saas_multitenancy.md` §2.4a; console impact in `subscription_console.md`.
- **D19 — Twelve owner calls taken 2026-08-09** *(the subscription/pricing
  clarification round, answered question-by-question in session; supersedes D18's
  SKU list and §2.4's module table wherever they disagreed — cite D19 for the SKU
  model, D18 for the parallel-plus-ratchet and board-format calls).*
  1. **SKU list of record.** **Core ₹600/user/month** = chat, memory, dashboard,
     artifacts, settings, **tasks (personal lens)**, **calendar**, **people
     directory**, **approvals**, **observability**. **Add-ons ₹300/user/month
     each**: CRM, Projects, Email, Meetings (was `notes`), WhatsApp, Workflows
     (was `automation`), **Finance**. **Builder ₹500/user/month.** Customer-facing
     SKU names are Meetings and Workflows; internal slugs may stay. Consequence
     for the one-store merge (D-PM-6/WS-27h): the Core/Projects boundary is drawn
     on FEATURES over the single `pm_tasks` store — personal task management is
     Core; portfolios, project boards, dependencies, ClickUp import/sync and
     org-wide views are the paid Projects module. A `calendar` feature slug must
     be minted (none exists today). The old paid `people` module row folds into
     Core; approvals + observability leave the `automation` bundle for Core.
  2. **The credit unit.** A **credit is the ₹10 purchase/display unit**; each
     model call draws credits **fractionally** at provider cost × 2 via the rate
     card (a cheap call ~0.2 credits, a large agent run ~15). Customers see a
     credit balance, never tokens; "AI action" is marketing shorthand for the
     credit, not a flat per-call price. **STT (Meetings) is metered as credits**
     on the same rate card; **embeddings and WhatsApp per-number fees are
     absorbed into module prices**. The rate card is **denominated natively in
     INR** — hand-maintained per-model prices, no FX machinery.
  3. **Seats.** **Hard cap**: assigning beyond `seats_purchased` is blocked with
     a buy-more prompt — purchase is always an explicit act. **Every member
     consumes a Core seat** (membership IS the Core billing event; no zero-seat
     members). Mid-cycle changes use the **processor's standard proration**.
  4. **The customer subscription console ships manage-only first.** Launch scope:
     view modules/seats/credit balance and per-module burn, assign/unassign seats
     within purchased caps, request module or seat changes (fulfilled manually
     while invoicing is by hand, per §5's Phase-2 posture). Online
     checkout/top-up arrives with MT-4. Owning spec:
     `specs/subscription_console.md` (WS-30).
  5. **Payments (§8 item 3 — closed): Razorpay only at launch**, behind the
     `payment_provider` seam; Stripe lands as a second implementation of the same
     seam when the first international customer appears.
  6. **Data residency (§8 item 4 — closed): promise India-only at launch.** All
     customer data on India-region infrastructure until a second residency tier
     is deliberately priced.
- **D18 — Three owner calls taken 2026-08-09** *(via the consolidation session's
  question round; recorded here so none is re-litigated. ⚠️ Part 3's SKU list is
  superseded by **D19**, and D19's module-first customer framing in turn by
  **D23** — cite D23 for anything a customer buys; module boundaries survive as
  internal atoms).*
  1. **Priority of record: parallel + ratchet.** App workstreams continue at full
     speed alongside WS-29; the price is **R5** (§1) — tenant-ready by
     construction, enforced by the shipped ratchet tests, so H2's 561-site
     conversion surface stops growing in unconvertible ways. Neither an MT-first
     freeze nor unruled parallelism was chosen.
  2. **Board format: compact rows.** §2 rows carry state + gates + pointers only;
     narrative lives in each owning spec's "Board record (2026-08-09)" section.
     Rationale: rows had reached 29.5k characters and §2 ~77k tokens — unreadable
     in one pass by the dispatch loop's supervisor, whose own contract says to
     read §2 only.
  3. **MT-2/MT-3 business inputs answered** (were the blockers in
     `saas_multitenancy.md` §8 items 1–2): **modules sell as Core ₹600/user/month**
     (Tasks, Calendar, Chat, People directory) **+ ₹300/user/month per add-on
     module** (CRM, Projects, Email, Meetings, WhatsApp, Workflows); **AI resells
     as a ₹10 "AI action" credit unit at ~50% gross margin** (rate card prices
     each model call at provider cost × 2, denominated in credits; credits sold
     via the rate card, never provider tokens). Recorded in `saas_multitenancy.md`
     §8; MT-4's payment-provider split (§8 item 3) remains the one open input.
- **D17 — Mem0 binds the tenant via connection options (Option A).**
  *(`agent-proposed, owner may overrule` — 2026-08-09; owning spec
  `saas_multitenancy.md` §0.1 path 8, shapes in `_implementation.md` §2.4.)*
  MT-1c's done-when 4 required this decision taken and written down — "leaving it
  undecided fails the ticket". The call: Mem0's pgvector conninfo gains
  `options=-c app.tenant_id=<org>` so the same RLS policies govern memory rows as
  every other table; per-tenant roles (B) add operational surface for no isolation
  gain, and scope-string-only (C) is an accepted-risk fallback nobody has accepted.
  Consequence: `org:global` memory scope becomes tenant-global, not
  deployment-global — coordinate with WS-10 S1 before adding any scope shape
  (`saas_multitenancy.md` §1.9).

- **D16 — The agent sandbox splits; the raw-SQL tool goes now, the container
  tier waits for the pooled cutover.** *(`agent-proposed, owner may overrule` —
  2026-08-08, owner delegated the call.)* MT-0c bundled four clauses of wildly
  different cost and urgency, so the cheapest and most valuable waited behind the
  most expensive. **MT-0c-1 (built):** `query_history` took a *model-generated SQL
  string* and ran it through `acb_graph` — and its keyword guard was wrong both
  ways, rejecting its own documented example (`CREATED_AT` contains `CREATE`)
  while letting `SELECT * FROM provider_keys` straight through. That is a live
  within-org read primitive **today**, so it is fixed now: search criteria, bound
  parameters, two tables, plus a build-failing ratchet against the shape
  returning. **MT-0c-2 (still parked, still OWNER-GATE):** the container/microVM
  tier. **D10's reasoning survives for the silo phase** — one tenant per box means
  an escaped agent reaches only the data it already had — so T2 becomes a
  precondition of the **§5.1 pooled cutover** (customer 8–12), not of Phase 0.
  Building Firecracker-grade isolation before customer #1 is speculative
  infrastructure paid for out of the runway that should be buying customers.
  Owner: **WS-29**, spec `specs/saas_multitenancy.md` MT-0c.
- **D15 — The tenant boundary is a ROW, not a deployment.** *(owner-requested
  2026-08-08; re-takes **D11**.)* Tenant = `organization_id`, enforced by Postgres
  **FORCE ROW LEVEL SECURITY** bound at the `get_db()` seam with `SET LOCAL
  app.tenant_id`; the deployment becomes a *placement* (region/tier), and a dedicated
  database or stack survives as a **priced tier**, not the architecture. D11's cost
  objection — *"a `WHERE organization_id = ?` on 111 tables and every query"* — does not
  hold: connection sites are a bounded set of **eight** (`saas_multitenancy.md` §0.1) and
  **zero existing `SELECT`/`INSERT` statements are rewritten**. **D11's §2–§5 survive
  untouched** — this changes tenancy only, never visibility. Consequences: row-level
  tenancy, an org switcher, multi-org users and per-org credentials all move **into**
  scope (D11 §6 listed all four as out); leak sites 1–10 stop being moot and become
  MT-1i; and **MT-0c requires un-parking D10's T2**, because "trusted colleagues, not
  hostile users" is exactly the threat model that selling externally retires. Owner:
  **WS-29**, spec `specs/saas_multitenancy.md`.
- **D1 — Cost attribution is one workstream.** Stamp every LLM call at the
  gateway choke points with (run_id, member_email, agent, instance). Per-room
  (multiplayer §5.3), per-instance (agent-kinds §9.4), per-member and
  per-Center views are all rollups of that one record. Owner: WS-6.
- **D2 — Budget subject: member first.** Per-member monthly caps ship first
  (WS-16); per-room `token_budget` + degrade-to-read-only (multiplayer Phase 4)
  builds later on the same records. Per-group rollups after.
- **D3 — Instancing storage: columns now, manifest later.** Add the `sharing`
  columns to `dynamic_agents` now (agent-kinds §3 shape; next free migration
  number) to unblock WS-14. When WS-8 Phase A lands, those columns become
  *derived from* `agent_defs` manifests — one store, not two. The
  agent_architecture manifest is the long-term source of truth.
  **Amended 2026-08-03:** WS-14's unblock does **not** wait on WS-8 Phase A.
  `config.json`-based instancing already ships via `AgentManifest.instance_key()`
  and is live on the blob store and the workspace file manager with no schema
  change (`agent_architecture.md` §12.1/§12.5). The `dynamic_agents` columns are
  WS-14's own migration; Phase A only changes where they are *derived from*.
- **D4 — Orchestrator org-memory: patch now, unify later.** The missing
  org/agent-scope read on the orchestrator path (`agent_architecture.md`
  §11.1.2) is fixed as a small standalone defect in WS-15. WS-8's A1 runtime
  unification remains the structural fix and deletes the duplicate path.
- **D5 — Shared mailboxes:** `email_app_master_plan.md` owns implementation;
  Centers C sequences it; research §16.7 is design reference only.
- **D6 — The Workflows app won.** `workflows_app.md` + `docs/workflow-editor/`
  are authoritative for graphs, compiler, editor, and workflow-as-tool.
  `multi_agent_orchestration.md` Phases 2–3 and §5.3 are superseded; its
  **Phase 4 alone remains live as WS-12; Phase 1 was struck to WS-23 and
  Phase 5.1 to WS-11 on 2026-08-03** (Phase 5.2 shipped as multiplayer rooms
  under WS-10).
- **D7 — MCP registry exists, with a MAF-side gap.** `13_mcp_servers.sql` +
  gateway CRUD + per-run injection are live (the coherence audit missed it by
  searching for the spec's planned name — R1's disease exactly).
  `mcp_plugin_integration.md` Phase A = shipped; Phases B/C remain research.
  **Verified 2026-08-01:** `_inject_mcp_servers` runs for every agent but
  writes `agent._mcp_servers`, which only the Copilot runtime reads — for
  native-MAF agents MCP injection is a **silent no-op** (no
  `MCPStdioTool`/`MCPStreamableHTTPTool` wiring exists). Any manifest
  `capabilities.mcp_servers` promise (agent_architecture §6) is unimplemented
  on MAF until WS-8 closes this. **Retargeted 2026-08-03:** that instruction is
  now carried in the owning spec as the ticket **WS-8c**
  (`agent_architecture.md` §12.2, AGENT-SAFE) — dispatch it from there, not from
  this decision record.
- **D8 — Budgets/caps enforcement lives at the gateway choke points**, never
  per-app. (Same principle as prompt caching and model tiers: one seam.)
- **D9 — "Pomad Centre" — RESOLVED 2026-08-01.** Owner confirmed it is not a
  real venture (a stray name that should have read Metorite). All 12
  sites across 8 files rewritten as "a second tenant deployment" — the
  phrasing that preserves each sentence's meaning, including the two
  security-requirement sites (`agent_platform_hardening` §64's T2 gate now
  reads "Before multi-tenant (a second org on this platform)"). The name no
  longer appears anywhere outside this decision record. *(2026-08-09: the
  replacement phrase itself — "a second tenant deployment" — embodied D11 and
  was re-swept to organization/placement language after D15;
  `department_centers.md` §4 Q1 keeps the twelve-site inventory as history.)*
- **D10 — Two owner calls taken 2026-08-03.** Recorded here so neither is
  re-litigated by a later dispatch.
  1. **Metorite is an internal Fracktal tool.** *(⚠️ Premise re-scoped
     2026-08-08 by D15/D16: still true as a fact today — no external tenant
     exists yet — but no longer the planning posture; WS-29 exists to retire it.
     The T2 parking below survives in narrowed form: un-parking is a
     precondition of the §5.1 pooled cutover (D16), not "a second org on this
     platform, or agent authorship from outside Fracktal". Every doc decision
     that rests on this premise was annotated with its expiry trigger in the
     2026-08-09 sweep — `agent_platform_hardening_2026-07.md` §1.5,
     `permissions_sandbox_b6.md` §P5-c/d, `workflows_app.md` §1.4,
     `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-1's enforce posture,
     `meeting_bot_platform_plan.md`'s ELv2 argument.)* The team uses it; there
     are no external tenants and no third-party agent authors. **Consequence,
     already applied in the specs:** WS-3's T2 / full run sandboxing
     (`permissions_sandbox_b6.md` §P5-c) is **parked** under a
     trusted-colleague threat model — the ladder must hold against colleagues,
     not hostile users, and P5-a's credential scoping plus P5-b's ceilings plus
     WS-3a/WS-3b address the concrete standing exposures. **Un-parking is
     OWNER-GATE and has an explicit condition: a second org on this platform,
     or agent authorship from outside Fracktal.** Until then P5-c carries no
     acceptance criteria and none should be written; P5-d is blocked behind it.
     The same threat model is what makes `ACTION_BROKER_ENFORCE` OFF an
     acceptable posture (audit-and-chokepoint rather than per-click approval)
     and what bounds the Agent Workshop's value in `agent_architecture.md` §12.
  2. **Loops in the workflow engine are approved**, against `workflows_app.md`
     §11's standing anti-n8n rule R1. Real automations iterate; an engine that
     cannot iterate pushes makers back to the toil the app exists to remove.
     The engine-complexity cost was stated and accepted. **R1 keeps its original
     meaning unchanged — it governs the node *catalog* (a node exists only if
     the Integration Registry has the integration), not the control-flow
     *vocabulary*** — and must not be cited as a blocker on WS-11's 8.3c.
     Recorded in `workflows_app.md` §8.3c and §11 R1.
- **D11 — ⛔ SUPERSEDED: the tenant boundary is THE DEPLOYMENT.** *(owner call,
  2026-08-03 — **re-taken 2026-08-08 by D15**. Retained verbatim below as the
  decision record; do not build against it and do not cite it for tenancy — cite
  D15. Its §2–§5 visibility content was never touched; its TV-1 carve-out is
  absorbed as WS-29 MT-1i, where the three joins are re-classed from
  "wrong within one org" to "leak across tenants".)*
  One deployment per tenant: a second organization gets its own box, its own
  database, its own credential set. **Row-level organization isolation is
  explicitly NOT being built.** Consequences, each verified against code and
  recorded in `specs/tenancy_and_visibility.md` §1: `organization_id` stays a
  **label, not a mechanism** — it is on **3 of 111** own tables (`app_user`,
  `org_role`, `org_group`) and is read by **zero** authorization decisions
  (`UserContext.organization_id` is populated by an extra `SELECT` at
  `acb_auth/deps.py:155-157` and never consulted; every `WHERE organization_id`
  in the gateway binds from `get_org_id()`'s hardcoded `slug='default'`, not
  from the caller). Nine of the ten enumerated leak classes are **moot by
  definition** rather than by fix; deployment-singleton credentials
  (`provider_keys.provider` is the PK; integration secrets go into the
  process-global `os.environ`) become **correct** rather than a gap. The cost of
  a second tenant — new box, new DB migrated from zero, new credential set, DNS
  + TLS + systemd units — is written down in §1.2 so the choice stays honest.
  **Do not** "fix" this by threading `user.organization_id` into queries: that
  is the first 5% of row-level multi-tenancy and creates a second scoping
  doctrine alongside D12's. The one carve-out is **TV-1** (§2 of that spec): the
  three `org_group` joins that match on **slug alone** are wrong *within* one org
  too, two of them inside the session-authority intersection —
  `gateway/rooms.py:181-199` (the `SELECT g.slug` at `:192`), `:368-403`
  (`SESSION_VISIBLE_SQL` from `:368`, slug join `:377`), `acb_auth/access.py:330-336`.
  *(The first two were published as `:170-179` and `:332-340`; both were wrong at
  `520476ab` and were corrected 2026-08-03 — see `tenancy_and_visibility.md` §2. The
  third was and is correct.)* AGENT-SAFE, one small PR, with a two-org fixture that
  must be verified red first — **but see the board row for the skip-green trap that
  made "verified red" unsatisfiable as originally written.** Owner: the new spec;
  **board row: WS-14a**, minted 2026-08-03 (it had none, which is why it never
  dispatched).
- **D12 — Visibility is private → Center → org, plus ad-hoc groups by invite;
  and a project belongs to a team by an explicit `group:` grant.** *(owner call,
  2026-08-03; owning spec `specs/tenancy_and_visibility.md` §3–§4.)* The owner's
  words were *"department-wise privacy so that the sales team cannot see what the
  finance team is doing… at the same time organizational-level sharing… and
  projects and groups where information can be shared between select users of
  different departments, depending on invite."* **department = Center =
  an `org_group` row** (R3; `department_centers.md` §1) — write "Center".
  **The primitive exists and must be generalised, not reinvented:**
  `routes/rooms.py::_valid_subject` (`:100-111`) already accepts
  `email | group:<slug> | org` and `chat_session.visibility` is already
  `private|people|org`, with group membership expanded at read time
  (`gateway/rooms.py:181-199` — corrected 2026-08-03 from the stale `:163-179`).
  **Correction to the claim that reached this
  board:** `app_grants` does **not** share that vocabulary — `routes/apps/
  grants.py::is_valid_subject` (`:68-85`) is `email | agent:<name> | agents:*`
  and **rejects the literal `org`** (`:77`), with no `group:` case at all; the
  docstring at `rooms.py:103` claiming the two are "identical" was **false** and
  was corrected 2026-08-03 (a docstring-only edit — the false claim was actively
  misdirecting implementers of this very decision).
  Rooms is the only surface honouring `group:` today; the gap table in §5 of the
  spec is the app-by-app map. **"A project belongs to a team" = an explicit grant
  row carrying a `group:<slug>` subject** — *not* derived from assignees (access
  would become a side effect of task assignment) and *not* an owning column
  (single-valued, so it cannot express the cross-Center project the owner asked
  for). This is the semantic that has blocked **WS-14** for weeks; it is
  answered. **Standing review rule:** a new persisted user-facing surface
  declares its tier — it does not inherit one by accident. Two doctrines in one
  codebase is what produced the Notes hole — **PR #346 merged as `d2ef7fa0` on
  2026-08-03**, so the Notes owner filter has landed
  (`routes/notes/core.OWNED_MEETING_PREDICATE`) and a grant table is the remaining
  work there; the spec's §3.3 and §5 rows were updated to match.
- **D13 — The project grant table is `gtd_*`-local, and it has no `role` column.**
  *(`agent-proposed, owner may overrule` — 2026-08-03; owning spec
  `specs/tenancy_and_visibility.md` §4.1.)* Registered here 2026-08-03 because it was
  discoverable only through a parenthetical in the WS-14 row, while being the first
  decision the tasks team slice makes. **The call:** `gtd_project_grant (project_id,
  subject, granted_by, created_at)` — a `gtd_*`-local table with a real FK onto
  `gtd_projects`. **Three alternatives, all rejected in §4.1:** a polymorphic
  `object_grants` (no FK, an index per `object_type`, and a platform migration
  decision taken inside an app ticket — if the owner takes it, it is its own ticket
  that *also* migrates `app_grants`); reusing `app_grants` itself (impossible without
  dropping its `app_id … REFERENCES apps(id)` key, `114_custom_apps.sql:58-67` — i.e.
  the `object_grants` option reached by mutating a live table four Custom-Apps code
  paths read); and an owning `group_id` column on the project row (single-valued, so
  it cannot express the cross-Center project D12 requires). **The `role` column is
  cut, not deferred:** every clause of the slice's acceptance is a read-path clause,
  so `role` would ship with one legal value and no reader — write-through-grant is
  unanswered and arrives later as an additive `ALTER TABLE` at the next free number
  (R1). **What must not fork is the subject grammar:** one validator for
  `email | group:<slug> | org`, and §4.1 now names its home —
  `packages/acb_auth/acb_auth/permissions.py` (pure, already owns the permission
  vocabulary, no new import edge), because "the shared validator" previously named no
  module and no shared home existed. Acceptance: `department_centers.md` C1.
  *(⚠️ 2026-08-09: re-audit C1 against WS-27e's owner-directed one-store revision
  (D-PM-6 — `pm_tasks` is THE task table, WS-27h retires `gtd_items`) before
  dispatching — the `gtd_*`-local grant table may be building on a floor that is
  scheduled for demolition. The subject grammar rule above is unconditional either
  way.)*

- **D14 — `manager`'s "org-wide visibility" is not `data:org:read`, and
  `data:org:read` should not be relied on by anything.** *(`agent-proposed,
  owner may overrule` — 2026-08-04; owning spec `specs/colleague_onboarding.md`
  §3.0(b).)* Minted because WS-24's capability matrix had to answer "does
  `manager` contradict D12's department privacy?" and the received answer named
  the wrong permission. **The measurement:** `data:org:read` is declared
  (`packages/acb_auth/acb_auth/permissions.py:132`), granted to `admin`,
  `manager` and `agent_service` (`130_org_access_control.sql:205, 221`;
  `:258`), and listed in the legacy-fallback set (`acb_auth/access.py:148`) —
  and **no route, query, predicate or frontend check in the repository ever
  reads it.** A repo-wide search outside the vocabulary, the seed migrations and
  the specs returns nothing. `org_access_control.md:81` described `manager` as
  "sees org-wide data" on the strength of it; that sentence was aspirational
  and was corrected on 2026-08-04 (the same edit replaced that row's *"all
  `feature:*` except `build.*`"* grants cell, which was wrong in five slugs —
  `manager` also lacks workflows, integrations, models, agents and every
  `center.*`).
  **The proposed call, in two parts.** (i) **No spec, ticket or acceptance
  criterion may rest on `data:org:read` until it has a consumer.** Writing one
  is its own ticket: either give it a meaning (which is an org-wide read path,
  i.e. the exact thing D12 constrains) or strike it from `CAPABILITIES` and the
  three seed grants. Leaving it as-is is also acceptable — it grants nothing —
  provided nobody *cites* it. (ii) **The department-privacy question the owner
  actually has to answer is about `admin:members:read`**, which is the floor for
  the **entire** `/admin` package (`routes/admin/_common.py:77-91`), not just
  the member list: a `manager` reads the full member directory, the role
  catalogue and the group list, and `/auth/me` returns `is_admin: true` for them
  (`routes/admin/me.py:96`). Combined with `feature:approvals`,
  `feature:observability` and `memory:write_org` (`131:62`), that is the real
  breadth of the role. **Rejected alternative:** quietly narrowing `manager` in
  a migration. It is a policy call about who may see the shape of the
  organisation, it is exactly the shape of D12, and no acceptance should be
  written for it until the owner decides. **Consequence if the owner does
  nothing:** `manager` stays as seeded and WS-24's matrix labels it accurately;
  nothing breaks, and the only standing rule is (i). *(2026-08-09: the
  zero-consumer measurement is retired — WS-27d's full-portfolio view is
  deliberately `data:org:read`'s first consumer, and granting it to a real
  member is owner-gated in §6 WS-27 (d). Part (i) still binds for every other
  spec: name the consumer or don't cite the permission.)*

## 4. Single-owner registry (who owns duplicated work)

| Work | Owner | Mirrors (link-only after §5) |
|---|---|---|
| **The user-management contract every app must follow** (identity chain, member lifecycle, permission vocabulary, the ten build rules) | **`specs/user_management_contract.md`** — created 2026-08-05, and it deliberately **owns RULES, not FACTS**: every fact is cited to the spec that owns it, so it can never become a fifth competing description of the access model | `org_access_control.md` (the model) · `colleague_onboarding.md` (the gate + the runbook + the matrix) · `tenancy_and_visibility.md` (D11/D12) · `department_centers.md` (Centers as projections). Surfaced to builders from root `AGENTS.md` constraint 10, `apps/services/gateway/AGENTS.md` and `workbench/AGENTS.md` |
| **Colleague onboarding readiness** (the pre-invite gate, the invite runbook, the role × app capability matrix) | **WS-24 — `specs/colleague_onboarding.md`** | `org_access_control.md` §7 (bootstrap) and its role table, lines 79-83 — **line 81's `manager` row was corrected 2026-08-04 per D14** (intent no longer claims org-wide data; the grants cell is now the literal seeded array, because *"all `feature:*` except `build.*`"* was wrong in five slugs) · `department_centers.md` §2 (the five-place Center registration checklist the runbook's step 3 depends on) · `tenancy_and_visibility.md` §5 (the app-by-app gap table — that doc owns the *doctrine*, this one owns the *measured current state per role*) |
| **The single member of record** | **`vjvarada@fracktal.in` is the only signed-in member** (⚠️ **owner-reported 2026-08-04, NOT measured** — it is a live-DB fact and §6 forbids an agent the tool that could measure it; re-check on the box before relying on it) | There is exactly one. `EXECUTIVE_EMAILS` is the bootstrap candidate list (`acb_auth/access.py:467-519`) and is **not** a role — a member's real access is resolved from `app_user` + `user_role` + `user_permission_override`. **No agent may add, promote or suspend a member**: `POST /admin/members`, `PUT /admin/members/{email}/roles` and `PATCH/DELETE /admin/members/{email}` are live-DB writes to the access model and are registered in §6. Onboarding runbook: `specs/colleague_onboarding.md` §2 |
| Groups admin UI + seeding | **WS-13 / Centers B** | groups_sessions_authority §6.5 · org_access §8 Ph2 · multiplayer §4.5 |
| Team-instanced agents | **WS-14 / Centers C3** (mechanism per D3) | `docs/multiplayer/agent-kinds.md` §6/§8 — **note the path: it is under `docs/multiplayer/`, not `project-docs/specs/`**, and its §6 roster is a **design proposal, not a work list** (seven of its twelve agents do not exist; it contradicts three shipped `config.json` files — both annotated there 2026-08-03) · agent_architecture §6/§12A · memory_architecture §6.1 · groups §6.2 |
| Shared mailboxes | ⚠️ **OWNERLESS IN FACT — do not dispatch** (measured 2026-08-03) | D5 assigns implementation to `email_app_master_plan.md`, sequenced by WS-14. That spec contains **zero** occurrences of "shared mailbox", and `email_account_member` — cited as Phase-2 content by `department_centers.md` and `org_access_control.md:311` — **exists nowhere in the repo** (0 hits in `*.sql` and `*.py`). D5's *sequencing* stands; its *ownership* is nominal. **Next action is a doc action, not a build:** either `email_app_master_plan.md` gains a section for it, or this row names a different owner. Recorded in `department_centers.md` C2. | org_access §8 Ph2 · groups §1 · research §16.7 |
| Per-Center approvals routing | **WS-14 C4 — 🔴 OWNER-DECISION, not dispatchable** | `org_access_control.md:405` Q2 is open verbatim (*"who is asked? … per-module approvers is a Phase 2 question"*), **and there is no column to route on**: `infra/postgres/66_pending_actions.sql:13-38` carries no requesting-member, group or Center column. ⚠️ **Corrected 2026-08-03** — this cell used to add "(`actor` is the proposing *agent*)", which is **false**: two of `actor`'s six writers put the requesting human in the string (`routes/apps/tools.py:393`, `routes/apps/actions.py:345`, both `actor=f"app:{slug}:{email}"`), so a group **is** derivable there. The verdict holds on the real evidence — `actor` is free text with five shapes (`app:<slug>:<email>` · `app:<slug>` · `workflow:<name>` · `tasks:<provider>` · `tasks:clickup:ws:<id>`) and a human in only **two of six** proposers, so a Center inbox filtered on it would be silently empty for every workflow-, publish- and provider-originated proposal. The new column must be written by every proposer, not parsed from an ad-hoc string. Evidence: `department_centers.md` C4. The ticket is "answer Q2, then add a column", never "add a filter" |
| Cost attribution | **WS-6** (D1) | multiplayer §5.3/Ph4 · agent-kinds §9 Q4 · Centers D |
| Budgets | **WS-16** (D2) | multiplayer §4.3/§5.3/Ph4 |
| Digest workflows | **WS-15** (also scores workflows G1) | workflows_app §1.2 |
| Orchestrator org-memory fix | **WS-15** (D4); structural fix WS-8 A1 | agent_architecture §11.1.2 |
| Workflow engine/editor | **workflows_app.md** (D6) | multi_agent_orchestration Ph2–3/§5.3 · Ph5.1 (Magentic/GroupChat as graph node types — reassigned to WS-11, 2026-08-03) |
| Isolation ladder (BO-7 / HH-6 / T0–T2) | **`permissions_sandbox_b6.md`** (the Phase-5 build order `P5-a…d`; WS-3) | `agent_platform_hardening_2026-07.md` §1.2 — the ladder *definition* only, and the single T0/T1/T2 table of record · `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-7 · `competitive_hardening_2026-07.md:119-141` (build log for the 2026-07-27 passes) |
| Context discipline / prompt budget | **WS-23** — `skills_registry.md` + `skills_scope_out.md` | multi_agent_orchestration Ph1 (struck 2026-08-03: 1.1 shipped, 1.2 moot, 1.3 delivered here) |
| Collaborative multi-agent chat (Shape C) | **WS-10** — shipped as multiplayer rooms (`docs/multiplayer/README.md`); the floor-control residue is OWNER-GATE | multi_agent_orchestration Ph5.2/§5.6 (struck 2026-08-03) |
| Calendar / Focus OS | **WS-21** — `calendar_focus_os.md` §5 canonical for `gtd_time_blocks`, §9 canonical for all F2/F3 acceptance; `calendar_timeboxing.md` §13 canonical for P4 external sync | The family has **four** docs, not two: `calendar_ai_review.md` and `calendar_ux_review.md` are **unregistered sub-docs**. `calendar_ai_review.md` is cited by three shipped migration headers (92 / 97 / 100) but by no board row and no spec index entry — *(focus_os §9.13 lists the third as 98; the file that cites it is 100)*. `calendar_ux_review.md` is the **sole** home of the block-reminders/notifications item (focus_os §9.13). **Horizons / Top-5 outcomes is DISPUTED between WS-21 (§4.7) and WS-18 — assigned here, to WS-21**; it is still DO-NOT-DISPATCH until it has acceptance (`calendar_focus_os.md` §9.10). |
| Chat HITL model | **generative_ui_2.md §2** (shipped) | chat_ux §12.3 (superseded) |
| Multiplayer prior art (`qm`, 2026-08-01) | **`multiplayer_prior_art_qm_2026-08.md` is reference-only** — it owns no work and no status; the specs it links stay authoritative | multiplayer README §4.6/§5.1/§6.4/§6.5 · memory-clearance §3.3/§7 · agent-kinds §9 Q1 · skills_scope_out §6 · WS-10 · WS-23 |
| Memory compartments + clearance (incl. `subject:`) | **`docs/multiplayer/memory-clearance.md` §7** (surface design §7.1); dispatched as **WS-10 S1** | memory_architecture §9 `3a′` (link-only since 2026-08-02) · multiplayer README §6.3/§8 Phase 3 (index only) · prior-art §QM-D1 (reference only) |
| Native CRM + the Zoho retirement path | **WS-26 — `specs/crm_app.md`** (minted 2026-08-05) | `department_centers.md` Sales Center "Pipeline" app (a projection of `/crm`, flipped live by WS-26c) · WS-1 interplay **settled 2026-08-05 (D-CRM-7/D-CRM-8) — the writer now EXISTS** (branch `ws-26b-zoho-sync`): `ingestion/sources/zoho/writer.py`, the sync engine's **single, broker-gated** writer with one grep-asserted caller (`routes/crm/sync_zoho.py::execute_push`) and three registered `crm.zoho_*` handlers that auto-apply while `ACTION_BROKER_ENFORCE` is off, retired at WS-26e. WS-1's "no Zoho write path anywhere in the repo" sentence was corrected in that same change (done-when 6) — this row and the WS-1 row now agree, and neither should be re-softened · WS-2 (the Zoho-token P0's endgame is WS-26e's **revoke**) · WS-20 §11's "Odoo/Zoho-bound items" (bind to `crm` `entity_ref` per WS-26d instead — ⚠️ as of 2026-08-06 WS-26d has made `"crm"` a KNOWN system so such a ref **parses**, but there is still no linker: nothing writes `wa_contacts.entity_ref` for any system, and the drawer's `crm` block is still `None`. Whoever binds these items owes both halves) · `orchestrator/sales_views.py` + `scripts/reconciler.py` + `skills/sales\|reconciler/*` keep reading the graph mirror until WS-26e repoints them |
| Native project management + the ClickUp retirement path | **WS-27 — `specs/project_management_app.md`** (minted 2026-08-05) | `task_manager_app.md` (the personal GTD lens — untouched as an app; its ClickUp provider **arm** retires at WS-27g while the provider *interface* stays, becoming the seam WS-27e's internal `metorite` provider uses) · `department_centers.md` C1/WS-13 (the tasks team slice and the People Center sub-app list; C1's `gtd_project_grant` = D13 stays C1's own — `pm_project_grants` is a sibling on the same subject vocabulary, never a replacement) · `task_manager_hr_planning_and_memory.md` (people/capability layer — WS-27 reads it, never rebuilds it) · `workflows_app.md` owns the automation engine WS-27f feeds (D6; the Paca-grade uplifts are recorded there as backlog, not here — **written up in full 2026-08-06 as `workflows_app.md` §13, items U1–U8**, where **U1** = the `pm.update_task` node and **U7** = agent dispatch, i.e. WS-27f's two halves, and U2–U6/U8 are engine work WS-27 does not wait on; §13 is backlog and changes neither Slice 3 nor Slice 4) · `paca_pm_research_2026-08.md` (reference-only, owns no work) · WS-1's BO-1a/BO-1b are **named prerequisites** of WS-27c, not discoveries |
| The People Center's surfaces (directory, org chart, capability search, seats) | **WS-28 — `specs/people_center_app.md`** (minted 2026-08-06) | It owns **surfaces, not facts**: `task_manager_hr_planning_and_memory.md` owns the HR data and the capability vectors · `org_access_control.md` owns identity, roles and overrides · `colleague_onboarding.md` owns the invite process and the role × app matrix · `department_centers.md` owns Centers and groups · `project_management_app.md` owns the work. WS-13's *People directory read view* is closed by WS-28b rather than staying open in Centers B |
| **Tenancy boundary** (which company) | **`specs/saas_multitenancy.md`** (**D15** §1 · the three planes §0.9 · tickets §11) + its child **`specs/saas_multitenancy_implementation.md`** (SQL, seams, ratchets, runbooks — shapes only, no decisions) | ⚠️ **`tenancy_and_visibility.md` §1 + §6 are SUPERSEDED** (D11 re-taken 2026-08-08). Cite D15 for tenancy, never D11 |
| Visibility model (who inside that company) | **`specs/tenancy_and_visibility.md`** (D12 §3–§4 · the app-by-app gap table §5 · TV-1 §2 — **unchanged and still binding**) | `department_centers.md` (the "separate deployment is for a separate org, never a department" rule) · `org_access_control.md` §8 Ph2 · `multi_user_organization_research.md` §5/§7/§8/§9/§17 (**research only, and superseded for planning by the new spec**) · `groups_sessions_authority.md` §3 (the intersection rule it constrains) · D9 (the twelve "second tenant deployment" sites) |

## 5. Documentation remediation backlog (WS-0)

> **Update 2026-08-01 (doc-truth pass): EXECUTED.** All Tier 1–3 items below
> were applied by a six-agent pass, each edit verified against code first.
> Kept as the record of what changed. **Residual items** (new or deferred):
> 1. ~~`project-docs/AGENTS.md` build-table rows are themselves stale~~
>    **CLOSED 2026-08-09** — the "What Has Already Been Built (as of
>    2026-06-20)" table was retired outright rather than refreshed: it was a
>    second competing status description (40%+ wrong: broker/meeting-bot/
>    WhatsApp rows claimed unbuilt over shipped work) and §4's doctrine says
>    mirrors are link-only. The file now points at §2 here and the owning
>    specs.
> 2. `note_taker_app.md` §3.13's status-as-blockquote → proper table (cosmetic).
> 3. `chat_ux.md` full archival decision (banner + supersession notes are in;
>    body retained as protocol reference for the still-open §12 VII–XI items).
> 4. ~~`calendar_focus_os.md` "breaks in the packer" may have partially
>    shipped — verify before dispatching F2.~~ **CLOSED 2026-08-03.** It
>    **shipped 2026-07-23** (`80722e17`, migration 97) as *packer geometry*: a
>    widened buffer behind the block that trips `max_focus_run_mins`, plus an
>    optional protected lunch window, applied to plan, replan, rollover **and**
>    the nightly job. The nuance that keeps F2 alive: **the break is a gap, not
>    a row** — nothing renders it, nothing can skip it, nothing counts it. The
>    `kind='break'` row is §9.1 S4.
> 5. ~~D9 (Pomad Centre) remains an owner call~~ — resolved 2026-08-01, all
>    12 sites rewritten as "a second tenant deployment" (see D9).
> 6. **Spec-index and docstring staleness (new, 2026-08-03).**
>    `project-docs/AGENTS.md`'s per-feature spec index is missing rows: it
>    has **no calendar row at all** (four calendar specs, none listed) and no
>    `agent_architecture.md` entry. Separately,
>    `project-docs/AGENTS.md:190` and `apps/AGENTS.md:23` still carry the
>    struck falsehood that the Action Broker *"ships with zero handlers and is
>    not yet wired into the write path"* — untrue since 2026-07-13; see WS-1's
>    five registration sites. ~~Both are AGENT-SAFE doc fixes, neither is in this
>    change.~~ **CLOSED 2026-08-09** — spec index completed (16 missing rows
>    added, incl. the whole calendar cluster and `crm_app.md`) and the broker
>    falsehood corrected at `project-docs/AGENTS.md` (glossary + build-row +
>    priorities) and `apps/AGENTS.md:24`.
> 7. **2026-08-09 — WS-29 consolidation pass EXECUTED** (this change). One
>    sweep, driven by four parallel audits (board digest · MT plan-of-record ·
>    D11/D10-language inventory · status-header inventory): **(a)** §2 compacted
>    per D18, narratives → owning specs' "Board record (2026-08-09)" sections
>    with corrections enumerated; **(b)** D11 and D10.1 bannered as
>    superseded/re-scoped, D9's replacement phrase re-swept, D13/D14 annotated;
>    **(c)** R5 minted, D17/D18 recorded; **(d)** the D15-conflict inventory
>    fixed across ~25 docs (deployment-tenancy claims, internal-tool premises,
>    `slug='default'` teachings, "second tenant deployment" phrasing) — rewrite
>    class: `agent_platform_hardening_2026-07.md` §1.5,
>    `permissions_sandbox_b6.md` P5-c/d parking,
>    `docs/DESIGN_LIMITATION_native_maf_mutation.md` ("tenancy not settled" was
>    false); **(e)** status headers added/corrected per the inventory (5 files
>    had none; 7 contradicted fact); **(f)** WS-25 re-measured (deploys green
>    2026-08-06/07 UTC, tip health-verify failure open); **(g)** MT specs updated:
>    §8 pricing inputs (D18), D17 Mem0 decision, H1 scratch-verify + PR #404,
>    MT-1a anchor corrections, §5.1 cutover trigger ADOPTED. Residuals that
>    remain open: §5 items 2–3 above; `multi_user_organization_research.md`
>    §17.3 got its rejection banner but the doc stays research-only;
>    `reference.md`/`system_architecture.md` carry stale-warning banners, not
>    re-verification (re-measure before relying).

**Tier 1 — status truth (hours; AGENT-SAFE; do before any dispatch):**
1. `whatsapp_message_manager.md` — header "PLANNING, no code yet" → point at
   §11 (W0–W14 built, 227 tests); reconcile §10 vs §11 phasing.
2. `task_manager_app.md` — header resume-point + §9.1/§8 status sweep against
   the repo (`/tasks/sync` ✅, EngageView exists, AssistantRail ✅).
3. `docs/multiplayer/README.md` — stamp the 12 stale claims (§2.2 of the
   audit): room compartments shipped, authorship shipped (139), participant
   table/roles vocabulary (`chat_session_participant`, owner/member/viewer),
   real endpoint surface (`routes/rooms.py`), floor default `'open'`.
4. `FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-1 — rewrite body per shipped state
   (broker live, handlers live for ClickUp/WhatsApp; remaining: email/Zoho
   handlers + enforce flip); reconcile the prod-SHA note; fix BO-2/BO-19
   internal contradictions; note BO-4 = BO-20.
5. Migration-number sweep (R1): groups §6 (134→138, 135→139, 133→137),
   org_access §10.1 (128/129→130), memory_architecture §9 (120→136),
   workflows §8 (131→132), plus "next free" phrasing in agent-kinds,
   agent_architecture, memory-clearance, multiplayer README.
6. Path sweep: `apps/gateway|orchestrator|ingestion/` → `apps/services/...`
   in the three foundation docs, `llm_caching_memory.md`,
   `mcp_plugin_integration.md`, `drawio_integration.md`.
7. `memory_architecture.md` §5 — mark §5.1 fixed (2026-07-30), §5.3 superseded
   by migs 136/137; §10 Q5 answered (clearance-keyed session cache, built).
8. `org_access_control.md` §10.2 — mark collisions 2/3/4 resolved (138/139 +
   intersection shipped); §8 Phase 2 row → "in progress as Centers B/C".
9. `mcp_plugin_integration.md` — Phase A marked shipped (D7), header updated.
10. `project_plan.md` — C-08/C-09 annotations (superseded by BO-12/ADR-028),
    M2.8 vs BO-21 honesty fix, HH-1/2/3 marked shipped, pointer to this doc.

**Tier 2 — make dispatchable (per-spec, before that WS dispatches):**
11. Calendar pair — add acceptance criteria + verification (contract items
    3/5); cross-map P0–P5 ↔ F0–F3; fix "PR not merged" (merged as #71);
    reconcile Pomodoro (shipped in F1 vs deferred in P5); one
    `gtd_time_blocks` column set (three variants exist).
12. `chat_ux.md` — fold live backlog (§11/§12 items VII–XI) into a short
    addendum; mark §12.3 superseded by generative_ui_2 §2; archive the rest.
13. `note_taker_app.md` — convert the §3.13 blockquote to a status table;
    reconcile §3.4/D4/D5 with the D3 AssemblyAI decision + deferred Tier-B.
14. ~~`agent_architecture.md` — one status for approve_all (§3.2 vs §11.3 vs
    §12 A0); Phases F/G dependency split (3a partly shipped).~~ **CLOSED
    2026-08-03 — both halves done:** A0 now carries one status (done
    2026-07-26, remaining scope named as the runtime/entrypoint check), and the
    §12 phase table splits F/G onto the still-open half of multiplayer 3a.
15. `email_app_master_plan.md` — refresh §3 at-a-glance to include §3.14;
    archive `email_feature_review_2026-07.md` per its own §9 instruction.
16. `department_centers.md` — corrections shipped alongside this doc: Phase C
    now names the `dynamic_agents` sharing-columns gap (D3), Phase E cites
    D1/D2, and §4 Q1 carries the full 12-site Pomad inventory.
17. Section-anchor fixes (audit §4.2): groups→memory-clearance §3.3/§3.1,
    groups→README §4.2/§7.1, memory_architecture→agent_architecture §7, etc.

**Tier 3 — archive/annotate:** multi_agent_orchestration Ph2–3 superseded
banner (D6) · "Agent Creator"→"Agent Workshop" sweep (R3, 5 sites) ·
`llm_caching_memory.md` proxy-hook sections struck per its own header ·
drawio §12's stray Hostinger-token action item moved to WS-2's list.

> 📋 **Handing this to another agent?** Start at [`HANDOVER.md`](HANDOVER.md) — branch state,
> the two migrations that are on no real database yet, the verification protocol, the ticket
> queue in dependency order, and a list of every trap that cost real time.

## 6. Owner-gate registry (agents must refuse these)

> **WS-29 / MT-0c-2 — un-parking the WS-3 T2 container tier.** Still OWNER-GATE, and
> **still parked** — narrowed by **D16** (2026-08-08). `saas_multitenancy.md` §0.9.3's
> *conditions* (no raw-SQL tool; no agent-reachable `app.tenant_id` write) are satisfied
> without it: **MT-0c-1 shipped the first**, and the second cannot be violated before
> `app.tenant_id` exists (MT-1b). What remains is the container/microVM boundary, which
> D10 parked on the "trusted colleagues" threat model — a model that survives the silo
> phase and dies at the pooled cutover. **An agent must refuse to build T2 and say so**;
> it is a precondition of the §5.1 cutover, not of Phase 0.
>
> **WS-29 — moving any customer onto the pooled tier.** Cutover is a data move against
> live customer data. AGENT-SAFE to build; **OWNER-GATE to execute.**
>
> **WS-29 / MT-1j — provisioning a REAL second organization.** *(Registered 2026-08-19
> with the mint of `saas_multitenancy.md` §11 MT-1j; the decision is **D43-3**.)*
> Building MT-1j and R8-testing it against scratch databases is **AGENT-SAFE and
> unblocked**. **Running it against a real second organization is OWNER-GATE**, and it is
> not a new gate class — it **composes three existing ones**: (a) the Customer Console
> service is on no box and where it runs was decided 2026-08-19 — D47, the production VPS (§8 gate 2, below as
> **(a)**); (c) provisioning writes an organization's membership, seats and subscription,
> which is the *"editing any live organization's entitlements, seats or credit balance"*
> class (§8 gate 4, below as **(c)**); and **H3's promotion of
> `infra/postgres/generated/04_policies.sql` is a human act in a window**
> (`saas_multitenancy_handover.md` H3 — 135 tables, an `ACCESS EXCLUSIVE` phase, and a
> lock-timeout design written after a 14h44m outage). **Before H3, a second organization
> exists with no database-level isolation** — measured 2026-08-19, `BASELINE_UNSCOPED`
> names 114 tables with no `organization_id` at all. An agent asked to "provision Fracktal
> as customer zero" (D36) must **build the path, test it two-org on scratch, and stop.**
>
> **WS-29 / MT-1f — turning per-tenant subdomains ON. ⚠️ WITHDRAWN (D51,
> 2026-08-24) — none of these acts will ever be performed.** The owner dropped
> subdomain workspaces entirely the day after the slice landed; the code half was
> removed with the decision. The five acts that were registered here (wildcard DNS
> record · DNS-01 wildcard cert + Caddy block · `AUTH_URL` pin ·
> `SUBDOMAIN_WORKSPACE_ENABLED` · `WORKSPACE_BASE_DOMAIN`) are struck — kept in
> this one paragraph so a future subdomain revival knows the runtime checklist and
> the CT-log/on-demand-TLS rejection that came with it (B1). The multi-org
> workspace CHOICE moved to a session claim: `saas_multitenancy.md` §11 MT-1k.


> **WS-30 / WS-31 — inviting a REAL member into a live organization, and flipping
> `MEMBER_INVITE_EMAIL_ENABLED`.** *(Registered 2026-08-24 with the mint of
> `customer_console.md` CP-2f + `subscription_console.md` SC-2c; the decision is **D49**.)*
> Building the invite path and R8-testing it against scratch databases is **AGENT-SAFE
> and unblocked**. Three acts on the live box are **OWNER-GATE and must be refused by
> name**:
> **(a)** granting the REAL deployment key the new `member_admin` capability — the
> capability-growth class of `customer_console.md` §8 gate 8, alongside `provision` and
> `seat_admin`; there is deliberately no HTTP route that edits a key's set;
> **(b)** running an invite against a live organization — it **writes that
> organization's membership**, which is §8 gate 4's *"editing any live organization's
> entitlements, seats or credit balance"* class, and it is now a write on **two planes**
> (tenant `app_user` + Console `org_membership`) rather than one;
> **(d)** setting `MEMBER_INVITE_EMAIL_ENABLED=true` (with a real `RESEND_API_KEY`) on a
> live deployment — the first flip makes this product send mail to third parties from
> `no-reply@metorite.com` on an admin's action, with **no rate limit in slice 1** (a
> named accepted risk in SC-2c, not an oversight). *(The label skips (c) on purpose: (c)
> is the MT-1j entry above, whose gate 4 clause this one composes rather than restates.)*
> An agent asked to "invite the team" must **build the path, test it against scratch
> Postgres, and stop.**

> **Two identity-boundary items, measured on the running deployment 2026-08-05 —
> both OWNER-GATE, and together they are what makes every other access control
> in this plan trustworthy or not.**
>
> 1. **`GATEWAY_INTERNAL_TOKEN` is byte-identical to `LITELLM_MASTER_KEY`** on the
>    box (same length, same sha256). It is *set*, so a "is it configured" check
>    reads green — it was set to the same value. The service identity is therefore
>    the key every agent's BYOK client holds. **Rotate it by redeploying**, never by
>    hand into `.env` alone: `deploy.yml` reconciles `.env.local` from `.env`, and
>    setting only the first locks out every signed-in member (see
>    `colleague_onboarding.md` §1.1's lockout warning).
>    ~~⚠️ Blocked 2026-08-05: the prescribed rotation *is* a redeploy, and the
>    delivery path is broken.~~ **UNBLOCKED 2026-08-09:** delivery recovered —
>    deploys landing since 2026-08-06, six green runs on 2026-08-07 UTC (#400
>    log-verified on the box; see WS-25). The rotation is executable again via a
>    redeploy; the
>    both-files reconcile warning above still binds, and the tip run's
>    health-verify failure (WS-25) is worth understanding before choosing the
>    deploy window.
> 2. ~~**Gateway `:8080` and workbench `:3001` are open to the internet**~~
>    **CLOSED 2026-08-05.** Both UFW rules removed (v4 and v6); verified from
>    outside the box that each now refuses while `https://api.…/health` still
>    answers 200 and the UI 307s to `/signin`. There is **no Hostinger cloud
>    firewall** on this VPS (`firewall_group_id: null`, firewall list empty), so
>    UFW is the only barrier and the only place this can regress.
>    Still owed: Caddy's `header_up -X-User-Email` / `-X-User-Role` strip
>    (`deploy/hostinger/caddy/Caddyfile`) — now defence-in-depth rather than the
>    load-bearing control, since the bypass path it guards is closed.
>
> Item 1 still blocks *trusting* app development: an owner predicate applied to a
> forged identity is not a control. Delivery works again (2026-08-09), so the
> only thing between here and the rotation is the owner choosing a window.

Force-push / history rewrite (BO-8) · credential rotation (Zoho, Hostinger
token) · enforcement flips (`ACTION_BROKER_ENFORCE` — ⚠️ **still UNSAFE as of
2026-08-11 and blocked on BO-1d**, not merely owner-gated: three `/tasks`
endpoints hard-500 and one write is silently swallowed because their callers
never read `_broker_gate`'s pending marker. BO-1a + BO-1b landed and did not
close this; see WS-1 and checklist §BO-1d · `AGENT_PERMISSION_MODE=
enforce`, `MEM0_ENABLED`, `GRAPHITI_ENABLED`, `WHATSAPP_ENRICHMENT`,
`SKILLS_FAIL_CLOSED` — the WS-23 fail-closed default-profile flip; review
`skills_scope_out.md` §3 dynamic-agent rows first · `SKILLS_INDEX_ONLY` —
the WS-23-successor skills-index flip: every agent's prompt becomes a
one-line-per-family index with bodies read on demand; see
`skills_scope_out.md` §7 · `INGESTION_CONSUMER` — the WS-4/BO-20
ingestion-consumer flip (the loop **shipped OFF** in BO-20a, 2026-08-02):
turning it on is not just "start a loop" — it cuts all three provider receivers
over from inline `emit_event` to enqueue-only so the consumer becomes the
**only caller of `emit_event`** (not "the single dispatch path" — that wording
was loose: `routes/agent.py:3476-3478` calls `dispatch_event` directly and is
untouched), which means Redis down = provider events dropped rather than
dispatched inline, logged as `<source>.queue.dropped`; see
`FOUNDATION_BUILDOUT_CHECKLIST.md` §BO-20.0 (answered: Option A) and its Q1) ·
**WS-6 observability activation** (Langfuse keys + bringing up
`--profile obs` in prod, `OTEL_EXPORTER_OTLP_ENDPOINT` in the deploy env,
`LLM_USAGE_AUDIT=1`, and re-enabling MAF telemetry by setting
`ENABLE_INSTRUMENTATION` — the kill switch is the env read at
**`executor.py:138`**, inside `_disable_agent_telemetry_once` (block
`:113-140`; the long-standing `:114` citation pointed into the comment banner
above it — corrected and re-verified 2026-08-03). It hides a known
ContextVar-reset bug that turns a successful streamed run into a `RUN_ERROR`) ·
**`copilot_sandbox_scope`** (`packages/acb_common/acb_common/settings.py:222`,
ships `""` = fully off, in-process everywhere). Putting `code_task` or
`app_builder` in it routes **real Copilot sessions into containers** — a live
execution-path change, not a config tweak. It was registered nowhere until
2026-08-03 ·
**`ISOLATION_TIER_ENFORCE`** — the new switch WS-3a introduces
(`permissions_sandbox_b6.md` §P5-a.2). Today every unscoped agent derives T2,
so flipping it **refuses most real runs**; it ships OFF and the refusal must be
behind it ·
**`CUSTOMER_CONSOLE_SPEND_GATE`** — WS-31 CP-6's two refusals (the 402 balance
gate and the 403 per-run breaker on the Customer Console's
`/v1/chat/completions`; `customer_console/main.py`, `_spend_gate_enabled`).
Ships OFF (registered 2026-08-18, same day it was built — the
`copilot_sandbox_scope` lesson). Flipping it is a **billing-behaviour change**
and the ORDER matters: with the shipped all-zero rate card it delivers every
cost of enforcement and none of the benefit — a zero-balance org (the state
provisioning leaves every org in) is refused on all AI calls while a funded
org can never reach 402, because nothing bills. Price the card first (itself
owner-gated commercial work, D19.2 — and `test_the_rate_card_ships_unpriced`
refuses a priced LADDER until BYOK zero-rating and the gate's pre-flight cost
invariant are closed; price live, never via migration), then flip ·
**the seven WS-31 Customer Console gates** (`specs/customer_console.md` §8),
registered 2026-08-18 because until then they lived **only in the spec** and a
gate the board cannot see is a gate a dispatched agent never reads. The
`GATEWAY_INTERNAL_TOKEN`/`LITELLM_MASTER_KEY` split (§8 gate 1) is already above;
the other six are **(a)–(f)**, and **(g)** is D42's neighbouring revenue gate,
registered here with them because an agent reading this list will meet it in the
same PR:
**(a) deploying the Customer Console service anywhere, and any VPS reach**
(§8 gate 2) — the service is built and on no box; where it runs was DECIDED 2026-08-19
(D47: the production VPS — own unit + env file, webhook-only public surface,
deploy gains the Console-ladder apply step); the DEPLOY act itself stays this
gate (`specs/customer_console_infrastructure.md` §5 item 4) ·
**(b) live Razorpay credentials and any real payment configuration** (§8 gate 3,
**CP-9**/D19.5) — ⚠️ *re-pointed 2026-08-18: this read "CP-8/D19.5", and CP-8 is
the **Operator Console and reconciliation**. The `payment_provider` seam had no
ticket body anywhere in the corpus and no code (one repo-wide hit,
`org_subscription.provider CHECK (…'razorpay'…)`); it is now
`specs/customer_console.md` **CP-9**, minted with this correction.* **Extended
the same day: creating the Razorpay ACCOUNT is owner-side even in TEST mode**,
and so is writing test-mode keys into CI or any deployment's env — any external
commercial account is an owner act (`specs/customer_console_infrastructure.md`
§5 item 5, §7). The consequence binds a dispatched agent: the whole seam is
built against a **fake provider with a real HMAC signer**, and WS-30 SC-4g
clause 2's test-mode capture rehearsal is **scripted and handed over, never
reported as run** ·
**(c) editing any live organization's entitlements, seats or credit balance**
(§8 gate 4) — concretely `POST /credits/grant`
(`customer_console/main.py:1104`, `grant_credits`), `POST /billing/seats`
(`:1049`, `assign_seat`), `POST /billing/seats/release` (`:1089`,
`release_seat`) and `POST /orgs/lifecycle` (`:637`, `set_lifecycle`) against a
real org — *(all four re-measured **2026-08-19** — :963/:908/:948/:500 →
:1104/:1049/:1089/:637 — after CP-9's six routes landed above and below them.
The 2026-08-18 set had itself corrected :690/:635/:675/:472, which predated
CP-2a/CP-2b and pointed into unrelated code. **Handler names travel beside the
numbers**, which is what made this drift a two-minute re-measure rather than a
hunt.)* Same gate class as WS-24 (d)'s member/role writes and as the
Subscription Console's fulfilment ·
**(d) flipping the Router ON for a real customer, and the §5.1 pooled cutover**
(§8 gate 5) — ⚠️ **the flag named in CP-4's done-when does not exist in code**
(verified 2026-08-18, repo-wide); CP-4 ships dark by having no caller, and this
gate binds the first caller ticket, which is where the flag arrives. The pooled
cutover half also carries the WS-3 T2 precondition (D16, top of this section) ·
**(e) issuing a production `cc_live_` key to a real organization** (§8 gate 6) —
`POST /keys` (`main.py:727`). Minting against fixtures is agent-safe ·
**(f) issuing a `cc_depl_` deployment key and setting it in a live deployment's
env** (§8 gate 7 — **new 2026-08-18 with CP-2b**, the sign-in↔registry wiring):
a credential issuance *and* a deployment env write, i.e. two existing gate
classes at once. The deployment key is the credential that lets a box ask the
registry about people, so a leaked or over-scoped one is a cross-deployment
question-asking capability; its capability set is deliberately `{resolve}` alone.
**Extended 2026-08-18 (F1 repair): setting `CUSTOMER_CONSOLE_RESOLVE_ENABLED=true`
on a live deployment** is the same gate — it is the Next-side switch that arms the
sign-in resolve hop (`workbench/control_plane/src/auth.ts`; ships unset = OFF,
byte-identical to pre-CP-2b sign-in). Flipping it is a deliberate availability
trade: a wired box **fails CLOSED** — every Console/gateway outage or
half-provisioned env becomes a sign-in refusal (`ConsoleUnavailable`) by design
(§6(g); the gateway route itself refuses when `is_wired()` is false, so a
half-provisioned box cannot silently admit). Flip only after BOTH sides are
provisioned: the gateway's `CUSTOMER_CONSOLE_URL` + `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`,
and the Console reachable from the box ·
**(g) issuing a discount code against a live org** (D42.3, contract
`specs/subscription_console.md` SC-4g) — the SC-4e adjustment gate class:
authoring codes and the ₹0 path against fixtures is agent-safe, redeeming one
against a real organization is a revenue act ·
**(h) setting `SELF_SERVE_SIGNUP_ENABLED=true` on a live deployment, and
expanding a real deployment key's capability set beyond `{resolve}`** (§8 gate
8 — **new 2026-08-19 with CP-2c**, D46): the flag opens organization creation
to the public internet; the capability growth (`{resolve, provision}`) is
(f)'s issuance class applied to a wider credential and is CP-2c's proposed
answer to D43-2's open sub-question. Building the flow against fixtures,
minting wide keys in tests and fencing both flag positions is agent-safe ·
**WS-12 Phase 4.0's target choice** (minimal-bump vs full-bump,
`multi_agent_orchestration.md` §6 Phase 4.0) — a cost/schedule call, and the
reason WS-12 has **zero** dispatchable PRs. An agent may produce the 4.1
evidence and must then stop and report ·
**WS-12 Phase 4.6's manual soak** of the Copilot streaming path — an agent
cannot simulate or self-certify it, and must not mark 4.6 done without a
recorded human sign-off ·
**Calendar external sync (WS-21)** — needs Google Calendar and/or Microsoft
Graph **OAuth client credentials (client id + secret + redirect URI)
provisioned on the VPS** and registered in the Integration Registry;
`calendar_timeboxing.md` §13 P4 clause 1 ("a `calendar_accounts` row created
through a real OAuth connect flow") is unverifiable without them ·
**outbound nudge sending — one shared gate for two rows:** WS-21 §9.4's
Waiting-on chase block and WS-18's follow-up nudges both end in a real
outbound message from a real account. Drafting and queueing are AGENT-SAFE;
**sending is not**, and neither row may flip it independently ·
creating the bot Google account + real-meeting joins · Meta app review ·
real-account email sends / live-DB one-offs (`merge_ghost_messages --apply`) ·
~~**the WS-10 floor-control re-decision**~~ — **DECIDED 2026-08-10 (D25.4): CUT.**
The five `chat_session.floor_mode`s, turn queue, observer lane,
handoff-with-a-note and HITL floor-holder routing are retired unbuilt; steer
suffices. This gate is closed — an agent asked to "finish multiplayer Phase 2"
builds the S1 `subject:` slice and treats the floor-mode design as
historical ·
~~**the per-Center approvals-routing decision (WS-14 C4)**~~ — **DECIDED
2026-08-10 (D25.3): Center leads approve their Center's actions** (approver role
on the `org_group`; proposals carry a requesting-Center column; fallback to org
admins when unset). Acceptance may now be written; the ticket is "add the
column, written by every proposer" per the measurement below. Registered 2026-08-03 because it read as a UI
filter and was not one — `pending_actions` (`infra/postgres/66_pending_actions.sql:13-38`)
carries no requesting-member, group or Center **column**, so the follow-on is "answer Q2,
then add a column at the next free migration number". *(Corrected 2026-08-03: the
companion claim that `actor` never names the human is **false** — `routes/apps/tools.py:393`
and `routes/apps/actions.py:345` write `app:<slug>:<email>`. The gate stands on the real
measurement: five ad-hoc `actor` shapes, a human in two of six proposers. See
`department_centers.md` C4.)* ·
**the WS-10 `prefs`/`user` backfill APPLY** — running the classifier's output
against live Mem0 personal memories (`docs/multiplayer/memory-clearance.md` §8 Q1:
*"it should be a deliberate, communicated choice"*). The classifier itself and a
**dry-run report** are AGENT-SAFE and are the whole of the agent's mandate; the
mutating pass is a live-DB one-off ·
`test_owner_bootstrap.py` against prod (never) · any deploy that changes auth
behaviour (supervised window per `FOUNDATION_CONTINUATION.md`) ·
**the four WS-24 colleague-onboarding gates** (`specs/colleague_onboarding.md`
§1.1), registered 2026-08-04 because "invite a colleague" reads like a UI
action and is not one:
**(a) installing the Caddy identity-header strip** — writing
`deploy/hostinger/caddy/Caddyfile` is AGENT-SAFE, `sudo install` +
`systemctl reload caddy` on the box is not; it changes auth behaviour, and the
pipeline only reinstalls the repo copy when the live one **fails**
`caddy validate` (`.github/workflows/deploy.yml:496-501`), so the two can drift
silently and an agent must not assume a merged repo file is live ·
**(b) provisioning `GATEWAY_INTERNAL_TOKEN`** — a credential, and it must land
in **both** `/opt/acb/app/.env` and the workbench's `.env.local`, because the
Next BFF mirrors the same `LITELLM_MASTER_KEY` fallback
(`workbench/control_plane/src/lib/gateway.ts:58-61`); a mismatch turns every
proxied browser call anonymous. **`GATEWAY_REFUSE_LLM_KEY_IDENTITY` is a
separate owner gate of its own** — it ships OFF, defaults to today's behaviour
exactly, and flipping it while the token is unset **401s every signed-in
member** ·
**(c) installing/scheduling the BO-23 backup timer** — building the dump
script, the manifest and the restore runbook is AGENT-SAFE; installing the
systemd unit and pointing it at prod data is not ·
**(d) any write to the member/role/group tables on the live box** —
`POST /admin/members`, `PUT /admin/members/{email}/roles`,
`PATCH`/`DELETE /admin/members/{email}`, `PUT /admin/members/{email}/overrides`,
`POST`/`DELETE /admin/groups/{slug}/members`. Inviting a real person, changing
what they can see, or removing them is the owner's act. An agent may write the
runbook, the preflight and the matrix, and must stop there ·
**running `scripts/onboarding_preflight.py` against production** — the script
is agent-safe to author and its DB checks read the live database, so an agent's
only mode is `--mode local`, which refuses the box-only checks by design ·
**the four WS-26 CRM gates** (`specs/crm_app.md`), registered 2026-08-05 (d added 2026-08-07):
**(a) the Zoho two-way sync against production** — **BUILT 2026-08-05 · BACKFILL RUN
2026-08-06 · SYNC LOOP ENABLED BY THE OWNER 2026-08-06.** (The old "never run"
reading is retired; the gate is NOT — it now governs changes to a *running* loop
rather than a first switch-on.) Measured at enablement: the loop cycles every 600s;
the first cycle pushed **nothing** (no row was dirty), pulled
737/1,189/1,516/551/1,909, and left **zero** rows dirty — echo suppression held. One
defect surfaced in the first cycles and is fixed in main (PR #375): the `Deals`
watermark could never advance, because this tenant returns no `Modified_Time` for any
module and no `Created_Time` for Deals, so that module re-pulled all 551 records every
cycle. Deal conflict resolution is consequently one-sided (native-wins) by design —
spec §7.1. Building the engine was AGENT-SAFE; **turning the sync flag on, the first
backfill run, and any hand-run sync cycle were the owner's acts and remain so**: the
engine **WRITES the live Zoho tenant**
(re-scoped 2026-08-05 per spec D-CRM-7, owner-directed), pushes native edits
up, and propagates deletes in both directions. The **code floor is
`admin:access:manage`**, not `integrations:use:zoho-crm` — audit finding
2026-08-05: migration 131 grants `member` `integrations:use:*`, so the
integration slug gates nothing ·
**(b) flipping `CRM_AUTO_LEAD`** — ships OFF; ON turns unknown inbound email
senders into CRM lead rows — **each born `zoho_dirty = true`, i.e. queued for
push into the live Zoho tenant on the next sync cycle (which
`POST /crm/sync/zoho` runs with or without `CRM_ZOHO_SYNC`)**. Ruled D-CRM-9
(owner, 2026-08-06): this is intended behaviour — agent- and auto-originated
writes enter the push queue exactly like human ones. So the flip is both a live
change to email-app behaviour and, transitively, a write path into Zoho.
**The settings field now EXISTS — `crm_auto_lead: bool = False` in
`packages/acb_common/acb_common/settings.py`, beside `crm_zoho_sync`** (WS-26d-autolead,
BUILT 2026-08-08, branch `ws-26d-autolead`; the ⚠️ "does not exist yet" note is
retired). **Built, NOT flipped, NOT deployed**, and while it is off the branch
changes no runtime behaviour at all. The hook is
`routes/email/scheduler_hooks.py::process_new_mail` — the one seam the scheduler,
the manual-sync route and the webhook all funnel through — and the flag is read
at that CALL SITE, before the CRM step is entered, so the OFF state issues no CRM
query; both the runtime regression and an AST assertion that the gate is
lexically outside the step live in `tests/unit/test_crm_auto_lead.py`. ⚠️ Three
things an owner should know before flipping: **(1)** the first ON-state run per
mailbox only ACTIVATES the cursor (`crm_auto_lead_cursors`, migration 163 — renumbered at merge; 157 is
held by open PR #399) and mints nothing — mail that arrived before that instant is
history by construction, which is what stops a deep resync minting a year of leads;
**(2)** the same is true after the flag is turned OFF and back ON, or after the
service is down, for more than an hour: the cursor RE-ANCHORS, the gap's backlog
mints nothing, and the cycle says so at WARNING on `sync.auto_lead_reanchored`.
That guard was added by diff review after an OFF→ON round trip was measured minting
**27 leads for a 27-day OFF window**, each pushing unattended into the live tenant —
so turning the flag off is genuinely a stop, not a pause that accumulates. ⚠️ **With
one bounded exception the owner should expect: mail received in the FINAL HOUR before
the flag goes back on IS minted** — one gap-width of mail (an hour), drained across
however many cycles the per-cycle cap takes, not a single batch. That is deliberate —
the re-anchor clamps the epoch one hour back rather than resetting it to now, because
this step only runs when a sync persisted mail, so the cycle that detects the gap is
always the cycle carrying the message that woke it; resetting would drop that message
every single night. If the OFF window's tail matters, flip the flag on at a quiet
moment; **(3)**
the accepted residual is that two concurrent syncs of one account can double-mint one
visible, hand-deletable duplicate (a UNIQUE index on `crm_leads.email` is refused:
1,516 imported rows may already carry duplicates, the migration-148 shape). One
operational note: a cycle that logs `sync.auto_lead_stalled` at WARNING every cycle
means a message at the head of the queue cannot be written and the cursor is
deliberately held — that is fail-closed toward the CRM and it needs a human, not a
restart ·
**(c) the WS-26e cutover + retirement** — the final import + parity check,
repointing the graph-mirror consumers (`sales_views.py`, `reconciler.py`),
retiring `ingestion/sources/zoho/` + cron + webhook + config (spec §7.4, which
includes an `.env.example` edit that plan-guard already blocks), and **revoking
the Zoho refresh token** — the act that executes part of WS-2's standing P0 ·
**(d) applying the WS-26f stage-metadata repair against prod**
(`POST /crm/import/zoho/stages?apply=true`) — it rewrites the live pipeline's lane
order, stage types and probabilities in one call, and the board every `feature:crm`
holder sees reorders under them; the dry-run (no `apply`) is agent-safe and is how the
proposal reaches the owner. If the tenant returns more than one pipeline the repair
must STOP unapplied (spec D-CRM-11). Re-minting the Zoho token with `settings.*` scopes,
should the probe report no-scope, is likewise the owner's act ·
**the five WS-27 Projects gates** (`specs/project_management_app.md`), (a)–(d)
registered 2026-08-05, (e) added 2026-08-08. ⚠️ **RE-CUT 2026-08-24 by D52 — read
this paragraph before the lettered ones.** (a) and (b) are **DEAD**: D52 deletes both
import endpoints and the whole outbound-push arm, and a gate over code that does not
exist teaches an agent to refuse the wrong thing. (c) **narrows** to the two acts that
are still the owner's — revoking the ClickUp tokens, and dropping ClickUp-sourced
columns on a live database. (d) is untouched. Their bodies are **kept struck rather
than deleted**, because the reasoning in (a) — *"the import is not wrong, it is
early"* — is the reusable part and the next connector will re-raise it verbatim ·
🆕 **(f) the D53 `gtd_*` → `pm_*` data move (WS-39 S3b) and the `gtd_*` drop (S3c)**
*(registered 2026-08-24 with D53.5)* — the same class as WS-29's pooled cutover: a
migration that **moves live customer task rows** between stores, and a later one that
**drops the source tables**, on a ladder that **cannot roll back** (R6). Building both
and R8-testing them against scratch databases is **AGENT-SAFE and unblocked**; running
either against a real database is the owner's act. ⚠️ The failure mode is specific and
worth naming: S3b writes into each member's **personal project**, so a mis-mapped
`member_email` does not lose a task — it publishes one person's private task into
somebody else's lens. Prove the mapping two-org on scratch before it is offered ·
~~**(a) running either ClickUp import endpoint against the production workspace**~~
**— DEAD 2026-08-24 (D52.2): both endpoints are deleted.** Struck body kept for its
reasoning:
~~⚠️ **ALSO BLOCKED ON WS-29a AS OF 2026-08-08**~~ — **LIFTED the same day:
migration 158 keyed all seventeen `pm_*` tables, which was the reason to
wait.** ⚠️ Two conditions replace it: migration 158 **must be applied to the
target database first** (it is on no real box yet — the deploy path is
broken, WS-25), and the mapping decision below still stands. Kept struck
because the reasoning is the reusable part: Metorite is becoming
multi-tenant and all seventeen `pm_*` tables carry no `organization_id`
(`specs/multi_tenancy.md` §2). Importing a real workspace now writes hundreds
of tasks, activities, attachments and grants into unscoped tables, which turns
a one-line default on empty tables into a backfill plus an `ALTER` on live
rows. The import is not wrong, it is **early**: land WS-29a first. —
building both is AGENT-SAFE; executing them is not. `POST /projects/import/clickup/plan`
writes nothing to our DB but **reads the live ClickUp tenant** and spends LLM
budget classifying it; `POST /projects/import/clickup` writes the live DB, and
during coexistence a re-import is last-import-wins on ClickUp-sourced fields
(spec §7.1). **Confirming the Space→Center mapping is itself the owner's act
(D-PM-10):** an agent may propose the mapping and must not apply one, because a
wrong map grants a Center visibility of another department's work. Code floor is
`admin:access:manage`, per the WS-26b finding that `integrations:use:*` gates
nothing ·
~~**(b) enabling the WS-27c outbound push**~~ **— DEAD 2026-08-24 (D52.1): WS-27c is
cancelled and the push arm is deleted.** ⚠️ Note what does NOT die with it:
`ACTION_BROKER_ENFORCE` is still blocked on **BO-1d** and stays in the flag list above
— BO-1d is about four callers mis-reading `_broker_gate`'s pending marker, which is a
broker defect that outlives ClickUp. Struck body: against the real workspace — the sync's
ClickUp writes flow through `_broker_gate`. BO-1a + BO-1b landed 2026-08-11 (approving
a delete no longer marks the row `failed`; an ignored pending marker no longer shows a
green "synced" task that exists in no workspace), so WS-27c is buildable — but
**`ACTION_BROKER_ENFORCE` is now blocked on BO-1d**, the four callers that still index
the gate's pending marker as a result (three hard-500, one silently swallowed) ·
**(c) the WS-27g cutover + retirement — NARROWED 2026-08-24 (D52.1) to two acts.**
What is left of this gate, and all that an agent must refuse by name:
**(c-1) revoking the ClickUp tokens** wherever they are stored — the live
`integration_credentials` rows and any `CLICKUP_*` value in a deployment's env. A
credential act, the §6 `secrets`/`env-write` class. WS-39 S1 deletes every *reader*
of those tokens, which makes revocation safe to do at leisure and **does not do it** —
a token nobody reads is still a live token at ClickUp until somebody revokes it there.
**(c-2) dropping the ClickUp-sourced columns on a live database** — D52.3 keeps them
deliberately (R6: the deploy migrates before it restarts), so the drop is a later
release and the owner's call.
~~The rest — final import + parity sign-off, flipping the sync to pull-only then off,
repointing the graph-mirror consumers off the ClickUp arm, retiring
`ingestion/sources/clickup/` + the `ClickUpProvider` arm + `skill-clickup-sync` +
catalog/OAuth entries~~ — **all of it is now AGENT-SAFE deletion in WS-39 S1**: with
D52 there is no external system to sign parity off against, so what was a cutover is
just removing code. ~~and the root-`AGENTS.md` constraint-8 amendment (Metorite becomes
the PM system of record) — that amendment ships in the WS-27g PR, never before~~ —
**struck: D52.4 moves that amendment into the WS-39 S1 PR, because the PR it was
reserved for will never exist** ·
**(d) granting `feature:projects` or `data:org:read` to any real member** on the
live box — the same member/role-table write rule as WS-24 (d); the full-portfolio
view is deliberately `data:org:read`'s first consumer, so granting it now grants
visibility that previously granted nothing. ·
~~**(e) answering D-PM-12 — whether a `blocks` dependency CONSTRAINS the schedule
or only describes it**~~ **ANSWERED 2026-08-08 and the gate is CLEARED.** The owner
was given the three options and their costs and delegated the choice back
(*"go ahead with the decision that you think would be best"*); recorded as
**D-PM-12 = (c) constrain-and-warn**, so WS-27p's "derived and shown, never
enforced" stands unamended and no cascade of writes was introduced. Kept struck
rather than deleted because the shape of the gate is the reusable part: an agent
must still refuse to make `blocks` **push dates** — moving to option (b) is a new
owner decision, not an extension of this one, and it would strike WS-27p's
paragraph rather than sit beside it.
