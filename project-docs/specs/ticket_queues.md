# Tickets and queues: a support queue on the one task store, fed by mail, chat and a Studio app

**Status: PROPOSED, 2026-11-16. Verified against the code on 2026-11-16
(`origin/main` at `471704477`).** This is the spec of epic E21, task 2202
("Ticketing and support"). Its workstream id is WS-TKT, and TKT-0 is task
3492. Nothing is built. The owner decides D-TKT-1 to D-TKT-6 (§9, task 3536). A slice
that a decision blocks waits for the answer. It does not build on a default.

**The seam owners.** This spec adds no second store, engine or event path. It
extends these owners:

| Topic | Owner | What this spec adds |
|---|---|---|
| The task store, task types, custom fields | `project_management_app.md` §12 (D52 to D54) | A queue is a project, and a ticket is a task of the type Ticket (§3.1) |
| Intake: capture, triage, accept | `project_management_app.md` §9.1, `routes/projects/intake.py` | Intake is the one entry of a ticket. A queue may accept at capture (§3.2) |
| Teams | E7 (task 2530), T-0 (task 2531): "a team is a group" | The queue names one group as its pool (§3.3) |
| The shared mailbox | `shared_mailbox.md` (WS-65) | A mailbox rule opens a ticket (§3.4). SM-5 copies the ticket's assignee (§3.8) |
| The mail rule engine | `email_app_master_plan.md`, `routes/email/automation/` | One action, "Open a ticket" (§3.4) |
| The workflow engine and its events | `workflows_app.md` §14 (OTW), `ai_studio.md` E3, E12, WFC | One node, "Open a ticket" (§3.5). The mail event stays OTW-6 |
| Studio apps and the door | `ai_studio.md` §3, `app_manifests/projects.py` | Task ops on the Studio door, and a view over the tasks of a project (§3.6) |
| The chat reach of an app | chat-reach, task 3440, and tasks 3493, 3494 | Nothing. This spec names them as dependencies (§3.7) |

**Owner ask, 2026-11-16, in chat.** The words are the owner's, so they sit in
a block that the linter does not read.

```text
"One of the use cases that I wanted to plan for and ensure that our AI Studio
is able to build it out is proper connections with the rest of the
communication surfaces (so that we can say "add tasks" or "assign tasks to
people" via the custom apps that we created). For example, if we create an AI
Studio app for creating support tickets, and if we get an email from a user
that is classified as support, then we should be able to automatically open a
ticket by creating, say, a workflow or an email rule. The assignment also will
happen according to the number of people that are assigned to the after-sales
or the support department in a non-random fashion. To figure out what is
lacking in our current architecture to enable this and add it to the plan."
```

## 0. The answer in one screen

Today no part of this flow runs from end to end. Each step of it stops at a
gap:

1. **A Studio app cannot touch a task.** The task ops of Projects are
   `off_door=TYPED`. So no Studio action or view can create, assign, list or
   read a task. No slice owns this.
2. **The Support desk template keeps a second ticket store.** It ships a
   `ticket` object in `studio_records`. The rule is "a ticket is a task type".
3. **No part of the product assigns work by itself.** D-PC-13 says "the AI
   suggests and never assigns". No round robin, no picker and no workflow
   node that sets an assignee exist.
4. **A mail cannot open a ticket.** The mail rule engine has no task action.
   No mail event binds an org, so no workflow can start on a mail.
5. **Mail is private to each member.** The shared mailbox is built in part
   and is dark. No decision lets a private mailbox feed an org queue.
6. **A workflow cannot write a task.** Each door refuses a `pm_task` step
   until the owner answers Q-d (WFC-4a-3, task 3306).
7. **A customer reply does not join its task.** The thread link syncs only
   the Done state.

The plan, as a ladder (§8). Each rung ends in a demo on production.

- **Rung 1, chat.** A support lead makes a queue and its rule in chat. Each
  ticket opened in chat goes to the next member of the support team.
- **Rung 2, mail.** A mail to the shared support address opens a ticket,
  assigned by the queue's rule. The customer's reply joins the ticket.
- **Rung 3, Studio.** Studio builds the support desk as an app over the task
  store. "Add a ticket" in chat goes through the app.
- **Rung 4, workflow.** A workflow that Studio built starts on each new mail,
  sorts it with Mira, and opens a ticket.

Chat comes first, because the owner put the chat first on 2026-11-15. Studio
work waits for the chat push to end.

## 1. The evidence

All paths are on `origin/main` at `471704477`. `G/` is
`apps/services/gateway/gateway/`.

### 1.1 The task store and intake

| Fact | Evidence |
|---|---|
| A ticket is a task type. The task store is the one store | `building_blocks.md` §2, the row "The task store" |
| A project has task types, and a task has a type | `pm_task_types` (`146_projects.sql`), `pm_tasks.type_id` |
| Custom fields: text, number, date, select, multi_select, boolean, url | `155_projects_custom_fields.sql`. No person field and no contact field |
| Intake writes a real task in a triage status, and emits `pm.intake.captured` | `G/routes/projects/intake.py`, `capture_intake` |
| Intake leaves routing out: "Those are `/workflows` nodes (D6), added when email capture (§6.5) lands" | `G/routes/projects/intake.py:41` |

### 1.2 Assignment

| Fact | Evidence |
|---|---|
| "The AI suggests and never assigns" | D-PC-13, `people_center_app.md:870`. D221 point 4 repeats it |
| A server read lists the members of a group | `G/routes/admin/groups.py:154` `active_memberships` |
| Leave today, by email, for a server job | `G/routes/people/absences.py:286` `away_rows` |
| Open work for each person | `G/routes/people/core.py:285` `compute_load` |
| An agent assigns only a person who can already open the task | `G/routes/projects/tasks.py:309` `_refuse_agent_assignees` |
| Round robin is a non-goal of the shared mailbox | `shared_mailbox.md` Q-SM-9 and §2 |
| The CRM lists assignment rules as a gap | `crm_platform.md`, the table "Not in this spec at all" |

### 1.3 Mail and WhatsApp

| Fact | Evidence |
|---|---|
| A mailbox has rules, with ten preset categories and custom rules | `G/routes/email/automation/rules.py` |
| The rule actions: archive, label, move, reply, draft, forward, notify, webhook. No task action | `G/routes/email/automation/actions.py` |
| An email becomes a task by a UI act only, in the member's personal project | `G/routes/tasks/capture_email.py`. The chat twin is debt in `acb_skills/chat_parity.py` |
| The thread link syncs only Done | `G/routes/tasks/email_link.py` |
| The Gmail receiver binds no tenant, so it starts no workflow | `G/routes/workflows/triggers.py:23` |
| The shared mailbox: SM-1 and SM-2 built, dark. SM-3 to SM-9 planned | `EMAIL_SHARED_MAILBOX`, `shared_mailbox.md` |

### 1.4 Workflows

| Fact | Evidence |
|---|---|
| Event topics are `pm.*` and `people.person.updated` only | `G/routes/workflows/catalog.py:131` |
| Each door refuses a `pm_task` step | `G/routes/workflows/service.py:80`. WFC-4a-3 (task 3306) waits on Q-d |
| `human_step` writes only to one member's personal project | `G/routes/workflows/human_step.py` |
| OTW-6 plans a mail event | OTW-6 (task 3017), `email` / `message.received`. E12 (task 2379) plans `email.*` and `whatsapp.*` |
| An event replay starts a second run | OTW-1 (task 3012) plans the claim |
| Event runs need `WORKFLOW_UNATTENDED_ORGS`. Production has it empty | `G/routes/workflows/triggers.py` |

### 1.5 Studio and the chat

| Fact | Evidence |
|---|---|
| A Studio `uses` names `<app>.<op>`, and the app list holds Knowledge only | `packages/acb_common/acb_common/app_manifests/__init__.py:61` |
| Task ops stay off the door | `app_manifests/projects.py:312`, `:318`, `:325` (`off_door=TYPED`) |
| The Support desk template keeps tickets in `studio_records` | `acb_common/studio_templates/support_desk.json` |
| The app `skill` section has no run time | F10b, `ai_studio_rows/F10.md` |
| Studio packs reach only the member's solo web chat | chat-reach, task 3494 |
| A published skill cannot name an MCP or an integration action | chat-reach, task 3493 |
| The chat creates and assigns tasks today, with a card | The `projects` pack: `create_task`, `assign`, `update_task` |

## 2. Scope and non-goals

**In scope.**

- A queue project, the Ticket type, and the queue's assignment rule.
- One entry for every ticket: intake.
- A mail rule of a shared mailbox that opens a ticket.
- A workflow node that opens a ticket.
- Task ops on the Studio door, and a Studio view over the tasks of a project.
- Chat tools that open a ticket and assign by the queue's rule.

**Not in scope.**

- **An auto-reply to the customer.** A send to an outside person always asks
  (D131 point 7). The assignee answers.
- **SLA timers and breach alerts** (E21-06). A later epic slice.
- **A web form and a customer portal** (E21-05). They wait for STU-O1.
- **WhatsApp to ticket** (E21-04). It waits for the org number of WS-WB. A bot
  hand-over (P 2656) will call the same entry.
- **An assignment by the AI.** The rule is code that an admin sets. No model
  picks a person.
- **A second contact store.** The requester links to `crm_contacts` (D174.7).

## 3. The design

### 3.1 A queue is a project, and a ticket is a task

A queue is an ordinary project with three extra settings:

- **The pool:** one group, `group:<slug>`, such as `group:support`. The
  project holds a grant for that group, so each member of the pool can open
  each ticket.
- **The rule:** how the queue picks an assignee (§3.3).
- **Accept at capture:** a new ticket skips triage and gets an assignee at
  once. Off means that the ticket waits in triage, as today.

A ticket is a task of the type Ticket. The Ticket preset adds three fields:

| Field | Type | Note |
|---|---|---|
| Category | select | The company's own list |
| Channel | select | mail, chat, WhatsApp, phone |
| Requester | contact | A new field type: a link to `crm_contacts`. No second contact store |

Priority is the task's own `importance`. A due date is the task's own date.

The settings live in one new table, `pm_queue_rules`, keyed by organization
and project. It is part of the task store block. It has FORCE row level
security (R5), and each column is nullable (R6).

### 3.2 One entry for every ticket

Each path calls `capture_intake`: chat, the mail rule, the workflow node, a
Studio action and later a bot. So the queue's rule runs on each path, and no
path can skip it.

When the queue accepts at capture, the capture does three things in one
transaction:

1. It writes the task in the queue's first open status, not in triage.
2. It runs the rule and writes the assignee.
3. It writes one line on the timeline. The line names the queue and its
   rule, such as "Assigned by the rule of the Support queue".

The intake wrapper keeps the source and the source reference of each
capture. A mail ticket has the source `email`, and its reference is the
message id. One unique key on the org, the source and the reference stops a
second ticket for one mail.

### 3.3 The assignment rule

**The rule is the admin's decision, made once.** The code only follows it.
This needs D-TKT-1, because D-PC-13 says today that nothing assigns.

**The pool.** The active members of the queue's group, from
`active_memberships`. The picker keeps only a member who already opens the
project through a grant. So a rule never widens who can open a ticket, as
`_refuse_agent_assignees` requires of an agent.

**The strategy, v1: round robin.**

1. Sort the pool by email.
2. Take the first member after the cursor. After the last member, start again
   at the first.
3. Skip a member who is away today (`away_rows`), when the queue turns that
   on.
4. Write the assignee, and move the cursor to that member.

The cursor sits on the `pm_queue_rules` row. The picker locks that row for
the transaction, so two tickets at the same moment get two members. An R8
test proves it on a real database.

**What a member sees.** Only "Assigned by the queue rule". A ticket never
shows why the picker skipped a member, and never shows a workload figure (D-PC-14,
the HR gate of D-PC-10).

**An empty pool.** The ticket stays unassigned. The queue's lead gets one My
Day card. Nothing drops in silence.

**Undo.** A member reassigns in one tap. The cursor does not move back.

**Least open work** is a later strategy. `compute_load` gives the figure. It
waits for D-TKT-4.

**One picker for every app.** The CRM wants round robin for a new lead
(`crm_platform.md`). The CRM calls the same picker over its own pool. This
spec builds the picker, and the CRM builds its own slice.

### 3.4 A mail opens a ticket: the mail rule

**Only an org mailbox feeds a queue.** The source is a shared mailbox
(WS-65), such as `support@`. A member's private mailbox never feeds a queue.
D-EM-4 and D-EM-17 keep it private. D-TKT-2 asks the owner to confirm this.

**The rule.** A rule of the shared mailbox (SM-6) matches a category. The
rule engine gets one new preset, "Support". The rule gets one new action,
`OPEN_TICKET`, with one fixed queue project.

**The action.** `OPEN_TICKET` calls `capture_intake` with:

- the subject as the title
- the source `email`, and the message id as the ref
- the thread link, as `capture_email.py` keeps it
- the sender as the requester, matched to a contact, or new when none matches

**Mail text decides nothing.** The text never picks the project, the
assignee or the priority. The rule names the queue. The queue's rule picks
the assignee. A model, when one sorts the mail, returns one value of a closed
list.

**The body is third-party text.** The ticket keeps the mark of an outside
source. When the chat reads the ticket, it frames the body as untrusted, as
`capture_email.py` frames a captured mail today.

**The trust level.** D131 point 4 starts a member's automation at "Ask each
run". A queue that asks for each mail is of no use. D-TKT-3 asks the owner for
a narrow exception: this one action may act and report at once. Its scope is
one named queue, and it has a daily cap. A ticket is a reversible write.

**A reply joins its ticket.** A new mail on the thread adds a comment to the
ticket, and the ticket opens again when it was done. The thread match is the
one of OTW-8 (task 3019). This spec reuses it and adds no second match.

### 3.5 A workflow opens a ticket

We add one node to the workflow engine: "Open a ticket". It names a queue project. Its
assignee is "by the queue's rule". It calls `capture_intake`, so the queue's
rule runs.

This node needs four things that other slices own:

- **The mail event:** OTW-6 (task 3017) builds the one emitter of `email` /
  `message.received`, bound to the org. E12 (task 2379) lists it in the
  catalog and adds `whatsapp.*`. E12 builds no second mail emitter.
- **The dedupe:** OTW-1 (task 3012), one run for one event.
- **The member that the write checks:** Q-d, WFC-4a-3 (task 3306).
- **The run identity:** B6, `ai_studio.md`.

A Mira step that sorts the mail is WFC-2 (task 3256). Its output for this use
is one value of a closed list.

### 3.6 Studio builds the support desk

**Task ops on the Studio door.** A Studio `uses` may name
`projects.capture_ticket`, `projects.create_task`, `projects.assign` and
`projects.list_tasks`. Each runs through the route's own checks. A read is
class A. A write is class B and shows the member's card. The flag is
`STUDIO_DOOR_OPS_ORGS`.

**A view over tasks.** A Studio view may take "the tasks of a project" as its
source. The view reads through `task_visibility_clause`, so a member sees
only the tickets that the member can open.

**The template.** The Support desk template moves onto the task store. It
makes a queue project, the Ticket type, the rule and a mail rule. Its views
read tasks. D-TKT-6 asks what happens to the template until then.

**The chat through the app.** "Add a ticket to the support desk" needs the
app's skill at run time (F10b). It also needs the reach of tasks 3493 and
3494. This spec cites them and builds neither.

### 3.7 The chat

The chat can open a ticket today: `create_task` with the type Ticket in the
queue project. This spec adds:

- **`open_ticket`:** a capture into a queue, so the queue's rule runs.
- **`assign` with `by_rule`:** "assign it to the next person" asks the
  queue's rule.
- **The queue settings:** set the pool, the rule and accept at capture. Each
  UI act has its chat twin (`test_chat_parity.py`).

Each write keeps its card, as the `projects` pack does today.

### 3.8 The shared mailbox follows the ticket

Q-SM-9 stays: the shared mailbox has no rotation of its own. When a mail
becomes a ticket, SM-5 copies the ticket's assignee to the thread. So one
conversation has one assignee. D-TKT-5 asks the owner to confirm.

## 4. Privacy and safety

- **Tenancy.** `pm_queue_rules` has FORCE row level security. The picker binds
  the tenant from the session or the event, never from the mail.
- **Grants never widen.** The pool keeps only the members who open the project
  through a grant.
- **HR facts stay hidden.** No member sees a leave reason or a workload figure.
- **Mail stays private** unless it arrives at an org mailbox (D-TKT-2).
- **Outside text stays data.** The ticket body keeps its outside mark.

## 5. Cost

The rule calls no model. A mail rule that matches a category may call
`acb_llm.decide`, as the rule engine does today (`email.rule_match`, dark).
One call costs about one small-model request for each mail of the shared
mailbox.

## 6. Flags

| Flag | What it opens | Default | Who flips it |
|---|---|---|---|
| `TICKET_QUEUES_ORGS` (new) | The queue settings, the rule, `open_ticket` | empty | The orchestrator, after the owner's preview |
| `EMAIL_SHARED_MAILBOX` | The shared mailbox | off | WS-65 |
| `STUDIO_DOOR_OPS_ORGS` | Task ops on the Studio door | empty | WS-STU |
| `WORKFLOW_UNATTENDED_ORGS`, `CORE_EVENTS_ORGS` | Event runs and the event bus | empty, off | WS-OTW |

`JOB_QUEUE` is off on production. No slice of rungs 1 to 3 needs it. TKT-14
checks whether the mail event needs it.

## 7. Owner gates

- Each flag flip on a live org waits for the owner's preview of its rung.
- No send to a customer. No write of a member or a role.

## 8. The ladder

### 8.1 Rungs and demos

| Rung | Demo task | The demo on production | Slices |
|---|---|---|---|
| 1 | 3532 | A support lead says in chat: "make a Support queue for the support team, round robin". Then "open a ticket: Acme's printer is down". The ticket goes to the next member of the team. A second ticket goes to the member after that | TKT-1 to TKT-6 |
| 2 | 3533 | A customer writes to the shared support address. A ticket opens in the queue, assigned by the rule. The customer's reply joins the ticket | TKT-7 to TKT-10 |
| 3 | 3534 | In AI Studio, the admin says "build a support desk". Studio makes the queue and a view of its tickets. In chat, "add a ticket to the support desk" works | TKT-11 to TKT-13 |
| 4 | 3535 | A workflow that Studio built starts on each new mail of the support address, sorts it with Mira, and opens a ticket | TKT-14 |

**Later, one line each.** WhatsApp to ticket (WS-WB, P 2656). SLA timers
(E21-06). Least open work (D-TKT-4). The CRM calls the picker for a lead.

**The rows of the local launch plan.**

- E21-01 is TKT-0.
- E21-02 is TKT-1 and TKT-4.
- E21-03 is TKT-7 to TKT-9.
- E21-07 is TKT-1 to TKT-3.
- E21-09 is TKT-8.
- E21-04, E21-05, E21-06, E21-08 and E21-10 to E21-15 stay local (D190.8).

### 8.2 The slices

| Slice | Task | Rung | What | Done when | Fence (R7) | Tier | Depends on |
|---|---|---|---|---|---|---|---|
| **TKT-0** | 3492 | 1 | This spec, its INDEX row and its work_plan §2 row | The lint passes, and INDEX lists the spec | `test_index_completeness.py`, `test_doc_names_resolve.py`, ste-lint | LOW | none |
| **TKT-1** | 3518 | 1 | `pm_queue_rules`: pool, rule, accept at capture, cursor (migration) | R8: org B cannot read org A's row. Each column is nullable | `test_queue_rules_r8.py`, new. `test_tenancy_boundary.py` | HIGH | D-TKT-1 |
| **TKT-2** | 3519 | 1 | The picker: round robin, the grant filter, skip away, the row lock | R8: two tickets at once get two members. A member with no grant is never picked | `test_queue_picker_r8.py`, new | HIGH | TKT-1 |
| **TKT-3** | 3520 | 1 | Capture accepts at capture, runs the picker and writes the timeline line | R8: one transaction. The dedupe key refuses a second ticket for one reference | `test_intake_queue_r8.py`, new. `test_projects_intake.py` | HIGH | TKT-2 |
| **TKT-4** | 3521 | 1 | The Ticket preset and the contact field type | A project gets the type and its three fields. A contact field holds a `crm_contacts` id that the reader can see | `test_pm_custom_fields_contact.py`, new | STANDARD | none |
| **TKT-5** | 3522 | 1 | Chat: `open_ticket`, `assign` with `by_rule`, the queue settings | Each UI act has a chat twin. Each write shows its card | `test_chat_parity.py`, `test_projects_chat_coverage.py` | STANDARD | TKT-3 |
| **TKT-6** | 3523 | 1 | The queue settings in Projects | Visual review in light and dark, compact density, a changed accent, phone width | Vitest for the settings panel | STANDARD UI | TKT-1 |
| **TKT-7** | 3524 | 2 | `OPEN_TICKET` on the mail rule engine, shared mailboxes only | A private mailbox cannot hold the action. Mail text never sets the project, assignee or priority | `test_email_open_ticket_r8.py`, new | HIGH | TKT-3, SM-6, D-TKT-2, D-TKT-3 |
| **TKT-8** | 3525 | 2 | The "Support" preset category | The preset matches a support mail in the eval set | `test_email_rule_presets.py`, extended | STANDARD | TKT-7 |
| **TKT-9** | 3526 | 2 | A reply joins its ticket | A reply adds a comment and opens a done ticket again | `test_ticket_reply_r8.py`, new | STANDARD | TKT-7, OTW-8 |
| **TKT-10** | 3527 | 2 | SM-5 copies the ticket's assignee to the thread | One conversation has one assignee | `test_shared_mailbox_assign_r8.py`, extended | STANDARD | SM-5, D-TKT-5 |
| **TKT-11** | 3528 | 3 | Task ops on the Studio door | A Studio action creates and assigns a task with the member's card. A read shows only visible tasks | `test_mcp_edge_coverage.py`, `test_studio_manifest.py`, extended | HIGH | none |
| **TKT-12** | 3529 | 3 | A Studio view over the tasks of a project | The view lists only the tickets that the viewer can open | `test_studio_views.py`, extended | STANDARD | TKT-11 |
| **TKT-13** | 3530 | 3 | The Support desk template on the task store | The template makes a queue, the type, the rule and a mail rule. No `ticket` object stays | `test_studio_templates.py`, extended | STANDARD | TKT-11, TKT-12, D-TKT-6 |
| **TKT-14** | 3531 | 4 | The workflow node "Open a ticket" | The node calls capture, so the queue's rule runs | `test_workflow_open_ticket_r8.py`, new | HIGH | OTW-6, OTW-1, WFC-4a-3, B6 |

**The order.** TKT-4 can start at once. TKT-1 to TKT-3 wait for D-TKT-1. Then
TKT-5 and TKT-6 run in parallel. In rung 2, TKT-7 waits for SM-6 and for
D-TKT-2 and D-TKT-3. TKT-11 has no dependency, but it stays in rung 3 because
Studio waits for the chat push.

A test file marked "new" does not exist yet. The slice that names it writes
it, red first.

## 9. Decisions for the owner

Task 3536 holds these questions for the owner.

| Id | Question | Recommendation | Why |
|---|---|---|---|
| **D-TKT-1** | May a queue rule that an admin sets assign a ticket? This amends D-PC-13, D-PM-10 and D221 point 4 | Yes. The admin decides once, and the code follows. The AI still never picks a person | The owner asked for a fair rule. A suggestion that a person must accept for each ticket is no queue |
| **D-TKT-2** | May a member's private mailbox feed an org queue? | No. Only a shared mailbox, and later the org's WhatsApp number | D-EM-4 and D-EM-17 keep a mailbox private |
| **D-TKT-3** | May `OPEN_TICKET` act at once, under D131? | Yes, as a narrow exception: one named queue, a daily cap, a closed list from a model, and no reply to the customer | A queue that asks for each mail is of no use. A ticket is a reversible write |
| **D-TKT-4** | Does the rule use workload, and does it skip a member on leave? | v1: round robin, and skip a member on leave when the queue turns it on. Least open work later | Round robin is fair and easy to explain. Load figures stay hidden (D-PC-14) |
| **D-TKT-5** | Does the thread's assignee follow the ticket? | Yes. Q-SM-9 stays for a mail with no ticket | One conversation has one assignee |
| **D-TKT-6** | What happens to the Support desk template until TKT-13? | Take it out of the gallery now. Keep each installed copy | A new company would keep tickets in a second store |

## 10. Agreements with other lanes

- **chat-reach (task 3440).** It owns the chat reach of a Studio app. Tasks
  3493 and 3494 are its design tasks. This spec builds neither.
- **The proactive loop (task 3439).** A mail fact goes on the one bus
  (`ingestion/event_hooks.emit_event`). This spec adds no second event path.
- **WS-65.** It owns the shared mailbox, its rules (SM-6) and thread
  assignment (SM-5). This spec adds one action and one copy.
- **WS-OTW.** It owns the mail event (OTW-6), the dedupe (OTW-1) and the thread
  match (OTW-8).
- **E7 Teams.** A team is a group. The queue's pool is a group.

## 11. Fences

Each slice of §8.2 names its test. Until a slice lands, its rule is advisory
(R7). Three rules have a fence from the first slice:

- No second ticket store: TKT-13 deletes the `ticket` object of the template.
  `test_no_gtd_table_names.py` stays.
- The rule never widens a grant: `test_queue_picker_r8.py`.
- Mail text never picks the assignee: `test_email_open_ticket_r8.py`.

## 12. Commands

```bash
node .claude/hooks/ste-lint.mjs project-docs/specs/ticket_queues.md
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py
```
