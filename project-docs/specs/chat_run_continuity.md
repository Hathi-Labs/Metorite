# Chat run continuity — an assistant run outlives the tab, the account and the org

**Status: ACTIVE.** Board row **WS-51**. Written 2026-10-10 from the owner's
ask of the same day. **Verified against code on 2026-10-10**, at `origin/main`
`e5644259b`.

| Slice | State |
|---|---|
| S1 — one activity feed and the nav badges | **BUILT** 2026-10-10, branch `chat-activity-badges` |
| S2 — a durable "needs input" | **BUILT** 2026-10-10, branch `ws51-s2-needs-input`, dark behind `CHAT_DURABLE_ASKS` |
| S3 — the top-right activity control | **BUILT** 2026-10-10, branch `ws51-s3-activity`, no flag |
| S4 — the server saves the prompt at run start | **BUILT** 2026-10-10, branch `ws51-s4-prompt-save` |
| S5 — unread replies, the toast, the tab title and the phone pill | **BUILT** 2026-10-10, branch `ws51-s5-attention`, no flag |
| S6 — the activity of the other signed-in accounts | Spec only |
| D-1 to D-3 — the deferred decisions | **OWNER-GATE** |

**This spec owns** how a member sees and resumes an assistant run that they
are not watching. That covers the run list, the badges, the "needs input"
state and its storage, and the activity control. `projects_ai_chat.md` §23
keeps the chat cache namespaces. `navigation_shell.md` keeps the shell bar,
the bell and the manifest. This spec adds one manifest field use (§4 S1) and
no second shell part.

---

## 1. The owner's ask (2026-10-10)

The owner's words, kept as written:

- "the AI chat across all the assistants or the chat apps will continue to
  work" …
- … "even if the user logs off, switches to another account, and switches
  back in"
- multiple sessions, across the chat apps
- "a robust mechanism … across AI chats, across organizations, across users"
- a bubble on the nav with the count of running agent sessions
- an alert at the top right when a session needs input
- on the desktop and on mobile

---

## 2. Scope and non-goals

**In scope.**

1. One list of the member's live runs, read once for the whole app.
2. A count on each app's nav entry, on the desktop sidebar and on the phone.
3. A "needs input" state that survives a restart and an org switch.
4. One top-right control that lists every run across apps and accounts.
5. Signals outside an open chat: unread dots, a toast, the tab title.

**Non-goals.**

- A run worker that a deploy cannot stop. That is D-1, an owner decision.
- Web push to a closed browser. That is D-2.
- Caps on concurrent runs. That is D-3.
- A new chat surface. Every slice reads the chats that exist.
- Any change to who may see a run. `SESSION_VISIBLE_SQL` and the tenant
  key `cc:<org>:liveruns` stay the only rules (#791).

---

## 3. The state on 2026-10-10

### 3.1 Already shipped

| PR | What it did |
|---|---|
| #780 | Chat uploads survive the run |
| #791 | The per-org run registry `cc:<org>:liveruns`. `GET /chat/active-sessions` became tenant-safe, and each row carries `startedAt` |
| #797, #805 | Dead-run recovery: the startup sweep, the "Metorite is updating" notice, held sends, and an interrupted reply with Continue. A late claimant stands down |
| #795 | An edit supersedes the last message |

### 3.2 Measured on `origin/main`

**The run.** A run is detached from its HTTP request
(`stream_relay.run_detached`, `apps/services/orchestrator/orchestrator/stream_relay.py:1072`).
The run pins its identity and its org at start. An account or org switch
reloads the page, and the run goes on. A browser can reattach within one hour,
the TTL of the Redis stream (`STREAM_TTL_SECONDS`, same file, line 85).

**A pending question.** `ask_questions`, `request_confirmation` and `ask_user`
wait on an in-process Future (`executor._pending_user_input`,
`apps/services/orchestrator/orchestrator/executor.py:738`), with a one-hour
timeout. A restart loses it. `POST /agent/respond-input`
(`apps/services/gateway/gateway/routes/agent.py:2725`) resolves the answer in
the caller's CURRENT org. So an answer from another org fails.

**The client.** `lib/chatStore.ts` is a module singleton keyed by thread id.
Before S1, `hooks/useActiveSessions.ts` ran one 5 s poll of
`/chat/active-sessions` in each of five surfaces. The HITL state lives in
`AgentChat` component state (`confirmations`, `elicitation`, `userInput`).
`lib/askPin.ts` holds `pendingAsk()`.

**The nav.** Before S1, `Sidebar.tsx` `NavLink` had one `badge` slot, in a
hard-coded `bg-warning`, and only `/agents` used it. The phone's Chats tab
showed a count on `/chat` only. The drawer links had no badge.

**The gaps.** Nothing tells a member "needs input" outside an open chat. No
tab title, favicon or web push signals it. No list joins the chats of two
accounts. The account switcher is the Gmail model (`lib/accountSwitch.ts`),
and a switch reloads the page.

---

## 4. The slices

Every slice is **AGENT-SAFE** to build, and each one ships dark where it
changes behaviour. S2 adds a table, so R5, R6 and R8 bind it. S6 reads a
second account's cookie, so it takes the full review loop.

### S1 — one activity feed and the nav badges (BUILT 2026-10-10)

**What.**

1. **One poller.** `src/lib/liveRuns.ts` reads `/api/chat/active-sessions`
   for the whole app. It polls every 5 s while the tab is visible, and every
   30 s while it is hidden. It polls at once when the tab shows again, and it
   stops when the last subscriber leaves. `useActiveSessions` keeps its API
   for its five consumers and reads the shared list. It still unions the list
   with the local `isLoading` flags.
2. **One mapping.** A pane names its agent in the shell manifest
   (`NavPane.agent`, `navigation_shell.md` §5.1). `src/lib/runActivity.ts`
   reads it. My Tasks names `task-manager`, My Email names `email-assistant`,
   Projects names `projects-assistant`, and App Workshop names `app-builder`.
   Every other agent counts on Chat, the orchestrator too. A run whose pane
   the member cannot see also counts on Chat. If two panes name one agent,
   the first pane owns it. Calendar can thus take `task-manager` (§5.3 of
   `navigation_shell.md`), and its runs stay on My Tasks. The
   tab's own record of a session's agent names a run that the server still
   calls `unknown`.
3. **The desktop sidebar.** `NavLink` takes `badgeTone` and `badgeLabel`.
   `src/components/NavBadge.tsx` draws the count, collapsed and expanded. A
   running count is `success`. `/agents` keeps its update count in `warning`,
   and S3 reserves `warning` for "needs you".
4. **The phone.** Each drawer link shows its app's count. The bottom bar
   shows the total on every page. On `/chat` the Chats tab carries it. On
   every other page the Menu tab carries it, because the Chats tab exists on
   `/chat` only.
5. **Access.** The badge is `role="img"` with a spoken name, for example "2
   assistants running". A collapsed link names its pane in `sr-only` text.
   The pulse is `motion-safe:animate-pulse`.
6. **The stale `isLoading`.** The reattach loop in `hooks/useAgentChat.ts`
   was aborted on unmount, and its catch and finally wrote nothing once
   cancelled. So `isLoading` stayed true with a dead controller, and the badge
   counted a run that the tab no longer watched. `releaseLoading` in
   `lib/chatStore.ts` now clears it on unmount, and on a steered (202) or
   refused reconnect. It clears only the state of the loop that owns it. A
   run that still runs stays counted, because the server's list reports it.
7. **Tokens.** The run dots in `app/chat/page.tsx`, `components/AgentChat.tsx`
   and the Tasks and Email rails use `bg-success`, not the raw palette.

**Done when.**

- One tab sends one request per tick, for any number of subscribers.
- A hidden tab waits 30 s between requests.
- Each of the five agents counts on its own pane, and every other agent
  counts on Chat.
- The sidebar, the drawer and the bottom bar draw the badge in `success`.
- An unmounted reattach loop leaves `isLoading` false.

**Fences (R7).**

| Fence | What it holds |
|---|---|
| `src/lib/liveRuns.test.ts` | One fetch for N subscribers, a late subscriber starts no request, no second request in flight, the hidden interval, and a tree sweep that refuses a second poller |
| `src/lib/runActivity.test.ts` | The agent-to-app map, the Chat fallback, the hidden-pane fold, and no two panes naming one agent |
| `src/components/navBadge.test.ts` | The badge on the sidebar, collapsed and expanded, and on the phone nav on every page |
| `src/lib/chatStore.test.ts` | `releaseLoading`, and its three call sites in the reattach loop |
| `src/lib/theme/conformance.test.ts` | Three palette budgets left the baseline, and `AgentChat.tsx` fell to 12 |

**Mutation record (2026-10-10).** Twelve mutants, twelve killed:

- the single-poller guard and the in-flight guard
- the hidden interval
- the map, the fold and the "unknown" rule
- the owner check of `releaseLoading`, and its unmount call
- the badge tone, the motion rule and the Menu badge

**Known limits.** Each item below is open, and none blocks S1.

- A folded sidebar section hides its items, and with them their badges. A
  later item puts the sum of its items on the section heading.
- The phone drawer shows the counts of the moment it opened.
- A member with no `chat` feature has no Chat pane. A run that folds to Chat
  then shows only in the phone total, and the desktop shows it nowhere. So
  for that member a run CAN be invisible on the desktop. The review of
  2026-10-10 found this.
- A replay that ends with no content keeps its controller
  (`useAgentChat.ts`, the `done` case and the end of the stream). The badge
  then counts the run until the chat unmounts. This defect is older than S1.

The run list is bound to the member like the read cache. `bindIdentity` in
`lib/dataCache.ts` empties it through `onClear`, and a generation count drops
a poll that was in flight when the member changed.

**No flag.** S1 ships with no flag, because it only reads a list that the
server already serves, and it writes nothing.

### S2 — a durable "needs input"

**What.** A pending ask becomes a row, not only a Future.

1. A new table, `chat_pending_ask`, holds one row per open question. A row
   names the request id, the org, the thread, the run and the actor. It also
   holds the kind, the question, the time it was asked, and the answer. It is
   tenant-scoped under FORCE ROW LEVEL SECURITY (R5a). Take the migration
   number at build time (R1).
2. After about ten minutes with no answer, the run writes a checkpoint and
   ends. The row stays open, in the state `parked`.
3. An answer to a parked row starts a NEW run on the same thread. That run
   reads the question and the answer as its first turn. This reuses the
   `run_restarted` shape of #797, which `POST /agent/respond-input` builds
   (`gateway/routes/agent.py`, near line 2805).
4. `POST /agent/respond-input` finds the row by its request id. It takes the
   org from the row, never from the request (R5e). Then it checks that the
   caller is a member of that org, with a right to the thread.
5. `GET /chat/active-sessions` adds `state`, `running` or `needs_input`. A
   parked row is listed as `needs_input`, with no live run behind it.

**Done when.** A question asked before a restart can be answered after it. An
answer from another org resumes the run in the run's own org. A member of a
second org cannot answer it. R8 binds: run the row-level security and the
lookup against a real Postgres.

**Fences.** A `tests/unit/test_pending_ask_*.py` suite under R8, and an
entry in `tests/unit/test_tenant_coverage.py`.

**As built (2026-10-10).** The flag is `CHAT_DURABLE_ASKS`, and it is OFF by
default. OFF writes no row, parks no run, and reports every run as
`running`.

1. **The table.** Migration 237 adds `chat_pending_ask`. It is expand only
   (R6). The key is `(organization_id, request_id)`. The states are `open`,
   `parked`, `answered` and `closed`. A row expires after 7 days
   (`CHAT_ASK_TTL_HOURS`). The migration carries its own FORCE policy, and
   the generated phase files carry the table too.
2. **The one write point.** `executor._push_sse_to_stream` carries every
   card, so `orchestrator/pending_ask.py` writes the row there. It writes a
   row only for a card whose Future still waits in this process.
3. **The run settles the row.** `executor.wait_user_future` is the wait of
   every parking site. It closes the row when the wait ends. After
   `CHAT_ASK_PARK_SECONDS` (600) it parks the run. The row goes to `parked`,
   a `RUN_FINISHED` with `parked: true` ends the stream, and the run's task
   is cancelled. The drain then saves the reply so far.
4. **A parked card stays open.** `request_confirmation` sends no
   `confirmation_resolved` for a parked card, so the chat can show it again.
5. **The late answer.** `POST /agent/respond-input` reads the row and moves
   it to `answered` in one statement. Then it answers 409 `run_restarted`
   with the question from the row. Two late answers resend once.
6. **The chat.** `GET /chat/pending-asks` lists the cards to draw again, for
   a member who may send in the room. `AgentChat` replays each card through
   its own HITL handler.
7. **The badge.** `runBadge` in `lib/runActivity.ts` is the one rule. A run
   that waits on the member turns the badge amber (`warning`), over the
   green count. Its spoken name is "1 assistant needs your answer".

**Where the build differs from the text above.**

- **Item 4, the org.** Under FORCE RLS a row is readable only after its org
  is bound. The route reads the row under the caller's own tenant, which the
  session binds (R5e). One identity resolves to one org
  (`acb_auth.access.resolve_identity`), so no member of two orgs can reach
  this route. A read across orgs needs a SECURITY DEFINER function, and this
  slice adds none.
- **The other account.** A tab that drew a card for account A sends `as`.
  When the browser is now signed in to account B, the BFF route refuses
  with 409 `answer_in_other_account`, and it calls no gateway. The chat
  says "Switch to A to answer". After the switch, the answer resumes the
  run in its own org.
- **"needs_input" is the asker's.** `/chat/active-sessions` reads the rows of
  the caller (`actor_email`). A viewer of a shared room sees the run as
  `running`.

**Fences (R7), as built.**

| Fence | What it holds |
|---|---|
| `tests/unit/test_pending_ask_store.py` | R8 as the NOBYPASSRLS role: the migration's own policy block, FORCE, two tenants, an unbound session, a write stamped with another org, one move, expiry, the late answer, and the list |
| `tests/unit/test_pending_ask_flow.py` | One row per waiting card, a run end that closes a card no wait settled, park and end, the open card, the late answer, the race, another org, needs_input after a restart, and the pending-asks route |
| `tests/unit/test_tenant_coverage.py` | The table is scoped and never exempt |
| `src/lib/runActivity.test.ts`, `src/components/navBadge.test.ts` | Amber wins over green, on the sidebar and on the phone |
| `src/lib/liveRuns.test.ts` | The poller carries `state` |
| `src/lib/pendingAsks.test.ts`, `src/lib/respondInput.test.ts` | The cards that come back, and the other-account refusal |

**Mutation record (2026-10-10).** Eleven mutants, nine killed on the first
run. The two that lived led to two new tests:

- A policy with no WITH CHECK lived. That mutant does the same thing,
  because Postgres uses USING as the check. The phase files also re-made the policy,
  so the suite now runs the migration alone. FORCE removed and `USING
  (true)` are both killed.
- A resend that did not move the row lived. A test of two answers that race
  now kills it.

**Review of #813 (2026-10-10).** One fix round closed two P1 findings and
three P2 findings:

- **The park pops the Future.** It removes the Future from
  `_pending_user_input` before it cancels the run. Before, ask_questions
  path A and the B1 bridge kept an orphan Future, and a late answer got 200
  and started nothing.
- **No answer is lost.** The park checks the Future again after each await.
  An answer that came in during the park moves the row to `answered`, and
  the run goes on. `resolve_user_input` now sets the result at once on its
  own loop, so the check sees it.
- **Only the asked member.** `/chat/pending-asks` and a late answer read
  only the caller's own rows (`actor_email`). A thread with no chat row
  passes the room gate for any member of the org.
- **A deleted chat asks nothing.** The chat delete closes its rows in the
  same tenant transaction. `/chat/active-sessions` lists a question with no
  live run only when its chat row exists.
- **The question is data.** `compose_card_answer` puts the question in a
  fenced block and breaks a fence marker, a platform tag or a forged "My
  answer:" inside it. The member's answer stays outside the block.
- **The chat asks only when the thread waits.** With the flag off the
  server never says `needs_input`, so the chat sends no pending-asks
  request.

Five more mutants, five killed: the Future left in place, no check after
the move, the actor filter removed twice, and no neutralising pass.

**Known limits.**

- A blocking generative UI is not drawn again from the server. The saved
  reply keeps its event, and its answer takes the same late path.
- A parked confirmation asks again in the new run. The new run calls the
  tool again, so the member approves twice. Nothing approves by itself.
- On a Windows checkout, three source-reading vitest files fail on CRLF.
  They fail on `origin/main` too, and none of them is an S2 file.

### S3 — the top-right activity control

**What.** One control in the shell bar's right side, beside the bell. It shows
an agent icon with the count of live runs. An amber dot, the `warning` tone,
shows when a run needs input.

The control opens one list across apps. Each row names the agent, the chat
title and a status: "running 2 min · latest step", "needs your answer", or
"new reply". A tap opens that chat in its app.

**Done when.** The control reads the S1 store, with no second poll. It reads
the S2 `state`. It draws on every page, desktop and phone.

**Fence.** A render test of the three row states, and the S1 tree sweep.

**As built (2026-10-10).** No flag. The control only reads the S1 store, and
it writes nothing. ⚠️ It works with the shell bar flag `NEXT_PUBLIC_SHELL_BAR`
ON or OFF (review of #815). The flag decides only WHERE the desktop draws the
control. Nothing in S3 is dark behind it.

1. **The control.** `lib/shell/ActivityControl.tsx` draws the `Bot` icon with
   `NavBadge` and `runBadge`, the one colour rule. Green counts the running
   runs. Amber counts the runs that need the member, and amber wins. With no
   run, the control shows the icon alone. It is ONE component in three
   places:
   - with the shell bar ON, the bar's right end, after the app's tools and
     the bell (`ShellBar.tsx`);
   - with the shell bar OFF, the sidebar head, beside the fold control
     (`Sidebar.tsx`), because that layout has no top bar;
   - on the phone, the Menu drawer, in both cases (`AppShell.tsx`).
2. **The panel.** `ActivityHost` mounts one `Modal`, and `AppShell` renders
   it in every layout. It sits under the bar (`end`), near the top when the
   bar is off (`top`), or as a bottom sheet on the phone (`sheet`). Escape and
   an outside press close it, Tab stays
   in it, and focus goes back to the control. The rows come from
   `useActivityRows`, which reads the same two sources as the badges: the S1
   store and the runs of this tab. Newest first. A row shows the agent, its
   app, the chat title and a status: "Running · 2 min · Search tasks" or
   "Needs your answer". With no run, it says "No assistants are running."
3. **The link.** No chat link existed before S3. The shell job door
   (`lib/shell/doJob.tsx`) is the deep-link idiom, so a row links to
   `<app>?do=open-chat&fill.session=<thread id>`. Chat, My Tasks, Projects
   and Email each declare `<ShellJob id={OPEN_CHAT_JOB} ungated>`. An
   `ungated` job listens with the shell bar flag off too, because this link
   does not come from the command bar and it saves nothing. The three apps
   open their assistant and call `askRailSession`. Then `useAgentSessions`
   opens the chat once its restore is done. Every other agent opens in Chat.
   App Workshop opens in Chat too, because its chat is per app, not per id.
   A run on a pane that the member cannot see also opens in Chat.
4. **The step.** `/chat/active-sessions` adds `lastStep` to a running row
   (`stream_relay.latest_step`). It reads the tail of the run's own stream,
   one XREVRANGE with COUNT 12, and adds no key.
   - ⚠️ **The step comes only from a tool NAME or a fixed label.** A tool
     start gives its name in words, and an MCP name loses its server prefix.
     A text delta gives "Writing a reply", and a thinking delta gives
     "Thinking". The text of a progress update, a result or an argument is
     never a step. A Copilot partial result with no tool id becomes a
     progress update that carries raw tool output, such as lines of a `.env`
     file (review of #815).
   - A tool name must look like an identifier, so free text never passes.
   - The server takes out tags, angle brackets and control characters, and it
     caps the step at 60 characters. The browser caps it again and draws it
     as text.
   - **Steps only on demand.** The route reads a step only for `?steps=1`.
     The poll sends it only while the panel is open (`wantLiveSteps`), and
     the BFF passes on only that parameter. So the 5 s badge poll reads no
     stream.
5. **The phone.** The phone has no shell row. The Menu drawer shows the same
   control beside "Search or ask anything", or beside the word "Assistants"
   when the shell bar is off. A tap opens the same panel as a bottom sheet.
   The Menu tab keeps its S1 badge, so a member sees the count before the
   drawer opens.
6. **Visibility.** The panel lists only the rows that `/chat/active-sessions`
   returns for the member and org, plus the runs of this tab. An asked chat
   opens only when `findSession` finds it in the member's own list.

**Where the build differs from the text above.**

- **"New reply" waits for S5.** The server keeps no "finished, unread" state
  for a run. A run leaves the list when it ends. So the panel shows two row
  states, and S5 adds the third with its unread rule.
- **A chat that is gone.** A link to a chat that is not in the member's list
  opens nothing. Chat says "That chat is no longer available."

**Fences (R7), as built.**

| Fence | What it holds |
|---|---|
| `src/lib/shell/activityControl.test.ts` | The quiet, green and amber states. The rows, their order and their text. The link of each agent, and the ungated job on each app that a link names. The panel is a `Modal` that hands back `onClose`. The empty state. A step drawn as text. The control in the bar, in the sidebar head and in the drawer, and the panel in every layout. Steps asked for only while the panel is open, and the BFF passes on only `steps=1` |
| `src/lib/railAsk.test.ts` | An ask is read and never taken, it opens once in each rail, and it goes stale. `findSession` finds only the member's own chat |
| `src/lib/liveRuns.test.ts` | The store carries the step, caps it, and publishes a new step |
| `tests/unit/test_run_last_step.py` | The step rule, tool output that never reaches the step, the MCP prefix, the 60-character cap, plain text, one tail read, the route, a question with no step, and a badge poll that reads no stream |

**Mutation record (2026-10-10).** Nine mutants, nine killed:

- every row sent to Chat, and amber that does not win
- an ask taken on read, and an ask that never goes stale
- the step left out of the publish key
- no cap, angle brackets kept, the whole stream read, and a step read for a
  question

The review fix round added six more mutants, six killed:

- progress text back in the step, and a free-text tool name allowed
- a step read on every poll, and a poll that always asks for steps
- no control in the sidebar head, and the open-chat job gated on the flag

**Visual review (2026-10-10).** The rig ran the control and the open panel
with two running chats and one that needs the member. It captured dark,
light, compact and violet accent at 1440, and the phone at 390. It also drove
Escape, the focus return, the Tab trap, and two links. A My Tasks row opened
the asked chat in the My Tasks rail, and a Chat row opened it in Chat.

**Known limits.**

- The control in the phone drawer shows the counts of the moment the drawer
  opened, like the drawer links (S1).
- A member with no `chat` feature cannot open a Chat link. The same gap holds
  for the S1 badge.

### S4 — the server saves the prompt at run start

**What.** Today the browser saves the member's turn (`lib/sessions.ts`, a
write-through cache). A tab that closes before the save drops the turn, and
the run goes on without it. The gateway saves the turn when the run starts.
The write is idempotent by message id, so the browser's save stays harmless.

**Done when.** A tab closed one second after a send still shows the prompt on
the next open. R8 binds the upsert.

**Fence.** A `tests/unit/test_chat_prompt_saved_at_start.py` suite under R8.
It closes the stream after the first event, then reads the turn back from
Postgres.

**As built (2026-10-10).** No flag. The write is idempotent by id, and the
browser save already writes the same row.

1. **The id.** The chat sends `userMessageId` and `userMessageTs` on every
   send, not only on an edit. The BFF forwards them as `user_message_id` and
   `user_message_ts`. With no id, the server saves nothing. That covers an
   old bundle and an API caller.
2. **The save point.** `/agent/run/stream` saves the turn after every
   refusal and after the supersede, and before the agent row of the run. The
   write goes through `_upsert_messages`, bound to the org of the session.
   The author is the caller from the session, never the payload. A caller
   who may not send in the room saves nothing (`prompt_to_save`). The turn
   and the agent row go in ONE worker call (`_mint_run_row`), with one
   `_ensure_session` and one 2 s budget. So a stalled database holds the
   first byte for one budget, not two. A browser id that names an agent row
   changes nothing, because the seam declines it.
3. **The time.** The row takes the browser's own time stamp, which the
   browser save also writes. The earlier turns carry the browser clock, so a
   server clock could sort the turn above the reply before it.
4. **An edit.** The supersede removes the old turn first. The new turn then
   goes in with its "Edited" marker.
5. **A steer.** The spec was silent. The route saves a steered turn with its
   actor, so it shows on a reload. The save is a background task that runs
   after the 202 is sent. The route saves nothing for a stop, or for a turn
   that it drops.
6. **The history.** A first turn sends no history, so the executor reads
   the store. That read now leaves out the current turn, which the model
   reads as `message`.
7. **`/copilot/chat`.** No client calls it, so S4 does not change it.

**Mutation record (2026-10-10).** Five mutants, five killed: no save at run
start, a server id in place of the browser id, no author, no `can_send`
check, and no history filter. The follow-up added three more, and the suite
killed all three: the save split from the mint with a second budget, the steered
save awaited before the 202, and the kind check of the seam removed.

### S5 — signals outside an open chat

**What.**

- A dot on a chat that got a reply the member has not read.
- A toast when a run ends while its chat is closed.
- In a hidden tab, the title and the favicon carry the count, for example
  "(1) Needs you · Metorite".
- On the phone, an agent pill above the bottom nav that shows the live step.

**Done when.** Each signal reads the S1 store and the S2 `state`. The toast
uses the shell's one toast viewport.

**Fence.** A `src/lib/runSignals.test.ts` vitest. It pins the title text for
each state, the unread rule, and one toast for each finished run.

**As built (2026-10-10).** No flag. Every signal reads the S1 store and this
tab's own runs, and no signal polls. The rules are pure functions in
`lib/runSignals.ts`. `lib/shell/RunSignals.tsx` feeds them and draws.

1. **Where unread lives.** It lives in the browser, in the member's own chat
   namespace. `chatKey("unread")` builds the key, so the scope is the email
   and the org. A switch of account or org reads another map and deletes
   nothing. A sign-out clears the map with the rest of that account.
   - **Why not the server.** A server `read_at` needs a table, a FORCE RLS
     policy and a migration (R1, R5, R6, R8). The spec asks for a dot, and the
     server keeps no read state today.
   - **The cost.** A reply read on one device stays unread on another.
2. **The finish rule.** A run that leaves the live list marks its chat
   unread. The run must have been on the server list once. A run that only
   this tab knew can leave the tab's set while it still runs. A new member
   empties the list, and `liveRunsGeneration` tells the tracker that this is
   no finish.
3. **Read.** An `AgentChat` holds its thread open (`useChatOpen`). A chat
   that the member watches in a visible tab marks nothing. A run that ends in
   an open chat of a hidden tab marks it, and the tab clears it when it shows.
   A deleted or forgotten chat leaves the map. The map keeps 50 chats, and an
   entry goes after 7 days.
4. **The toast.** "<Agent> finished", the chat title, and "Open", for 8 s.
   Open takes the `open-chat` link of S3. The open chat never gets a toast. A
   run that ends while the tab is hidden gets its toast when the tab shows.
   Two tabs that see one run end show one toast: the entry records the run's
   start time and a `toasted` mark.
5. **The badge.** `runBadge` takes a third count. The order is what the
   member must do: amber (needs you), then blue (`info`, a new reply), then
   green (running). The control, the sidebar, the drawer and the phone tabs
   all read it. The panel adds a "New reply · 5 min ago" row. Each chat list
   draws `components/SessionRunDot.tsx`, and a folded group on /chat shows a
   blue dot too.
6. **The hidden tab.** "(N) Needs you · Metorite" wins over "(N) New reply ·
   Metorite". The favicon swaps to `public/favicon-needs.png` or
   `public/favicon-reply.png`, which were made once. The page's own title and
   icon come back when the tab shows.
7. **The phone pill.** It shows the newest live run that is not an open chat,
   its step or "Working", and "+N". A run that waits shows "Needs your
   answer". It sits at `z-50` on the bottom nav, with the safe-area inset.
   While it shows, `data-agent-pill` on `<html>` lifts the toasts and the
   page's last row above it. The line moves only with motion allowed. Only the
   pill asks for `?steps=1`, and only while it shows in a visible tab.

**Fences (R7), as built.**

| Fence | What it holds |
|---|---|
| `src/lib/runSignals.test.ts` | The unread rule, scoped by member and org. A new member, a tab-only run and a watched chat mark nothing. One toast for each run, never for the open chat, and one across two tabs. The title text for each state, the favicon swap and the restore. The pill: shows, hides, links, +N, tokens, motion and steps. The badge order, the panel row and the dot on the four chat lists |
| `src/components/navBadge.test.ts` | The phone tab and drawer badges take the unread count |

**Mutation record (2026-10-10).** Thirteen mutants, thirteen killed:

- the server-seen rule, the open chat, the member reset, a tracker that does
  not move on
- the title order, the title restore, the favicon restore
- the pill that shows the open chat
- a hidden tab that toasts at once, another tab's toast ignored, an open that
  clears nothing
- blue under green in the badge, and a deleted chat that keeps its dot

**Visual review (2026-10-10).** The rig ran /chat with one run that ended and
one that went on, and the phone at 390 with three runs. It captured the
toast, the chat list and the panel in dark and light at 1440, and the pill in
dark and light at 390. In a real page, a hidden tab read "(1) New reply ·
Metorite" with the reply favicon, and both came back when the tab showed. The
console showed no error.

**Review of #818 (2026-10-10).** One fix round closed two P1 findings and
the P2 findings.

- **An outage is not an end.** A Redis, Postgres or question read error in
  the gateway sets `X-Runs-Partial: 1`. The BFF passes it on, and it marks
  its own lost call the same way. The poller keeps its last list on a partial
  answer. The mark is a header, not a 503, because every own-API 5xx raises
  "Metorite is updating".
- **Two misses.** A run ends only when two complete polls in a row lack it.
  A deploy loses two polls at most (`lib/gatewayFetch.ts`).
- **A parked question never ends.** An answer or an expiry is no reply. Its
  identity is `ask:<thread>:<kind>`, the same in every tab.
- **One summary toast.** Three or more runs that end in one poll show "N
  assistants finished". Its Open shows the activity panel.
- **Another tab.** A chat that shows in a visible tab writes a heartbeat
  (`chatKey("open")`, 12 s). Another tab then marks nothing and shows no
  toast.
- **Interrupted.** A run that ends within 90 s of an outage says "<Agent>
  was interrupted".
- **The dock.** `AgentChat` takes `visible`. The Projects dock passes false
  while a task hides the chat.
- An unread entry older than 7 days does not count, before any write. A
  finish waits until a member scope is bound.

Twelve more mutants, twelve killed. One lived on the first run: the test of
the question read used a broken Postgres, which marked the list by itself.
The test now runs on a healthy fake.

**Known limits.**

- Unread does not follow the member to another device.
- A run ends 10 s after the fact in a visible tab, and 60 s in a hidden tab.

### S6 — the activity of the other signed-in accounts

**What.** The S3 list adds the runs of the other accounts on this device. The
BFF reads `active-sessions` with each signed-in slot's own cookie. A tap on
another account's row switches account (`lib/accountSwitch.ts`), then opens
the chat.

**Done when.** A run in account B shows in account A's list, with B's name.
No row of B reaches A's tenant cache. The full review loop runs, because this
reads a second identity.

**Fence.** A vitest on the BFF route. It proves that each slot's request
carries that slot's cookie only, and that a row keeps its account label.

---

## 5. Deferred — owner decisions

| Id | Decision | Recommendation | Gate |
|---|---|---|---|
| D-1 | A separate run-worker service, so a deploy never stops a run | Build after S2, which makes a stop cheap to resume | **OWNER-GATE** |
| D-2 | Opt-in web push for "needs input" | After S5 | **OWNER-GATE** |
| D-3 | Caps on concurrent runs | 5 per member and 20 per org | **OWNER-GATE** |

An agent must refuse these by name until the owner decides.

---

## 6. File paths

| Path | Role |
|---|---|
| `workbench/control_plane/src/lib/liveRuns.ts` | The one poller (S1) |
| `workbench/control_plane/src/lib/runActivity.ts` | The agent-to-app map and the counts (S1), the panel rows and the chat link (S3) |
| `workbench/control_plane/src/lib/shell/ActivityControl.tsx` | The activity control and its panel (S3) |
| `workbench/control_plane/src/lib/railSessions.ts` | `askRailSession`, `findSession` (S3) |
| `workbench/control_plane/src/lib/runSignals.ts` | The unread map, the finish rule, the toast, the tab signal and the pill model (S5) |
| `workbench/control_plane/src/lib/shell/RunSignals.tsx` | `RunSignalsHost` and `AgentPill` (S5) |
| `workbench/control_plane/src/components/SessionRunDot.tsx` | The dot on a chat row in every chat list (S5) |
| `workbench/control_plane/src/hooks/useActiveSessions.ts` | `useActiveSessions` and `useRunActivity` |
| `workbench/control_plane/src/components/NavBadge.tsx` | The one nav count |
| `workbench/control_plane/src/components/Sidebar.tsx` | `NavLink`, `paneBadge` |
| `workbench/control_plane/src/components/AppShell.tsx` | The phone drawer and bottom bar |
| `workbench/control_plane/src/lib/chatStore.ts` | `releaseLoading`, `setSessionAgent` |
| `workbench/control_plane/src/hooks/useAgentChat.ts` | The reattach loop |
| `workbench/control_plane/src/lib/nav.ts` | `NavPane.agent` |
| `apps/services/gateway/gateway/routes/chat.py` | `GET /chat/active-sessions` (S2 adds `state`) |
| `apps/services/gateway/gateway/routes/agent.py` | `POST /agent/respond-input` (S2) |
| `apps/services/orchestrator/orchestrator/executor.py` | `_pending_user_input` (S2) |
| `apps/services/orchestrator/orchestrator/stream_relay.py` | `run_detached`, `list_live_runs` |

---

## 7. Verification commands

From `workbench/control_plane`:

```bash
npx vitest run src/lib/liveRuns.test.ts src/lib/runActivity.test.ts \
  src/lib/chatStore.test.ts src/components/navBadge.test.ts
npx vitest run src/lib/theme/
npx tsc --noEmit
npx vitest run
```

For S2 and S4, start the scratch database first (`engineering_practice.md`
§1.1), then run the named `tests/unit/` files with `uv run pytest`.
