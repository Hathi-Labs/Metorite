# Proactive agent — Mira acts for each person without being asked (WS-AUTO)

> **Product:** Metorite · **Feature:** automations, standing watches and the chase, on a trust ladder · **Updated:** 2026-11-08 · **Owner:** vjvarada
> **Status:** 📋 **SPEC, FIVE SLICES BUILT.** The first slice of AUTO-2 is merged (PR 1442, migration 313, dark): the run ledger, the incidents and the stall guard. The brief is its only caller. AUTO-3 is decided (D131). AUTO-7a is merged (PR 1615). H-328 parts (a) and (b) are merged, and part (c) is open. **AUTO-7b is built, dark behind `AUTO_PARK_ORGS` (task 1411), and waits for review** (§4.2, §9). **AUTO-8a is on main (PR 1878), dark behind `HEADLESS_TURN_ORGS` (task 1392). It has no production caller, and its tests prove it** (§4.4, §9). **AUTO-8b-1 is built, dark, and waits for review (task 2194).** The settle of a round has a time limit, and each round checks the bound of the goal cap (§4.4). AUTO-8b-2 (task 2230) waits for an owner decision. **The owner answered AUTO-Q1 on 2026-11-06** with the recommended option (D192, §12).
> **Verified against code on 2026-11-02 at `ce29836ea`.** Each path in §10 was read on that commit. The AUTO-7b paths were read again on `0591d53b3` (2026-11-04). The AUTO-8a paths were read on `2d2e71978` (2026-11-05). The AUTO-8b-1 paths were read on `6c8d48d81` (2026-11-08), the base of the branch `auto-8b-mt-57b4e62c`. The line numbers of Open Instinct come from a read on 2026-11-02, and no code of it ran.
> **Board:** WS-AUTO in `work_plan.md` §2.1. **Decision:** D161, and D192 for the answer of §12. D75, D100, D103, D124, D129, D131, D132, D140, D145 and workflows G5 bind it.
> **Metorite:** project "Automations", epic 807, this spec task 1396. The slices are AUTO-1 (816), AUTO-4 (817), AUTO-5 to AUTO-10 (1389 to 1394). AUTO-8a is task 1392, AUTO-8b-1 is task 2194 and AUTO-8b-2 is task 2230. AUTO-2 is task 814, AUTO-3 is task 815 and AUTO-Q1 is task 1395.
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
| The goal record | WEB-10 (`web_research.md` §6.1) | **Built dark.** Migration 325 and `acb_common/goal_store.py` hold `goal_records` and `cap_reached`. The dark headless turn of AUTO-8a is the one caller |
| The credit ledger | `apps/services/customer_console/customer_console/credits.py` | `usage_event` rows live here, in the console service |
| The trust ladder | `work_plan.md` §3, D131 | Decided 2026-10-27 |

**⚠️ Two changes since the task text.**

1. **AUTO-8 uses the goal record.** WEB-10 merged on 2026-11-01. `goal_store.record_round` holds the rounds and the cost, and it returns `cap_reached`. AUTO-8 calls it and keeps no counters of its own.
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

- **AUTO-7a.** A second ContextVar `no_person_present`, which only server code sets. It is set in the job worker's handler dispatch, and in `_execute_run` for a trigger in `UNATTENDED_TRIGGERS` (`schedule`, `webhook` and `event`). An `api` run stays unmarked, because a chat starts it and its card can reach that chat. A known gap: an `api` run that a marked run starts loses the mark after a person approves its gate. AUTO-8 must close that gap before it lets an unattended turn write. Refusals sit in `broker.approve` and `reject`, in the workflow resume, and in the elicitation answer. `request_confirmation` returns deny.
- **AUTO-7b (built, dark).** Park and ask, for WORKFLOW runs only. A marked run that reaches an approval gate parks there. With `AUTO_PARK_ORGS` and `DECISIONS_ORGS` on for the run's org, the gate asks ONE person through ONE decision (ALN-2a): the workflow's owner, `workflows.owner_email`. The subject is a private task in the owner's personal project, one for each parked gate. The task sends no `pm.task.created` event, so a parked gate cannot start a workflow that parks again with no end. The task, the decision and the pause row commit in one transaction. In each other case the gate files the broker proposal of AUTO-7a, and it never files both. The wait scan settles a parked gate from the decision row only. A yes of the owner resumes the run once, any other answer ends it, and the DB clock ends an unanswered one after 7 days. The scan tells the owner once, as a comment on the task. After a resume the run keeps its mark, so a second gate parks again. `resume_run` now claims the pause and the run with a compare-and-set. The module is `routes/workflows/park.py`. Two limits hold. First, the wait scan settles a parked gate only for an org on `WORKFLOW_UNATTENDED_ORGS`. If the org leaves that list, a yes resumes nothing and no expiry ends the run. That stays so until the org is on the list again. Second, a webhook run and an event run carry no org today, so their gates always take the broker door. Out of scope: an agent turn and a park inside a model turn (AUTO-8), and the decision card on My Day (ALN-2b).

### 4.3 A candidate check before any model call

Task 469 says it well: use triggers, and do not poll the model. An event, or one daily query, finds a candidate. Only then does the model run. A watch with no candidate costs no model call. The check is code. It reads the same tables the member can read.

### 4.4 A credit cap on each turn (AUTO-8)

Each turn has a tool list (read tools by default), a round cap, a time cap and a credit cap. At the cap, the turn stops and reports that once. The paying member comes from our own tables, through `job_member_scope`. The Router writes a `usage_event` row for each model call of the turn. A kill switch stops all headless turns for an org.

**`cap_reached` lives on the goal row, not on the run row.** `goal_store.record_round` writes it on the `goal_records` row of the turn, in the write that records the round. The table `unattended_runs` holds no cap state, so the ledger row of the turn ends `ok`.

**AUTO-8a (built, dark).** The detached kind `auto.turn` runs one turn for one goal of kind `headless.turn`. The module is `gateway/jobs/headless_turn.py`. The kind registers only with `JOB_QUEUE` on, and no production code enqueues it. The payload is `{goal_id, candidate}` and nothing else. The member is `goal_records.member_email`, read under RLS, and never comes from the payload.

The handler takes these steps in order. A payload of another shape gives up before step 1.

1. It starts the clock of the turn and calls `unattended.recover` for its slot. So the time cap holds the checks below too.
2. It reads `HEADLESS_TURN_ORGS`.
3. It reads the goal as its member. A goal that allows more than 50 credits gives up with `CapTooHigh`.
4. It checks that the member is still in the org.
5. It runs the candidate check. No candidate means no model call, and the goal does not change.
6. It begins the ledger row and runs the rounds under the stall guard.

Before each round, the turn reads the flag and the goal again. So the flag is the kill switch, and an org that leaves the list stops before its next round. Each round checks the bound of 50 credits again. So a goal that another write raises over the bound between two rounds gets no next round.

The producer sets `dedupe_key` to the goal id (AUTO-1 and AUTO-5), so one goal has at most one live turn in the queue.

**The caps of AUTO-8a.** A turn runs 5 rounds at most, and 120 seconds for the whole turn. Module constants bound both. The credit cap is the cap of the goal, and a goal that allows more than 50 credits gets no turn. `goal_store.cap_block` is the one cap rule. A round whose cost the meter cannot read counts as the rest of the budget, so the goal blocks.

**No cost reader, no call.** AUTO-8a has no reader of the cost of a run. The default meter raises `NoCostReader` before the first call of a round. So with the flag on, the turn makes no model call. It gives up with a named reason and logs `headless_turn.no_cost_reader` at error. AUTO-8b-2 adds the reader.

**Read tools only.** One round is one `run_agent` run of the internal agent `worker`, with one bound job and `no_egress`. The job holds only tools that the risk registry marks `read_only` and not `open_world`. The pin of the worker refuses every other tool. The worker makes at most 4 model calls in one round (`subagent_contract.MAX_CHILD_ROUNDS`). AUTO-8a changes no code of `orchestrator/executor.py`.

**The settle of a round (AUTO-8b-1).** After a call, the turn reads the meter and records the round. These two steps have `SETTLE_MARGIN_S` (60 seconds), and each step takes at most a third of it. The time cap of 180 seconds plus the margin stays inside `worker.HANDLER_TIMEOUT` (240 seconds). That stays inside the lease of the job (300 seconds). So the worker does not cancel a turn while it records a round.

A meter that does not answer in time charges the rest of the budget: the cap minus the credits used. So the goal blocks with `cap_reached`. A record that does not end in time logs `headless_turn.round_unrecorded` at error, and the job gives up with `TurnFailed`. The settle catches only the timeout of its own scope. It never catches a cancel.

**Limits.** ADVISORY (R7), with no fence: no credit stop happens inside one call. The cost of a round is known only at its end, so the last round can go past the goal cap. The goal cap is the cap. The Router's `RunCeiling` is a fixed tripwire of 500 credits for each run, and the Router checks it before each model call. It does not stop a turn at the goal cap.

A stop of the worker during the settle cancels the turn, and then the cost of that round does not reach the goal. In `run_agent`, the run binds the agent name `worker` and the app `chat`. So the usage row does not name `auto.headless` yet. AUTO-8b-2 decides that attribution.

### 4.5 A cap on each member (AUTO-Q1, D192)

A member has at most 10 standing watches. A higher plan can raise the number, and WS-PLN owns the plan shape. Over the cap, Mira refuses the next watch with a plain message. The member can list, pause or delete a watch in chat and in the list of AUTO-4. The member purge removes the watches of a leaver.

**Who pays.** A watch spends the member's own AI allowance. The confirm card shows "about X credits a month". When the allowance runs out, the watch pauses and tells the member once.

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
| **AUTO-7b** (1391, task 1411) | Park and ask (§4.2). Needs AUTO-7a, ALN-2a and H-328 part (b). **Built, dark, in review** | A fixture run parks and writes one decision. A yes resumes it once, a no ends it, and an expiry ends it. A fence proves that the run cannot answer its own decision. Advisory, with no fence of its own (R7): the D131 five acts always ask. This holds by construction, because a parked gate resumes only on the owner's yes (F3, F8), and a write node still files its broker proposal. Non-goals: an agent turn (AUTO-8), `schedule_run` (AUTO-1), an act classifier, Reach and `tell_me` (AUTO-6), a new subject kind or a migration, the decision card (ALN-2b) and the flag flip | HIGH | AGENT-SAFE. The flip of `AUTO_PARK_ORGS` waits for ALN-2b. Owner question before merge: one task for each parked gate lands in the owner's inbox |
| **AUTO-8a** (1392) | A headless turn with a credit cap, dark (§4.4). Needs AUTO-2, AUTO-7a. **Built, dark, no production caller, proven by tests** | A turn stops at its cap with `cap_reached` on the goal row and reports once. A run with no candidate makes no model call. R8: the run reads only what its member can read, and org B sees nothing. A kill switch stops all headless turns for an org. With no cost reader, no model call runs | HIGH | AGENT-SAFE. The flip of `HEADLESS_TURN_ORGS` is the owner's, because a turn spends credits |
| **AUTO-8b-1** (2194) | The settle of a round has a time limit, and each round checks the bound of the goal cap (§4.4). Needs AUTO-8a. **Built, dark, in review** | A meter that sleeps past `SETTLE_MARGIN_S` ends the round inside the margin. `record_round` runs, and the round charges the cap minus the credits used. A goal that another write raises over 50 credits between rounds gets no next round. R8: in the timeout case the goal of org A blocks with `cap_reached`, org B sees nothing, and the ledger row ends `ok` | HIGH | AGENT-SAFE. It adds no flag. The flip of `HEADLESS_TURN_ORGS` stays the owner's |
| **AUTO-8b-2** (2230) | The cost reader, a ceiling for each turn and the attribution. Needs AUTO-8b-1 and an owner decision | The Console has a door that reads the spend of one run, and the turn's meter calls it. A per-turn ceiling header can only lower `RunCeiling`. The attribution seam in `orchestrator/executor.py` makes the usage row of a turn name `auto.headless` | HIGH | Owner decision first. A Console route and a change of `RunCeiling` are the owner's |
| **AUTO-6** (1390) | Silent by default (§4.1). Needs AUTO-2 | A run that finds nothing writes no outbox row and no bell row. Ten runs that find the same thing give one message. Quiet hours hold. The fence goes red under a mutation | STD | AGENT-SAFE |
| **AUTO-1** (816) | "Do this every Monday": a `schedule_run` tool makes a workflow with one Mira step. Needs AUTO-2, AUTO-3, H-328, and a chat way to save a workflow | A request creates one automation, and the confirm card shows the next three run times. It delivers on Monday. It runs as the member who made it | STD | AGENT-SAFE. D145 preview. No second org before H-328 |
| **AUTO-4** (817) | One list of every automation, in My profile. Needs AUTO-1 | A member pauses an automation from the list, and the next run does not happen. An admin sees the org's automations | STD | AGENT-SAFE. D145 preview |
| **AUTO-5** (1389) | "Keep watching this for me": a watch from chat (§4.5). Needs AUTO-1, AUTO-6, AUTO-7, AUTO-8, AUTO-Q1, H-328 | "Tell me if any deal over 5 lakh goes quiet for a week" makes one watch. It stays silent while nothing happens, and it speaks once when the condition holds. A second member cannot see the watch. The cap refuses the next watch with a plain message | HIGH | AGENT-SAFE. D145 preview. No second org before H-328 |
| **AUTO-9** (1393) | The chase. Needs AUTO-5 and PZ-36 (task 1134) | A quiet task gets a nudge after the set time. An answer stops the chase. Three unanswered nudges tell the member once. A nudge never reaches someone who cannot open the task | STD | AGENT-SAFE. D145 preview |
| **AUTO-10** (1394) | What Mira already told me: one read tool over the Reach ledger and the run ledger. Needs AUTO-6 | A watch reports a change and not the same fact twice. "What are you watching?" lists the member's watches with their last result, and only the member's own runs | STD | AGENT-SAFE |

**The cap of AUTO-8 is the goal record.** WEB-10 is on main, so AUTO-8 writes its rounds and its credits through `goal_store.record_round`. A time cap stays in the run, because the goal record holds no clock.

### Build order and parallel work

**The order has nine steps.**

1. H-328, already in progress.
2. AUTO-7a, then AUTO-7b after ALN-2a.
3. AUTO-8a (on main) and AUTO-8b-1 (built), then AUTO-8b-2.
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

- `apps/services/gateway/gateway/routes/workflows/` (`scheduler.py`, `triggers.py`, `hooks.py`, `service.py`, `publish.py`, `tools.py`, and `park.py` of AUTO-7b)
- `apps/services/gateway/gateway/routes/projects/decisions.py` (`ask_decision`, `answer_decision`) and `personal.py` (`create_personal_task`, `announce=False`)
- The flag `AUTO_PARK_ORGS` (default empty, owner-only, reloads on SIGHUP): `packages/acb_common/acb_common/settings.py`, `env_guard.py` and `.env.example`. Its one reader is `park.park_allowed`, and `DECISIONS_ORGS` must list the org too
- `infra/postgres/132_workflows.sql`, `infra/postgres/313_unattended_ledger.sql`, `infra/postgres/271_reach_requests.sql`, `infra/postgres/66_pending_actions.sql`
- `apps/services/gateway/gateway/jobs/queue.py`, `jobs/worker.py`, `jobs/unattended.py`, and `jobs/headless_turn.py` of AUTO-8a (read on `2d2e71978`) and AUTO-8b-1 (read on `6c8d48d81`)
- `packages/acb_common/acb_common/goal_store.py` (`goal_member`, `get_goal`, `record_round`, `cap_block`) and `packages/acb_skills/acb_skills/subagent_contract.py` (`Job`, `bind_job`, `child_may_hold`, `child_pin`)
- The flag `HEADLESS_TURN_ORGS` (default empty, `*` allowed, owner-only, reloads on SIGHUP): `packages/acb_common/acb_common/settings.py`, `env_guard.py` and `.env.example`. Its one reader is `headless_turn.headless_allowed`
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
| An unattended run cannot approve its own action | AUTO-7a: `tests/unit/test_no_person_present.py`, `tests/unit/test_no_person_present_r8.py` (R8) and `evals/trajectories/test_no_person_trajectory.py`. A mutation that drops the mark turns it red |
| A parked gate writes one decision and no proposal (F1) | AUTO-7b: `tests/unit/test_auto_park.py`, `test_f1_*` (R8 and hermetic) |
| Off, a refusal, no owner or no org: the broker door of AUTO-7a, byte for byte (F2) | `test_auto_park.py`, `test_f2_*` |
| A run with no person cannot answer, and only the decision row decides (F3) | `test_auto_park.py`, `test_f3_*` (R8 and hermetic) |
| The decider is `workflows.owner_email` (F4) | `test_auto_park.py`, `test_f4_*` (R8 and hermetic) |
| The settle code writes no answer, calls no `answer_decision` and counts no person (F5) | `test_auto_park.py`, `test_f5_*` (source scans) |
| A yes resumes once, under two scans or a scan and the broker door (F6) | `test_auto_park.py`, `test_f6_*` (R8 races) |
| A no ends the run, and an expiry ends it by the DB clock and tells once (F7) | `test_auto_park.py`, `test_f7_*` (R8) |
| After a resume the run keeps its mark, and a second gate parks again (F8) | `test_auto_park.py`, `test_f8_*` (R8) |
| The new module needs no new allow-list entry in `test_no_person_present.py` (F9) | `test_auto_park.py`, `test_f9_*` |
| An unattended path sends only through Reach | New with AUTO-6: a scan in `tests/unit/test_unattended_silent.py` for a direct send, a bell write or a mail call in the new modules |
| A run with no candidate makes no model call | AUTO-8a: `tests/unit/test_headless_turn.py` and `tests/unit/test_headless_turn_r8.py` (R8) |
| A turn stops at its credit cap, writes `cap_reached` on the goal row and reports once | AUTO-8a: same two files. A second delivery reports nothing |
| A turn stops at its round cap and its time cap | AUTO-8a: `tests/unit/test_headless_turn.py` |
| The kill switch stops the next round | AUTO-8a: `tests/unit/test_headless_turn.py` |
| A turn holds read-only tools only | AUTO-8a: `tests/unit/test_headless_turn.py`, and `tests/unit/test_subagent_worker.py` for the pin of the worker |
| With no cost reader, no model call runs | AUTO-8a: `tests/unit/test_headless_turn.py` and `tests/unit/test_headless_turn_r8.py` (R8) |
| The headless handler runs with no person present | AUTO-8a: `tests/unit/test_no_person_present.py` |
| No credit stop inside one call | ADVISORY (R7), no fence. The goal cap is the cap. `RunCeiling` is a fixed tripwire of 500 credits for each run. AUTO-8b-2 adds a ceiling for each turn |
| The settle of a round ends inside `SETTLE_MARGIN_S`, and a meter that does not answer in time charges the rest of the budget | AUTO-8b-1: `tests/unit/test_headless_turn.py` and `tests/unit/test_headless_turn_r8.py` (R8) |
| The clock of the turn starts before `unattended.recover` | AUTO-8b-1: `tests/unit/test_headless_turn.py` |
| Each round checks the bound of the goal cap | AUTO-8b-1: `tests/unit/test_headless_turn.py` |
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

For AUTO-7b: `uv run pytest tests/unit/test_auto_park.py tests/unit/test_no_person_present.py tests/unit/test_workflows_start_run_org_fence.py -q`, with `TENANT_LADDER_DATABASE_URL` set for the R8 cases.

For AUTO-8a: `uv run pytest tests/unit/test_headless_turn.py tests/unit/test_headless_turn_r8.py tests/unit/test_no_person_present.py -q`, with `TENANT_LADDER_DATABASE_URL` set for the R8 cases.

For AUTO-8b-1: the AUTO-8a command, plus `tests/unit/test_job_effects_fence.py`, `tests/unit/test_background_ai_member.py`, `tests/unit/test_unattended_ledger.py` and `tests/unit/test_job_queue.py`.

---

## 12. Owner questions

**AUTO-Q1** (task 1395) was the one open question. The owner approved the plan on 2026-11-02 and left it open. **On 2026-11-06 the owner chose the recommended answer** ("go ahead with your recommendations for all the decisions"). D192 records it. The question task is Done, with the answer in a comment.

| Part | Question | The owner's answer (D192) |
|---|---|---|
| 1. The default | Does each member get proactive Mira on day one, or opt in? | **On, at the safest level.** My Day and Reach already speak within their caps. A standing watch exists only when a member asks for one, so nothing new runs by itself |
| 2. The cap | How many standing watches may a member have? | **10 for each member** at first. A higher number on a higher plan. WS-PLN owns the plan shape |
| 3. Who pays | A watch spends AI credits with no person present | **The member's allowance.** The confirm card shows "about X credits a month". Over the cap, the watch pauses and tells the member once |

**What the answer does not change.** Each slice ships dark. Each slice that changes what a member sees needs a D145 preview and the owner's approval. Each flag that spends credits stays the owner's flip. An eval over USD 0.50 still needs the owner's OK.
