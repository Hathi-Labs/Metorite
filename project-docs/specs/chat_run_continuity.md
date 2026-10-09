# Chat run continuity — an assistant run outlives the tab, the account and the org

**Status: ACTIVE.** Board row **WS-51**. Written 2026-10-10 from the owner's
ask of the same day. **Verified against code on 2026-10-10**, at `origin/main`
`e5644259b`.

| Slice | State |
|---|---|
| S1 — one activity feed and the nav badges | **BUILT** 2026-10-10, branch `chat-activity-badges` |
| S2 — a durable "needs input" | **BUILT** 2026-10-10, branch `ws51-s2-needs-input`, dark behind `CHAT_DURABLE_ASKS` |
| S3 — the top-right activity control | Spec only |
| S4 — the server saves the prompt at run start | Spec only |
| S5 — unread replies, the toast, the tab title and the phone pill | Spec only |
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
| `tests/unit/test_pending_ask_flow.py` | One row per waiting card, park and end, the open card, the late answer, the race, another org, needs_input after a restart, and the pending-asks route |
| `tests/unit/test_tenant_coverage.py` | The table is scoped and never exempt |
| `src/lib/runActivity.test.ts`, `src/components/navBadge.test.ts` | Amber wins over green, on the sidebar and on the phone |
| `src/lib/liveRuns.test.ts` | The poller carries `state` |
| `src/lib/pendingAsks.test.ts`, `src/lib/respondInput.test.ts` | The cards that come back, and the other-account refusal |

**Mutation record (2026-10-10).** Ten mutants, eight killed on the first
run. The two that lived led to two new tests:

- A policy with no WITH CHECK lived. That mutant does the same thing,
  because Postgres uses USING as the check. The phase files also re-made the policy,
  so the suite now runs the migration alone. FORCE removed and `USING
  (true)` are both killed.
- A resend that did not move the row lived. A test of two answers that race
  now kills it.

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
| `workbench/control_plane/src/lib/runActivity.ts` | The agent-to-app map and the counts (S1) |
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
