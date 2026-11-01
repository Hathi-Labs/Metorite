# Project alignment — Metorite checks work against the project's rules (WS-ALN)

> **Product:** Metorite · **Feature:** the project of a task, the project's rules, and a check that asks a person · **Updated:** 2026-11-02 · **Owner:** vjvarada
> **Status:** 📋 **SPEC, NOTHING BUILT.** ALN-0 (this spec) is the first slice. ALN-1 is next. The owner did not answer ALN-Q1 to ALN-Q5, and told us to start. The build runs on the working defaults of §14.
> **Verified against code on 2026-11-02 at `ce29836ea`.** Each path in §11 was read on that commit. Earlier task text came from a read at `308df8828`, and §2 lists each fact that changed.
> **Board:** WS-ALN in `work_plan.md` §2.1. **Decision:** D150. D12, D75, D93, D100, D104, D105, D128, D131, D140, D145 and workflows G5 bind it.
> **Metorite:** project "Projects/Tasks App", epic 1368, this spec task 1369. The questions are tasks 1370 to 1374. The slices ALN-1 to ALN-14 are tasks 1375 to 1388, in order.
> **Research first:** the comment "Research notes, 2026-11-02 (project alignment)" on epic 1368, copied into §9.
> **Gate summary:** every slice is AGENT-SAFE. The flag that spends credits stays the owner's flip. An eval over USD 0.50 needs the owner's OK. Each slice that changes what a member sees follows the D145 loop.

---

## 0. Thesis

Many people create tasks and edit documents in Metorite. A product manager writes the architecture document, and other people do the work. The owner wants Metorite to notice when the work leaves the document.

**Metorite knows the project of each task.** Capture stays free of friction. Mira suggests the project, and the member accepts with one tap.

**Metorite checks work against the project's accepted rules.** A lead pins published pages to a project. Mira draws short rules from the pages, with a quote for each rule. The lead accepts each rule. The checker reads rules and never whole documents.

**Metorite asks, and never accuses.** When a task may deviate, the person who changed it hears first. The lead hears after that. A deviation has three answers: fix the work, accept an exception, or change the document. The third answer matters, because the document can be the stale one.

**Prevention comes before the alert.** Mira and agents read the active rules before they create or take work (ALN-6). That costs less than a check.

**The checker must earn the right to speak.** It runs in shadow first. An eval gate decides when it may speak. The owner flips it.

---

## 1. Scope and non-goals

### 1.1 In scope

1. **A durable trigger** (§5): a job for each task change and each page publish.
2. **A decision primitive** (§3.2): a question with named options, for a named person, on a task.
3. **A project suggestion** for captured work (ALN-3).
4. **A pin** of published pages to a project (§4.1) and **rules** drawn from them (§4.2).
5. **The checker** (§6) and its **findings** (§4.3).
6. **Two cards**, one for the person and one for the lead (ALN-9, ALN-10).
7. **The reverse check** (ALN-11): a changed page marks its rules, and open work gets a recheck.
8. **A view** of open findings, by project and by rule (ALN-13).

### 1.2 Non-goals

1. **No score for any person.** No table, no report and no view ranks or counts people (ALN-Q5).
2. **No check of a personal task.** A task in a member's personal project (`pm_projects.personal_owner`) is never read by the checker. A move into a project is the moment it becomes project work.
3. **No second rule engine.** §3.4 says why.
4. **No external document.** A Google or Notion page that `pm_task_refs` links is out. The checker quotes only what Metorite can read.
5. **No hard block at first.** ALN-12 is optional and waits on ALN-Q2.
6. **No grant from a rule.** A rule is guidance. D140.2 holds: the member is the only authority.
7. **No check of a comment** in v1. The title, the description and the acceptance text are in.
8. **No auto-move of a task** into a project (ALN-Q3).

---

## 2. What exists today

The facts of task 1369 were re-read on `ce29836ea`. Two of them changed.

| Piece | Where | State |
|---|---|---|
| The write handlers | `apps/services/gateway/gateway/routes/projects/tasks.py` (`create_task` line 913, `patch_task` line 1041, `move_task_in` line 1249) and `personal.py` (`create_personal_task` line 647) | The four seams. They serve people, chat and the MCP. Recurrence (`recurrence.py`) and the file import (`import_writer.py`) skip them |
| Generic row helpers | `routes/projects/core.py` (`insert_row` line 2157, `update_row` line 2169) | Generic. `update_row` has 19 files with a call and more `UPDATE pm_tasks` sites. They are not seams |
| The event bus | `core.py` (`emit`, line 3812) | Best effort. It logs a failure and raises none |
| The event payloads | `tasks.py` lines 1026 and 1184 | `pm.task.created` and `pm.task.updated` carry no `organization_id`. Only `pm.task.assigned` does. `move_task_in` emits `pm.task.moved` with the task id alone |
| A page publish | `routes/knowledge/pages.py` (`publish_page`, line 631) | It emits no event. An accepted proposal (`routes/knowledge/proposals.py`) writes a version with the reason `accept` and emits none |
| The job queue | `gateway/jobs/queue.py` (`enqueue` line 258), `worker.py` (`register` line 178) | A tenant-bound, deduped queue. Default OFF: `worker_enabled()` reads the setting `job_queue`. Only the brief registers kinds (`brief/run.py`) |
| The unattended guard | `jobs/unattended.py` (`run_guarded`, line 369) | Built by AUTO-2 in migration 313, dark. It gives at-most-once runs and a stall stop |
| Background AI | `gateway/decide_features.py` (`ask` line 899, `mode_for` line 464), `crm_sync/note_labels.py` | `mode_for` reads one org list for each prefix: `my_day.` and `crm.` have their own. `crm_sync/note_labels.py` is the model: a body hash, a daily cap in Redis (`reserve_request`), a cool-down and `job_member_scope` |
| The bell | `routes/projects/notifications.py` (`NOTIFICATION_KINDS` line 70, `deliverable` line 177, `notify` line 293) | Five kinds: `assigned`, `mention`, `comment`, `nudge`, `agent_question`. `deliverable` drops a recipient who cannot open the task |
| My Day acts | `routes/shell/needs.py` (line 266) | "Every A* act is a link until MD-8". MD-8 has no task |
| Reach | `gateway/reach/requests.py` (`tell_me` line 208), `reach/classes.py` (`REACH_CLASS` line 23) | Reach is the only path to push. `SOURCES` is `email.rule`, `chat` and `workflow` |
| A typed question | `routes/projects/agent_runs.py` (`ARCHIVE_OPTIONS` line 319) | The nearest shape: two options for the member who owns the run. It has no named approver |
| A project suggestion | `routes/tasks/ai.py` (`_suggest_project`, line 151) | A keyword match on the member's own personal tree only |
| Task context for agents | `apps/services/agent_edge/agent_edge/tools.py` (`get_task_context`, line 2865) | It returns the task, parents, project with description, blockers, subtasks, runs, refs and 20 comments. It returns no rule and no guidance |
| Documents | `infra/postgres/272_knowledge_pages.sql` | `kb_pages` has `state` (`draft`, `published`, `archived`). `kb_links` allows `target_kind = 'project'`, and its only writer (`routes/knowledge/backlinks.py`) writes `page` targets |
| The store of rules | `project-docs/specs/agent_coordination.md` line 445 (SW-31, task 1048) | **Not built.** No `pm_agent_guidance` exists in any `.sql` file |
| The lead | `infra/postgres/146_projects.sql` line 64 (`pm_projects.lead`) | One email, nullable. `routes/projects/authority.py` counts the lead of any ancestor project. No product manager role exists |

**⚠️ Two changes since the task text.**

1. **SW-31 is a planned spec, not code.** ALN-5 cannot extend a table that does not exist. §4.2 fixes this: the first slice to need the table builds it in one shape.
2. **`kb_links` has no version column and no state.** KG-4 (not built) adds a `relation` and a `removed_at`, and still no version. So the pin of §4.1 is its own small table.

---

## 3. The design

### 3.1 Five parts

1. **A trigger that does not miss** (§5, ALN-1). Events are best effort and name no tenant. So the check runs from a durable job.
2. **Know the project** (ALN-3). A task in the personal inbox gets one suggestion with a reason. It extends LP-2 (task 989) and merges SW-35 (task 1052). One engine only.
3. **Name the source of truth** (§4, ALN-4 to ALN-6). A lead pins pages. Mira proposes rules. The lead accepts each rule.
4. **Check cheaply, and prove it** (§6, ALN-7 and ALN-8). One `decide` call for each changed task. The server verifies the quote. Shadow first, then an eval gate.
5. **Ask a person** (§7, ALN-2, ALN-9, ALN-10). The person hears first, then the lead. Each answer goes on the record.

### 3.2 One person decides (ALN-2)

Nothing today shows a question with named options to a named person, and records the answer on a task. ALN-2 adds that primitive. The shape is a new table `pm_decisions`, in migration 326 (recheck the number at dispatch, R1). `pm_intake` has fixed verbs, no named decider and no options. An `agent_runs` elicitation needs a run. The table reuses the bell, Reach and the My Day provider registry, and it runs no action. So it is not a third approval stack.

| Column | Meaning |
|---|---|
| `subject_kind`, `subject_id` | `task` or `page`, and its id |
| `decider` | The email of the named person |
| `options` | Two to four objects `{key, label}` |
| `question` | At most 140 characters |
| `expires_at` | Default 7 days after the ask |
| `payload` | JSONB. It serves SW-18 and Studio B2c |
| `answered_by`, `answer_key`, `answer_reason` | Nullable until the answer. The reason is optional |
| `source` | `align` or `auto` |

A unique guard allows one answer for each decision. The table is tenant-scoped with FORCE RLS (R5). It widens `pm_notifications_kind_check` with the kind `decision`.

A decision has a subject (a task or a page), a named decider, two to four options, a one-line question, an expiry and an answer. The answer writes one `pm_activities` row. A second answer is refused. AUTO-7 (task 1391) parks a blocked action on the same primitive.

### 3.3 Three answers to a deviation

| Answer | Who | What happens |
|---|---|---|
| **Fix the work** | The person | The finding closes when the next check finds no conflict |
| **Accept an exception** | The lead | The finding closes. The task shows the exception with its reason. The same finding does not return for the same text and rule version |
| **Change the document** | The lead | A new task goes to the page owner. The rule gets the state `needs review` until the page changes |

The person has a fourth path: "It is right, here is why". The reason goes to the lead. It is not an answer. It asks for the lead's answer.

### 3.4 Why this is a `decide` feature on a job, and not a second rule engine

Workflows G5 (`workflows_app.md` line 42) says new automations land in Workflows, and not in new bespoke rule engines. This feature obeys G5 for these reasons.

1. **It is not a rule engine.** A rule engine runs a condition that a person wrote. Here, a model judges free text against a sentence, and the server only verifies the quote. No condition language exists, and no one writes a trigger.
2. **It is a `decide` feature** (D75, D93, D100). The registry in `decide_features.py` holds it as `projects.align`. It has a mode (`off`, `shadow`, `on`), an org list, a cool-down and a log. Email triage, My Day and the CRM note labels use the same seam.
3. **It runs as a job.** The queue gives a durable trigger, a tenant binding, a dedupe key and a retry. A workflow trigger gives none of them for a task change. `emit` is best effort. H-328 (task 856) shows that workflow runs bind no tenant.
4. **A workflow can still use it.** A later workflow node may read a finding. Nothing here blocks that.

If the owner wants a hand-written rule ("a task in Billing needs a due date"), that is a Workflows automation. It is not this feature.

---

## 4. Data shapes

Every table is tenant-scoped with FORCE ROW LEVEL SECURITY (R5), and every migration is expand only (R6). Migration numbers are taken at build time (R1). Migration 324 is the highest on `ce29836ea`.

### 4.1 The pin of a page to a project (ALN-4)

A new table `pm_project_authorities`.

| Column | Meaning |
|---|---|
| `id`, `organization_id` | Key and tenant. The tenant defaults to the bound tenant |
| `project_id` | The `pm_projects` node that the page governs. A space or a project |
| `page_id` | The `kb_pages` row. It must be `published` at the pin |
| `page_version` | The version of the page at the pin. The quotes of §4.2 point at this version |
| `pinned_by`, `pinned_at` | Who pinned, and when. An email, from the session |
| `state` | `active`, `stale` or `removed`. `stale` means the page moved to `archived` or has a newer version than the rules know |
| `removed_by`, `removed_at` | Nullable. A removal keeps the row |

One active pin for each project and page. A project lead, or a member with `projects:settings:write` (`core.py` line 703), pins and removes.

**The leak check at the pin.** A page with narrower grants than its project would leak its text through the quotes of a finding. The pin refuses that case. A lead must widen the page, or choose another page. This is the safer of the two options in task 1378. The warning option can come later.

KG-4 may mirror each pin as a `governs` relation for graph reads. The pin table stays the authority.

### 4.2 The rule (ALN-5)

SW-31 plans `pm_agent_guidance`: one markdown block for each scope and version. A project needs many rules, each with its own history. So the store gets one shape for both.

**What SW-31 must add** (task 1048 changes, or ALN-5 builds the table in this shape if SW-31 has not):

| Column | Meaning |
|---|---|
| `kind` | `guidance` (default) or `rule` |
| `rule_key` | A UUID. It stays the same across the versions of one rule. NULL for guidance |
| `source_page_id`, `source_page_version` | Nullable. The page and the version that the rule came from. NULL for a rule that a lead typed |
| `quote` | The exact words of the page. At most 400 characters. NULL for a typed rule |
| `level` | `soft` or `hard` |
| `state` | `proposed`, `active`, `needs_review` or `retired` |
| `proposed_by` | `mira` or an email |

The unique index of SW-31 is (org, project, version). That index would force one rule for each project. It must become (org, project, `kind`, `rule_key`, `version`), with `rule_key` NULL for guidance. Guidance keeps its limit of 8,000 characters. A rule body is one sentence of at most 300 characters.

**Rules.**

1. Only a lead (or a member with `projects:settings:write`) makes a rule `active`.
2. Mira proposes. A proposal waits for the lead (D104: an agent never publishes).
3. The server checks that `quote` occurs in the text of (`source_page_id`, `source_page_version`). A proposal with no real quote is refused.
4. A page gives at most 12 proposals, and a project at most 40 active rules. A longer list is noise.
5. Page text is untrusted input. The proposer gets no tools.
6. The nearest rule wins on a conflict, as SW-31 plans. A rule on a child project beats a rule on its parent.
7. Rules return to agents in their own key (`rules`) beside `guidance`, so the 8,000-character block of guidance stays one block.

### 4.3 The finding (ALN-7)

A new table `pm_alignment_findings`.

| Column | Meaning |
|---|---|
| `id`, `organization_id` | Key and tenant |
| `project_id` | The project of the task at the check |
| `subject_kind` | `task` in v1. `pr` joins with ALN-14 |
| `subject_id` | The task id. A foreign key with ON DELETE CASCADE |
| `rule_id`, `rule_version` | The row of §4.2 that the check used |
| `content_hash` | The hash of the title, the description and the acceptance text at the check |
| `verdict` | `deviates` or `unclear`. An `aligned` check writes no row |
| `task_quote` | The exact task words that conflict. At most 300 characters. The server proved that they occur |
| `state` | `open`, `fixed`, `accepted` or `dismissed` |
| `visibility` | `shadow` or `live`. A `shadow` row reaches no one |
| `raised_at`, `surfaced_at` | When the check made the row, and when a person first saw it. NULL while `shadow` |
| `answered_by`, `answered_at`, `answer_reason` | Nullable. The reason is at most 500 characters, and a person wrote it |
| `decision_id` | Nullable. The ALN-2 decision that asked |

**Indexes.** A partial unique index on (org, `subject_id`, `rule_id`) WHERE `state = 'open'` keeps one open finding for each task and rule. An index on (org, `project_id`, `state`) serves the view.

**A dismissal stands.** A new check skips a task when a row with `state` in (`dismissed`, `accepted`) holds the same `content_hash` and `rule_version`. It runs again only when the task text or the rule changes.

**No person column except `answered_by`.** There is no `raised_for` and no counter by person. This is the data form of ALN-Q5. `answered_by` exists for the record on the task, and no query groups by it.

**The purge.** The member purge and the project delete handle the table. The tenant purge list names it.

---

## 5. The trigger seam (ALN-1)

**The seam is the four handlers**, because no single write seam exists. The D80 fence (`tests/unit/test_pm_task_insert_sites.py`) holds the creators. ALN-1 does not hook `insert_row` or `update_row`.

1. In `create_task`, `patch_task` and `move_task_in`, the handler enqueues one job of kind `align.check` with `jobs.queue.enqueue(db, ...)`. It uses the same tenant session that the handler holds. The enqueue runs in the same transaction as the write, inside a savepoint (`db.begin_nested()`). A crash between a commit and a later enqueue would lose the job, and this form cannot. `create_personal_task` is not in the list, because a personal task is never checked (§1.2). The payload holds the task id only and no org, because the session binds the tenant. The dedupe key is `align:<task_id>:<content_hash>`, which fits the 200-character limit.
2. `patch_task` enqueues only when the hash of the title, the description and the acceptance text changed. An edit of a due date enqueues nothing.
3. A move out of the personal root (`move_task_in`) enqueues, because that is the moment a personal note becomes project work.
4. The page signal is not part of ALN-1. Pins arrive with ALN-4, so the `kb.page.published` job for each pinned project belongs to ALN-5 or ALN-11. With no pin, a publish enqueues nothing.
5. Recurrence and the file import stay out in v1. A recurring copy repeats text that was checked. An import is a bulk load that an owner would not want to check. Both are stated here so a later slice can reverse the choice.
6. ALN-1 does not stamp `organization_id` on the events. H-328 part (c) owns that stamp, once, in `routes/projects/core.py` (`emit`). No check depends on the bus.
7. A failed enqueue never fails the member's write. The savepoint rolls back, and the handler swallows the error, logs it and goes on.
8. The queue is dark: `job_queue` is OFF by default. A new per-org flag `ALIGN_JOBS_ORGS` (default empty) gates the enqueue. A handler enqueues only when `worker_enabled()` is true AND the org is in the list. The reason: `prune` deletes only `done` and `failed` rows, so queued rows would grow without a bound while the worker is off. ALN-1 adds the flag to the settings list.
9. `align.check` has no handler until ALN-7, and the worker claims only registered kinds. So ALN-1 registers a stub handler that completes the job and does nothing else. ALN-7 replaces it.

---

## 6. The checker (ALN-7, ALN-8)

### 6.1 The flow

1. The worker claims an `align.check` job. The handler is registered in `detached` mode with `idempotent=True`, because a model call needs `detached`. It reads an intent mark first, so a second try is safe.
2. It loads the task. It stops if the task is in a personal project, has no project rules, or the project is paused.
3. It computes the content hash. It stops if a finding of §4.3 already stands for that hash and rule version.
4. It reserves one call of the org's daily cap in Redis (`reserve_request`, as `note_labels.py` does). Over the cap, the job re-queues for the next day and is never lost.
5. It binds the payer with `job_member_scope` (§6.3) and calls `decide_features.ask("projects.align", ...)`. The question is a `ChoiceQuestion` with the answers `aligned`, `unclear` and `deviates`, and the id of the rule that conflicts.
6. The server verifies the answer. The quoted task words must occur in the task. The rule id must be an active rule of the project. No proof, no finding.
7. In `shadow` mode it writes a row with `visibility = 'shadow'` and tells no one. In `on` mode it writes a `live` row and starts ALN-9.

### 6.2 Mode and the flag

The mode order is `off`, `shadow`, then `on` (D75.7, D100.2). `mode_for` needs a new branch: a `projects.` prefix that reads a new setting `projects_decide_orgs`, as `crm.` reads `crm_decide_orgs`. The feature goes in `REGISTERED`. **The flag spends credits, so the owner flips it** (CLAUDE.md §3a rule 3). ALN-8 sets the gate for `on`.

### 6.3 Who pays and the cap

ALN-Q4 sets the payer. The working default is the org's AI allowance. `job_member_scope` needs a member from our own tables, never from request input. ALN-7 uses the nearest project lead. With no lead, it uses the first org admin. ALN-7 names the exact read.

The cap is a working default of **300 checks for each org each day**, in Redis, and an admin can see the count. ALN-7 measures the real cost in a week of shadow data on our own org, and the owner then sets the number.

**Order of cost.** The cheap checks come first: no rules, a personal task, an unchanged hash, a dismissal that stands. Each one stops before any model call.

---

## 7. Who hears, who sees, and who is "the lead"

### 7.1 Who hears (ALN-Q1)

The working default: the person hears first. The lead hears after 2 working days with no action, or at once when the rule is `hard`. "The person" is the member who last changed the task, or its assignee when the change came from an agent.

### 7.2 Visibility

1. **A card never shows a task or a page to someone who cannot open it.** Each recipient passes `deliverable` (`notifications.py` line 177).
2. **A member who cannot open the rule's page** sees the rule text and not the page quote.
3. **The lead outside the task's grant** gets no card and no title. A project admin gets one notice that a finding waits, with no text.
4. **A personal task** has no lead and gets no check.
5. **The view** (ALN-13) shows a member only the projects and tasks that member can open.

### 7.3 Finding "the lead"

`pm_projects.lead` is one email, and it can be empty. A leaf project often has none. The rule:

1. Start at the project of the task. Use its `lead` if set.
2. Else walk up to the nearest ancestor with a `lead`. `authority.py` counts the lead of any ancestor too.
3. If the chain has no lead, notify the members who hold `projects:settings:write`. They get one notice to set a lead. It names the project and no task.
4. The lead is a stored fact, read in the handler. It is never request input.

There is no product manager role. A project lead is the role in v1. A later slice may add a role, and this rule then reads it first.

### 7.4 The channels

A card is a bell row, a My Day card and one Reach request. Reach is the only path to push, so its daily cap, quiet hours and dedupe all apply. ALN-2a adds one bell kind (`NOTIFICATION_KINDS` and its CHECK, by migration) and one `REACH_CLASS` row. ALN-2b adds one My Day provider with a two-answer act. The act is a link until MD-8, as `needs.py` says, so ALN-2b opens the task card for the answer.

---

## 8. Safety

1. **Task and page text is untrusted.** The checker, the proposer and the project suggester get no tools. An instruction in a task or a page is content, never an order. Each has an injection fixture that makes no tool call.
2. **The server verifies each quote.** A task quote must occur in the task text. A rule quote must occur in the page version. A model cannot invent either.
3. **Stored text is small.** A finding holds option keys, a rule id and a short quote. It holds no whole task and no whole page.
4. **A failed check never blocks work.** A model error, a cap or a cool-down leaves the task alone. This holds for ALN-12 too.
5. **No score for any person** (§1.2, §4.3). A fence reads the schema.
6. **An org admin can turn the feature off,** and a project can pause its checks.
7. **Copy says "may not match".** It never says the person is wrong. The card says why, and what each answer does.
8. **Money and people.** The feature sends no mail to an outside person and moves no credit. The credit-spending flag stays the owner's flip.

---

## 9. What other apps do

The research is from 2026-11-02. It used vendor docs and third-party posts. The line "not verified" marks what no official page confirmed.

| App | What it does | We take | We do not take |
|---|---|---|---|
| **Linear, Triage Intelligence** | Compares each triage issue with the workspace history. It proposes team, project, assignee and labels, and it can auto-apply. A person can see why. Admins add guidance at each level | Suggest with a reason, one tap, guidance for each project (ALN-3, LP-2) | Auto-apply by default. A move shows the task to a whole project (ALN-Q3). Not verified: its model and its scoring |
| **CodeRabbit, linked-issue assessment and pre-merge checks** | Reads the linked issue, compares the code, and flags unmet requirements and work beyond the scope. Each check runs in off, warning or error mode. Error mode can block a merge | The three modes (ALN-Q2) and the check of a pull request against its task (ALN-14). Not verified: whether it reads architecture documents | A block by default |
| **Atlassian Rovo, and AI Checklists for Jira** | No built-in Rovo feature checks a work item against a Confluence page. A custom agent can read both. A paid app reads acceptance criteria from a Confluence page and flags a mismatch when the page changes | The reverse direction: a changed document flags open work (ALN-11). Not verified: the current Atlassian product, because the sources are community posts | — |
| **Asana, Goals and AI Studio** | Goals carry a status and one owner. An AI health check names what is not on track. AI Studio flags risk and slippage | One accountable owner, and a project-level view (ALN-13). Not verified: who receives an AI Studio alert | The status of a goal as a signal. It says nothing about whether work follows a document |

**What this shows.** The "which project" half exists elsewhere, and two tasks here plan it. The check against a project's documents is new. We found no product that does it for ordinary work. The closest match works on code.

**Not researched.** Laws on employee monitoring in India or elsewhere. ALN-Q5 sets the product line, and a customer may still need legal advice.

Sources: `https://linear.app/docs/triage-intelligence` · `https://docs.coderabbit.ai/guides/linked-issues` · `https://docs.coderabbit.ai/pr-reviews/pre-merge-checks` · `https://asana.com/product/ai/ai-studio` · `https://stiltsoft.com/?p=16135`.

---

## 10. Phases and slices

**Tier** is the review tier of `next-ticket.md` step 1b. Every slice ships dark unless the row says otherwise. Migration numbers are taken at build time (R1). A slice that changes what a member sees follows D145: preview, owner approval, then the gates.

### Phase 0 — The spec

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-0** (task 1369) | This spec, D150, the board row | `spec-auditor` returns GO for ALN-1. `test_index_completeness.py` and `test_decision_ids.py` pass | LOW | AGENT-SAFE |

### Phase 1 — Foundations

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-1** (1375) | The durable trigger (§5), the flag `ALIGN_JOBS_ORGS` and a stub `align.check` handler. Needs ALN-0 | R8: a task change enqueues one job in the bound org, and the stub completes it. With the worker off or the org not listed, no row appears. An edit with the same text enqueues none. Org B sees none of org A's jobs. A failed enqueue never fails the write. With the flag off, no job row appears | HIGH | AGENT-SAFE |
| **ALN-2a** (1376) | The table, the service (ask, answer, expire), the activity row, the bell kind, the verb, `REACH_CLASS` and `KIND_ORDER` (§3.2). Flag `DECISIONS_ORGS` (default empty). Needs ALN-0 | R8: a decision reaches its named person, and an answer writes one activity. A person outside the grant gets no row. A second answer is refused. An expired decision takes no answer | HIGH | AGENT-SAFE |
| **ALN-2b** (1376) | The My Day provider and the task-card UI. `needs.py` offers only a link act until MD-8, so the act opens the task card. Needs ALN-2a | Visual review in four contexts. D145 preview first | STD | AGENT-SAFE |

### Phase 2 — The source of truth

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-4** (1378) | The pin (§4.1). Needs ALN-0 | R8: a lead pins a page. A member with no authority gets 403. Org B sees none of it. A page of another org cannot be pinned. A page with narrower grants is refused. An archived page shows the pin as `stale` | HIGH | AGENT-SAFE |
| **ALN-5** (1379) | The rule store (§4.2) and the proposer. Needs ALN-4 | R8: only a lead makes a rule active. Org B reads none. A proposal with an invented quote is refused. On a real architecture document, a lead accepts at least half of the proposals, and the report names the number | HIGH | AGENT-SAFE |
| **ALN-6** (1380) | Follow the rules first. Needs ALN-5 | An agent that claims a task in a project with 3 rules gets all 3 in `get_task_context`. A project with no rules and a rule on a parent project both pass a test. New chat tools have a `skill_projects/manifest.py` row, and new edge routes are in `MCP_WRITE_ROUTES` | STD | AGENT-SAFE |

### Phase 3 — The silent check

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-7** (1381) | The checker in shadow (§6). Needs ALN-1, ALN-5 | R8: org B reads no finding of org A. Unchanged text makes no model call. An injection fixture makes no tool call. A week of shadow data on our own org gives the rate of findings and the cost for each task | HIGH | AGENT-SAFE. The flag is the owner's flip |
| **ALN-8** (1382) | The eval gate. Needs ALN-7. Folder `evals/project_align/` | At least 60 labelled cases. The gate number is in this spec before the run. The result cannot move it | STD | **OWNER-GATE** for a run over USD 0.50 |

### Phase 4 — The people

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-9** (1383) | The person's card. Needs ALN-2a, ALN-2b, ALN-8 | One deviation gives one card, however often the task is saved. A member who cannot open the page sees the rule and not the quote | STD | AGENT-SAFE. D145 preview first |
| **ALN-10** (1384) | The lead's card (§3.3, §7.3). Needs ALN-9 | R8: an answer closes the finding and writes one activity. A second answer is refused. A lead outside the grant gets no card and no title. An accepted exception does not raise the finding again | STD | AGENT-SAFE. D145 preview first |
| **ALN-3** (1377) | The project suggestion. Needs LP-2 (task 989) | An eval set of 30 items passes its written gate. A member never sees a project they cannot open. An injection fixture makes no tool call | STD | AGENT-SAFE. Waits for LP-2 |

### Phase 5 — The loop

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-11** (1385) | A changed document rechecks open work. Needs ALN-1, ALN-5, ALN-8 | A page publish makes no model call until a lead confirms the changed rule. A rule change on a project of 300 tasks stays inside the daily cap. The digest names only tasks the lead can open | STD | AGENT-SAFE |
| **ALN-13** (1387) | The alignment view. Needs ALN-10 | The panel reads for a lead and for a member, each with only what they can open. A rule with a high dismissal rate goes quiet and the lead gets one note. No view ranks a person | STD | AGENT-SAFE. D145 preview first |

### Phase 6 — Optional

| Id | What | Done when | Tier | Gate |
|---|---|---|---|---|
| **ALN-12** (1386) | The optional gate on Done. Needs ALN-10, and ALN-Q2 must choose it | R8: a task with an open hard finding cannot move to done. After the lead's answer it can. With the checker off or over its cap, the move passes | HIGH | AGENT-SAFE. Build only if the owner picks option A or C |
| **ALN-14** (1388) | Check an agent's plan and a linked pull request. Needs ALN-8, SW-18 (task 349) | A pull request that names a rule's forbidden approach raises one finding. A run with no pull request raises none | STD | AGENT-SAFE |

### Build order and parallel work

**The order** has seven steps.

1. ALN-0.
2. ALN-1, ALN-2a and ALN-2b (after ALN-2a).
3. ALN-4, ALN-5 and ALN-6.
4. ALN-7 and ALN-8.
5. ALN-9, ALN-10 and ALN-3.
6. ALN-11 and ALN-13.
7. ALN-12 and ALN-14.

**What can run in parallel.**

1. ALN-1, ALN-2a and ALN-4 need only ALN-0. They touch different files, so three sessions can build them at once.
2. ALN-6 and ALN-7 can run in parallel after ALN-5 and ALN-1.
3. ALN-3 needs LP-2 only. It can run at any time after LP-2 lands.
4. ALN-11 and ALN-13 can run in parallel after ALN-10.
5. Keep three or four slices in flight at most (CLAUDE.md §4).

**The first dispatchable slice is ALN-1,** once this spec merges. ALN-2a and ALN-4 are dispatchable at the same time. ALN-1 comes first because ALN-7 and ALN-11 read through it.

**⚠️ An outside prerequisite.** ALN-5 needs the rule store of SW-31 (task 1048). If SW-31 has not built the table, ALN-5 builds it in the shape of §4.2. SW-31 then adds only its editor and its MCP return.

---

## 11. Current file paths

Each path was read on `ce29836ea`.

- `apps/services/gateway/gateway/routes/projects/tasks.py` (`create_task`, `patch_task`, `move_task_in`)
- `apps/services/gateway/gateway/routes/projects/personal.py` (`create_personal_task`)
- `apps/services/gateway/gateway/routes/projects/core.py` (`insert_row`, `update_row`, `emit`, `SETTINGS_WRITE`)
- `apps/services/gateway/gateway/routes/projects/notifications.py` (`NOTIFICATION_KINDS`, `notify`, `deliverable`)
- `apps/services/gateway/gateway/routes/projects/intake.py`, `candidates.py`, `landing.py`, `custom_fields.py`, `authority.py`, `agent_runs.py`
- `apps/services/gateway/gateway/routes/tasks/ai.py` (`_suggest_project`)
- `apps/services/gateway/gateway/routes/shell/needs.py`, `routes/shell/feed_decide.py`
- `apps/services/gateway/gateway/reach/requests.py` (`tell_me`), `reach/classes.py` (`REACH_CLASS`)
- `apps/services/gateway/gateway/jobs/queue.py`, `jobs/worker.py`, `jobs/unattended.py`
- `apps/services/gateway/gateway/decide_features.py`, `crm_sync/note_labels.py`
- `apps/services/gateway/gateway/routes/knowledge/pages.py` (`publish_page`), `routes/knowledge/proposals.py`, `routes/knowledge/backlinks.py`, `kb_index/query.py`
- `apps/services/agent_edge/agent_edge/tools.py` (`get_task_context`, `_create_task`)
- `infra/postgres/146_projects.sql`, `147_projects_personal.sql`, `255_pm_task_refs.sql`, `272_knowledge_pages.sql`
- `project-docs/specs/agent_coordination.md` (SW-31), `workflows_app.md` (G5), `knowledge_graph.md` §3.2 (KG-4), `proactive_reach.md`
- `tests/unit/test_pm_task_insert_sites.py` (the D80 fence)

---

## 12. Verification commands

```bash
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"   # R8 needs a database
uv run python scripts/affected_tests.py --run
uv run pytest tests/unit/test_align_trigger.py tests/unit/test_align_findings.py -q
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_pm_task_insert_sites.py -q
uv run ruff check apps/services/gateway apps/services/agent_edge
bash scripts/ci_local.sh
```

For this spec only: `uv run pytest tests/unit/test_index_completeness.py tests/unit/test_decision_ids.py tests/unit/test_board_index.py -q`.

---

## 13. Fences (R7)

| Rule | Fence |
|---|---|
| A task write enqueues a check, and an unchanged text enqueues none | New with ALN-1: `tests/unit/test_align_trigger.py` (R8) |
| No hook sits on `insert_row` or `update_row` | `tests/unit/test_pm_task_insert_sites.py`, plus a scan in the new file |
| A new table is tenant-scoped | `tests/unit/test_tenant_coverage.py` |
| A rule is active only by a lead, and its quote exists in the page version | New with ALN-5: `tests/unit/test_project_rules.py` (R8) |
| A finding has a real task quote, and a dismissal stands | New with ALN-7: `tests/unit/test_align_findings.py` (R8) |
| No score for any person: no table or column of this feature groups by a person | New with ALN-7: a schema scan in `test_align_findings.py`. Advisory for the views until ALN-13 |
| A personal task is never checked | New with ALN-7: same file |
| The checker, the proposer and the suggester get no tools | New with ALN-5 and ALN-7: injection fixtures that assert no tool call |
| A card reaches only a person who can open the task | New with ALN-2a: `tests/unit/test_decisions.py` (R8) |
| A proactive path sends only through Reach | New with ALN-9: a scan for a direct send in the new modules |
| A failed check never blocks a move | New with ALN-12: same fence as the hard rule |

---

## 14. Owner questions

On 2026-11-02 the owner approved the full project plan and told us to start. The owner left these five questions open. Each answer below is a **working default, not an owner decision**. The build uses it. The question task stays open. If the owner changes an answer, this spec changes with it.

| Id | Question | Working default, not an owner decision |
|---|---|---|
| **ALN-Q1** (1370) | Who hears first when work deviates? | **The person hears first** (option A). The lead hears after 2 working days, or at once for a hard rule. Sets ALN-9 and ALN-10 |
| **ALN-Q2** (1371) | Can a deviation stop the work until the lead approves? | **Warn by default** (option A). A lead can mark one rule "needs approval". Then ALN-12 holds a move to Done. Never at capture. A block needs the gate of ALN-8 first |
| **ALN-Q3** (1372) | May Metorite move a task into a project by itself? | **Suggest only** (option A). One tap accepts. Sets ALN-3 |
| **ALN-Q4** (1373) | Who pays for the checks, and what is the daily cap? | **The org's AI allowance**, under a daily cap for each org that an admin can see (option A). The first number is 300 checks. Text that did not change is never checked twice. A check over the cap waits for the next day. ALN-7 measures the real cost first |
| **ALN-Q5** (1374) | Does Metorite ever keep a score for each person? | **No** (the recommended line). Personal tasks are never checked. No report ranks people. Counts are for each project and each rule. An org admin can turn the feature off. Binds ALN-7, ALN-10 and ALN-13 |

**What the working defaults do not change.** Each slice ships dark. Each slice that changes what a member sees needs a D145 preview and the owner's approval. The flag that spends credits stays the owner's flip. An eval over USD 0.50 needs the owner's OK.
