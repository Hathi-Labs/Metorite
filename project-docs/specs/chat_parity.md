# Chat parity — each UI action is also a chat action

**Status: ACTIVE. P-0 is BUILT (2026-11-01, branch
`chat-parity-p0-mt-7f1c2417`).** The fence `tests/unit/test_chat_parity.py`
holds each request of the control plane to one decision. Today's gaps sit in
a debt list, so CI passes. §6 sets the order of the slices.

**PAR-1 (task 3099, no dead end) is BUILT, 2026-11-17, with no flag.**
`show_in_app` opens every live page of `nav.ts`, the Admin pages too
(`orchestrator/in_app_path.py`, `PANES`). `/settings/organization` opens for an
admin of the org only, by the right of the verified run member, and a member
who is no admin gets "Only an admin of your organization opens Organisation."
The Organisation page reads `?tab=` once (`orgTab.ts`), so the chat lands on
Members & roles. `acb_skills/chat_parity.py` gains `APP_PAGE`: the live page of
each app, for an act that has no chat tool yet. `None` marks an app with no
live pane (notes, memory, agents). PAR-1c (task 3677, Done) gave the chat and
the sidebar one admin right for the page: `admin:members:read`.

**PAR-0 (task 3098, 2026-11-22): §6 holds the ladder of the epic, and §7 its
decisions.** §6 sets the order of the work now.

**The fence blocks every pull request** (owner decision, 2026-11-01). It is a
SAFETY fence in `tests/fences.json`, so it blocks in build mode too. The
ceilings in `chat_parity.py` are the baseline at `origin/main` `fc1b5edb6`.
From that point they only go down.

**Epic:** Metorite task 1238. Written 2026-11-01.

**Owner rule, 2026-11-01, in chat.** The words are the owner's, so they sit
in a block that the linter does not read:

```text
"every single possible action that is possible to do via button clicking
and the UI/UX should be possible to do via the chat interface across the
entire application."
```

## 1. The rule

Each action that a member can do in the app is also possible from the chat.
A read is an action too: the chat can read each list that the app shows.

**An action** is one request that the control plane sends to its BFF. It is
a verb and a path under `/api/`, with `{}` for each id. Each action has
exactly one decision in `packages/acb_skills/acb_skills/chat_parity.py`:

| Table | What it holds |
|---|---|
| `COVERED` | The chat tool that sends the same request to the gateway |
| `EXEMPT` | A kind of `EXEMPT_KINDS` and a reason |
| `DEBT` | The wave of the epic that gives the chat a tool |

Two apps keep their own tables, and the parity module does not copy them.

- **Projects.** Fence F3 holds each method of the Projects client
  (`projects_agent_parity.md` §6.4). A request to a Projects route from any
  other file takes its decision from the row of the Projects manifest.
- **Knowledge.** A request to a Knowledge route takes its decision from the
  Knowledge manifest (`acb_skills.knowledge_manifest`).

The parity module takes a row for a route of these two apps only where the
manifest gives no built tool.

## 2. Exemptions are few, and of a fixed kind

An exemption is only for a request that is not an act of a member in any
sense. `EXEMPT_KINDS` holds the kinds:

| Kind | The request |
|---|---|
| `oauth` | An OAuth redirect, consent or callback. The provider needs the browser |
| `download` | A file stream for a download, a preview or a picture |
| `stream` | A live stream, a poll or a heartbeat that refreshes a view |
| `webhook` | A call that a provider or a machine makes |
| `health` | A health ping, or a probe of the session |
| `session` | Sign-in, sign-up, sign-out or a switch of account |
| `chat` | The chat's own transport: a turn, a stop, an answer to a card |
| `secret` | The member types a password, a key or a token |

A new kind needs the owner. Do not add a kind to make a row pass.

**Secrets stay in the UI** (owner decision, 2026-11-01). A member types a
password, a key or a token only in the form of the app. No chat tool takes
one. So the `secret` kind stays, and its rows are not debt.

## 3. The fence and the ratchet (R7)

**Fence:** `tests/unit/test_chat_parity.py`. Its reader is
`tests/unit/_chat_parity_reader.py`. It runs no node and no gateway. It sits
in the `safety` list of `tests/fences.json`. The pr-check shards run each
safety fence and block on it, with no `harden` label.

1. The reader reads each request of the UI from the TypeScript source, with
   the tokenizer of F3. It follows a literal path, a constant, an object of
   constants, a path builder, and a request seam. A seam is a function that
   takes a path and calls `fetch`. A seam can come from another file.
2. Each action is in exactly one table, or its manifest decides it. A new
   action fails. The message names it, the file and the function that send
   it, and its gateway route.
3. A row that names an action that the UI no longer sends fails.
4. The reader also reads each tool function in `TOOL_SOURCES`, and the
   helpers that it calls. A `COVERED` tool must be a registered tool. Its
   source must name the gateway route of the action, with the same verb.
5. The debt of each app must equal `DEBT_CEILING`. A new debt fails. A paid
   debt lowers the ceiling in the same change. So the list only shrinks.
6. A request that the reader cannot follow is UNREAD. The count of each file
   must equal `UNREAD_CEILING`. Write a new path as a literal, or send it
   through a seam.

Mutation tests in the same file prove the fence. A new action fails. So do
a stale row, a false claim, a bad exemption and a new debt.

## 4. What the fence cannot judge

- **The body of a request.** The fence holds the verb and the path. Two
  buttons that send different fields to one route are one action. Fence F2
  holds the fields of Projects only.
- **A tool that names a route as an inner step.** A write tool can read a
  list first. The fence then counts the tool as one that can read the list.
  A reviewer checks that a `COVERED` row is the same act.
- **A tool that works in the process.** The workflow tools and the memory
  tools call the gateway's code, not its routes. The fence cannot see that,
  so their actions stay in `DEBT` until a tool sends the route.

## 5. The waves

`WAVES` in the parity module holds the waves. P-1 is Calendar and Tasks.
Section 6 sets the order of the waves. Each wave moves its rows from
`DEBT` to `COVERED`, and lowers the ceiling of its app.

**The ratchet names its tasks (PAR-1b, task 3100, 2026-11-17).** Each wave of
`WAVES` lists the board tasks that build it, and a wave with no task fails.
`P0_CEILING` keeps the debt of each app at P-0. A ceiling above its baseline
names the task that pays it in its comment, as "Paid by task N", and N is a
task of the wave of that app. Eight ceilings had gone up since P-0 (calendar,
chat, email, integrations, knowledge, settings, shell, studio), and each one
names its task now. The CRM wave P-3 had no task, so task 3678 is its task.
Fence: `tests/unit/test_chat_parity.py`.

**A new UI action needs a chat tool, or a row in the parity table.**
`workbench/control_plane/AGENTS.md` carries this rule to UI work.

## 6. The ladder

**PAR-0, task 3098, written 2026-11-22.** This section is the plan of epic
1238. It follows the gratification ladder of `orchestration_playbook.md` §6d
(D211). The board holds the state and the owner of each task. This section
holds the order, and the reason for it.

### 6.1 Why the order starts at a dead end

On 2026-11-13 a member asked the production chat for the page that invites
users. The chat opened People, which is the wrong page. On the second try it
said that no page was an invite screen. That is a broken live feature, so it
is rung 0.

The chat had two faults. `PANES` held no Admin page, so the chat could not open
Organisation. The invite request had no chat tool: it was a debt row of wave
P-8, and no wave after P-0 had a task.

Production showed two more gaps in 21 days of "I can't" answers. On 2026-11-12
the chat could read a Knowledge page and could not write one. On 2026-11-09 a
file that a member gave to the chat could not go on an email.

### 6.2 The debt today

`DEBT_CEILING` and `P0_CEILING` in the parity module own these numbers. This
table is a reading of 2026-11-22, at `origin/main` `fe0f4aeff`. The total was
412 at P-0, and it is 427 today. Eight ceilings went up, and four went down.

| App | P-0 | Today | Wave | Task |
|---|---|---|---|---|
| projects | 7 | 7 | F3 | `projects_agent_parity.md` §6.4 owns it |
| tasks | 11 | 9 | P-1 | 3112 |
| calendar | 1 | 5 | P-1 | 3112 |
| email | 13 | 14 | P-2 | 3111 |
| crm | 74 | 72 | P-3 | 3678 |
| notes | 51 | 51 | P-4 | 3113 |
| whatsapp | 25 | 25 | P-4 | 3113 |
| people | 19 | 19 | P-5 | 3114 |
| knowledge | 17 | 19 | P-5 | 3105 |
| memory | 5 | 5 | P-5 | 3114 |
| workflows | 24 | 18 | P-6 | 3115 |
| apps | 29 | 28 | P-6 | 3115 |
| agents | 20 | 20 | P-6 | 3115 |
| chat | 25 | 28 | P-7 | 3115 |
| shell | 15 | 16 | P-7 | 3115 |
| settings | 21 | 30 | P-8 | 3108 |
| admin | 36 | 36 | P-8 | 3102, 3107 |
| integrations | 19 | 22 | P-8 | 3109 |
| studio | 0 | 3 | P-9 | 3115 |

### 6.3 Rungs 0 to 3, slice by slice

Take the slices of the lowest open rung first. Inside a rung, take them in the
order of its table. HIGH has the meaning of `next-ticket.md` step 1b.

A letter after a wave number, as in P-5a or P-8b, names one slice of that
wave. The titles of the board tasks use these names. `WAVES` in the parity
module holds the wave number only, and D-PAR-5 ties each wave to its tasks.

#### Rung 0 · No dead end

The chat opens each live page, and the Admin pages too. The ratchet names the
task of each wave.

| Order | Slice | Task | Tier | State |
|---|---|---|---|---|
| 1 | PAR-1: the chat opens each live page | 3099 | STANDARD | Done, PR 2557 |
| 2 | PAR-1b: the ratchet names its tasks | 3100 | LOW | Done, PR 2561 |
| 3 | PAR-1c: one admin right for the Organisation page | 3677 | LOW | Done |
| 4 | The demo of rung 0 | 3101 | none | In progress |

- **Demo (task 3101).** On production, an admin asks the chat to open the page
  that invites users. The chat opens Organisation on the members tab. The
  playbook gives rung 0 no demo task, and the owner made this one. It stays.
- **The owner does this.** He runs the demo on production, and says so on task
  3101.

#### Rung 1 · Invite a person from the chat

| Order | Slice | Task | Tier | State |
|---|---|---|---|---|
| 1 | PAR-2a: the `org-admin` pack, with list, roles, status and access requests | 3102 | HIGH | In progress |
| 2 | PAR-2b: the invite card | 3925 | HIGH | Waits for the owner (the mail) |
| 3 | The demo of rung 1 | 3104 | none | To do |

- **The split (owner, 2026-11-23, answer on task 3102).** The audit of PAR-2
  found the slice too big for one HIGH pull request, and the invite mail open.
  So PAR-2a takes four jobs, and PAR-2b takes the invite after the owner
  decides the mail.
- **PAR-2a, four tools.** Each tool calls the gateway as the member. The
  gateway guard stays the only check.

  | Tool | Card | Gateway route | Guard |
  |---|---|---|---|
  | `list_members` | no | `GET /admin/members`, `GET /admin/members/requests` | `require_admin_user` |
  | `set_member_roles` | yes | `PUT /admin/members/{email}/roles`, after `GET /admin/roles` | `admin:members:manage` |
  | `set_member_status` | yes | `PATCH /admin/members/{email}` | `admin:members:manage` |
  | `answer_access_request` | yes | `POST /admin/members/requests/{email}/approve` or `/deny` | `admin:members:invite` |

  An access request takes the email as its key, not an id. The verb is
  `deny`, not `decline`.
- **PAR-2a pays six debt rows** of `chat_parity.py`: `GET /api/admin/members`,
  `GET /api/admin/members/requests`, `GET /api/admin/roles`,
  `PATCH /api/admin/members/{}`, `PUT /api/admin/members/{}/roles` and
  `POST /api/admin/members/requests/{}/{}`. The admin ceiling goes from 36 to
  30. A BFF path that differs from the gateway path takes the tuple form of
  `COVERED`.
- **PAR-2b pays two rows:** `POST /api/admin/members/invite` and
  `GET /api/org/seats`. The gateway invite route is `POST /admin/members`. The
  seats read is `GET /seats/overview`, and it answers 503 when no Customer
  Console is wired, as in `dev_up` and the tests. The card then says that the
  seat count is not known.
- **The owner of the pack.** A pack needs an owner agent in
  `_PACK_OWNER_AGENTS` (`gateway/routes/agent.py`). PAR-2a adds `org-admin`.
  It owns the pack and never runs as an agent, as `studio-builder` does.
- **The fences of PAR-2a.** They are `tests/unit/test_chat_parity.py`,
  `tests/unit/test_tool_packs_registry.py` and the new unit tests of the
  module. An R8 test also runs roles, status and access requests for two orgs
  on a private database. `_KIND_TOOLS` and the class C rows of
  `test_held_card_writes.py` hold Projects tools only, so they do not bind
  this pack.
- **Demo (task 3104).** An admin invites a person from the chat with one card.
  The person then shows as invited on Organisation.
- **Who sends the mail.** The gateway route `POST /admin/members` sends
  nothing. The notice mail comes from the Next hop `api/admin/members/invite`,
  and only when `MEMBER_INVITE_EMAIL_ENABLED` is on. A chat tool calls the
  gateway, so it would send no mail, and a chat invite would differ from a UI
  invite. PAR-2 must make the two do the same (D-PAR-3), and its card must say
  that a mail goes to an outside person (D131.7).
- **Reason.** It is the act that the member of §6.1 wanted to do.
- **The owner does this.** He approves the preview of the invite card, and
  then the merge of the HIGH slice. He adds `org-admin` to `TOOL_PACKS` before
  the release. `TOOL_PACKS` is the owner's choice.

#### Rung 2 · Write Knowledge from the chat

| Order | Slice | Task | Tier | State |
|---|---|---|---|---|
| 1 | PAR-3: make a page, change a page, make a space (wave P-5a) | 3105 | STANDARD | To do |
| 2 | The demo of rung 2 | 3106 | none | To do |
| 3 | PAR-3b: the other Knowledge debt rows | 3901 | STANDARD | To do |

- **Demo (task 3106).** The chat researches a topic. After one card, it saves
  the result as a draft page. A person publishes it in the app.
- **An agent never publishes (D104.4, D-KB-4).** Each agent write to Knowledge
  is a draft, or a card that a person answers. PAR-3 and PAR-3b build drafts
  and proposals only. The tools that make proposals sit behind `KNOWLEDGE_PROPOSALS`, which
  is off by default, so the owner flips it (§7). The manifest holds publish,
  archive and restore as a person's act (`_PERSON_PUBLISHES`). Those rows stay
  in the debt until the owner decides what the owner rule of 2026-11-01 means
  for them (§7, item 7).
- **Reason.** Production gave an "I can't" answer here on 2026-11-12. The debt
  of Knowledge also went up, from 17 to 19 rows.
- **Each write goes on one card.** Row 3 does not block the demo, and it holds
  no publish, archive or restore row.
- **The owner does this.** He approves the preview of the three cards.

#### Rung 3 · Admin, Settings and Integrations from the chat

| Order | Slice | Task | Tier | State |
|---|---|---|---|---|
| 1 | PAR-4: groups, roles, seats, remove a member, the org profile, member access (wave P-8b) | 3107 | HIGH | To do |
| 2 | PAR-5: appearance, number system, branding, the assistant profile and its summaries, zone, shell (wave P-8c) | 3108 | STANDARD | To do |
| 3 | PAR-6: list, test, disconnect, MCP servers, agent connections (wave P-8d) | 3109 | HIGH | To do |
| 4 | The demo of rung 3 | 3110 | none | To do |
| 5 | PAR-4b: the billing reads and the two order writes | 3902 | HIGH | Waits for the owner (§7) |

- **Demo (task 3110).** An admin does the Admin, Settings and Integrations jobs
  from the chat alone. The seat part of the demo needs the Customer Console
  wired on production: without it, `/seats/assign` and `/seats/release` refuse
  with 503. The deployment key and the `seat_admin` grant are owner gates
  (`customer_console.md` §8, gates 7 and 8).
- **Reason.** Wave P-8 holds 88 debt rows, the most of all waves. The Settings
  debt went up the most, from 21 to 30 rows. PAR-4 builds on the pack of rung 1.
- **Order.** PAR-5 is STANDARD, so it can run while PAR-4 waits for the owner.
- **Row 5.** The admin debt holds 14 rows of billing and seats. PAR-4 names the
  seats only. An order is money, so the owner decides first.
- **The owner does this.** He approves the previews, and the merge of each
  HIGH slice.

### 6.4 Rungs 4 to 8, one line each

The order comes from three facts: the "I can't" answers of production, the
most-used debt actions that P-0 listed (epic 1238, comment of 2026-11-01), and
the debt of each app. The board gives no
tier for tasks 3678, 3113, 3114 and 3115. Step 1b of `next-ticket.md` sets it
at build. Each of these rungs ends in a demo task, and the table names it.

| Rung | Apps, in order | Wave | Task | Demo | Debt rows | Reason |
|---|---|---|---|---|---|---|
| 4 | Email, then Calendar and Tasks | P-2, P-1 | 3111, 3112 | 3904 | 14 and 14 | Production said "I can't" on 2026-11-09. Three most-used actions are here. |
| 5 | The shell, then Workflows | P-7, P-6 | 3903 (cut from 3115) | 3905 | 16 and 18 | Four most-used actions are here. |
| 6 | WhatsApp, then Notes | P-4 | 3113 | 3906 | 25 and 51 | Two most-used actions are here. |
| 7 | People and Memory, then CRM | P-5b, P-3 | 3114, 3678 | 3907 | 24 and 72 | People and CRM each hold one of the most-used actions. |
| 8 | Chat threads, Custom Apps, Agent Registry, AI Studio | P-7, P-6, P-9 | 3115 | 3908 | 79 | No evidence of demand yet. |

- **Rung 4.** The three actions are mail search, a task from an email and the
  day state. Task 3111 also puts a file from the chat on an email. Both tasks
  are STANDARD on the board.
- **Rung 5.** The four actions are the Home feed, "Needs you", the decision on
  a pending action, and a workflow run. Task 3115 holds 113 rows, so this rung
  takes its shell and Workflows rows as one slice, PAR-8 (task 3903). The demo
  leaves out the decision on a pending action until the owner answers §7,
  item 6.
- **Rung 6.** The two actions are a WhatsApp message and the meeting notes.
  WhatsApp goes first, because Notes has no live page (`APP_PAGE`).
- **Rung 7.** The two actions are a people lookup and the end of a CRM task.
  Memory holds none. The CRM rows come from the CRM manifest, and not from the
  `DEBT` table, so the build of task 3678 confirms the row of the CRM task.
  People goes first: it pays the same demand with one third of the rows.

### 6.5 The standing sweep (PAR-7, task 3116, LOW)

PAR-7 is not a rung. Each week it reads the "I can't" answers of the
production chat. It ranks them by app, and posts the list on epic 1238.

The sweep moves the order of rungs 4 to 8 only. When the sweep ranks an app
above the app of the next open rung, the plan moves that wave up. The move is
a pull request that changes §6.4 and names the post of the sweep. A rung that
has a slice in progress keeps its place.

## 7. Decisions

These decisions are local to this spec. They take no D-number of
`work_plan.md` §3, and they reopen none. The owner decided D-PAR-7 on 2026-11-01. The others are
**PROPOSED for the owner's Approve of this spec**: the owner accepts them when
he approves the merge.

| # | The choice | Reason | State |
|---|---|---|---|
| D-PAR-1 | The ladder of §6 sets the order of the epic. A wave number is a name, not an order. | The order follows what a member met on production. | PROPOSED for the owner's Approve of this spec |
| D-PAR-2 | The chat opens an Admin page by the right of the verified run member, as PAR-1 built it. A tool argument never gives that right. The same rule binds each `org-admin` write. | R5: the chat trusts no identity from input. | PROPOSED for the owner's Approve of this spec |
| D-PAR-3 | An invite, and each member or role write, goes on one approval card. It never runs with no card. A chat invite does what a UI invite does, so its card says if a notice mail goes to the person. | A role write changes access. The notice mail of an invite goes to an outside person (D131.7), and today only the Next hop sends it. | PROPOSED for the owner's Approve of this spec |
| D-PAR-4 | The order is demand first, then risk. A HIGH slice keeps the full loop, and waits for the owner's Approve of the merge. | A STANDARD slice of the same rung can merge while a HIGH slice waits. | PROPOSED for the owner's Approve of this spec |
| D-PAR-5 | Each wave names its board tasks in `WAVES` (PAR-1b), and a task that this plan makes joins `WAVES` in its own pull request. A pull request that pays a debt row lowers the ceiling of its app in the same change, whatever epic it belongs to. | Fence rule 5 of §3 needs the debt to equal the ceiling, so the paying change must lower it. Other epics already paid rows this way. | PROPOSED for the owner's Approve of this spec |
| D-PAR-6 | The weekly sweep of PAR-7 moves the order of rungs 4 to 8 (§6.5). Advisory (R7): no test holds it. | The P-0 list of most-used actions is from 2026-11-01, and demand changes. | PROPOSED for the owner's Approve of this spec |
| D-PAR-7 | Secrets stay in the UI. | Owner decision, 2026-11-01 (§2). This row cites it and does not reopen it. | DECIDED |

**Open for the owner.**

1. **The `TOOL_PACKS` lines.** Add `org-admin` after PAR-2 merges and before
   the demo of rung 1. Say which pack holds the tools of PAR-5 and PAR-6: a new
   slug needs its own line before the demo of rung 3.
2. **The previews.** Approve the preview of each new card: the invite, the
   Knowledge writes, and the Admin, Settings and Integrations writes.
3. **The flag flips.** Approve each one on its task: `KNOWLEDGE_PROPOSALS` for
   the Knowledge cards, and `MEMBER_INVITE_EMAIL_ENABLED` for the invite mail.
   No agent of this plan flips a flag.
4. **The merge of each HIGH slice:** tasks 3102, 3107, 3109 and 3902.
5. **Billing in the chat.** Decide if the chat gets a tool for an order and
   for the use of an order code. They move money.
6. **The decision on a pending action.** A run with no person present cannot
   approve its own action (D161.5), and the five acts of D131.7 always ask.
   Decide the case with a person present: can the chat approve an action that
   the chat itself asked for?
7. **Publish, archive and restore of a Knowledge page.** The owner rule of
   2026-11-01 says each UI action works from the chat. D104.4 says an agent
   never publishes. Decide: an exemption for these three rows, or a card that
   the person answers.
8. **The Customer Console for seats.** The seat writes of PAR-4 need it wired
   on production (`customer_console.md` §8, gates 7 and 8).
