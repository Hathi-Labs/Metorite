# Proactive agent — Mira acts for each person without being asked (WS-AUTO)

> **Product:** Metorite · **Feature:** automations, standing watches and the chase, on a trust ladder · **Updated:** 2026-11-02 · **Owner:** vjvarada
> **Status:** 📋 **SPEC, FIRST SLICE BUILT.** The first slice of AUTO-2 is merged (PR 1442, migration 313, dark): the run ledger, the incidents and the stall guard. The brief is its only caller. AUTO-3 is decided (D131). H-328 (task 856) is in progress. AUTO-7 is the next slice. AUTO-Q1 is open, and the build runs on its working default (§12).
> **Verified against code on 2026-11-02 at `ce29836ea`.** Each path in §10 was read on that commit. The line numbers of Open Instinct come from a read on 2026-11-02, and no code of it ran.
> **Board:** WS-AUTO in `work_plan.md` §2.1. **Decision:** D151. D75, D100, D103, D124, D129, D131, D132, D140, D145 and workflows G5 bind it.
> **Metorite:** project "Automations", epic 807, this spec task 1396. The slices are AUTO-1 (816), AUTO-4 (817), AUTO-5 to AUTO-10 (1389 to 1394). AUTO-2 is task 814, AUTO-3 is task 815 and AUTO-Q1 is task 1395.
> **Research first:** the comments "Research notes, 2026-11-02 (Open Instinct)" on epic 807, copied into §8, and the Hermes research of 2026-10-27.
> **Gate summary:** every slice is AGENT-SAFE. A flag that spends credits stays the owner's flip. A slice that changes what a member sees follows the D145 loop.

---

## 0. Thesis

Mira acts for each person without being asked, within limits that the person sees. Today Metorite gives insight. The owner wants automated work, and then proactive tasks. The owner said Metorite should feel like more of an agent for every person in the company.

**The workflows engine owns the triggers.** It has cron, event and webhook triggers. There is no second scheduler (workflows G5).

**The job queue owns the capped headless turn.** `jobs/worker.py` with `unattended.run_guarded` runs one Mira turn with no session. It is one queue, not a second loop.

**Reach and My Day are the only mouth.** An unattended turn says nothing, or it makes one `tell_me()` call. It has no other way to reach a person.

**A standing watch is a template.** It is a time watch, an event watch, a digest or a chase. It is never a raw cron prompt.

**The person sees the limits.** Each automation has a trust level, a channel and a cost. The person can list, pause and delete it.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **The trust ladder** (§5): Suggest, Ask each run, Act and report (D131).
2. **Automations from chat** (AUTO-1) on the workflows scheduler, and **one list** of them (AUTO-4).
3. **Standing watches** from chat (AUTO-5), as templates.
4. **Silent by default** (AUTO-6): Reach is the mouth.
5. **A run with no person cannot approve its own action** (AUTO-7).
6. **A headless turn with a credit cap** (AUTO-8).
7. **The chase** (AUTO-9) and **the record of what Mira told me** (AUTO-10).

### 1.2 Non-goals

1. **No per-person VM or desktop.** One VM for each person breaks the cost model. Customer code never runs in our process.
2. **No new channel.** WhatsApp and web push exist. We add no iMessage.
3. **No payments layer.** Money needs the owner.
4. **No third-party agent stack.** Metorite is multi-tenant Postgres with RLS.
5. **No state in files.** State lives in tenant tables.
6. **No raw cron prompt.** Open Instinct's cron has no cap, no ledger and no budget.
7. **No mail to an outside person** from an unattended run (CLAUDE.md §3a rule 3). An outside send stays a D131 act that always asks.
8. **No agent-to-agent work inside one company** now. `agent_asks` and `nudge_task` exist. Revisit after AUTO-5.

---

## 2. What exists today

| Piece | Where | State |
|---|---|---|
| The workflows engine | `apps/services/gateway/gateway/routes/workflows/` (`scheduler.py`, `triggers.py`, `hooks.py`, `service.py`) | Cron scan every 30 seconds (`scheduler.py`, `_scan_once` line 111), event dispatch (`triggers.py`, `dispatch_event` line 46), a webhook route (`hooks.py`, `workflow_hook` line 68) and approval nodes |
| The tenant gap | Same files. `grep "await _get_db()"` finds 12 hits in `routes/workflows/` | Schedule, event, webhook and manual runs bind no tenant. In production FORCE RLS gives such a read zero rows. **H-328 (task 856) closes this, and it blocks any second org** (§6) |
| Workflow publish | `routes/workflows/publish.py` (`POST /{workflow_id}/publish`, line 49) | An HTTP route that needs `PUBLISH_PERMISSION`. **Chat has no tool that saves or publishes a workflow.** The Studio tools draft only |
| The job queue | `gateway/jobs/queue.py`, `worker.py` | `detached` mode for a model call. `idempotent` flag. Dark: `worker_enabled()` reads `job_queue` |
| The run ledger | `gateway/jobs/unattended.py` (`begin` line 202, `run_guarded` line 369), migration `infra/postgres/313_unattended_ledger.sql` | **Built, dark** (`unattended_ledger`). At most once per `(org, job, slot)`, an orphan mark, one incident for each job and error class, and a stall stop after 10 minutes. `brief/run.py` is the only caller |
| What AUTO-2 does not cover | — | The workflows scheduler, Reach and My Day do not call the ledger yet |
| Reach | `gateway/reach/requests.py` (`tell_me` line 208), `reach/classes.py` | A judge, a cap, quiet hours, a 15-minute batch and a dedupe. `SOURCES` is `email.rule`, `chat` and `workflow` (line 44). The limits are `BACKLOG_LIMIT` 20 and `SELF_DAILY_LIMIT` 20 |
| Reach requests table | `infra/postgres/271_reach_requests.sql` | `source` is `TEXT NOT NULL` with **no CHECK**. The allow-list is only the Python set |
| Today's approvals | `gateway/chat_supersede.py`, `routes/email/automation/held_card.py`, `infra/postgres/66_pending_actions.sql` | `request_confirmation` asks the acting member in a chat. `pending_actions` has no approver column. Neither knows a run with no person |
| Nudges and watches in chat | `apps/skills/skill-projects/skill_projects/manifest.py` (lines 304, 313, 487) | `nudge_task`, `set_reminder` and `watch`. `watch` follows one task or project. Nothing holds "keep watching X for me" |
| The goal record | WEB-10 (`web_research.md` §6) | **Not built.** No `goal_records` table and no `cap_reached` exist in any code file |
| The credit ledger | `apps/services/customer_console/customer_console/credits.py` | `usage_event` rows live here, in the console service |
| The trust ladder | `work_plan.md` §3, D131 | Decided 2026-10-27 |

**⚠️ Two changes since the task text.**

1. **AUTO-8 cannot use `goal_records` or `cap_reached` yet.** WEB-10 is not built. AUTO-8 either waits for WEB-10, or it builds the cap on its own counters and adopts the goal record later. §9 picks the second path, so AUTO-8 does not wait.
2. **AUTO-6 has no CHECK to widen.** The task says to add `automation` to the allowed `tell_me` sources "with its CHECK". `reach_requests.source` has no CHECK. The change is one word in the Python set `SOURCES`, plus a plan-text word in `reach/plan.py` line 299.

---

## 3. The model

```text
trigger                      candidate check              headless turn              mouth
(workflows: cron, event,  →  (cheap, no model call)  →   (jobs/worker.py +     →    tell_me() → Reach
 webhook; AUTO-5 template)   found nothing = stop         run_guarded, capped)        or silence
```

1. **A trigger fires.** It is a workflows trigger (AUTO-1) or a watch template (AUTO-5). It runs as the member who made it, with that member's access.
2. **A candidate check runs first** (§4.3). No candidate means no model call.
3. **The headless turn runs** (AUTO-8), under a tool list, a round cap, a time cap and a credit cap.
4. **The turn ends in silence or in one `tell_me()` call** (AUTO-6). Reach judges the message and budgets it.
5. **The ledger records the run** (AUTO-2). A repeat of the same slot never runs twice.

---

## 4. The rules

### 4.1 Silent by default (AUTO-6)

A proactive agent that speaks on each run gets muted in a week. So an unattended turn ends in exactly one of two ways: silence, or one `tell_me()` call. It has no other way to reach a person. It cannot call the chat, the bell or email directly. A quiet mode allows side effects only, with no message, for a run that only updates a record.

### 4.2 A run with no person cannot approve its own action (AUTO-7)

The server marks each unattended run `no person present`. The mark never comes from model output or from tool input. A marked run cannot answer a confirmation, cannot answer an elicitation, and cannot settle a `pending_actions` row. Without this rule, a prompt injected into a page or a mail could approve its own write while nobody watches.

**AUTO-7 extends a mark that exists.** `ask_tools._CARDS_REFUSED` (`packages/acb_skills/acb_skills/ask_tools.py` line 57) is set at `executor.py` line 6018 from `shows_cards`. **A caveat:** the Copilot SDK JSON-RPC thread may read a fresh context. So a fence must verify the `carry_run_context` claim, and must not trust it.

- **AUTO-7a.** A second ContextVar `no_person_present`, which only server code sets. It is set in the job worker's handler dispatch and in the non-manual `_execute_run`. Refusals sit in `broker.approve` and `reject`, in the workflow resume, and in the elicitation answer. `request_confirmation` returns deny.
- **AUTO-7b.** Park and ask. An action that needs a person parks and asks through a decision (ALN-2a, task 1376) or through Reach. Until ALN-2a exists, a marked run fails closed.

### 4.3 A candidate check before any model call

Task 469 says it well: use triggers, and do not poll the model. An event, or one daily query, finds a candidate. Only then does the model run. A watch with no candidate costs no model call. The check is code. It reads the same tables the member can read.

### 4.4 A credit cap on each turn (AUTO-8)

Each turn has a tool list (read tools by default), a round cap, a time cap and a credit cap. At the cap, the turn stops, writes `cap_reached` in its run row and reports that once. The paying member comes from our own tables, through `job_member_scope`. The run writes a `usage_event` row of kind `automation`. A kill switch stops all headless turns for an org.

### 4.5 A cap on each member (AUTO-Q1)

A member has at most 10 standing watches. Over the cap, Mira refuses the next watch with a plain message. The member can list, pause or delete a watch in chat and in the list of AUTO-4. The member purge removes the watches of a leaver.

---

## 5. The trust ladder (D131)

| Level | What Mira does |
|---|---|
| **Suggest** | Mira puts a card in My Day. Nothing changes |
| **Ask each run** | Mira prepares the change. The member approves it with one tap, on a card that shows the exact payload |
| **Act and report** | Mira makes the change inside a scope and a spend cap that the member approved once. Each run reports what changed, with Undo |

**The rules of D131.**

1. An automation that Mira proposes starts at Suggest. An automation that a member makes starts at Ask each run.
2. Only the member moves an automation up. After five approved runs in a row with no edit, Mira may offer the next level once.
3. An Undo, a failed run or a change of scope sets the automation back to Ask each run. Mira tells the member why.
4. **Five acts always ask.** The five acts are:
   - money
   - a member or role write
   - a delete
   - a bulk change
   - a send to an outside person that the scope does not name

   D124.2 asks about each of these acts too.
5. An admin can set the highest level for the organization.

AUTO-7 enforces rule 4 for a run with no person present. D131 gives the levels, and this spec adds no level.

---

## 6. The blocker: H-328

**H-328 (task 856, in progress) is the named blocker for any second org.** The workflow run lifecycle outside the agent tools names no tenant. A schedule, event or webhook run reads `workflows` unbound. In production FORCE RLS gives that read zero rows, and a write fails. The same gap means an agent cannot start a workflow with a long wait.

**What it means for this spec.**

1. No slice that makes a schedule, event or webhook run ships to a second org before H-328 lands. That is AUTO-1 and AUTO-5.
2. Slices may build dark in the meantime, and customer zero is one org.
3. H-328 binds the tenant from a stored fact. A trigger uses the workflow row. A sweep uses the run row. The ambient context is never the source.
4. Its check is `git grep -n "await _get_db()" -- apps/services/gateway/gateway/routes/workflows/`. The entry stays open until the only hits are tenant-discovery reads.

AUTO-7, AUTO-8 and AUTO-6 do not need H-328. They run on the job queue, which binds the tenant.

---

## 7. Safety

1. **Unattended means untrusted input.** A page, a mail or a task that a watch reads can carry an instruction. The turn gets read tools by default and no write tool without a level above Suggest.
2. **The member is the only authority** (D140). A watch runs as its member, with that member's access. It reads nothing the member cannot read.
3. **A repeat is not a retry.** AUTO-2 never retries a send whose outcome is unknown. A lost message is safer than a duplicate.
4. **Fail closed.** No answerer, no cap left, or no candidate: the run stops.
5. **No money and no outside person** (CLAUDE.md §3a rule 3). Both stay D131 acts that always ask.
6. **A nudge goes to a colleague through Metorite,** never to an outside person. A colleague on leave (`people_absences`) is skipped.
7. **Fences** in §11 make each rule fail a test when broken.

---

## 8. What other apps do

The source is the research comment of 2026-11-02 on epic 807. The owner asked: can we reuse the workflows and schedules module for proactivity, or does Open Instinct use a mechanism worth lifting? **The answer is to do both, and the reuse part is far larger.**

**Open Instinct** (`https://github.com/mariagorskikh/open-instinct`, MIT, TypeScript, created 2026-10-03, 429 stars, status 0.1, third party). Read as text on 2026-11-02. No code ran. It has one proactive mechanism: a cron or one-shot prompt that the agent writes for itself in chat.

| Question | What Open Instinct does | Source line |
|---|---|---|
| What wakes it? | An inbound message or webhook, a schedule polled every 30 seconds, an operator task, or another agent. It has no heartbeat and no poll loop. The agent never decides to start | `packages/inkbox/src/events.ts:350-380`, `packages/core/src/scheduler.ts:355-377` |
| Whether to speak | The code has little. The prompt says to reply with nothing. Event ids dedupe. Refusal notices have a limit. The cap of 3 messages a day is only a requirement | `prompt.ts:230-231`, `runtime.ts:104`, `notifier.ts:49-62`, its requirements document, lines 393-434 |
| On the fly | `schedule_create` is a tool, so the agent makes its own cron job in chat. It has no watch-list type. A watch is a prompt on a timer | `packages/core/src/core-tools.ts:123-148` |
| Safety | A scheduled run has `ownerTurn=false` and cannot settle its own approval. This is its best idea | `core-tools.ts:255-262`, `runtime.ts:607-611` |

**We take four things.**

1. A standing watch that the agent writes in chat, as templates and not raw cron (AUTO-5).
2. Silent by default (AUTO-6).
3. An unattended run cannot approve itself (AUTO-7).
4. The follow-up cadence of 3 days, 3 nudges, then escalate (AUTO-9). The source is a requirement, not code: its requirements document, lines 239-245.

**We do not take.**

| Item | Why not |
|---|---|
| The per-person VM and desktop | One VM for each person breaks the cost model. Customer code never runs in our process |
| iMessage | WhatsApp and web push exist |
| The payments layer | Money needs the owner |
| Maritime, Inkbox, Composio and Pi | Each is a third-party stack. Metorite is multi-tenant Postgres with RLS |
| JSON files for state | We use tenant tables |
| Its cron | It has no cap, no ledger and no budget |
| Agent-to-agent work in one company | A good idea for later. Metorite has `agent_asks` and `nudge_task` today. Revisit after AUTO-5 |

**What Metorite has that Open Instinct only writes as a requirement.** Reach builds a judge, a daily cap, quiet hours, a batch and a dedupe (`proactive_reach.md`). The workflows engine has cron, event and webhook triggers.

**Hermes Agent** (`cron/` in NousResearch/hermes-agent, research of 2026-10-27). A schedule from chat, a run ledger, at-most-once dispatch and one incident for each error. AUTO-2 takes the ledger and the incidents. AUTO-1 takes the tool that makes a schedule from chat.

**Gaps that remain after the workflows module.**

1. Event, schedule and webhook runs bind no tenant (H-328).
2. Chat cannot save a workflow. Studio tools draft, and no chat tool publishes.
3. No headless agent turn has a credit cap (AUTO-8).
4. No cross-app "keep watching" matcher exists. Events carry a source and a type only.

**Not verified.** Production flags for Reach, Studio and the unattended ledger. Whether Reach's loop is live for each org. The README's claim of 837 tests. The `computer` package, `hooks.ts` and the gateway source of Open Instinct.

---

## 9. Slices

**Tier** is the review tier of `next-ticket.md` step 1b. Every slice ships dark. Migration numbers are taken at build time (R1).

### Done

| Id | What | State |
|---|---|---|
| **AUTO-2** (814), first slice | The ledger, the incidents and the stall guard: `jobs/unattended.py`, migration 313 | **Merged 2026-10-29, dark.** Open: the workflows scheduler, Reach and My Day call the ledger |
| **AUTO-3** (815) | The trust ladder | **Decided: D131.** Done 2026-10-27 |

### To build

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **H-328** (856) | Bind the tenant in the workflow run lifecycle (§6). Owned by the HANDOFF, in progress | The `git grep` of §6 shows only tenant-discovery reads | HIGH | AGENT-SAFE. Blocks any second org |
| **AUTO-7a** (1391) | The mark `no person present` and its refusals (§4.2). Needs AUTO-2 | A fence (R7): an unattended turn that tries to approve gets a refusal, and no write happens. The fence verifies the `carry_run_context` claim on the SDK thread. A mutation that drops the mark turns the fence red | HIGH | AGENT-SAFE |
| **AUTO-7b** (1391) | Park and ask (§4.2). Needs AUTO-7a and ALN-2a | An action that needs a person parks and asks. An injection fixture in a watched page asks for an approval, and nothing runs. With no answerer, the run fails closed | HIGH | AGENT-SAFE |
| **AUTO-8** (1392) | A headless turn with a credit cap (§4.4). Needs AUTO-2, AUTO-7a | A turn stops at its cap with `cap_reached` and reports once. A run with no candidate makes no model call. R8: the run reads only what its member can read, and org B sees nothing. A kill switch stops all headless turns for an org | HIGH | AGENT-SAFE |
| **AUTO-6** (1390) | Silent by default (§4.1). Needs AUTO-2 | A run that finds nothing writes no outbox row and no bell row. Ten runs that find the same thing give one message. Quiet hours hold. The fence goes red under a mutation | STD | AGENT-SAFE |
| **AUTO-1** (816) | "Do this every Monday": a `schedule_run` tool makes a workflow with one Mira step. Needs AUTO-2, AUTO-3, H-328, and a chat way to save a workflow | A request creates one automation, and the confirm card shows the next three run times. It delivers on Monday. It runs as the member who made it | STD | AGENT-SAFE. D145 preview. No second org before H-328 |
| **AUTO-4** (817) | One list of every automation, in My profile. Needs AUTO-1 | A member pauses an automation from the list, and the next run does not happen. An admin sees the org's automations | STD | AGENT-SAFE. D145 preview |
| **AUTO-5** (1389) | "Keep watching this for me": a watch from chat (§4.5). Needs AUTO-1, AUTO-6, AUTO-7, AUTO-8, AUTO-Q1, H-328 | "Tell me if any deal over 5 lakh goes quiet for a week" makes one watch. It stays silent while nothing happens, and it speaks once when the condition holds. A second member cannot see the watch. The cap refuses the next watch with a plain message | HIGH | AGENT-SAFE. D145 preview. No second org before H-328 |
| **AUTO-9** (1393) | The chase. Needs AUTO-5 and PZ-36 (task 1134) | A quiet task gets a nudge after the set time. An answer stops the chase. Three unanswered nudges tell the member once. A nudge never reaches someone who cannot open the task | STD | AGENT-SAFE. D145 preview |
| **AUTO-10** (1394) | What Mira already told me: one read tool over the Reach ledger and the run ledger. Needs AUTO-6 | A watch reports a change and not the same fact twice. "What are you watching?" lists the member's watches with their last result, and only the member's own runs | STD | AGENT-SAFE |

**The cap of AUTO-8 does not wait for WEB-10.** AUTO-8 keeps its round, time and credit counters in its own run row. When WEB-10 lands, the goal record may hold them, and AUTO-8 adopts it. This keeps AUTO-8 off the critical path of WEB.

### Build order and parallel work

**The order has nine steps.**

1. H-328, already in progress.
2. AUTO-7a, then AUTO-7b after ALN-2a.
3. AUTO-8.
4. AUTO-6.
5. AUTO-1.
6. AUTO-4.
7. AUTO-5.
8. AUTO-9.
9. AUTO-10.

**What can run in parallel.**

1. H-328, AUTO-7a and AUTO-6 need only AUTO-2. They touch different code, so they can run at once.
2. AUTO-8 waits for AUTO-7a, because no turn may write before AUTO-7a.
3. AUTO-1 waits for H-328 and for a chat way to save a workflow.
4. AUTO-4 and AUTO-10 can run in parallel after AUTO-1 and AUTO-6.
5. Keep three or four slices in flight at most (CLAUDE.md §4).

**The first dispatchable slice is AUTO-7a.** It needs only AUTO-2, it is small, and no unattended turn may write before it. H-328 is already in progress in its own session. AUTO-6 is dispatchable at the same time.

---

## 10. Current file paths

Each path was read on `ce29836ea`.

- `apps/services/gateway/gateway/routes/workflows/` (`scheduler.py`, `triggers.py`, `hooks.py`, `service.py`, `publish.py`, `tools.py`)
- `infra/postgres/132_workflows.sql`, `infra/postgres/313_unattended_ledger.sql`, `infra/postgres/271_reach_requests.sql`, `infra/postgres/66_pending_actions.sql`
- `apps/services/gateway/gateway/jobs/queue.py`, `jobs/worker.py`, `jobs/unattended.py`
- `apps/services/gateway/gateway/brief/run.py` (the one caller of the ledger)
- `apps/services/gateway/gateway/reach/requests.py` (`tell_me`, `SOURCES`), `reach/classes.py` (`REACH_CLASS`), `reach/plan.py`
- `apps/services/gateway/gateway/decide_features.py`, `routes/shell/needs.py`, `routes/shell/feed_decide.py`
- `apps/services/gateway/gateway/chat_supersede.py`, `routes/email/automation/held_card.py`
- `apps/skills/skill-projects/skill_projects/manifest.py` (`nudge_task`, `set_reminder`, `watch`)
- `apps/services/customer_console/customer_console/credits.py` (`usage_event`)
- `project-docs/specs/workflows_app.md` (G5), `proactive_reach.md`, `core_brief.md`, `web_research.md` §6 (WEB-10), `personalization.md` §15 (PZ-33 to PZ-36), `ai_studio.md` (B2c)

---

## 11. Fences (R7) and verification

| Rule | Fence |
|---|---|
| An unattended run cannot approve its own action | New with AUTO-7: `tests/unit/test_unattended_no_self_approval.py`. A mutation that drops the mark turns it red |
| An unattended path sends only through Reach | New with AUTO-6: a scan in `tests/unit/test_unattended_silent.py` for a direct send, a bell write or a mail call in the new modules |
| A run with no candidate makes no model call | New with AUTO-8: `tests/unit/test_headless_turn.py` (R8) |
| A turn stops at its credit cap | New with AUTO-8: same file |
| A run happens at most once for a slot | `tests/unit/test_unattended_ledger.py` (exists) |
| No second scheduler | New with AUTO-1: a scan that finds no new cron loop outside `routes/workflows/scheduler.py` |
| A watch reads only what its member reads | New with AUTO-5: an R8 suite with two orgs |
| A new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` |
| No workflow run reads an unbound `workflows` row | H-328: the `git grep` check, and `tests/unit/test_workflows_tool_rls_r8.py` |

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_unattended_ledger.py -q
uv run ruff check apps/services/gateway
bash scripts/ci_local.sh
```

For this spec only: `uv run pytest tests/unit/test_index_completeness.py tests/unit/test_decision_ids.py tests/unit/test_board_index.py -q`.

---

## 12. Owner questions

**AUTO-Q1** (task 1395) is the one open question. The owner approved the plan on 2026-11-02 and left it open. The answer below is a **working default, not an owner decision**. The build uses it. The question task stays open. If the owner changes an answer, this spec changes with it.

| Part | Question | Working default, not an owner decision |
|---|---|---|
| 1. The default | Does each member get proactive Mira on day one, or opt in? | **On, at the safest level.** My Day and Reach already speak within their caps. A standing watch exists only when a member asks for one, so nothing new runs by itself |
| 2. The cap | How many standing watches may a member have? | **10 for each member** at first. A higher number on a higher plan. WS-PLN owns the plan shape |
| 3. Who pays | A watch spends AI credits with no person present | **The member's allowance.** The confirm card shows "about X credits a month". Over the cap, the watch pauses and tells the member once |

**What the working default does not change.** Each slice ships dark. Each slice that changes what a member sees needs a D145 preview and the owner's approval. Each flag that spends credits stays the owner's flip. An eval over USD 0.50 still needs the owner's OK.
