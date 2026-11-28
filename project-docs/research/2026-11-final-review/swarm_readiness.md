# Swarm readiness review, Metorite (2026-11-10, read-only, origin/main)

BLUF: Metorite can run a SMALL, organized swarm now (3 to 4 lanes, each one app project, started by hand with box.sh). It cannot run a self-staffing swarm yet. Dispatch is the gap (no owners on slices, no queue route, no pull start). Isolation has two real holes (shared R8 scratch DB, migration number collisions). 25 owner questions sit in epic text where Needs attention cannot see them.

Counts below: 10 areas, 38 rows. READY 12, PARTLY 19, MISSING 7.

Evidence key: file paths are on origin/main. "task N" is a Metorite task number. Items I did not verify are marked (unverified).

---

## 1. Dispatch: can an agent find its next slice

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 1.1 | `my_work` queue order | PARTLY | `my_work` serves only runs, held-by-you tasks and tasks ASSIGNED to the member. Live call today returned 10 rows, all already "In progress", none from the new epics; only 4 of 10 had a `plan` field. SW-68a ranks inside one epic only, behind `MY_WORK_PLAN_ORDER_ORGS` (agent_coordination.md:1488). Cross-epic order is advisory until PJP-6c (task 2508). | Bulk `set_assignees` on the first-wave slices (new task). Longer: `list_agent_queue` (SW-68, task 1914). |
| 1.2 | Slices have owners | MISSING | Task 2546 section 7.1: 94 of 107 swarm slices have no owner, so `my_work` offers none. All five new epics (2675, 2713, 2732, 2790, 2758) and WB-0/HX-0/LL-0 show `assignees: []`. | Owner names (2546 section 9) or assign to the owner account for the first wave. New task. |
| 1.3 | `get_plan` | READY | Tool returns ready, waves, critical path, slack, next_slice per epic. Caveat: estimates are rule-based guesses (2546 section 7.4). | Size the first slice per stream, re-read. |
| 1.4 | Delegation briefs and "Start here" | PARTLY | Contract is playbook section 6b. I did not read the project descriptions (unverified). Fence is advisory until KIT-5 (`test_handoff_comments.py`, new). | Spot-check three briefs for the six lines (owner slot, entry, critical path, hand-ins, hand-outs, first command). |
| 1.5 | Lanes per app project | PARTLY | A lane is a text file: `scripts/home_server/lanes/lane-rules.md`, `~/lanes/keeper-lanes.txt` (home_box.md:167). SW-67 (lanes as records, task 1913) is not built. The keeper passes no `--model` and no `--permission-mode` (home_box.md section 3.7, task 2466 gap 7). | Start lanes by hand with `box.sh start` until SW-67. Fix keeper flags (task 2466). |
| 1.6 | Claim and hold rules | READY | One open run per task, 409 `task_held`, heartbeat 1/min, stale after 5 min (agent_coordination.md section 1; MCP instructions). Weakness: task 2518 is still held_by_you and stale since 2026-11-09 18:11. | Weekly sweep of stale holds (memory: idle-agent-holds). |
| 1.7 | Pull start | MISSING | SW-100 (task 2527) needs SW-78 release 2 (task 1924), SW-67, SW-68, SW-49, SW-77. Runner release 1 is task 1731, box token EA-19 (task 1819). Pull start is off by default even when built. | Human starts lanes (box.sh). No shortcut. |

## 2. The slice contract (work_plan section 1, seven points)

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 2.1 | Seven points met by new slices | PARTLY | WB-0, HX-0, LL-0, 2622, 2541, 597 all carry: Do, Done-when, risk tier, fence test or test name, Depends. | See 2.2 to 2.4. |
| 2.2 | Point 4, current file paths | PARTLY | WB-0, HX-0, LL-0 name target spec files but no code anchors. 597 to 599 cite spec line numbers (`email_app_master_plan.md:593,640,643,1576`) that go stale. | Footer line "Files:" per slice. |
| 2.3 | Point 5, exact verification commands | PARTLY | Slices name a fence test, not the pytest/tsc command. | Footer line "Verify:" with the command. |
| 2.4 | Point 7, gate label | MISSING | No slice I read says AGENT-SAFE or OWNER-GATE. They give a risk tier instead. Small slices skip the spec-auditor and quote the task as the contract (next-ticket.md step 2), so the gap goes straight to the implementer. | Footer line "Gate:" on every slice. One bulk `update_tasks`. |
| 2.5 | Owner questions inside spec slices | PARTLY | BK-0 must settle 4 spec conflicts with a new D-number. KS-Q1 needs a D-number (owner gate). WB Q3 needs a read of live Meta terms. | The spec records the recommendation as "proposed"; the owner answers later. |
| 2.6 | Stale text risk | PARTLY | LL-0 says "add section 6c" and KIT-0 says "write the kit contract". Both sections 6a and 6c already exist on main in `orchestration_playbook.md`. | Re-verify anchors at dispatch. LL-0 and KIT-0 may close as "already written". |

## 3. Isolation

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 3.1 | Worktrees | READY | `scripts/worktree.sh new/list/remove`; worktree-notice hook; rule in CLAUDE.md section 4. Cost: 3.4 GB disk each, own `npm install` (dev_capacity.md section 3). | None. |
| 3.2 | One DB per run for R8 | MISSING | `scripts/dev_db.sh` uses ONE pair of fixed containers (`metorite-scratch-console` :5433, `metorite-scratch-tenant` :5434), "reused, never recreated" (lines 28 to 31, 61). Ports are env-overridable but the container name is fixed. Memory: shared-scratch-db-parallel-r8, dev-db-test-pollution. `dev_up.sh` is per worktree (slot = cksum of path mod 50, ports 6400/6450/8400/3400 + slot, busy-port probe, lines 179 to 227). | New task: `dev_db.sh --slot` derives container name, ports and DB name from the worktree slot. Until then: R8 runs only through `ci_local.sh` (own containers, `slots.sh`), one R8 run at a time per box. |
| 3.3 | Migration number collisions (R1) | PARTLY | `premerge.sh` (lines 79 to 92) checks that a new migration is above main's highest, and only when the branch is behind main. Three collisions in two weeks (CLAUDE.md rule 7). With N agents each taking max+1 at build time, collisions are certain. | New task: the merge lane renumbers at merge time, or each lane reserves a number range in a task comment. |
| 3.4 | Port collisions of local stacks | PARTLY | Slot hashing with probing is good. Visual rig :3101 and browser suites still collide (memory: visual-rig-port-collision). Browser suites need `flock ~/lanes/e2e.lock` (home_box.md section 7); a closed local port HANGS on the box. | Lane rule: browser run only under the flock. Already in home_box. |
| 3.5 | Sub-agent scratch and cleanup | PARTLY | Rule exists (CLAUDE.md "clean up after yourself", stop-children hook, reaper). Sub-agents share the parent's machine memory (dev_capacity.md section 4.3). | Count 2 GB per test-running sub-agent in the lane budget. |

## 4. CI capacity for N parallel agents

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 4.1 | Home box and PC lanes | PARTLY | `slots.sh` sizes runs to memory: 5 GB fixed + 3 GB per shard; selected tests reserve 8 GB, a full suite reserves 20 GB and peaked at 17.7 GB; 6 GB kept free. Full suite takes about 41 minutes, one at a time (home_box.md section 6.1). On 2026-11-08 nine lanes plus eight runners on 24 GB gave swap and 36 minute shards (dev_capacity.md section 3). PC `metorite-ci` is the second lane. | Cap lanes by the formula in dev_capacity.md section 4.2. Do not exceed 4 lanes on the 24 GB box. |
| 4.2 | GitHub runner slots | PARTLY | GitHub Free gives 20 concurrent jobs per org; PR 2039 used 9 jobs and a run can reach about 17 (dev_capacity.md section 2). So 1 to 2 PRs at a time on Free. The org moved to Team on 2026-11-07 (task 1907) but the API read `free` on 2026-11-10 (unverified). | Run `gh api orgs/Hathi-Labs --jq .plan.name`; confirm Team (60 jobs). |
| 4.3 | Release slots | READY | Three a day, 05:30, 13:30, 21:30 UTC, started by one timer on the home box (home_box.md section 6.2; D194). | None. Keep exactly one timer. |
| 4.4 | Merge order and behind-main checks | PARTLY | One `lane-merge` lane merges serially from `~/lanes/merge-queue.tsv` (home_box.md section 3.8). `premerge.sh` merges main in and runs collision fences only; `--with-tests` adds affected tests (lines 135 to 163). Today two PRs needed a re-run because one merged first. | Rule in lane-merge.md: queue order = critical-path order; before each merge, merge main into the branch and wait for pr-check on that SHA only if files overlap. New task. |
| 4.5 | PRs per day | PARTLY (estimate) | Not measured by me. Hard caps: 3 to 4 branches in flight per integrator (engineering_practice.md section 5); one full local suite at a time; three releases a day. My estimate: 8 to 12 merged PRs a day, if the owner-preview wait is small. Low confidence. | Run the delivery-review skill (CIV-10) for real numbers before the first swarm. |
| 4.6 | R8 suites in CI | PARTLY | R8 suites run on `main` and in the release gate only (CLAUDE.md section 6, CIV-4b). A SQL bug can pass PR check and fail at release. | Agents run R8 locally (see 3.2) for any query or migration slice. |

## 5. Review and verification

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 5.1 | Verifier is never the implementer | READY | `.claude/agents/ws-verifier.md`; next-ticket.md step 1b; lane-rules rule 5. (Agent files not read in full.) | None. |
| 5.2 | diff-reviewer for HIGH, Opus | READY | next-ticket.md step 1c table; fence `test_agent_model_routing.py`. | None. |
| 5.3 | Harden pass | PARTLY | One `harden` PR per feature (engineering_practice.md section 7a). Six parallel features mean six harden PRs, each a whole-feature Opus review. In build the process fences and STE lint only warn. | Create the harden task per epic when the epic is created. |
| 5.4 | UI gate captures | READY, costly | Four captures and a `Visual review:` line block even in build (next-ticket step 1d). Browser runs are memory heavy (8.5 GB). | Serialize browser runs by flock. |
| 5.5 | Cap on PRs waiting for the owner | MISSING | SW-49 review cap (task 1882) not built. | Lane rule: a lane with 2 PRs waiting on owner preview stops pulling. |

## 6. Coordination across people and agents

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 6.1 | Hand-off comments (section 6b) | PARTLY | Contract exists; fence advisory until KIT-5. All five new epics show `links: []`. Known cross-epic pairs without links: BK-11 and WB (same booking API), CX-3c and BK-6 (org's own Razorpay), CX-3a and KS-13 (prices), KS-12 and Email, CX-4 and Calendar. | One agent pass writes `blocks` links and `Hand-off:` comments for these five pairs. |
| 6.2 | Shared spec files | PARTLY | `project-docs/INDEX.md` (172 lines) gets a row from every spec slice (WB-0, KS-0, CX-0, HX-0, ...). Fences: `test_index_completeness.py`, `test_index_union.py` (resolve by union). | See 6.4. |
| 6.3 | D-number collisions | PARTLY | Latest D is D204 (`work_plan.md`). `test_decision_ids.py` fails on duplicates after both merge (it exists because D113 was minted twice). Rule: first merge wins (memory). Pending D-numbers: 2546 section 9, BK-0, KS-Q1, probably WB/CX. | Reserve a block in one task comment (for example BK D206 to D207, KS D208, CX D209 to D210, WB D211). |
| 6.4 | `nav.ts`, shared migrations | PARTLY | `nav.test.ts` fences nav; migrations see 3.3. | Same as 3.3. |
| 6.5 | Single owner per spec | READY | work_plan section 1 point 6. | None. |

## 7. Owner bottlenecks

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 7.1 | Open owner questions | MISSING (invisible) | In epic descriptions: WB 3 (Q1 to Q3), BK 4 (BK-Q1 to Q4), KS 6 (KS-Q1 to Q6), CX 7 (CX-Q1 to Q7), CM 5 (CM-Q1 to Q5), CH 0. TOTAL 25. Each epic's rollup `waits.count` is 0, so none show in Needs attention. Comments on the epics not read, so 25 is a lower bound. Most carry "recommend yes". | One `ask_on_task` per epic with the numbered list and the recommendations, so the owner can answer "approve all recommendations" once. New task. |
| 7.2 | Other owner items | PARTLY | Task 2580 (renew grants), 2546 section 9 (names, WM-5 tag, D-number), KS-11 and CM-Q5 are owner gates, WB-16/BK-13 owner live checks. | Same ask route. |
| 7.3 | Approvals in Metorite only | PARTLY | Needs attention fix (2545) is live. The Approvals filter 2622 is Backlog (was 251 rows, 12 archive requests hidden). | Do 2622 first. |
| 7.4 | Grants expiry | PARTLY | All six `ALLOW-UNTIL` lines read 2026-11-30 (`.claude/OWNER_GRANTS.md` lines 79 to 84). 20 days left. Task 2580 is Backlog. Only the owner can write it. | Open the file for the owner: `code -g .claude/OWNER_GRANTS.md:79` in the checkout the PR uses, and give the new date line. |
| 7.5 | D145 previews | PARTLY | Every UI slice needs a preview and Approve; D200 skips the wait for small low-risk UI. | Swarm tasks tagged LOW or small UI. |

## 8. Observability of the swarm

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 8.1 | Heartbeats | READY | `post_agent_activity` each minute; stale detection. Zombie hold exists (task 2518). | Sweep. |
| 8.2 | Fleet panel | PARTLY (unverified) | 2546 section 8 says the agent view folds into the Fleet panel; SW-50/SW-31b not checked. | Check it shows runs from all connections before the swarm. |
| 8.3 | Needs attention | PARTLY | 2545 live; 2622 pending. About 120 `blocked` rows persist until blockers have owners (2546 section 7.5). | 2622. |
| 8.4 | Weekly review | PARTLY | `delivery-review` skill (CIV-10) covers CI. Learning loop is manual until LL-3 (playbook section 6c); epic 2624 planned. | A person runs it each Monday. |
| 8.5 | Box visibility | PARTLY | `keeper.log`, `merge-queue.log` on the box only. | Add a daily post of both logs to a task (new, small). |

## 9. Cost and limits

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 9.1 | Model routing | READY | `CLAUDE_CODE_SUBAGENT_MODEL=sonnet`; agent files name models; HIGH on Opus; fence `test_agent_model_routing.py`. Gap: keeper-started lanes pass no `--model` (1.5). | Fix with 1.5. |
| 9.2 | Write cap 250 per 600 s | PARTLY | Cap counts rows, per connection. A 1,000 row plan stalls. MCP-G28 (task 2541, HIGH, 480 min, needs 2427) adds a calls counter and a rows counter. | Do 2427 then 2541 early. |
| 9.3 | Account limits | PARTLY | Twelve lanes used about 4% of one Max 5x weekly limit in one hour (dev_capacity.md section 2, 2026-11-07). Declared accounts (task 2526) not built. `box.sh accounts` is manual. | Run `box.sh accounts` at the start of each work period. |
| 9.4 | Eval spend gate | PARTLY | Rule only: eval over USD 0.50 needs owner OK (memory: eval-cost-needs-owner-ok). I found no fence. | Name a fence or label it advisory (R7). |

## 10. The kit for outside agents (Codex, Copilot, Gemini)

| # | Item | Status | Evidence | Smallest fix |
|---|---|---|---|---|
| 10.1 | What works today without the kit | PARTLY | Repo has root `AGENTS.md` (270 lines) and `.github/copilot-instructions.md`, so Codex and Copilot can read project rules. They can use git and `gh`. | None. |
| 10.2 | What does NOT work | MISSING | (a) The MCP app door is dark (task 1723), so Knowledge, CRM and Calendar tools are unreachable. (b) The guards are Claude Code hooks (`plan-guard.mjs`, `ste-lint.mjs`): Codex, Copilot and Gemini bypass them, so nothing stops a push to `main`, a write to `OWNER_GRANTS.md` or an ssh, except git-side checks. Branch protection (WS-38a) is on the roadmap's critical path as open (work_plan section 2.0 step 7; status unverified). (c) The core protocol of 8 lines lives in the unbuilt kit (KIT-1, task 2549). | Do not run outside agents against the main repo until (b) is closed. |
| 10.3 | Minimum to unblock | MISSING | KIT-0 (2548) and KIT-1 (2549) core AGENTS.md of at most 150 lines; the one pack for the first client (Codex 2550 or Copilot 2551); KIT-3 secret guard (2553); MCP connect with OAuth or box token (MCP-G17, task 1819). | Server-side guard first: branch protection and CODEOWNERS on `.claude/OWNER_GRANTS.md`, `deploy/`, `.github/`. New task if WS-38a is not done. |

---

## TOP 10 THINGS TO DO BEFORE THE FIRST SWARM (ranked)

1. **Give the first-wave slices an owner so `my_work` serves them.** New task. Bulk `set_assignees`; then re-read `my_work` and `get_plan`.
2. **Ask the 25 owner questions in 6 `ask_on_task` batches** (with the recommendations) so they show in Needs attention. New task. Include the D-number block (6.3) and the 2546 section 9 items.
3. **Private R8 database per worktree or lane** (`dev_db.sh --slot`). New task. Until done: one R8 run at a time.
4. **Migration number rule for N lanes:** reserve ranges or renumber in lane-merge. New task (R1, `premerge.sh`).
5. **Merge-train rule in `lane-merge.md`:** order by critical path, merge main before checks, one re-run budget. New task. Run `delivery-review` (CIV-10) first to get real PRs-per-day.
6. **Slice footer "Files / Verify / Gate" on every first-wave slice.** New task (bulk `update_tasks`).
7. **Lanes with correct flags:** fix keeper `--model` and `--permission-mode` (task 2466 gap 7); SW-67 (task 1913) later. Cap lanes at 4 on the 24 GB box.
8. **MCP-G28 (task 2541) with 2427:** the write path for planning and bulk work. HIGH, Opus.
9. **Approvals filter (task 2622), then renew the grants (task 2580)** before 2026-11-30. Open `OWNER_GRANTS.md:79` for the owner.
10. **Close the outside-agent hole:** branch protection and CODEOWNERS (WS-38a, task number unknown; new task if none), then KIT-0 and KIT-1 (tasks 2548, 2549). Until then, outside agents stay off the main repo.

## RECOMMENDED FIRST WAVE (9 slices, parallel)

Rule used: at most one agent per app project where files overlap, no OWNER-GATE act, and no shared code file.

| # | Slice | Project | Tier | Files it touches | Conflict risk |
|---|---|---|---|---|---|
| 1 | 599 E-11+H-261, close the transaction before provider calls | My Email | HIGH, Opus, R8 test | email sync code and its test | none with others |
| 2 | 597 + 598 together (E-05+E-04, E-09), email spec reconcile and flag read | My Email | LOW | `specs/email_app_master_plan.md` lines 593, 640, 643; box read | Do as ONE PR (same spec lines). RC-0 (2641) edits the same spec, so RC-0 waits. Box read uses the `deploy` and `secrets` grants (until 11-30) and must be reported. |
| 3 | 2427 then 2541 MCP-G28, batch write path | MCP | HIGH, Opus, R8 | MCP write routes, `agent_oauth.py` limits; decision in `work_plan.md` | Mints a D-number. Take the reserved one. |
| 4 | 2622 Approvals filter | Projects/Tasks | STANDARD | Needs-attention route and UI | UI captures; preview or D200. |
| 5 | 1827 CRM switch dialog copy and retry | CRM | LOW | `cutoverSwitch.ts` and its test | none. (This is a UI bug fix, NOT the Zoho cutover. Tasks 806 and 803 run the real cutover and need the customers' consent.) |
| 6 | HX-0 (2762) harness manifest spec | Proactive Intelligence | LOW (docs) | new spec, `INDEX.md`, new `test_harness_spec_in_index.py` | INDEX.md |
| 7 | WB-0 (2651) WhatsApp bots spec | WhatsApp | LOW (docs) | new `whatsapp_bots.md`, `INDEX.md` | INDEX.md |
| 8 | CX-0 (2733) CRM complete spec | CRM | LOW (docs) | new `crm_complete.md`, `INDEX.md` | INDEX.md; same project as #5, different files |
| 9 | LL-0 (2625) and KIT-0 (2548): check whether sections 6a and 6c already satisfy them, then close or amend | MCP | LOW (docs) | `orchestration_playbook.md` | Both touch one file: one agent does both. |

Wave 1b (after wave 1 merges): BK-0 (2676), KS-0 (2714), CM-0 (2791), RC-0 (2641). Reasons: BK-0 needs a D-number and the BK-Q1 spec amendment; KS-0 needs a D-number (KS-Q1, owner gate); CM-0 sits behind CX-0/CX-1 in the CRM project; RC-0 shares a spec file with #2.

Shared-file conflicts to expect in wave 1:
- `project-docs/INDEX.md`: slices 6, 7, 8 each add a row. Merge them one after another through lane-merge; `test_index_union.py` resolves by union.
- `project-docs/work_plan.md`: D-numbers from slice 3 and from the pending 2546 decision. Take numbers from the reserved block, since `test_decision_ids.py` only fails after the second merge.
- `specs/email_app_master_plan.md`: slice 2 only (and RC-0 later).
- No migration is expected in wave 1. If slice 1 or 3 adds one, take the number at merge time.

Capacity of wave 1: 9 branches is above the cap of 3 to 4 per integrator. Start 4 at once (1, 3, 4, 6), keep the docs slices as quick fillers, and let lane-merge pace the rest. Slices 1 and 3 are HIGH and need the R8 database (3.2): run them on different boxes or one after the other.

M0 note: tasks 597 to 599 are due 2026-11-17 (7 days). Start 599 and 597+598 first.

---

## What I did not read or verify

- `.claude/agents/*.md` bodies, `next-ticket.md` steps 3 to 7 (only grep and steps 1 to 2), `engineering_practice.md` section 7a in full, `ci_velocity.md` beyond section 4.
- `.claude/OWNER_GRANTS.md`: dates only, from a grep (a shell redirect to it was blocked by plan-guard, correctly).
- Delegation briefs in app project descriptions and the "Start here" map, comments on all epics, the epic tasks 2547 (kit), 2624 (learning loop), tasks 2545, 1723, 2546 PR 2040, and slices 2676, 2714, 2791, 2641.
- Live state: GitHub plan, home box memory and lane list, branch protection, the Fleet panel, whether any first-wave slice is already claimed.
- The 25-question count comes from epic descriptions only. Questions in comments would add to it.
