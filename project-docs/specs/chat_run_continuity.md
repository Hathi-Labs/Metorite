# Chat run continuity — an assistant run outlives the tab, the account and the org

**Status: ACTIVE.** Board row **WS-51**. Written 2026-10-10 from the owner's
ask of the same day. **Verified against code on 2026-10-10**, at `origin/main`
`e5644259b`.

**§4.12 (RESUME, task 3255) is a SPEC of 2026-11-14, verified at
`3040ac9e4`. RESUME-1a, RESUME-2a, RESUME-2b, RESUME-4a-1, RESUME-4a-2 and RESUME-5 are built, and all six are dark. RESUME-2d (2026-11-19, no flag) and RESUME-2e (2026-11-20, no flag of its own) are built. RESUME-3a (the chat client, PR 2443) is built (the rows below).** It makes a chat pick up exactly where
it left off. A late Approve runs the stored call of its card once, and a
release keeps the work.

| Slice | State |
|---|---|
| S1 — one activity feed and the nav badges | **BUILT** 2026-10-10, branch `chat-activity-badges` |
| S2 — a durable "needs input" | **BUILT** 2026-10-10, branch `ws51-s2-needs-input`, dark behind `CHAT_DURABLE_ASKS` |
| S2 item 8 — a late approve acts once (HRD-22, task 3231, HRD-22b, task 3294) | **BUILT** 2026-11-14, branch `mt-1740009c-late-approve`. HRD-22b closes the gaps of its second review (across chats, a Redis fault, the grant race). Dark behind `CHAT_LATE_APPROVE_ORGS`. The production flip is OWNER-GATE (`enforcement-flip`), and waits on D-HRD-7 |
| S3 — the top-right activity control | **BUILT** 2026-10-10, branch `ws51-s3-activity`, no flag |
| S4 — the server saves the prompt at run start | **BUILT** 2026-10-10, branch `ws51-s4-prompt-save` |
| S5 — unread replies, the toast, the tab title and the phone pill | **BUILT** 2026-10-10, branch `ws51-s5-attention`, no flag |
| S5 — the amber dot on a waiting chat (task 3600, rung 0, STANDARD, AGENT-SAFE) | **BUILT** 2026-11-17, branch `stu-needs-dot`, no flag. The dot on a chat row in `/chat` and the chat picker says "Needs your answer" in amber when the server marks the thread `needs_input`. It wins over the green and the blue dot. Done when a card opens in chat A, the member opens chat B, and the row of chat A shows the amber dot. The dot goes when the row closes. The side rails keep the old dot. The text of §8 that says `CHAT_DURABLE_ASKS` is off is stale: production has it on |
| S6 — the activity of the other signed-in accounts | Spec only |
| S7 — browser push for "needs your answer" and "finished" | The transport is BUILT and dark (WS-66 PN-1, 2026-10-25). The two chat events are not built (H-289). D-2 is decided: opt-in per member |
| The run cap — 5 live runs per member (D-3) | **BUILT** 2026-10-10, branch `ws51-run-cap-and-todos`, no flag |
| §4.9 — a message sent during a run always steers it | **BUILT** 2026-10-12, branch `composer-always-steer`, no flag |
| §4.9 — a steer is a user turn, and a Projects or CRM card closes on it (task 2494) | **BUILT** 2026-11-09, fix round 1 2026-11-11, branch `steer-redraws-card-mt-fdaefd16`, no flag |
| §4.9 — a closed card says "Not done", and each steer gets an answer (task 3130) | **BUILT** 2026-11-13, fix round 1 2026-11-13, fix round 2 2026-11-14, branch `mt-e29742c6-steer-unsent-card`, no flag |
| §4.10 — one run status in the open chat | **BUILT** 2026-10-19, branch `chat-run-status`, no flag |
| §4.11 — the line of a slow tool (HRD-6 part 1, task 2985) | **BUILT** 2026-11-13, branch `mt-83488e2c-long-reads`, dark behind `CHAT_PROGRESS_NOTICE_ORGS` |
| D-1 — a separate run worker | **DEFERRED** 2026-10-29 on evidence (§5). Not built |
| §4.12 — a chat picks up exactly where it left off (RESUME, task 3255) | **SPEC** 2026-11-14, branch `mt-7c00040b-resume-design`. RESUME-0a to RESUME-8 on rungs 0 to 3. The owner accepted D-RES-1 to D-RES-8 on 2026-11-14 (§4.12.8). RESUME-1a, RESUME-2a, RESUME-2b, RESUME-4a-1, RESUME-4a-2, RESUME-5, RESUME-2d and RESUME-2e are built (the rows below) |
| §4.12 RESUME-1a — a card stores the call that it guards (task 3260) | **BUILT** 2026-11-15, branch `stu-resume1`, dark behind `CHAT_RESUME_ORGS`. Migration 448 adds `chat_pending_ask.stored_call`. RESUME-2a reads it. The production flip is OWNER-GATE (`enforcement-flip`) |
| §4.12 — the retention of a waiting card's row (task 3435) | **BUILT** 2026-11-16, branch `stu-pa-prune`, no new flag and no migration. A prune hook of the job worker clears the stored call of each expired row and deletes a row 7 days after it expires. The member purge deletes the rows that asked a leaver. The prune runs only with `JOB_QUEUE` on, and `JOB_QUEUE` is off on production (§4.12.6, step 1a) |
| §4.12 RESUME-2a — a late Approve runs the stored call once, in a new reply (task 3261) | **BUILT** 2026-11-16, branch `stu-resume2`, dark behind `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS`. The fixes of the review of 2026-11-16 (P1-A, P1-B, P2) are built. The follow-ups of the re-review of 2026-11-16 (P2-1, P2-2, P2-3, task 3467) are built, branch `stu-resume-fu`. The flip needs these first: the prune of task 3435, RESUME-3a (PR 2443), and the flag order of §4.12.4 item 2. The same flag turns on case A of RESUME-2b, so the flip also needs RESUME-3b (task 3475) and RESUME-2d (the reconnect replay obeys the waterline, BUILT 2026-11-19). The case B defect of the audit of 2026-11-16 (a continuation that skipped the session left a stale hit) is fixed by RESUME-4a-1 (PR 2451, merged). No migration. RESUME-2b (task 3460, the row below) builds case A. RESUME-2c (task 3461) is not built. The production flip is OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-2b — a late Approve goes on in the same reply, case A (task 3460) | **BUILT** 2026-11-17, branch `stu-resume2b`, dark behind `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS`, with no migration and no new flag. A parked turn that is the last turn, in a room whose members did not change, keeps its run id, its stream and its reply row. D-RES-7: a park keeps its stream 24 h. Each doubt gives case B, and the review fixes of 2026-11-17 (P1, P2-1, P2-2), of round 2 (P1, P2-a, P2-b) and of round 3 (P2-1) are built. The flip waited for two slices. RESUME-2d closes the gap of round 2, P2-c (BUILT 2026-11-19, the row below). RESUME-3b (task 3475) is the chat side, and it is not built. The production flip is OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-2d — the reconnect replay gives the whole run or none of it (task 3724) | **BUILT** 2026-11-19, branch `mt-82b507bd-resume2d`. No flag, no migration and no new state (D-RES-10). A solo chat replays the same bytes. A reader of a shared room replays the run only when a participant row places the reader in the room at the start of the run, with a margin of 2 s. Any other reader gets only the end frame, marked `withheld: true`. Fences: `tests/unit/test_reconnect_waterline.py` and `tests/unit/test_reconnect_waterline_r8.py` (R8) |
| §4.12 RESUME-2e — the memory of a case A run reads part one once (task 3818) | **BUILT** 2026-11-20, branch `mt-5c919acd-resume2e`. No flag of its own and no migration. It acts only on a case A run, so only where `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both name the org. A plain run and a case B run are as before, byte for byte. Fences: `tests/unit/test_resume_continuation.py` (section 16) and `tests/unit/test_resume_continuation_r8.py` (R8) |
| §4.12 RESUME-3a — the chat takes a resumed run, case B (task 3262) | **BUILT** 2026-11-16, branch `stu-resume3`. No flag of its own. The park words and the quiet end of a park show where `CHAT_DURABLE_ASKS` is on. Only the resume route (`_resume_from_row`, with both resume flags on) sends the 429 cap reason. The `resumed` path shows only where RESUME-2a answers `resumed`. RESUME-3b (task 3475) and RESUME-3c (task 3476) are not built |
| §4.12.7 PARK-LINE — a parked reply names the approved acts that it ran (task 3595, slice 3595-a) | **BUILT** 2026-11-17, branch `stu-park-line`. The rule of an act that ran is positive proof by the tool (`act_ran`). No flag and no migration, so it is live at the merge. Slice 3595-b is not built |
| §4.12 RESUME-4a-1 — a continuation marks the stored native session stale (task 3478) | **BUILT** 2026-11-16, branch `stu-resume4a`, dark behind `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS`, with `MAF_NATIVE_SESSIONS` on. No migration and no new flag. RESUME-4a-2 is the next row. RESUME-4b (task 3479) is not built. The production flip is OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-4a-2 — a late decline reaches the model (task 3478) | **BUILT** 2026-11-17, branch `stu-resume4a2`, dark behind `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS`. The owner decided D-RES-9 (§4.12.8). Server only: no client sends this request until RESUME-7, because the web card sends `REJECT`. The fixes of the review of 2026-11-17 (P2-1, P2-2) are built. One owner question is open (§4.12.4, "What RESUME-4a-2 built"). No migration and no new flag. The production flip is OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-3b — the chat takes case A, the same reply (task 3475) | **BUILT** 2026-11-19, branch `stu-resume3b`. No flag of its own, and no migration. The server field `parkedRunId` shows only where `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both name the org. The client paths show only where RESUME-2b (PR 2583) sends `sameReply` or a `resumed` start. This row replaces what the RESUME-3a row says of RESUME-3b. RESUME-3c (task 3476) is not built. RESUME-3d (task 3785) is built (the row below). The production flip stays OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-3d — a listed continuation names its reply row (task 3785) | **BUILT** 2026-11-19, branch `mt-dbde72d3-resume3d`. No flag of its own, and no migration. The live row of a continuation run carries `messageId`, its reply row, only where `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both name the org. A tab that did not get the 200 then replays the run into that row, with no read of the saved rows, in case A and in case B. With a flag off, each row is as before, byte for byte. The production flip stays OWNER-GATE (`enforcement-flip`) |
| §4.12 RESUME-5 — the stop parks a run that waits on a card (task 3265) | **BUILT** 2026-11-19, branch `resume5`, dark. A run parks at the stop only where `CHAT_DURABLE_ASKS` is on and `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS` both name its org. No migration and no new flag. `gateway-drain.sh` calls `POST /internal/drain/park` once, before its first count. Each waiting run parks in its own task. Its row is `parked`, its reply has no "interrupted" mark, and an Approve after the restart goes on in the same reply (§4.12.5). A card that opens after the call does not park. The `gateway-drain.sh` edit is under the dated `deploy-write` grant. The production flip is OWNER-GATE (`enforcement-flip`), and it waits for the flip of RESUME-2a and RESUME-2b |

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
- An org cap on concurrent runs. The owner refused it (D-3, §4.8).
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
8. **A late approve acts once (HRD-22, task 3231, BUILT 2026-11-14, dark).**
   The build is AGENT-SAFE. The flag flip on production is OWNER-GATE
   (`enforcement-flip`). `chat_failure_modes.md` row C6 is the class, and
   D-HRD-7 (PROPOSED) is the rule. The module is `acb_skills/late_approve.py`.
   Review round 1 (2026-11-14) changed the keys and added the rule that an
   act runs once.
   - **The flag.** `CHAT_LATE_APPROVE_ORGS` is a comma list of org ids, or
     `*`. Empty is off, and that is the default. Off, nothing is read or
     written, and each card and each message is as before, byte for byte.
     The one reader is `late_approve.late_approve_on`. A SIGHUP reload
     applies a change (`docs/flag_reload.md`). It needs `CHAT_DURABLE_ASKS`,
     because only a durable row parks.
   - **The rule.** A late tap on Approve is the member's own consent. It
     approves one act: the same card, in the same thread, for the same
     member, within 30 minutes. The new run calls the tool again, and the
     gate says yes once, with no new card.
   - **⚠️ Two limits for the owner (D-HRD-7).** The 30 minutes start at the
     tap, not at the card. A parked card stays answerable for 7 days
     (`CHAT_ASK_TTL_HOURS`), so a card that parked 6 days ago can still act,
     if its text and its act keys are still equal. The build keeps both
     limits as they are, because they are the owner's decision (D-HRD-7,
     and D-RES-1 of §4.12).
   - **The act.** The act of a card is its text and its act keys. The text is
     `whatsapp_acts.card_text` of the clipped title, detail and context, with
     the facts that the tool bound (`whatsapp_acts.card_facts`, WAC-4). The
     act keys come from `ask_tools.act_keys`. A digest of the act names each
     record. So twin cards of one act share one record, and a card of another
     act never reads, uses or removes it.
   - **The cards that take a grant.** Only a plain confirmation card of
     `request_confirmation`: no rows, no reply form, and no held send id. The
     card is not cut: no field lost text to the clip, and the body holds no
     cut mark (`card_body.is_cut`). The real tool of the call (`card_call`
     binds it) passes the three tests of `egress.is_card_write_tool`. Its name
     is on `HELD_CARD_WRITE_TOOLS`, its annotation says `card_write`, and its
     parameters are the pinned set. So `bulk_update`, a send, an email card,
     an egress card, a card of ticked rows and a WhatsApp run never take one.
   - **Step 1, the offer (the run).** After the row moves to `parked`, and
     while the card's Future still waits, `pending_ask.park` records the act.
     It writes `key("chat-late-ask", member, thread, request id)` = the
     digest, and `key("chat-late-offer", member, thread, digest)` = the act.
     Both live until the row expires, and no longer. The member is the
     verified member of the run, and the row must have asked that member.
   - **Step 2, the grant (the gateway).** `_late_answer_from_row` calls
     `grant_from_offer` only after `move_ask` moved the row to `answered`. It
     takes the digest of the row once. Only the answer `APPROVE` of a parked
     `confirmation` row then takes the offer and writes
     `key("chat-late-approve", member, thread, digest)` for 30 minutes. The
     org and the member come from the session. The thread and the request id
     come from the row.
   - **Step 3, the use (the card gate).** Before a plain card goes out,
     `request_confirmation` takes the grant of its own digest once (GETDEL).
     It says yes only when the text and the act keys are equal too. The org,
     the member and the thread come from the run, never from input. A miss, a
     mismatch and a Redis fault each show the normal card.
   - **An act runs once.** Each approve of a plain card marks the act SPENT
     on its offer key, and in a member-level key (`chat-late-spent`, with no
     thread), and removes its grant. That covers a live approve and a used
     grant. The marks last as long as a row (7 days by default). The rule
     after HRD-22b (task 3294):
     1. **Across chats.** A twin card in chat A takes no grant after the
        member approved its twin in chat B, because the member-level mark
        reads SPENT. A use marks the member-level key with ONE `SET … GET`,
        which alone decides, so a live approve that lands between the grant
        and its use makes the use refuse, and of two uses at the same moment
        one writes.
     2. **A fault of Redis.** The grant also asks Postgres. A confirmation
        row of the same member, in any thread, with the same title, detail
        and context, answered `APPROVE` after this row was asked, means that
        the act ran. An `APPROVE` with edits counts. The grant refuses then,
        and when the read fails or the row cannot be compared. The RESUME path
        (`_resume_from_row`) asks the same read before it claims the stored
        call, and a twin that ran takes the old path, so a card shows.
     3. **A mark that expired.** The Postgres read also stops a stale card
        that finds the offer of a new twin.
     Each refusal shows the normal card (the safe side). The cost: when the
     member repeats an identical act within the life of the marks, its late
     approve shows one more card.
   - **The new message.** When the gateway writes a grant, the late answer
     gets one more paragraph after `My answer: APPROVE`. It says that the
     member approved after the chat stopped waiting. It tells the model not
     to run the call again if it already ran, and else to make it once with
     the same values. The chat still draws the turn as an approve
     (`cardAnswer.ts`). With no grant, the message is as before.
   - **The offer goes with its row.** A "Not now", a `REJECT` and an answer
     during the park write nothing and drop the offer. A twin of the same act
     then shows its card: the safe side. A chat delete and an edit close rows
     in SQL and do not call Redis. A closed row never parks again, so its own
     offer is inert. After an edit, a later twin in the same thread can still
     reach that offer by its digest, and its tap is the member's consent to
     the same act. The offers expire with the row.
   - **No migration.** Each record lives in tenant Redis, through
     `acb_common.tenant_redis`. `chat_pending_ask` does not change (R6).

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
| `tests/unit/test_late_approve_grant.py` | Item 8, hermetic: the offer at the park, the grant after the move, one use, each card that takes no grant, a changed card, a decline, the flag off, a Redis fault, the new message, and the place of the grant write |
| `tests/unit/test_late_approve_grant_r8.py` | Item 8, on a real Postgres as the NOBYPASSRLS role and a private real Redis (`HELD_R8_REDIS_URL`): the production sequence (one write, no second card), a changed card, a decline, another thread, another member, another org, and a second use |
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
- With `CHAT_LATE_APPROVE_ORGS` off, a parked confirmation asks again in
  the new run, and the member approves twice. With it on (item 8), a late
  tap is the member's own consent. It approves the same card once, in the
  same thread, within 30 minutes. Any other card asks again, and nothing
  approves without a tap of the member.
- On a Windows checkout, three source-reading vitest files fail on CRLF.
  They fail on `origin/main` too, and none of them is an S2 file.

**The display rule (owner report, 2026-10-13).** After a restart, the owner
approved a "Create 9 tasks" card. His bubble then showed the fenced envelope,
`<<<asked-question>>>` and all. The rule is now this:

1. **The model reads the envelope, and the member never does.** The 409
   `run_restarted` carries two parts. `resumeMessage` is the envelope, and
   `resumeDisplay` is what the card asked and what the member chose
   (`gateway/chat_recovery.py`, `CardResend`).
2. **The display is the `card_answer` custom event on the member's turn.**
   This is the same seam as the `edited` marker. The chat stamps it, and both
   saves keep it in `custom_events`: the run route's save at run start
   (`prompt_to_save`) and the browser's save. The model reads only the
   turn's content.
3. **The bubble draws the answered card.** `CardAnswerContent` draws inside
   the member's normal bubble. It shows "Approved", "Approved 7 of 9",
   "Rejected" or "Your answer", the question, and then the approved rows by
   name or the member's own words. A card answer offers no Edit.
4. **An old row still draws as a card.** A row saved before this rule holds
   only the envelope. `parseCardAnswerEnvelope` reads that exact shape,
   anchored at both ends, for display only. Other text stays plain.
5. **A rows card lists its rows inside the quote.** The answer names row ids
   (`row-1`), and the next run reads only text. So the quoted block lists each
   row by id and label. The neutralising pass covers each label. A card with no
   rows keeps its envelope byte for byte.
6. **A blocking generative UI stays answered.** Its run died before its tool
   result kept the answer. The chat reads the answer from the `card_answer`
   event instead (`genUiAnswersFromTurns`).

The notice now says what has happened: "Your answer went to it as a new
message."

**Status, 2026-10-17: BUILT, branch `dismissible-notices`.** The two restart
notices and the other-account notice are one slim line with a Dismiss
control, and the dismissal stays after a reload (`isInfoOnlyNotice`).

**Review of #942 (2026-10-13).** One fix round closed four P2 findings:

- **The neutralising pass sees look-alikes.** Before its ASCII patterns, it
  applies NFKC, removes each format character (zero width, bidi), and maps
  each Unicode dash to `-`. A row label keeps 500 characters, the card's own
  `ROW_LABEL_MAX`, so the next run reads the title the member approved.
- **A display must agree with its words.** A `card_answer` display stays only
  on an envelope that has the same question, decision, rows and words. The
  gateway checks it on both writes (`card_answer_marker`). The chat checks it
  again on read and draws a mismatch as plain text. A generative UI card
  locks only from the viewer's own turns.
- **A busy resend steers, and is not lost.** `sendMessage` now reports what
  became of a send (`onSettled`). After #920, a resend that meets a live run
  steers it, through the composer's own path (`sendRoute`, `steerMessage`).
  The live run then reads the answer at once. The steer bubble carries the
  `card_answer` display, and the gateway saves the row with both marks. The
  notice follows the server's answer. A folded steer reads "Your answer went
  into the current reply." A new run reads "went to it as a new
  message". A refused steer keeps its card open and shows the reason.
- **An answer whose card left the stream** draws as "Approved" with its row
  count, never as the raw `APPROVE` JSON.

**After #920 (2026-10-13).** The steer bubble draws the answered card inside the steer box,
with the steer cue under it. A row with both marks draws the same after a reload.

Nine more mutants, nine killed. Known limit: a resend that the run cap refuses
keeps its card open. But the durable row is already `answered`, so a second
answer to a parked card gets the old 409.

**Fences (R7).** `src/lib/cardAnswer.test.ts`,
`tests/unit/test_card_answer_display.py`, the R8 case in
`tests/unit/test_chat_prompt_saved_at_start.py`, and
`e2e/chat-card-restart.spec.ts`.

### S3 — the top-right activity control

> ⚠️ **Amended 2026-10-12 by D99.** With `NEXT_PUBLIC_SHELL_CHAT` on, the control leaves the
> shell bar and sits in the chat bar at the foot of the page, and a row opens its chat in the
> one assistant. It is the same component, and it reads the same store.
> `navigation_shell.md` §7.1 owns the place.

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
     start gives its name in words. An MCP tool start shows
     "<server label>: <tool in words>" from the server's stamp
     (`chat_connected_systems.md` §3.8). The stamp holds a name and a label,
     and never an argument. Other names lose their prefix.
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

### S7 — browser push (decided 2026-10-10. The transport is built, 2026-10-25. The two events are not built)

**The decision.** The owner called web push "a good idea" on 2026-10-10. So
D-2 is decided: push is opt-in, per member. Nothing is built yet.

> ⚠️ **2026-10-25, D114. WS-66 builds this transport as PN-1**, because
> reminders need it first. The design of the iOS and Android web app is in
> `calendar_events_reminders.md` §5.9. Two items change. Item 4 grows: push
> carries every card that Reach delivers (RE-9), and the two events below. Item 5 has one exception: a reminder push holds its title and time,
> unless the member hides details on the lock screen. Every other push keeps
> item 5.

**What.** A push to the member's device when one of their own runs needs an
answer, or finishes, while no tab of theirs shows it.

**Done when.**

1. **The keys.** We generate the VAPID key pair once, and we store it through
   the existing secrets path (`scripts/secrets.sh`). No key goes in the tree,
   and no customer sets up a key.
2. **The service worker.** One service worker receives the push and shows the
   notification. A tap opens the chat through the S3 `open-chat` link.
3. **The opt-in.** A member turns push on with a toggle in their own
   settings. The browser asks for permission only after an explicit click on
   that toggle, never on page load.
4. **The events.** Only two events send a push: "needs your answer" (S2) and
   "finished" (S5). Only the member's own runs send one. A run in a shared
   room pushes to its actor only.
5. **No content.** The payload holds the agent name and a fixed title, such
   as "Projects assistant needs your answer". It holds no message text, no
   question and no chat title, so the push service reads no content.
6. **The subscriptions.** Each device subscription is a row, scoped to the
   org under FORCE ROW LEVEL SECURITY (R5). Take the migration number at
   build time (R1). R8 binds the policy.
7. **Sign-out.** A sign-out deletes the subscription of that device, and the
   service worker unsubscribes.

**Fence (R7).** A `tests/unit/test_push_subscriptions.py` suite under R8: the
policy, two tenants, and the delete at sign-out. A `src/lib/push.test.ts`
vitest: no prompt without a click, and a payload with no content field.

### §4.8 — the run cap (D-3, BUILT 2026-10-10)

**The decision.** The owner, 2026-10-10: "Do not limit the organization
because an organization might have many people, but possibly we can limit 5
agent runs concurrently per user." So a member may have at most 5 live runs.
There is no org cap.

**As built.** No flag. The setting is `CHAT_MAX_RUNS_PER_MEMBER`, default 5,
and 0 turns the cap off. `orchestrator/run_cap.py` holds the rules.

1. **Where.** `POST /agent/run/stream` asks `admit_member_run` after the steer
   decision. A steer into the member's own live run returns 202 before the
   check, so it never counts. The check comes before the memory read, the
   Graphiti episode, the supersede, the prompt save (S4) and the mint.
2. **The count.** The member's own entries in `cc:<org>:liveruns` (#791),
   read through `acb_common.tenant_redis`. An entry counts only while
   `run_liveness` says `live` (#797). A run that ended, a dead process's run
   and a parked question hold no slot. The thread about to start does not
   count, because a new run on it replaces the old one.
3. **One email, one org.** The index is per org. One email resolves to one
   org today, so the per-org count is the per-member count. If a member of
   two orgs arrives, that member could run 5 in each org. The fix is then a
   second index keyed by member.
4. **Who is exempt.** A caller with no member address (automation, cron, a
   service) is not capped. An automation run on a member's behalf does not
   count either. Those paths bind no session member, so their index entry
   has no actor, and an entry with no actor matches nobody.
5. **The refusal.** HTTP 429 with `{"error": "too_many_runs", "limit": 5,
   "running": [thread ids]}`. Nothing is saved, filed or minted. The chat
   route names the code `too_many_runs` from the body, so a plain 429 stays
   `rate_limited`.
6. **The race.** The run registers its index entry only when its stream
   starts. So the check and a reservation are one step, under a per-member
   `SET NX` lock with a 3 s TTL, as `cc:recover` does in #797. The
   reservation (`cc:<org>:runslots`) holds a slot until the run registers, or
   for 60 s. A refusal after the check releases it.
7. **The client.** One notice in the run-error idiom: "You have 5 assistants
   running. Wait for one to finish or stop one, then send again." Its button
   is "Open activity", which opens the S3 panel. The turn leaves the thread,
   and its words go back to the composer. A 429 is not an update outage, so
   nothing is held and nothing retries. An edit that the cap refuses keeps
   the old turn and says why.
8. **No browser row.** On the gateway path a new turn waits for the server's
   answer before the chat saves it (`awaitingServer`). S4 already saves an
   accepted turn on the server, so this costs nothing, and a refused turn
   leaves no row. A send that an app update holds (#797) carries the same
   mark until the server takes it (review of #821).
9. **The composer.** The refused words go back to the composer. Text that
   the member typed since keeps its place, and the refused words follow it
   after a blank line (`mergeCappedText`).
10. **The room.** The shared room's `USER_MESSAGE` event goes out after the
    cap, so a refused send shows the other members nothing. A steer still
    sends it.

**Over-admission, and when.** The lock holds the cap exactly under a true
race. Two cases can admit one run too many. The first is a lock wait that
runs out, because a holder stalled past the TTL. The second is a run that
takes more than 60 s between the check and its stream start. A Redis error
admits the run (fail open), as `register_live_run` does.

**Fences (R7).**

| Fence | What it holds |
|---|---|
| `tests/unit/test_run_cap.py` | The 6th refused and the 5th allowed. A steer not counted. Dead and ended runs. Other members, automation and a service caller. Reservations, their lapse and their release. Six concurrent sends admit five, through a fake Redis that yields. The route: a refused run reaches no memory, no mint and no run |
| `tests/unit/test_chat_prompt_saved_at_start.py` | R8: a refused run leaves no member's turn, no agent row and no chat row in Postgres |
| `src/lib/runCap.test.ts` | The code and its words, the frame, the turn that leaves the thread with one notice, the words back in the composer, no hold, and the wiring |
| `tests/unit/test_run_errors.py` | `too_many_runs` is in both vocabularies |

**Mutation record (2026-10-10).** Five mutants, five killed: no lock, no
liveness rule, no actor filter, a mint before the refusal, and no cap branch
in the client seam.

**Known limits.**

- `/copilot/chat` has no client, so it is not capped. Its runs still count.
- `POST /agent/run` and `/agent/run/async` are not capped, and their runs
  are not in the index. The chat never sends them: `useAgentChat` sends only
  `copilot` (to `/agent/run/stream`) or `litellm` (no agent run). The chat
  route sends a body with `mode: "langgraph"` to `/agent/run`. No client
  sends that mode, so only a hand-made request reaches it. The cap is a
  fairness limit, not a security boundary, so this stays open.

### §4.9 — a message sent during a run always steers it (BUILT 2026-10-12)

**The owner's ask (2026-10-12).** "By default, we should always steer the
messages, so don't even give the option for stopping and sending." The
agent must go on with its reply, and it must use the new words as more
context for that reply.

**The rule.** A message that the member sends while a run is live steers
that run. The client POSTs it the normal way, and the gateway folds it in
(§QM-1, the STEER route of `orchestrator/steer.py::route_turn`). The live
stream goes on. The client does not stop the run, does not drop the partial
reply and does not open a second stream.

**What is gone.** The send-mode menu (Send, Queue, Steer), the
stop-and-send path and the local send queue. An old queued item in a
browser goes into the box once, and it never sends by itself
(`takeLeftoverQueue`).

**The composer.** One box. The message takes the full width. One bar inside
the box holds Attach, the agent's controls and ONE round control:

- Stop, while a run is live and the box is empty. Esc does the same.
- Send, as soon as the box has text. Then the status row says "Adds to the
  current reply".
- On a phone, the agent's controls take their own row above Attach and the
  round control.

**How a steer flows.**

1. `AgentChat.submitText` asks `sendRoute`. A live run on the gateway path
   gives `steer`.
2. `useAgentChat.steerMessage` calls `sendSteer` (`lib/chatSteer.ts`). The
   bubble draws at once with the `steered` mark, as "Adding to the reply".
3. `/api/agent/chat` sends the turn to `POST /agent/run/stream`.
   `_route_incoming_turn` gives STEER, and `_apply_turn_decision` answers
   `202 {steered: true}` with no stream. The bubble then says "Added to the
   reply". The saved row carries the `steered` mark.
4. `send_steer` writes the steer to `cc:steer:` first, then sends it on the
   control bus. The owner's applier buffers it. The run's next model call
   gives it to the model as a USER message, right after the tool results
   (`steer.SteerTurns`, task 2494).

**A steer is the member's own turn (task 2494, 2026-11-09).** The model read
a steer at the end of a tool result as a prompt injection, and it ignored the
steer. So these rules hold:

- No tool result carries a steer. `SteerTurns` is a chat middleware on a
  per-run view of each native agent (`_tool_injection._apply_steer_turns`).
  It puts each note into the request as a user message. It puts the note
  back in each later request of the same run.
- Only `stream_relay._steer_apply` fills the buffer, and only the chat route
  of the session's member calls `send_steer`. A tool, an email body or a web
  page never becomes a user turn.
- A Projects card (`skill_projects.writes._confirm`) and each CRM card set
  `close_on_steer`. A steer closes the card as REVISED, the tool writes
  nothing, and the model can show a new card with the change.
- A closed card is not a decline, so its answer never says "Cancelled".
  `_confirm` raises `refusals.CardClosedByChat`, and the Projects wrapper
  gives the model `writes.CARD_REVISED`. Each CRM card returns
  `_closed_by_chat`. A card with rows gives `ask_tools.REVISED_ROWS`, an
  empty set that `is_revised` tells from a card with no ticks (fix round 1).
- The session history stores each steer turn after its tool result
  (`native_session_store.SessionHistoryProvider.after_run`).
- ⚠️ On the web chat, the next turn does not load that session yet. The
  gateway saves the steer as a user row, and the save digest does not hold
  it, so the next load gives `digest_drop`. The next turn then reads the
  member's words from the text history, and the tool calls of the steered
  run are lost. Task 2948 owns the fix.
- A Copilot SDK agent gets no steer turn. Its steer waits for the run end,
  and the follow-up below answers it.
- Fence: `tests/unit/test_steer_user_turn.py`.

**A closed card says so, and each steer gets an answer (task 3130,
2026-11-13).** In production, a member asked for an email reply, and the
reply card opened. The member typed two more messages, and the first one
closed the card. The answer met only the last message. It did not say that
the reply was not sent. So these rules hold:

- The server ends the answer with one line, not the model. The line comes
  when a chat message closed a write card that nobody answered.
- The line is "Not done: «<act>». The card closed when a new message came
  in. Say 'do it' to get it back." The words are true in a shared room,
  where another member can close the card.
- Two closed acts read "«a» and «b»". Three or more read "«a», «b», and
  «c»", and the line then says "cards" and "them".
- The act comes from the card's own title (`ask_tools.card_act`). A title
  can hold a name or an address that a stranger wrote. So the act is inert
  text in one « » fence, at most 120 characters.
- In the act, no markdown or link character stays, and no fence mark stays.
  A line break is a space. Each run of slashes after a colon goes, so no
  scheme links. "www." becomes "www " with a space, so GFM does not link
  it.
- An underscore inside a word stays, so `john_doe@acme.test` keeps its
  name. GFM never reads it as emphasis there. An underscore at a word edge
  goes. The workbench's remark-gfm 4.0.1 shows no link and no emphasis in
  any line. Only an address shows as a mail link of its own text.
- Each write card of each app waits in `ask_tools.request_confirmation`, so
  one record covers email, Projects, CRM and Studio. `run_agent_stream`
  opens the record for each run (`open_closed_cards_for_run`).
- A card that the member approved or declined adds no line. A later card
  takes the place of a closed card only when it holds every act key of the
  closed card.

**The act key** (`ask_tools.act_keys`, bound by `ask_tools.card_call` in
the gate wrapper of `_tool_injection`):

- The key is the tool, each id argument, and the people of `to`. Case and
  spaces do not count, and free text (a body, a note) is never in it.
- A call with no object id also adds its identity arguments: `title`,
  `name`, `lead_name`, `subject`, `deal_name`, `contact_name`,
  `company_name`, `account_name`, `project_name` and `app_name`. A Studio
  `manifest` gives them one level down.
- A call has no object id when its tool creates (the verbs create, add,
  log, new, make and send), or when each id names a place. A place is
  `project_id`, `account_id`, `parent_task_id` and the other
  `CONTAINER_IDS`.
- So lead Veena and lead Raj are two acts. Two calls of `log_activity` on
  one record with two subjects are two acts. Two new mails to one person
  with two subjects are two acts. `update_task` on task 12 keys on the
  task only.
- A batch tool shares the family of its single tool: `create_tasks` with
  `create_task`, and `update_tasks` and `bulk_update` with `update_task`.
  Each item of a batch is one key, with the batch's own ids.
- A batch card takes the place of a closed single card only when one of
  its items has the same key. A closed batch card goes only when a later
  card holds every one of its items.
- A card with no key takes the place of no card. A delegation tool opens a
  scope with no key. A batch whose items do not read has no key. In
  `approve_all` mode the wrapper does not run, so no card has a key, and
  every line stays.

**How each class errs.**

- A new object that the redraft renames gets a new key. The old line stays
  although the act was done under the new name. That side is the safe
  side: a line that is too careful, never a silence.
- A change to an object with an id keeps one key whatever it changes. A
  second approved change to the same task takes away the line of the
  first change, although that change did not run.
- A batch card that the member approves with the matching row unticked
  still takes the place of the closed card. The key binds when the card
  opens, before the answer.

**The run ends.**

- The executor takes the line before it saves the native session.
  `executor._close_native_session` puts it at the end of the session's last
  answer (`native_session_store.add_closing_line`), and then the session is
  saved. `executor._closed_card_events` sends it just before each
  `RUN_FINISHED`, after the CRM note. The relay keeps it, so the stored
  message holds it.
- A parked run (`pending_ask.park`) sends the line before its own
  `RUN_FINISHED`.
- No line comes when the run ends by a Stop (`stream_relay.cancel_run`), by
  a `RUN_ERROR`, or by the dead-run finish of `run_liveness`. The member
  then sees the card close, and no answer follows it.
- The outcome word is "closed by your message"
  (`ask_tools.CLOSED_BY_YOUR_MESSAGE`). HRD-7 keeps it in the one table of
  card outcomes, `ask_tools.CARD_OUTCOMES` (`chat_failure_modes.md` C1).
- A run that takes two or more steers asks for an answer to each. The
  newest steer turn ends with `steer.steers_frame`, and a replay of two or
  more steers ends with it too. One steer gets no frame.

**"do it".**

- After the card closes, "do it" can draw the same card again. A unit test
  ends the first run through `_close_native_session`. The next turn then
  reads the call, its arguments and the line from the session, and it draws
  the same card on a new id. A scripted model stands in for the real one.
- ⚠️ On the web chat after a steer, the next turn reads the text history
  (`digest_drop`, task 2948). There the model must find the act again from
  the line and the history. Only a live-model eval can prove that path, and
  that eval needs the owner's OK for its cost.
- No flag. The line only tells the truth, and the frame adds one sentence.
- Fence: `tests/unit/test_steer_unsent_card.py`.

**A steer after the last tool call.** Before 2026-10-12 such a steer was
lost. The applier had discarded its durable copy, and the next run start
cleared the buffer. Now three steps answer it:

1. `stream_relay.push_event` calls `steer.return_unconsumed` before it writes
   a terminal event (`RUN_FINISHED` or `RUN_ERROR`). Each unread note goes
   back to `cc:steer:` under its own id. The run then takes no more notes,
   and a late steer stays in the store.
2. Once the stream ends, the chat asks once for the steer follow-up
   (`steerFollowUp: true`, with no words).
3. The route's gate starts no run while a run is live or when nothing is
   stored. Otherwise the ENGAGE seam takes the stored steers, makes them the
   run's message, and drops their copy from the history (`without_replayed`).

**A Stop asks for no follow-up, from any tab or member.** `cancel_run`
records the Stop for the thread (`cc:steer-stopped:`, through the same
prefixed Redis client). It does this first, because the cancelled run's own
end gives its unread steers back, maybe later and on another worker. Then:

- The gate answers `202 stopped` to every follow-up, and drops the stored
  steers.
- The member's next real message drops them too, and it does not replay
  them. The next run start clears the mark.
- We DROP the stopped run's steers, and we do not keep them for a replay.
  The member said stop after the steer. A replay would make the agent act on
  the steer again. The words are not lost: the transcript and the saved row
  keep them, and the next message's history carries them.
- The client clears the owed follow-up on Stop and on a `stopped` answer. It
  cancels a waiting retry, and it ignores a steer answer that lands after a
  Stop (`stopEpoch`).

**Review of #920.** Four rules close the gaps the review found:

- Any 202 with `steered: true` owes a follow-up. That covers a plain send
  that the gateway steered, for example into another member's run in a room.
- A follow-up that meets `run_in_flight` asks again. The wait doubles from
  2 s to 30 s, for 20 tries (`followUpRetryDelay`).
- A refusal after ENGAGE (another member's run, a supersede race) gives the
  taken steers back to the store (`steer.give_back`).
- `without_replayed` drops only user turns after the last assistant turn. An
  older turn with the same short words stays.

**Round 2 of the review.**

- The durable copy of a steer goes when a model call reads it as a user
  turn (task 2494), not when the owner buffers it. A steer in the buffer then survives
  the owner process dying, for example in a deploy restart.
- A follow-up that gets 409 or 429 draws no card and no run-cap notice. It
  asks again with the same wait. After the last try, the steer says "Your
  added message waits for the next reply."
- Edit after a steer works. A steer row is part of the turn it joined. Edit
  offers the prompt, and `plan_supersede` removes the turn with its own
  steer rows. The note quotes them as `added_while_running`. Another
  member's steer still refuses the edit.

**Round 3 of the review.** The edit note keeps the steps that already ran.
The steer quotes once came before the step budget, so a few of them pushed
the steps out, and the model could repeat a side effect. Now:

- The completed steps have first claim on the budget, as before #920.
- The added messages fit into what is left. Each is cut at 120 characters,
  and one line counts the rest.
- The closing instruction is always whole (`_cap_keeping`).
- A note with no added message is byte for byte the note of `main`.

Round 3 adds three mutants, and all three are killed: steer quotes placed
before the step budget, a cap that cuts the closing instruction, and an
added message that is not cut at 120 characters.

**The fallbacks.**

| The server answers | The client does |
|---|---|
| `202 {steered: true}` | The bubble says "Added to the reply". The run owes a follow-up |
| `202 {aborted}` or `{dropped}` (a bare "stop") | The bubble goes, as the server saved no row |
| A stream (the run had ended, or a dead run was closed) | It follows that stream as a normal turn, in place of the bubble |
| A refusal (409, 429 or a network error) | The bubble goes, the words go back to the box, and a plain notice says why |
| The direct model path (`litellm`) | It sends nothing. The words stay in the box with a notice |

**Fences (R7).**

| Fence | What it holds |
|---|---|
| `src/lib/chatSteer.test.ts` | One control by box state, no send-mode menu, a steer that never stops, enqueues or drops the reply, the acknowledgement, a refusal that keeps the words, the follow-up rule, and the queue migration |
| `tests/unit/test_steer_routing.py` §8 | The give-back at the terminal event, the store before the event, a closed run, and one replay per steer id |
| `tests/unit/test_steer_follow_up.py` | The route: a second message steers and starts no run, a late steer gets ONE follow-up run that reads it once, and a follow-up with nothing owed starts nothing |
| `tests/unit/test_chat_prompt_saved_at_start.py` | R8: the steered row carries the `steered` mark in Postgres |
| `e2e/chat-steer.spec.ts` | The browser: the stream goes on, the words show as added, the follow-up answer draws below the steer, and Stop and Esc halt the run |

**Mutation record (2026-10-12).** Round 2 adds ten mutants, and all ten
are killed:

- no Stop mark, a gate that ignores the mark, and a mark that a new run never
  clears.
- an ENGAGE that replays a stopped run's steers.
- a client that owes a follow-up after a Stop, and a `stopped` answer that
  keeps the retry.
- a drop of the durable copy at buffer time.
- a follow-up refusal that draws a card.
- a steer row that refuses the edit on the server, and Edit on the steer row
  in the client.

Round 1 had eleven mutants, eleven killed: the client
stops on send, the client enqueues, the button ignores the box, no give-back
at the terminal event, a give-back that saves nothing, no follow-up gate, a
replay that keeps the history copy, no follow-up from the hook, a steered
row with no mark, no give-back after a refusal, and a replay that matches an
older turn.

**Known limits.**

- A follow-up needs an open chat. If the member closes the tab, the steer
  stays in the store for 30 minutes, and the next message replays it.
- The steer bubble sits below the reply it joins, in time order. That is
  also the order of a reload.
- The follow-up gate reads a stale `cc:active` of a dead owner as
  `run_in_flight`. It does not run the dead-run recovery of
  `_route_incoming_turn`. The steer stays stored, and the next message
  replays it.

### §4.10 — one run status in the open chat (BUILT 2026-10-19)

**The owner's report (2026-10-19, production).** "I have no idea if the
agent is working in the background or not … if an agent session is running,
it must appropriately show in the chatbox. Sometimes I just see a thinking
container for a fleeting moment before it disappears."

**The rule.** The server is the source of truth. A run is live while
`GET /chat/active-sessions` lists it. ONE client state holds the run of each
chat, and every indicator reads it. No indicator decides on its own.

**What caused the symptoms.** Before this slice, eight places each decided
"a run is active". The table names each one and how it disagreed.

| Source | What it read | How it failed |
|---|---|---|
| The live stream in `useAgentChat` | A close of the stream | The BFF reads the gateway with a 310 s budget. The ids of the live stream are `local-*`, so the BFF cannot reattach. A run longer than 310 s lost its stream, and the chat read the clean close as "done". This is the owner's screenshot. |
| The reattach loop in `useAgentChat` | One read of the run list at mount | It ran once. A stream that ended later never got a reattach. It kept its controller when the replay came back empty, so Stop stayed forever. |
| The reattach cursor | `lastEventId` | It replayed only the tail into a message that it had emptied. A second return to the page lost the start of the reply. |
| The history load in `AgentChat` | The `messages` of its first render | That render held no messages, so each load replaced the thread. A reply that had reattached went away: the "fleeting" thinking container. |
| The recovery poll | A reply that ends in a full stop | It called a run done when its text ended in a full stop. After 45 s it marked a live run "interrupted". Now `serverHoldsRun` guards both exits: while the server listed the run in the last 15 s, the poll keeps the recovery. |
| The top banner and the composer row | `recovering` and `runStatus` | Each read its own flag. The banner sat at the top of the thread. |
| The Stop of the composer and the header dot | `isLoading` | Both went idle with the stream. |
| The context ring ("19%") | `isLoading` | It pulsed with the stream, so it looked like run progress. It is the context window in use. |

**The state.** `src/lib/runStatus.ts` holds the reducer.
`SessionStreamState.run` in `lib/chatStore.ts` keeps it per thread, and
`dispatchRun` is the only writer.

| Phase | When | What the member sees |
|---|---|---|
| `idle` | No run | The key hint, and Send |
| `starting` | This tab sent a turn, and no event came yet | "Starting" at once, and Stop |
| `working` | A stream event came, or the server lists the run | The step and the time, for example "Asking Projects assistant · 1m 12s", and Stop |
| `waiting_for_you` | A card waits, and the run waits on it | "Waiting for your answer", in the warning tone, and Stop |
| `parked` | A card waits, and no run runs (`live: false`), or the stream ended with a `done` that carries `parked` (RESUME-3a) | "Waiting for your answer" with no time, in the warning tone, and Send (D-RES-6). A park never shows "Done". When the server stops listing it, the row goes quiet (`idle`) |
| `finishing` | A stream ended with no terminal event | "Checking the run". The chat asks the server at once |
| `done` | A terminal event came, or the server no longer lists the run | "Done" for 5 s. The time shows only when the terminal event gave the real end |
| `failed` | The turn failed | The error card in the thread |
| `stopped` | The member pressed Stop | "Stopped", for 5 s |

**The transitions.**

1. A send gives `starting`. The first stream event gives `working`.
2. A stream that ends with a terminal event gives `done`. A stream that
   ends without one gives `finishing`. The close alone never gives `done`.
3. From `finishing`, a server list that names the run gives `working` and a
   reattach. A list that does not name it gives `done`. With no answer for
   20 s, the run settles.
4. A list that names a run with no stream on it asks for a reattach. Two
   reattaches are at least 5 s apart.
5. An open stream with no event, that the server no longer lists, gives
   `finishing` after 15 s.
6. A run with no stream that the server has not confirmed for 60 s
   settles. Nothing stays "working" forever.
7. A list from before a Stop or a terminal event is lag. The state ignores
   it for 10 s. The state also ignores a list from before the phase began.
8. The list can lag a new run by up to 10 s. An open stream is the
   authority then.
9. The state keys a run by its server identity (`runKeyOf`). That is the
   run id of the row, else its start time. A listed run with ANOTHER
   identity is a new run in every phase. Another tab, a phone or a room
   member started it. The new run gets its own bubble. It keeps no
   message, step, start or card of the old run.
    Two keys compare only in kind: a run id with a run id, a start time
    with a start time. While a stream of this tab reads the run, a moved
    start time with no run id is the same run.
10. A list that names a run while the chat rests (`idle`, `done`, `failed`
    or `stopped`) also names a new run, unless its identity is the same.
11. The first server row after a send binds the identity of that send's
    run. A list from before the send binds nothing.
12. A chat that mounts over a live phase kept in the store, with no stream
    that reads it, goes to `finishing` ("checking") until the server
    answers. The mount trusts no kept message, step or count. While it
    checks, the composer offers no Stop and no "working…"
    (`isRunActiveForUi`). The row draws nothing at once on a mount, except
    for a send.
13. An attach that cannot start (another attach holds the thread, or a send
    took it) gives `attach_skipped`. The next server list asks again. An
    aborted attach never holds the thread.

**When the chat asks the server.** The chat asks on mount and on each poll
of the shared poller. That poller runs every 5 s in a visible tab and every
30 s in a hidden tab.

The mount sends no request of its own. It waits for the next complete poll.
An ask during a poll that started less than 250 ms ago shares that poll. An
older poll owes one more poll, 1 s after it ends, never back to back. The chat also asks on a return to the tab, when a
stream ends, and when a run ends or stops. While a run shows, the chat asks
for the step of the run (`?steps=1`).

**The reattach.** `attach` in `useAgentChat` is the one way into a live run.
A reload, a return to the page, a switch back to a chat, a second tab and a
cut stream all use it.

- It never starts beside a stream that already reads the thread.
- It replays the WHOLE run, with no cursor. The gateway resets the stream
  for each run.
- It replays into the message of the run. That is the message this tab saw,
  else a reply saved mid-stream, else the saved row on the server. Only with
  none of these does it add a placeholder, with no thinking flag.
- A reply of an old run is never a target (`planReplay` and `rejectFor` in
  `lib/runReplay.ts`). That is the reply of the last run that ended in this
  chat. When the server named a run with another identity, it is every
  reply that the chat holds. The attach first settles a reply that the old
  run left marked as streaming.
- It first draws the saved rows that the chat lacks and that are newer than
  what shows. That is the prompt of a run started elsewhere.
- After its read of the server, it checks for a send again. A send that
  started in that time keeps its controller and its Stop.
- The replay builds into a shadow copy. The copy replaces the message only
  when it has caught up, so the message never goes blank.
- It gives up the loading state on every exit.
- A re-attach that gets no stream doubles the wait before the next one, up
  to 60 s (`attach_failed`). So a listed run that cannot replay is never
  asked in a loop. A stream event sets the wait back to 5 s.

**The card of a run in flight.** A card that a run waits on comes once, as
a stream event, and lives in the state of `AgentChat`. A chat that unmounts
and mounts again (the Email pane, a rail, the dock) lost it while the run
still waited. The stream loops now keep the card events of the run in
flight (`noteCardEvent` in `lib/chatStore.ts`), and a chat that mounts
draws them again.

A card redraws as WAITING only when nothing settles it
(`planCardRedraw` in `lib/cardReplay.ts`). These settle a card:

- An answer or a decline that this tab sent (`noteCardAnswered`). The
  redraw shows that answer.
- A `confirmation_resolved` event.
- For a question, later model output: a text delta, a text message start,
  reasoning or `done`. The tools of one model turn run at the same time, so
  a parallel tool or delegation streams while the question waits. Its
  `tool_*` and `sub_agent_*` events never settle the question. When in
  doubt, the card redraws as waiting. A stale waiting card costs one
  harmless 4xx. A hidden one blocks the run.
- A real list of pending asks from the server that does not hold the card
  (`cardsToSettle`). A failed read settles nothing.

A refused answer of a redrawn card never turns into a steer, so an answer
never reaches the agent twice.

Every end of a run clears the log: a `done`, a failure, a Stop, and a run
that the server stopped listing. A new run key clears it too. A new run
also starts with no attach backoff of the old run. If a remount then
learns that the run ended while away, its cards are finalized. Fence:
`e2e/email-pane-confirm-card.spec.ts`.

**The status row.** `components/RunStatusRow.tsx` draws the state in two
places. One is above the composer. The other is in the thread where the
reply will appear, while no live bubble shows the run. A change shorter
than 400 ms never shows or hides the row (`steadyPresence`). A send by the
member shows at once.

Every surface uses the one chat, `AgentChat`. That is `/chat`, the
assistant dock and its full view, and the phone sheet. It is also the
Projects, Tasks, Email and CRM rails. So every surface draws the same row.

**The run id.** The live-run index writes `runId` once per run, at the
first register in `run_detached`. The executor registers the same run
again with `keep_run_facts`. That keeps `startedAt` and `runId`, and it
replaces only the token, so the executor's `finally` removes its own entry.
Before, the second register wrote a new `startedAt`, and the key of the
run moved once early in every run. Fence:
`tests/unit/test_active_sessions_tenant.py`.

**The gateway.** Each row of `GET /chat/active-sessions` now carries `live`
and `runId`. The `live` field says that a run runs for the thread. A `needs_input` row with
`live: false` is parked. An older gateway sends no `live`, and the client
reads that as live.

**The context ring.** The "19%" ring is the context window in use. It is not
run progress. Its spoken name and its title say so ("Context window 19%
full"), and it no longer pulses with a run.

**The dot of the session list.** The dot reads the list of the server and
the open streams of this tab (`useActiveSessions`). The open chat reconciles
with the same list, so the two agree. When a run ends or stops, the chat
asks for the list again at once, so the dot clears with "Done".

**Fences (R7).**

- `src/lib/runStatus.test.ts` tests each transition and a reload into each
  state. It also tests a silent delegation of ten minutes, a server that
  says done during a run, and the no-flash rule.
- `src/lib/liveRuns.test.ts` tests the poll listener and its start time. A
  partial poll is not heard. A refresh in flight polls once more.
- `tests/unit/test_pending_ask_flow.py` tests the `live` field.
- `e2e/chat-run-status.spec.ts` tests a silent tool call, a cut stream, a
  reload and a move to another app. It also tests a switch of chat, a run
  that ends while away, the three waiting states and no flash.

**Known limits.**

- The BFF still cuts a live stream at 310 s. The client reattaches, so the
  member sees no gap. A BFF reattach with `local-*` ids needs the gateway to
  skip what it already sent. That is a separate change.
- After a full reload, a run that ended while the page was away reads as
  `idle`, not `done`, because no tab saw it run. The reply shows from the
  saved row.

### §4.11 — the line of a slow tool (HRD-6 part 1, BUILT 2026-11-13, dark)

**The owner's rule (2026-11-13, task 2985).** "Large CRM queries or other
queries might take even minutes. In that case inform the user that such a
query is going to take a while to get all the data. Please wait." Before this
slice, a slow tool ran with no word, or it failed at its cap.
`chat_failure_modes.md` §4 row B6 holds the class.

**The rule.**

1. A tool call that runs past 6 s sends ONE line. A tool marked read-only
   (`tool_annotations`) sends the owner's words: "This is a large read. It can
   take a few minutes to get all the data. Please wait." Any other tool sends
   "This is taking longer than usual. Please wait."
2. A tool can send its own line first through
   `acb_skills.progress_notice.emit_progress`. The timer then sends nothing for
   that call. Each call gets one line at most.
3. While the line of one call shows, the line of a parallel call waits.
   When the showing line goes, the oldest call that waits and still runs gets
   its line, once. A call that ends while it waits gets no line. So two lines
   never show at once.
4. A card is the sign of a wait for the member. A delegation tool sends no
   line, because each tool of the delegated run has its own timer. A tool that
   waits for the member sends no line. Some tools show a card in their own
   body, for example a Projects, CRM or email write (`request_confirmation`,
   `ask_questions`). Such a tool stops its timer at the card. Its line goes,
   and the call gets no second line after the answer.

**The event.** The line is one `CUSTOM` event on the run's own queue, the
channel of `navigate`:
`{"type": "CUSTOM", "name": "progress", "value": {"text": ..., "tool_call_id": ...}}`.
The same event with an empty `text` takes the line of that call away. It goes
when the call ends or shows a card. So the line of a delegated read goes when
that read ends, not when the whole hand-off ends. No tool argument and no
result text goes in either event.

**The seam.** `acb_skills/progress_notice.py` holds the writer, the per-run
function middleware, `member_wait`, `settle_channel` and the flag reader
`progress_notice_on`. `orchestrator._tool_injection._apply_progress_notice`
attaches the middleware to each native MAF agent of the run. It runs right
before `_apply_answer_check`, which stays last, so the timer wraps each tool
call and the number check's recorder sits inside it. A Copilot SDK agent gets
no timer.

**What the member sees.**

- **The web chat.** The live loop of `useAgentChat` keeps the line on the
  message (`progressNotice`). `ThinkingContainer` draws it under its header,
  which names the running step, so the member sees it with the trail closed.
  The clear event, the end of that step, answer text or the end of the run
  takes it away (`lib/chatStream.ts`). The attach loop replays the run, and
  then it follows the run live. It applies each line and each clear in order.
  So after a reload, in a second tab or back in the chat, the line is true. A
  step that still runs shows its line. A line that ended or went to a card is
  gone. No loop keeps it in `customEvents`. `withCustomEvent`, the BFF save and the gateway fold
  (`chat_fold.py`) never keep it, so a stored chat never holds it.
- **The screen reader.** The `role="status"` region mounts with the trail and
  stays empty until a line comes, so a reader announces the line.
- **WhatsApp.** The line goes as the channel's early message
  (`whatsapp_ui.WhatsAppRun.notify`), once in a reply. The channel's own 20 s
  line (`REPLY_WORKING`) then stays quiet. Before the answer or the failure
  text goes, `bot_run` awaits `settle_channel`. An early message on its way
  lands first, and `settle_channel` cancels one that has not landed in 6 seconds.
  So "Please wait" never follows the answer. A run with no WhatsApp profile has no early
  message, and this slice builds none.

**The flag.** `CHAT_PROGRESS_NOTICE_ORGS`, a comma list of org ids or `*`.
Empty is the default. Then the seam attaches nothing, the hook of the card
finds no call, `settle_channel` finds no early message, and each WhatsApp cap
is as before. So each run sends exactly the bytes that it sent before. A
SIGHUP reload applies a change (`docs/flag_reload.md`), and a production flip
is gate `enforcement-flip`.

**The caps on the path of a long tool call** (2026-11-13). None of the
general caps is under 200 s. The WhatsApp run's cap holds the whole run. So
a long-read org gets a longer cap, and its sweep gets a longer stale time. A
long-read org is one that `CHAT_PROGRESS_NOTICE_ORGS` or `CRM_LONG_READ_ORGS`
names (`bot_run.long_reads_on`). Every other org keeps 240 s and 300 s.
`whatsapp_assistant_channel.md` §5.4 holds that rule.

| Cap | Where | Value | On a 3-minute tool |
|---|---|---|---|
| Native idle watchdog, no tool open | `orchestrator/watchdog.py:45` | 120 s | Does not apply while a tool is open |
| Native idle watchdog, a tool open | `orchestrator/watchdog.py:46` | 600 s | Holds |
| Idle watchdog, a card waits | `orchestrator/watchdog.py:47` | 3600 s | Holds |
| One tool call, Tier 1.5 and Tier 2 shim | `orchestrator/watchdog.py:49` | 300 s | Holds |
| A delegated run | `acb_skills/agent_tools.py:65` | 900 s | Holds |
| The relay subscriber read | `orchestrator/stream_relay.py:283` | 30 s, then it reads again | Holds |
| The relay stream and the active flag | `orchestrator/stream_relay.py:86` | 3600 s, renewed on each event | Holds |
| The BFF fetch of the run stream | `src/app/api/agent/chat/route.ts:908` | 310 s, then up to 6 reattaches (`:611`) | Holds |
| Node `fetch` (undici) body gap | library default | 300 s with no byte | Holds |
| Caddy `reverse_proxy` | `deploy/hostinger/caddy/Caddyfile:85` | No read or write timeout | Holds |
| Process heartbeat | `orchestrator/run_liveness.py:91` | 30 s | Holds while the event loop turns. A tool that blocks the loop fails it |
| WhatsApp run | `whatsapp_channel/bot_run.py` `run_timeout_for` | 420 s for a long-read org, 240 s for any other | Holds one 180 s read and its model calls in a long-read org |
| WhatsApp sweep stale time | `whatsapp_channel/bot_run.py` `stale_for` | 480 s for a long-read org, 300 s for any other. Always longer than the run's cap | A run that still works is never taken |
| The CRM tool's HTTP read | `acb_skills/crm_tools.py:320` | 30 s | Under 200 s. The CRM long mode (HRD-6 part 2) owns it |
| The CRM total's statement | `gateway/routes/crm/aggregate.py:254` | 10 s | Under 200 s. HRD-6 part 2 owns it |

**Fences (R7).**

- `tests/unit/test_progress_notice.py` tests the flag, the seam order and the
  per-run view. It tests one line and one clear for each slow call, the two
  texts, the tool's own line, no stacked lines and the exempt tools. It tests
  a card in the body of a tool, and the WhatsApp early message and its
  settle. It tests the WhatsApp caps of each org, and the fold. A native stream outlives the
  bare idle cap, and the line comes between the tool's start and its result.
- `src/lib/progressNotice.test.ts`: the line shows. It goes at its clear
  event, at the end of its step, at answer text and at the end of the run. It
  draws under the header while the turn works, and not after it. The attach
  loop ends with the true line: none after a clear, the line with no clear
  yet. No loop, `withCustomEvent` or the BFF save keeps it.

### §4.12 — a chat picks up exactly where it left off (RESUME, task 3255, SPEC 2026-11-14)

**Status: 📋 SPEC, 2026-11-14. RESUME-1a (2026-11-15) and RESUME-2a
(2026-11-16) are built and dark, and so are the RESUME-2a follow-ups (task
3467). RESUME-3a (2026-11-16) is built, and RESUME-4a-1 (2026-11-16),
RESUME-4a-2 (2026-11-17), RESUME-2b (2026-11-17, branch `stu-resume2b`) and
RESUME-5 (2026-11-19, branch `resume5`) are built and dark. RESUME-3b and
RESUME-3d (2026-11-19) are built, with no flag of their own. Nothing else is
built.**

Metorite task 3255
(RESUME-0). Verified against code at `origin/main` `3040ac9e4`. HRD-22 (task
3231) is on `main`, and this section read its commits too.

**The owner's ask (2026-11-14), kept as written.** The block below quotes it.

```text
I think we need to have a better mechanism for when the agent chat is
refreshed or we close the chat and restart. A card that also shows that the
agent has a restart is actually unnecessary if we are able to exactly pick up
where we left off. We need to really improve the UX of long-running agent
sessions in which we have closed and reopened the chat session, etc. Figure
out what needs to be done.
```

**The answer in one screen.**

- A refresh, a second tab and a reopen during a live run come back to the
  same run today. The attach replays the whole run, with its partial answer,
  its steps and its open card (§4.10). §4.12.2 names the small gaps.
- The break is the card that waits. After 10 minutes the run parks and ends.
  A late Approve then starts a NEW run, which plans again and draws a NEW
  card. That is the triple approval of 2026-11-14.
- The fix: the card stores the call that it guards. A late Approve runs that
  stored call once, with no model. Then the model goes on with the same
  reply. No second card, no "restarted" notice, and no member turn.
- Consent stays the member's. The stored call writes only when the card that
  the tool builds again equals the card that the member approved. A changed
  record gets a new card, and one line says why.
- A release (three a day, D194) now lands in the working day of our members.
  With this design, a release parks each run that waits on a card. A working
  run goes on by itself in the new process.
- The rung 1 demo: approve a card an hour later on production. The work goes
  on with no second card and no notice.

#### 4.12.1 The evidence

**Production, 2026-11-14, org Fracktal, session 36762da6.**

| Time (UTC) | What happened | The code that did it |
|---|---|---|
| 09:13:29 | The chat drew "Move this deal to Payment Complete?" (request 95ff6b013d9c) | `update_deal_status` (`apps/agents/agent-crm/agents.py`) reads the deal, then awaits `ask_tools.request_confirmation` in its own body |
| 09:23:29 | `pending_ask.parked`. No answer came in 600 s, and the run ended | `executor.wait_user_future` calls `pending_ask.park`, which cancels the run |
| 09:32:52 | The member tapped Approve. The log shows `agent.pending_ask_answered_late state=parked`, then `agent.user_input_after_restart` | `routes/agent.py` `_late_answer_from_row`, then the 409 `run_restarted` |
| 09:32:52 | The chat showed "The assistant restarted · Your answer went to it as a new message." It sent the fenced envelope as a new member turn | `AgentChat.tsx` `postRespondInput`, and `runErrors.ts` `run_restarted` |
| About 09:33 | The new run planned again. It called `update_deal_status` again and drew card 264a5a094536 | Nothing joins the new card to the old answer |
| 09:43:02 | The second card parked | As at 09:23:29 |
| 09:57:59 | A late Approve again, and a third card e561b95bbc2d | As at 09:32:52 |
| 09:58:12 | The member approved the third card while its run waited, and the deal moved | The live Future path |

The member approved one change three times and saw the "restarted" notice
twice. After each late Approve, ten minutes passed with no answer. So the
member probably tapped and left. An approval that does not act at once is lost on a
member who does not wait.

**The last 72 hours of gateway logs** come from the brief of task 3255. They
hold 162 runs, 75 cards, 5 parks and 4 late answers. They also hold 6
`stream_relay.control_undelivered` and 4 `stream_relay.cancel_run` lines. A
`control_undelivered` is an answer or a steer
that found no live owner (`stream_relay._deliver_control`). A `cancel_run`
is a Stop.

**The flags on production:** `CHAT_DURABLE_ASKS=true`,
`HELD_CARD_WRITES_ORGS=*`, and `MAF_NATIVE_SESSIONS=true`
(`chat_failure_modes.md` §3).

#### 4.12.2 The scenario map

"Met" means that the member gets the same run, partial answer, steps and
card. No notice about a restart shows.

| # | Scenario | What the member sees today | "Exactly where we left off" | Gap, and its slice |
|---|---|---|---|---|
| 1 | A refresh during a stream | The thread draws from the local cache at once. The status row stays empty until the first poll (up to 5 s), then shows "Working · <step>". `attach` replays the whole run into a shadow copy. The partial answer, the steps, the trail and an open card come back (`useAgentChat.ts` 1109-1451) | Met for a live run | A question card that the member answered just before the reload comes back as waiting. Only a confirmation has a resolve event. A second answer then goes out as a new message. HRD-16 |
| 2 | Close and reopen during a run | As in 1 while the run is live. A run that ended while away shows its saved row. A cached reply that is still marked as live polls Postgres every 1.5 s, for 45 s | Met for a live run | An attach stream gets no BFF reattach, so the BFF cuts it every 310 s and it replays again. The stream lives 1 h after its last event and holds 50 000 events at most. Advisory, no slice |
| 3 | Reopen after the run ended | The saved rows, the phase `idle`, and no "Done". A reply with the `run_interrupted` mark shows "The assistant was interrupted by an update" with Continue | Met | The interrupted notice after a release. RESUME-6 |
| 4 | A card waits past 10 minutes | The run parks. The BFF drops `parked` from `RUN_FINISHED` (`src/app/api/agent/chat/route.ts` 549), so "Done" flashes and the card goes, then comes back from `GET /chat/pending-asks`. The row says "Paused. Answer the card to go on". Approve gets the 409 `run_restarted`. The chat sends the envelope as a new turn, and shows "The assistant restarted · Your answer went to it as a new message." below the new reply. The new run draws a new card | The same card stays in the same reply. Approve does the act once, and the same reply goes on, with no notice | The production case. RESUME-1 to RESUME-3, with HRD-22 as the stop-gap |
| 5 | A release restart during a run | The stop waits up to 60 s for working runs, and never for a run that waits on a person (`gateway/routes/drain.py`, `deploy/hostinger/gateway-drain.sh`). The stop cuts a longer run: "The assistant was interrupted by an update", with Continue. A run on a card ends at once, and its row stays `open`. With the RESUME flags off, an answer takes the path of 4. With the RESUME flags on, an Approve of a card that a stop cut resumes in case B, in a new reply (corrected 2026-11-19). RESUME-5 (built 2026-11-19, dark) parks the card at the stop instead, so the Approve goes on in the same reply. A send in the gap shows "Metorite is updating", and the chat holds it in memory only | The reply finishes by itself. The card waits as if no release came | RESUME-5, RESUME-6 |
| 6 | A second device or a second tab | It reads the run from the poll and replays it with the open card. Only the asked member sees `needs_input`. Two answers to a confirmation: `confirmation_resolved` closes the other card, and the loser reads "Not applied · <title>. Nothing waits on this answer now. The run ended, or someone answered it." Two answers to a question card: no event closes it, and the losing answer becomes a steer | One answer counts. The other device shows that someone answered the card | HRD-16. For a parked card the row moves once, and the loser gets the plain 409 |
| 7 | A dropped network | The read throws. The row shows "Checking the run", and then "Done" after 20 s with no answer from the server, while the run can still go on. The toast says "You are offline". A send while offline shows "Metorite is updating", and the chat holds it in memory only | "Checking the run" stays until a complete poll answers. Offline says offline. A send waits in an outbox | HRD-17 |

**A defect that the map found.** The BFF route of the pending asks answers
200 `[]` on each failure (`src/app/api/chat/pending-asks/route.ts` 27-31).
The chat reads that as a true list, and `cardsToSettle` closes a card that
still waits. `pendingAsks.ts` states the opposite rule: a failed read settles
nothing. RESUME-0a fixes it on rung 0.

#### 4.12.3 Can the code resume a parked call?

**MAF can do it.** The installed version is `agent-framework-core` 1.19.0.

- `Agent.run(messages=None, session=..., stream=True)` takes no new message.
  `normalize_messages(None)` gives `[]` (`_types.py` 1952), and no check
  refuses an empty input.
- The function loop runs only the calls of the NEW model response
  (`_tools.py`, `_extract_function_calls`). So MAF sends a history that ends
  in an assistant `function_call` X and a tool `function_result` X to the
  model. The model then goes on with the same turn. MAF never runs X again.
- ⚠️ **Corrected 2026-11-16 (RESUME-4a).** MAF swaps an
  `[APPROVAL_PENDING]` placeholder only inside its own approval flow.
  `_replace_approval_contents_with_results` needs a
  `function_approval_response`, and no code here makes one. So Metorite
  must swap the placeholder in `session.state` itself. RESUME-4b does that.
- MAF has its own approval too (`approval_mode="always_require"`, and
  `function_approval_request` and `function_approval_response` in
  `session.state["tool_approval"]`). This repo draws its cards inside the
  tool body, and the store keeps only the messages (`native_session_store.py`
  rule 6). So this design does not use the MAF approval.

**The repo cannot do it yet. It lacks four parts.**

1. **No record of the open call.** The card event and `chat_pending_ask`
   hold the title, the detail, the context and the request id. They hold no
   tool, no arguments and no call id (`pending_ask._COLUMNS`). The gate
   wrapper (`_tool_injection._agated`) knows the tool and the arguments. A
   function middleware knows the call id (`context.metadata["call_id"]`, as
   `acb_skills/progress_notice.py` reads it).
2. **A park saves no session.** `park` cancels the run inside the tool.
   `CancelledError` passes each `except Exception`, so the executor saves the
   native session only at a normal end (`_close_native_session`,
   `_finish_native_session`). The drain saves the reply text only.
3. **A run needs a member turn.** `_native_session_skip` gives
   `no_current_turn`. `_compose_session_run` raises "no current user turn".
   `begin_turn` hashes `current_message`.
4. **An open call has no repair.** An OpenAI-style chat completion refuses an
   assistant tool call that has no tool message. Neither MAF nor the repo
   repairs it. So a session that the store saves with call X must also hold a
   placeholder result for X.

**The verdict: feasible, in two depths.**

- **The note path** needs none of parts 2 to 4. Metorite runs the stored call
  with no model. Then it starts a run whose input is one framed note with the
  result. The model reads the text history and the note, and goes on. Rung 1
  builds this path.
- **The exact path** (RESUME-4b) saves the session at the park, with call X
  and a placeholder result. The continuation puts the real result under X
  and runs with no new message. The model sees its own call and its own
  result, as if no pause came.
- **Why at the park, and not at the card** (corrected 2026-11-16). A
  snapshot at the card stays behind when the member approves live and the
  run then fails. Also, `session.state` never holds the turn in flight. MAF
  stores the turn in `after_run` only at the end of a run. So the park save
  builds the response again from the streamed updates.

The precedent for a stored call with no model is live. A WhatsApp Confirm
(`gateway/routes/whatsapp_channel/acts.py` `_run`) runs the same tool with the
same arguments inside `whatsapp_acts.confirming`. The card gate then says yes
only for a card that equals the stored card text.

#### 4.12.4 The design: the card stores its call, and an Approve resumes it

**1. The stored call (RESUME-1).** When a tool draws a card, the card gate
binds the open call to the request id. The row of `chat_pending_ask` keeps it
in a new nullable column, `stored_call` (jsonb). RESUME-1a built it on
2026-11-15 with migration 448, dark behind `CHAT_RESUME_ORGS`.

The audit of 2026-11-15 split the slice and set corrections C1 to C9.
RESUME-1a stores the call of a plain card. RESUME-1b (task 3434) stores the
question cards. Task 3435 owns the prune of old values.

The migration only adds the column (R6). The column holds version 1 of this
shape. `acb_skills.stored_call.stored_call_of` builds it:

- `v`, `kind` (`confirmation`), `tool`, `module` and `agent`. A module that
  the loader makes for one run reads as `agent:<name>` (C7). The agent lets
  RESUME-2 refuse a card of a delegated child run (C8).
- `arguments`: the bound arguments of the call, as JSON gives them back
  (`whatsapp_acts._stored_arguments`).
- `call_id`: the MAF call id (C4). A function middleware reads it from
  `context.metadata["call_id"]` (`stored_call.CallIdMiddleware`). The
  progress notice is dark, so it cannot be the source. With no middleware,
  the value is null.
- `card_text`, `act_keys` and `digest`, from `late_approve.act_of`. The act
  keys are the `late_approve.keys_wire` strings (C7).
- No org, member or thread. The columns of the row hold them. The run id is
  a column too (C6).

**Correction of C6 (audit of 2026-11-16).** C6 said that no source gives the
message id of the reply at the card. That is wrong. The `run_liveness`
instance record of the run holds `messageId`, and `pending_ask._run_facts`
reads that record. RESUME-2b needs the message id, so it reads it there.

The card gate binds the call BEFORE the card goes out, out of band, by the
request id (C3). On path A the executor's drain writes the row, and the drain
cannot read the context of the tool. `pending_ask.note_event` takes the call
into the row. The wait of the card drops what is left, so nothing stays in
the process while `CHAT_DURABLE_ASKS` is off.

The row writes the call when the card opens, not at the park. So a crash, a
release and a park all keep it. A move to `answered` or `closed`, and
`close_thread_asks`, set it to NULL (C9). `pending_ask._COLUMNS` leaves it
out. So `GET /chat/pending-asks`, `GET /chat/active-sessions`, the My Day
chat card and the waiting list never return it. `pending_ask.read_stored_call`
is the one reader, and no route calls it in RESUME-1a.

**The retention of a row (task 3435, BUILT 2026-11-16).** Before this, no code
deleted a row. No move changes a row after it expires, so its payload and its
stored call stayed for ever. Four cases kept them: a crash with a card open, a
parked card that expired, and a chat deleted while `CHAT_DURABLE_ASKS` was off.
The fourth is a member who left. Now these rules hold:

- **The prune.** `pending_ask.prune` runs in the daily prune job of each org,
  as the hook `pending_ask.prune_hook` of `gateway.jobs.worker.prune_hooks`. It
  is one statement in the org's session, and it names the bound org beside
  RLS. The hook never raises, and its log names the error class only.
- **An expired row loses its stored call** at the next prune, in each state.
  The arguments of a call never outlive the card.
- **A row 7 days past `expires_at` goes** (`PENDING_ASK_KEEP_DAYS`), with its
  payload and its answer, in each state. No reader takes an expired row, so
  the 7 days only let a person who looks into a fault read the card.
- **A row that has not expired never changes**, in each state. So a card that
  waits, and a call that a claim can still take, stay as they are. A move to
  `answered` or `closed` already sets the stored call to NULL.
- **A bound.** One prune changes at most 1000 rows of each kind in one org
  (`PENDING_ASK_PRUNE_BATCH`). The daily run keeps up only while an org makes fewer than 1000 expired cards a day. Above that, the table grows (task 3560 raises the bound or runs the prune more often).
- **The member purge** deletes the rows that asked the leaver in the bound
  org (`chat_pending_asks` in `routes/admin/members.py`).
- **A deleted chat.** The prune does not look for rows whose chat is gone. A
  run can start before its chat row exists (migration 237), so such a test
  could take the row of a live card. The expiry bounds such a row: its stored
  call goes at `expires_at`, and the row goes 7 days later.

The prune needs no migration. The primary key starts with the org, so the
prune reads the rows of one org only. After the first runs, the table holds
about 14 days of rows. Fences: `tests/unit/test_pending_ask_prune.py`,
`tests/unit/test_pending_ask_prune_r8.py` (R8) and
`tests/unit/test_admin_member_purge.py`.

Only a call that can resume stores one (D-RES-2):

- A plain confirmation card: no rows, no reply form and no held send. Then
  `late_approve.act_of` is the one resumable test (C1). It refuses a cut
  card (C2), a tool that fails the three tests of
  `egress.is_card_write_tool`, and act keys that do not read. Each listed
  tool draws its card before it writes, and the RESUME-1 fence holds that.
- A question card (`ask_questions`, `ask_user`), in RESUME-1b. Its result is
  the member's answer, so no tool runs again.

A call with arguments that JSON cannot hold, or above 64 KB of UTF-8, stores
none. A NUL character in an argument value or key stores no call, because jsonb refuses it. The card keeps its row. `CHAT_RESUME_ORGS` must name the org of the run (C5).

**2. The late Approve (RESUME-2).** `POST /agent/respond-input` finds no
Future and reads the row, as today. When `CHAT_RESUME_ORGS` and
`CHAT_LATE_APPROVE_ORGS` both name the org, a row with a stored call takes
these steps:

1. The org comes from the session. The member must be the row's
   `actor_email`, and the thread must be the row's thread (R5e, as today).
2. The run cap admits the continuation (§4.8). A refusal leaves the row as it
   is, and the card waits.
3. The row moves from `parked`, or from `open` with a dead run, to `answered`
   in one statement. A second answer finds nothing.
4. The gateway starts a continuation run on the thread. It answers
   `200 {"ok": true, "resumed": true, "threadId", "runId"}`, with no 409 and
   no envelope.

The continuation run does these steps, in order:

1. It keeps the run id of the parked run. It deletes the parked
   `RUN_FINISHED` from the stream (XDEL). Then it marks the thread active with
   no stream reset (`stream_relay.mark_active(reset=False)`). So a replay
   reads one run with a pause in it. RESUME-2b put the XDEL first, because a
   subscriber stops at the first `RUN_FINISHED`.
2. It pushes `confirmation_resolved` for the old request id. Each tab and
   device closes the card as "Approved".
3. It runs the stored call with no model, as the member, inside the one
   stored-call yes (`stored_call.resuming`). The web yes is
   `stored_call.resume_answer`, which reads `late_approve.act_of`. The
   WhatsApp code stays as it is (correction 2 below).
   The yes holds for ONE card, and only when its text and act keys equal
   the stored ones. The stream shows the step under the old call id. For a
   question card no tool runs. The result is the member's answer, in the
   text that the question tool gives the model.
4. The model goes on. On the note path, the run input is one framed note. On
   the exact path (RESUME-4b), the input is the session with the real result
   in place of the placeholder. On the note path, the run marks the stored
   session of the thread stale first (RESUME-4a-1), so the next turn reads the text history.
5. The run ends as each run does. The fold folds the whole stream into the
   same reply row, and it clears a `run_interrupted` mark on that row.

The note is for the model, and the member never reads it. The title and the
result go into a fence as inert text, as `compose_card_answer` does today:

> [Metorite] The member approved the card «title». Metorite ran the tool with
> the values of that card. It returned: «result». Go on with your reply from
> where it stopped. Do not call the tool again for this card.

**Three cases.**

| Case | When | What happens | What the member sees |
|---|---|---|---|
| A | The thread rests, and the parked turn is its last turn | The steps above | The card turns to "Approved", and the same reply goes on below it |
| B | The thread rests, and the member wrote after the park | The stored call runs. The model reads the note at the end of the chat, not inside the old turn | The old card turns to "Approved". A new reply opens at the end of the chat, with no member bubble before it |
| C | A run is live on the thread | The stored call runs at once, with no model. Its result goes into the live run as a steer note (§4.9) | The old card turns to "Approved". The live reply goes on, and it knows that the act is done |

In cases B and C the stream of the parked run is gone, because a new run
reset it. When the member approves after the stream expired (D-RES-7), case A
shows as case B: a new reply directly below the parked one.

**What RESUME-2a built (2026-11-16).** The audit of 2026-11-16 split RESUME-2
into three slices. RESUME-2a builds case B for each thread at rest: a new run
id, a new reply row, and no member turn. RESUME-2b (task 3460) builds case A,
the same reply: steps 1 and 5 of the continuation, and D-RES-7. RESUME-2c
(task 3461) builds case C. Until then a live run on the thread keeps today's
path.

- **The route.** `POST /agent/respond-input` calls `_resume_from_row` before
  `_late_answer_from_row`. It runs only with `CHAT_DURABLE_ASKS` on, and with
  `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both naming the org of the
  session. RESUME needs both flags. With either flag off it reads nothing,
  and the answer is the answer of before.
- **The flag order** (follow-up P2-2). Since task 3467, either flag writes
  the SPENT mark of a twin card. The grant still needs
  `CHAT_LATE_APPROVE_ORGS`. Even so, keep this order for an org. Put it in
  `CHAT_LATE_APPROVE_ORGS` before `CHAT_RESUME_ORGS`. Remove it from
  `CHAT_LATE_APPROVE_ORGS` only `chat_ask_ttl_hours` after its removal from
  `CHAT_RESUME_ORGS`. A box that runs code from before task 3467 writes the
  mark only under `CHAT_LATE_APPROVE_ORGS`. `docs/flag_reload.md` holds the
  same rule.
- **The client.** No client reads the answer `resumed` before RESUME-3. So
  `CHAT_RESUME_ORGS` must not flip for an org before RESUME-3 merges.
- **The checks before the claim.** The answer is `APPROVE`, or a decline
  since RESUME-4a-2. The row of the session's org waits, its `actor_email` is the
  session's member, its thread is the request's thread, and its kind is
  `confirmation`. The stored call is version 1 of kind `confirmation`, its
  tool is on `egress.HELD_CARD_WRITE_TOOLS`, and its agent is the row's
  agent. No run is live on the thread. An open row whose run is dead is
  closed first (`recover_dead_run`). The member may run the agent. Each miss
  takes today's path.
- **The cap.** The run cap admits the run before the claim. A refusal is the
  429 `too_many_runs`, and the card waits.
- **The claim.** `pending_ask.claim_stored_call` is ONE statement. A CTE
  locks the row (`FOR UPDATE`) and keeps its stored call. The UPDATE moves
  the row to `answered`, sets the column to NULL, and returns the value of
  the CTE, because Postgres 16 has no `RETURNING OLD`. Its WHERE names the
  org beside RLS, the request id and the states `parked` and `open`. It
  also names `expires_at > now()`, the member, the thread and a stored call
  that is not NULL. A deadlock (40P01) and a lock timeout (55P03) mean no claim. It
  adds no state. No row back releases the slot and answers the plain 409.
- **The give-back** (review, P2). The run route refuses a continuation
  after the claim in two cases. Another member's run took the thread
  (`_refuse_if_another_run_is_active`). Any run is live on the thread
  (`_refuse_if_any_run_is_active`, before the mint, and
  `start_detached(exclusive=True)`). So a continuation never cancels a run
  in another tab of the same member. A refusal, and each raise before
  `start_detached` began, gives the claim back (`_give_back_claim`).
  `pending_ask.restore_stored_call` is ONE statement under the predicates
  of the claim. It moves the row back to `parked` with its stored call, and
  the slot goes. So the member can answer the card again. A raise after
  `start_detached` began keeps the claim (`ResumeLaunch.phase`). The
  refusal of `_refuse_if_any_run_is_active` comes before the mint, so it
  writes no reply row. The exclusive refusal of `start_detached` comes
  after the mint, so it leaves one empty reply row. The mint stays before
  the start, because the run, its checkpoints and the restart sweep write
  into that row. `start_detached` reads the task of the thread after its
  `is_active` await, so a run that starts during that await refuses too.
- **The starter.** The run route `run_agent_stream_endpoint` is the ONE
  starter. `_resume_from_row` calls it with `_continuation`, a server-only
  `ResumeLaunch`. Its HTTP value is `_no_continuation`, which reads no
  request input (R11). The run route then shows no member turn and takes
  no second cap slot. It passes `resume_call` to the executor, and it starts
  the run with `stream_relay.start_detached`, which needs no reader. `run_detached`
  calls the same `start_detached`, then reads the stream. A continuation
  binds no surface.
- **The prologue.** `executor._resume_prologue` runs after the run binds its
  org, member and relay thread, after the tool injection and the gate, and
  before the compose. It finds the tool by name in the run's own wrapped
  list, never by a module import. A pack tool is in that list (review,
  P1-B). With `TOOL_PACKS` on, the park ended the run of the card before
  the pack became sticky, and the continuation has no words. So
  `_start_pack_turn` takes the tool name from the kwarg only (R11), and it
  holds the allowed pack that holds the tool (`tool_packs.pack_of_tool`,
  `tool_packs.with_resume_pack`). An admin toggle still drops the pack. A
  Copilot agent keeps its tools in `agent._tools`, which the prologue never
  reads. So its stored call always gives `tool_missing` and runs nothing
  (fail closed). It refuses unless the tool passes
  `egress.is_card_write_tool`, its module is the stored module, and the
  stored agent is the run's agent. It calls the wrapped function once, with
  a cap of 60 s while no card shows. The step shows under the stored call
  id, or a new id when that is null. The run's message is then the note, with
  the title and the result as fenced data
  (`chat_recovery.compose_resumed_call_note`). A refused call runs nothing,
  and the note tells the model to call the tool again (no write).
- **The words of the member.** Memory reads and extracts `""`. The typed and
  sent words are `""`. The native session neither loads nor saves the run
  (`resume_note`).
- **A twin card** (review, P1-A). The last test of the yes is
  `late_approve.claim_act`. It marks the act SPENT and reads the old value
  in ONE `SET … GET`. An act that was SPENT already takes no yes. So of two
  twin rows approved at the same moment, one only writes, and the other
  shows its card. A run with no late-approve scope takes no yes, and a
  fault takes none (fail closed). The SPENT mark lives as long as a row
  (`spent_ttl_s`: 168 h by default), so a twin row that waits a week still
  finds it. The cost: an identical act in the same thread within that life
  shows one more card. `take_grant` refuses an act that was SPENT already
  too.
- **The follow-ups of the re-review** (task 3467). P2-1: `drop_offer`
  tests and deletes the offer in ONE step (`TenantRedis.delete_unless`, a
  fixed Lua script). So it never deletes a SPENT mark that a twin's
  `claim_act` writes between a read and a delete. P2-2: a live approve and
  a claim write the mark when either flag names the org
  (`late_approve._run_scope(spend=True)`).
  P2-3: `claim_act` gives `taken`, `spent` or `unknown`. A Redis fault, no
  scope and no act give `unknown`. The card shows for both `spent` and
  `unknown`. The note of `unknown` (`chat_recovery.RESUMED_UNKNOWN`) says
  that the change may have run already. It never says that it ran.
- **At most once.** The claim comes before the call. A process crash between
  the claim and the start of the run leaves an answered row with no stored
  call, no write and no run. No "interrupted" notice shows, because no run
  record exists yet. A second Approve of that card gets the plain 409, so
  the member asks again.

**What RESUME-2b built (2026-11-17, branch `stu-resume2b`, dark).** Case A,
the same reply. It needs no migration and no new flag.

- **The reply row in the stored call.** `pending_ask._record` adds
  `message_id` to the stored call. It comes from the `run_liveness` record of
  the run (`_run_facts`), never from an event. Version 1 stays, and each
  reader allows a call with no key. A call stored before RESUME-2b has none,
  so it takes case B.
- **The choice.** After the claim, `_same_reply_of` gives case A only when
  each test holds. The row was `parked` and names its run, and the stored
  call names its reply row. The stream still ends in the parked
  `RUN_FINISHED` of that run (`stream_relay.parked_end_of`), so no Stop and
  no new run came after it. The reply row is the last turn of the chat
  (`routes.chat.reply_turn`, under the session's org). No member joined after
  the reply row, and the label of the room did not change since the park.
  Each doubt and each fault give case B.
- **A decline never takes case A (the merge with RESUME-4a-2).** A late
  decline keeps no run and no stream. It starts the short continuation of
  RESUME-4a-2 with a new run id and a new reply row. Its answer and its
  kwarg are the ones of RESUME-4a-2, byte for byte. The executor never marks
  its start (`_same_reply_run` refuses `declined`).
- **The label (review of 2026-11-17, P1, and round 2, P1).** Part two runs at
  the intersection of now, and the row keeps its first label for ever. So
  `_same_label_as_park` compares the label of now (`chat_fold._run_authority`)
  with the `authority` label of the reply row, which the fold at the park
  wrote. It compares the members AND the caps (`routes.chat.label_of`). A
  member who joined or left gives case B. A cap that a member gained or lost
  gives case B too. No label means solo at the park, and the room must still be solo.
  The read of the label fails open, so a failed room read (`_resolve_room`)
  and a shared room with no label give case B too.
- **The window after the check (round 2, P1).** A change can come after the
  check and before the executor reads the access of the run. So the final
  fold of a same-reply run reads the label of now again
  (`chat_fold._same_label_as_row`). Part two goes into the fallback row when
  the label differs from the row's label, or a member joined after the row.
  With no label, a room that is shared now, or a room read that failed, sends
  it there too.
- **A member who came back (round 2, P2-b).** A set of members cannot show a
  member who left and came back. Such a member has a history waterline
  (`join_message_ts`) later than the reply row, so a reload hides part two
  from them. So a waterline later than the row's `timestamp_ms` gives case B
  (`_LAST_TURN_SQL`).
- **The last turn (review, P2-2).** "After" is the order of `_get_messages`:
  a larger `(timestamp_ms, id)`. So a shown row with the same millisecond and
  a larger id comes after the reply. An empty agent row that the mint left
  shows to no reader, and it does not count.
- **The start.** Case A starts the run with the parked run's id and its
  reply row, so the mint changes nothing. `start_detached(keep_stream=True)`
  checks the parked end again before any state changes. It deletes that entry
  (XDEL) BEFORE it marks the thread active with no reset, because a
  subscriber stops at the first `RUN_FINISHED`. The fold reads the whole
  stream and writes it into the same row.
- **Part one is never written over (review, P2-1).** The XDEL must delete
  exactly the parked end. A count of 0 means that a trim or another party
  took it after the check. Then `start_detached` raises `ParkedStreamGone`
  before the thread reads as active. The restart sweep reads the fallback row
  from the record of the run (`sameReplyFallbackId`).
- **The head decides (round 2, P2-a).** The executor marks the `RUN_STARTED`
  of a same-reply run `resumed` (`_same_reply_run`, from the server's kwarg).
  So the stream tells part one's start from a continuation's start
  (`stream_relay.part_one_at_head`). A case B run starts a stream of its own,
  so its start has no mark. A later Approve can then go on in its reply.
  `parked_end_of` refuses a head whose first start carries the mark.
- **The events of this run (round 3, P2-1, and its delta).** The start of a
  same-reply run writes the id of the parked end that it deleted into the run
  record (`parkedEndId`). The events of THIS run are the events after that id
  (`chat_fold.same_reply_own`). In a chain of two continuations, that leaves
  out part two, which an earlier start marked too. The fold
  (`chat_fold.same_reply_events`) takes three paths:
  1. This run's own marked start landed, and part one's start is at the head.
     The whole replay goes into the reply row.
  2. Else the events of this run go into a fresh fallback row. That is the
     rest of a run whose part one a trim took. It is also the
     `run_interrupted` marker of a run that died before its start, or the
     rest of a part two whose start a trim took.
  3. With no event after the id, nothing new goes to a row. A record from
     before round 3 has no id. Then a run with no marked start writes nothing
     new too, and the last marked start begins the events of the run.

  Part one, and an earlier part of a chain, never go to a row again. A run
  whose own start never landed writes no `agent_run` trace, because the trace
  is an upsert on the shared run id.
- **The mark of step 5.** A row of case A has no `run_interrupted` mark,
  because a dead run puts other events after the park. So the clear of step 5
  needs no code of its own.
- **A parked end that goes before the start.** `start_detached` raises
  `ParkedStreamGone` before any state changes. The run route raises
  `_SameReplyGone`, and `_resume_from_row` starts case B under the same claim
  and the same cap slot. So the old row keeps its words, and the tap is not
  lost.
- **The answer.** The 200 adds `sameReply` and `messageId`, the reply row of
  the run. The row exists before the answer goes, and only the member who
  approved reads it.
- **D-RES-7.** `pending_ask.park` calls `stream_relay.keep_parked_stream`
  right after it sends the parked `RUN_FINISHED`, when both resume flags name
  the org. The stream then lives 24 h (`PARKED_STREAM_TTL_SECONDS`). The two
  `mark_inactive` calls after a park keep the 24 h, and the next
  `mark_active` of the thread ends them.
- **A known gap that must close before the flag flip (round 2, P2-c).**
  `GET /agent/run/{thread_id}/reconnect` replays the stream from `0-0`. It
  checks room membership (`_thread_owner_ok`), and it checks no history
  waterline and no caps. D-RES-7 keeps a parked stream for 24 h, so the
  window in which a late joiner can read part one in the stream is wider. The
  gap was there before RESUME-2b. Do not flip `CHAT_RESUME_ORGS` before a
  slice makes the reconnect replay obey the waterline and the label. The
  slice RESUME-2d (§4.12.7) owns it. The rule is decided: a reader gets the
  whole run or none of it (D-RES-10, 2026-11-19). **Closed 2026-11-19** by
  RESUME-2d (built, no flag).
- **Known P3 gap: a Stop in the start window (verifier of 2026-11-18, F2).**
  A Stop (`stream_relay.cancel_run`) can come after `parked_end_of` and
  before the XDEL of `start_detached`. With no live run, it pushes a
  `RUN_FINISHED` with `cancelled` after the parked end. The XDEL still deletes
  the parked end, so case A goes on with a terminal event in the middle of
  the stream. A tab that attaches from `0-0` while the run works stops at
  that event. The run ignores the Stop, because `start_detached` calls
  `clear_stopped`. The row still gets both parts. RESUME-3b owns it: the
  attach loop reads on past a terminal event in the middle of a stream.
- **Known P3 gap: the 24 h map is in one process (verifier, F5).** The map
  `stream_relay._PARKED_STREAMS` lives in the process that parked the run.
  The next run of the same thread, in another process, does not clear that
  entry. When the first process then calls `mark_inactive` for the thread (a
  Stop that it serves), the stream of the next run lives 24 hours. The cost is the
  Redis memory of one stream only. Production runs ONE gateway worker today.
- **Not in this slice.** The chat side of case A is RESUME-3b (task 3475).
  The continuation sends a second `RUN_STARTED` with the same run id, and a
  replay of the chat must keep one bubble. The park's fold sealed the row, so
  the upsert refuses a live checkpoint of the BFF until the final fold. The memory
  extraction at the run end read the parked part again. RESUME-2e (task 3818)
  closes that gap (§4.12.7).

Fences: `tests/unit/test_resume_continuation.py` (sections 13, 14 and 15) and
`tests/unit/test_resume_continuation_r8.py` (R8, the real stream on a private
Redis, and the real rows under RLS). The label check has
`test_a_changed_room_gives_a_new_reply` (R8),
`test_a_cap_that_changed_after_the_park_gives_a_new_reply` (R8) and
`test_the_label_check_compares_members_and_caps`. The window has
`test_a_change_in_the_window_sends_part_two_to_its_own_row` (R8). A member who
came back has `test_a_member_who_left_and_came_back_gives_a_new_reply` (R8).
The tie rule has `test_the_case_a_checks_on_real_rows` (R8). The head has
`test_three_parts_go_into_one_row_unless_a_trim_took_part_one` (R8),
`test_a_continuation_with_no_start_never_writes_part_one_again` (R8),
`test_a_trimmed_head_never_writes_over_part_one` (R8),
`test_same_reply_events_decide_by_the_head` and
`test_a_same_reply_fold_never_writes_over_part_one`.

The decline rule has `test_a_late_decline_never_goes_on_in_the_same_reply`,
`test_a_decline_is_never_a_same_reply_run` and
`test_a_late_decline_of_a_case_a_card_takes_the_path_of_4a_2` (R8). The real
HRD-22b witness composes with case A in
`test_a_true_twin_takes_the_old_path_and_never_case_a` (R8). The other relay
tests stub that witness, and each card has a title of its own thread
(`_title_of`). A clock that steps back made an earlier approve read as a twin
(verifier of PR 2583).

**What RESUME-4a-2 built (2026-11-17).** D-RES-9 decides it. A late decline
(`declined: true`) of a row with a stored call takes the path of a late
Approve, with these changes:

- **The route.** The decline branch of `respond_user_input` calls
  `_resume_from_row` first. The flags and the checks are the same as for an
  Approve. Each miss takes `_late_decline_from_row`, as before.
- **A decline ALWAYS closes the card** (review of 2026-11-17, P2-1). A "no"
  needs no run slot. A cap refusal takes `_late_decline_from_row`, which
  closes the row with a 200. A refused start, or a raise before
  `start_detached` began, keeps the row `closed`. The slot goes, and the
  answer is `200 {"ok": true, "declined": true}`, with no `resumed`. No
  give-back moves a declined claim. Fence: `test_resume_continuation.py`
  and `test_resume_continuation_r8.py` (R8).
- **The claim.** `pending_ask.claim_declined_call` is ONE statement. It has
  the CTE and the predicates of `claim_stored_call`. It moves the row to
  `closed`, not `answered`. `restore_stored_call` moves an `answered` row
  only.
- **The answer.** `200 {"ok": true, "resumed": true, "declined": true,
  "threadId", "runId"}`.
- **The prologue.** It marks the stored native session stale first, as
  for an Approve. It closes the old card as `REJECT`, and it finds and
  calls NO tool. The run holds no pack for the tool. The note
  (`chat_recovery.RESUMED_DECLINED`) holds the text of
  `card_outcome_text(CARD_DECLINED)`. The title and the values of the card
  are in a fence of their own (`<<<card-values>>>`).
- **A `REJECT`.** The chat's confirmation card sends `REJECT`, and never
  `declined: true`. A late `REJECT` keeps the resend path of today, so the
  model reads it as a new message. No client sends a decline of a card
  with a stored call today. RESUME-7 (the "Not now" on My Day) can use
  this path. So this path is server only until RESUME-7.
- **Open owner question.** Must a late `REJECT` of a plain card also start
  the continuation? The change is one line in `_resumable_row` and a fence.
  Until the owner answers, a `REJECT` keeps the resend path.

**Corrections of the audit of 2026-11-16.**

1. C6 is wrong. Item 1 above has the correction.
2. Step 3 of the continuation said that RESUME-2 lifts
   `whatsapp_channel/acts._run` and `whatsapp_acts.confirming` into one
   function. That is wrong. `act_function` imports only `ALLOWED_ACTS` and
   `PROFILE_ACTS`, and `confirming` compares the text only. The web yes uses
   `late_approve.act_of`, which compares the text and the act keys.
3. D-RES-7 (the live stream stays 24 h) belongs to RESUME-2b.
4. HRD-22 is on `main`. It is not in flight.

**3. Consent.** The member's tap is the only consent. Each rule below fails
closed:

- The stored call runs once. The row moves once, and the yes holds for one
  card.
- It runs for the same member, thread and org as the card. They come from the
  session and the row, never from the request. The request names only the
  request id and the answer.
- The tool builds its card again from the record as it is now. The yes holds
  only for an equal card (the text and the act keys). A changed record gives
  another card. The yes then says no, the tool writes nothing, and the new
  card shows (D-RES-3).
- An expired row (`expires_at`, 7 days) runs nothing.
- A "Not now" and a `REJECT` run no tool. A `REJECT` starts no continuation.
  With both resume flags on, a late "Not now" of a row with a stored call
  starts a short continuation (RESUME-4a-2, D-RES-9). The model then says
  that nothing was done. A row with no stored call runs nothing and starts
  no run, as before.
- A tool that left the resumable list runs nothing, and the card asks again.
- A continuation is the member's own run. It counts on the run cap, and it
  binds the member and the org of the session.

**4. A card can wait hours or days.** No process holds a parked card. Its
state is the row (7 days), the stored call on the row, and the saved reply in
Postgres. The exact path adds the native session row.

The live stream stays 24 h after the park (D-RES-7). The run cap counts no
parked card. So a card that the chat asked on Friday can resume on Monday.

- **My Day.** WAIT-1 and WAIT-2 (§8, tasks 1550 and 1551) show the waiting
  card on My Day. A tap opens the chat at the card. RESUME-7 adds Approve and
  Not now on the My Day card itself (D-RES-5). That Approve takes the same
  path, and the reply goes on with no chat open.
- **Push.** The "needs your answer" event of S7 (H-289) opens the chat through
  the S3 `open-chat` link. A push holds no content (S7 item 5), so a push
  never approves.

**5. What the member sees (RESUME-3).** When nothing changed, the chat shows
no notice. These are the exact words:

| Moment | Words | Where |
|---|---|---|
| A card waits, live or parked | "Waiting for your answer" | The status row. "Paused. Answer the card to go on" goes (D-RES-6) |
| Approve, and nothing changed | "Approved · <title>", then the reply goes on | The receipt line of the card (`confirmationQueue.ts`), as for a live card |
| The record changed after the card | "This changed after the card was made, so nothing was done yet. Check the new card." | One line above the new card |
| The card expired | "This card expired, so nothing was done. Ask again if you still want it." | The receipt line |
| Another tab or device answered first | "Already approved · <title>" or "Already answered · <title>" | The receipt line |
| The run cap refused the continuation | "You have 5 assistants running. Wait for one to finish or stop one, then answer the card again." | The cap notice. The card stays |
| A card that cannot resume (D-RES-2) | "The chat had stopped waiting · Your answer went to it as a new message." When the answer steered a live run: "The chat had stopped waiting · Your answer went into the current reply." | The slim notice, in place of "The assistant restarted". RESUME-3a changed the title (`runErrors.ts`). The bodies stay |
| The stored call failed in the tool | No notice. The reply says what the tool said | The model reads the text of the tool |

**Case B needs a new rule (RESUME-3a, C1).** RESUME-2a starts a NEW run
with a new run id and a new reply row. After a reload, `GET
/chat/active-sessions` lists the parked row with no run id, so `runKeyOf`
cannot tell the parked run from the new one. With no rule, the attach
replays the new run into the OLD reply. So the run state
(`lib/runStatus.ts`) holds two rules:

- The 200 `resumed` dispatches the run event `resumed {runId, messageId?}`.
  It marks that run new, even with no run key. A list taken before the
  Approve, or within `START_GRACE_MS` after it with no row, changes nothing.
- After a park, a run that the server lists as running, or as waiting live,
  with another run key is a new run. It never replays into a reply that
  shows (`staleTail`). A list that names the SAME run key is the old run. A
  park is the phase `parked`, or the quiet rest after it.
- A `resumed` event for the run key that the list already bound and attached
  changes only `resumedAt`. It keeps the reply id and `staleTail`.

The attach replays into the `messageId` of the event when the server names
one (C2). Else it takes the server's own reply row of the run, from the saved
rows. Else it draws a placeholder. In each case one bubble shows after the
fold (`chatResume.test.ts`, `e2e/chat-card-restart.spec.ts`).

RESUME-2b (2026-11-17) adds `messageId` and `sameReply` to the 200, so the
`messageId` branch of `replayIdOf` has a value once a flag is on. Before
that, one bubble rested on the minted row of S14 and on the poll, which skips
identical text.

The same bubble of case A belongs to RESUME-2b and RESUME-3b. RESUME-3a
changes the chat in these places:

- `postRespondInput` takes `resumed: true`. It closes the card as "Approved",
  sends no envelope, draws no notice, and asks for the run list at once, so
  the attach starts.
- The BFF keeps `parked` on `RUN_FINISHED`. A park then shows no "Done", and
  the card stays in place.
- A parked `done` settles the reply, and keeps its card on screen. It emits
  no `onRunFinalized`, and it clears the card log, so a remount takes the
  card from the pending asks only. It still emits `onRunRested`, so a slot of
  a redraft ends (C3). The run state goes to `parked`, never `done`.
- A 429 `too_many_runs` keeps the card, with the cap words above as its
  reason.
- When the server stops listing a parked thread, the chat reads the pending
  asks once, after the run rests. A card that the list does not hold closes.
- A `confirmation_resolved` with `APPROVE` for a card that waited parked
  here, and that this tab did not answer, leaves "Approved · <title>".
- The two `run_restarted` notices take the words above.
- In case A, RESUME-3b (task 3475) keeps the reply open when a replay reads
  on after a `done` in the middle of the stream. It is built (below).
- RESUME-3c (task 3476) adds the words that need a server code. They are
  for a changed record, an expired card, and an answer from another tab
  first.

**What RESUME-3b built (2026-11-19).** The chat side of case A. Case A keeps
the parked run's id, so the keys tell the parked run from a new one. Each
rule below is a fence in `chatResume.test.ts`:

- **The list.** A parked row of `GET /chat/active-sessions` names its run
  (`parkedRunId`), the `run_id` of the caller's newest waiting question. It
  shows only where both resume flags name the org. The run state keeps it as
  `parkedRunKey`.
- **The same run.** After a park, a listed run with that key, or with the
  run key that this chat already holds, is the SAME run. It is not new, it
  has no stale tail, and the parked reply stays the target of the replay.
  A match by the run key alone also sets `parkedRunKey`, so the line below
  draws under the reply (review of 2026-11-19, P2-1).
- **The 200.** `resumedOf` reads `sameReply` (only JSON `true`). Three
  things send a `resumed` event into the parked reply. They are `sameReply`,
  the key `parkedRunKey`, and the run key while the chat rests after the
  park. A decline that comes late never takes case A. Its 200 has a new run
  id and no `messageId`, so it stays a new run.
- **The second start.** `api/agent/chat/route.ts` sends one frame
  `run_resumed` for a `RUN_STARTED` marked `resumed`, after the run binding
  accepts it. A plain start sends no frame, as before.
- **The end of a replay.** The attach loop reads on past a `done` in the
  middle of the stream, as it did before. The fault was its terminal flag,
  which nothing reset. Now `replayEndAfter` gives the end: the last `done`
  after the last `run_resumed`. A `run_resumed` makes the reply stream again,
  and a cut after it reads as not finished. The e2e holds this with a
  replay that is cut after part two (review of 2026-11-19, P2-3).
- **Not in this slice.** A replay that starts at the second start, with no
  part one, is not handled here. RESUME-2d (task 3724) may cut part one for
  a late joiner, and it owns that replay.
- **The line.** In case A, the "Approved" line of another device draws under
  the parked reply, the run's own (`receiptAnchorBeforeRun`).
- **A known gap (task 3818), closed by RESUME-2e.** The memory extraction
  at the end of a case A run read the parked part again (RESUME-2b). Now it
  reads only the part after the park (§4.12.7, the RESUME-2e row).

**What RESUME-3d built (2026-11-19).** A tab that opens during a
continuation gets no 200, so it had no reply id. The attach then took the
last saved reply. In case B that is the parked reply, so the new run
replayed into it, and the text could show twice until a reload. Now the
live row of a continuation names its reply row:

- **The server.** The continuation start (`start_detached`, with
  `continuation=True`) writes the `messageId` of the run record into the
  run's entry in `cc:<org>:liveruns`. A plain run writes no reply row. The route
  `GET /chat/active-sessions` sends it on the live row, only where both
  resume flags name the org. It adds no key, no table and no migration.
  Only a reader who may see the chat gets the row, as before.
- **The chat.** The run state keeps the listed id as `listedReplyId`, and
  a new run takes it as its reply. The attach then replays into that row,
  and it reads no saved row (`attachPickOf`). In case A that is the parked
  reply, and in case B the new one.
- **With no field.** An old gateway, or a flag that is off, sends no
  `messageId`. Each state and each attach is then as before.

**6. HRD-22 and this design.** HRD-22 (task 3231) is the stop-gap on rung 0.
On a late Approve of a parked plain card, it writes a one-use grant in Redis.
The equal card of the NEXT run then passes with no new card. It cuts three
approvals to one.

The member still sees the envelope turn, the notice, and a new run that plans
again. This design takes the parts of HRD-22:

- The equal-card rule of HRD-22 (the card text and the act keys) is the
  consent check of RESUME-2.
- HRD-22 writes its offer at the park, in Redis. RESUME-1 writes the stored
  call at the card, on the row.
- With `CHAT_RESUME_ORGS` on, a row with a stored call takes RESUME-2, and
  HRD-22 never sees it. A row with no stored call takes HRD-22. That is a row
  from before RESUME-1, or a call above the size cap.
- RESUME-8 removes the HRD-22 grant after RESUME has been on for each org for
  7 days, the life of a row (D-RES-8).

#### 4.12.5 A release keeps the work (RESUME-5, RESUME-6)

**The state.** D194 releases at 05:30, 13:30 and 21:30 UTC. That is 11:00,
19:00 and 03:00 in India, so two of the three land in the working day of our
members. The stop waits up to 60 s for working runs
(`GATEWAY_DRAIN_WAIT_SECONDS`, `TimeoutStopSec=75`). It never waits for a run
on a card (`routes/drain.py`). The runs live in the gateway process, so the
stop ends each run that is still there.

**The evidence of D-1 is out of date.** §5 deferred D-1 on 2026-10-29 because
the release landed when nobody worked, from 23:49 to 01:33 UTC. D194 changed
that on 2026-11-07. This design keeps D-1 deferred, and hands the work over
inside one process (D-RES-4). Before RESUME-5 starts, an agent reads the
`gateway-drain:` lines of the last 7 days on the box. That read changes
nothing. RESUME-5 was built on 2026-11-19 with no such read, because its
brief kept the agent off the production box. The read is still open, and
it must come before the flag flip.

**RESUME-5, park at the stop.** `gateway-drain.sh` first calls a new route,
`POST /internal/drain/park`. Each run of the process that waits on a person
parks at once, with the rules of `pending_ask.park`. Its row goes to
`parked`, its stored call is already on the row, and its reply saves with no
"interrupted" mark. Then the drain counts as today.

After the release, an Approve resumes the card as §4.12.4 says, in the same
reply (`sameReply` true, case A). The member sees "Waiting for your answer"
before, during and after the release.

**What RESUME-5 built (2026-11-19, task 3265).** These rules bind the slice:

- **The flags.** A run parks at the stop only where `CHAT_DURABLE_ASKS` is
  on and `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS` both name its org
  (`chat_resume_on` and `late_approve_on`). The slice adds no flag and no
  migration. With either flag off, no wait takes the stop signal, and the
  route answers `{"tried": 0, "parked": 0}`. The bytes of each run, row and
  message are as before.
- **The gate.** AGENT-SAFE to build, dark. The flag flip is OWNER-GATE
  (`enforcement-flip`). The edit of `deploy/hostinger/gateway-drain.sh` is
  under the dated `deploy-write` grant of `.claude/OWNER_GRANTS.md` (to
  2026-11-30).
- **The park runs in the run's own task.** The route wakes each wait that
  exists now (`pending_ask.park_at_stop`). Each wait then parks in its own
  task (`executor.wait_user_future`, `pending_ask.park_for_stop`). `park`
  reads two ContextVars of the run. The HRD-22 offer reads the card of the
  wait, and the "Not done" line reads the closed cards of the run. A call
  from the route task would lose both.
- **The flags are read twice.** A wait takes the signal only when both flags
  name the tenant that the run bound. The park reads them again with the org
  of the row. A refusal keeps the wait, as before. No tenant and no member
  come from the request (R5).
- **The count waits for the park.** Until its park takes the Future, a
  thread reads as waiting on a person. So `GET /internal/drain` reads a woken
  thread as a running run (`pending_ask.parking_threads`). Then the stop
  waits for the drain of the run. The drain saves the reply and forgets the
  record, so the next process writes no `run_interrupted` mark.
- **The script.** The call has its own bound (`GATEWAY_DRAIN_PARK_SECONDS`,
  8 s), and the route waits at most `pending_ask.STOP_PARK_SECONDS` (5 s).
  The bound of the wait starts before the call, so the whole stop step stays
  inside `TimeoutStopSec=75`. A 404 from an older gateway (the first
  release), and each other failure, lets the drain go on. The script always
  exits 0. It writes a `gateway-drain: parked N run(s)` line only when a run
  parked, and a `no park answer` line when the call failed.
- **A card that opens during the wait of the stop does not park.** The
  owner's orchestrator decided it on 2026-11-19. The signal wakes only the
  waits that exist when the route runs. A later card takes the restart path
  of before (§4.12.2 row 5): the stop cuts the run, and the row stays
  `open`. With the RESUME flags on, an Approve then resumes it in case B,
  because `_same_reply_of` takes case A only for a `parked` row. The stored
  call is on the row from the card (`note_event`, `_record`), and
  `_thread_at_rest` closes the dead run first. So the card writes once.

Fences (R7):

- `tests/unit/test_gateway_drain.py`: the script, the route and the count.
- `tests/unit/test_pending_ask_flow.py`: the park in the run's own task, the
  flags, the late card, a refusal, and an answer that comes first.
- `tests/unit/test_resume_continuation.py`: the restart and the same reply.
- `tests/unit/test_resume_continuation_r8.py` (R8):
  `test_a_card_parked_at_the_stop_goes_on_in_the_same_reply`.

**RESUME-6, a cut run goes on by itself.** The next process closes a run that
the stop cut, as today (`run_liveness.sweep_dead_instances`,
`chat_recovery.recover_dead_run`). Then the sweep starts a continuation run
for it with no tap, when all of these hold:

- The run was a chat run of a member, and it started less than 30 minutes
  ago.
- No write was open at the cut. A `TOOL_CALL_START` with no result is a read,
  or no tool call is open.
- `CHAT_AUTO_CONTINUE_ORGS` names the org.

The continuation keeps the run id and the stream, as in RESUME-2. Its note is
`RESUME_NOTE` (`gateway/chat_recovery.py`), with the steps that already ran
(`chat_supersede._cap_keeping`, the edit note of §4.9). A read that the cut
stopped runs again. The member sees the same reply go on, with no notice and
no Continue.

When a write was open at the cut, nothing goes on by itself. The member sees
the "interrupted" notice with Continue, as today. The note then tells the
model to check that write before it acts again.

#### 4.12.6 What this retires, and the migration path

| What goes | When |
|---|---|
| The 409 `run_restarted` and the envelope turn, for a card with a stored call | RESUME-2 and RESUME-3 on, for each org |
| The words "The assistant restarted" (`runErrors.ts` `run_restarted` and `run_restarted_steered`) | RESUME-3a changed the words (2026-11-16). The code `run_restarted` stays for a card that cannot resume |
| The S2 known limit "A parked confirmation asks again in the new run" | RESUME-2 on |
| "Paused. Answer the card to go on" | RESUME-3a (2026-11-16) |
| "The assistant was interrupted by an update", after a release | RESUME-6 on, for a run with no open write |
| The HRD-22 grant (`acb_skills/late_approve.py`, `CHAT_LATE_APPROVE_ORGS`) | RESUME-8, 7 days after RESUME is on for each org |

**The path.**

1. Build dark. `CHAT_RESUME_ORGS` and `CHAT_AUTO_CONTINUE_ORGS` are comma
   lists of org ids, or `*`. Each is empty by default. A SIGHUP reload
   applies a change (`docs/flag_reload.md`). Each needs `CHAT_DURABLE_ASKS`.
   Off, each run, row and message is byte for byte as before.
1a. Turn on `JOB_QUEUE` on production before `CHAT_RESUME_ORGS` names an
   org (gate `enforcement-flip`). The prune of task 3435 runs only in the job
   worker. With `JOB_QUEUE` off, the stored call of an expired row stays on the
   row.
2. Show the owner the rung demo on this PC (D145).
3. Turn the flag on for one pilot org on production (gate
   `enforcement-flip`). Run the demo there.
4. Turn it on for `*`.
5. After 7 days at `*`, RESUME-8 removes what the table names.

#### 4.12.7 The slices (D211)

Each slice is one PR. A foundation rides with the first rung that needs it
(D211 rule 5).

| Slice | Rung | What | Done when | Fence (R7) | Tier | Depends on |
|---|---|---|---|---|---|---|
| RESUME-0 (task 3255) | — | This section, the INDEX row and the WS-51 row (task 3255) | STE lint and the doc fences pass | `tests/unit/test_index_completeness.py` | LOW | none |
| RESUME-0a (task 3259) | 0 | A failed read of the pending asks settles no card | The BFF route answers a failure as a failure. The chat closes no card on a failed read | `src/lib/pendingAsks.test.ts` | STANDARD | none |
| RESUME-1 (task 3260) | 1 | The card stores its call: the `stored_call` column, the bind at the card, and the resumable list. RESUME-1a (task 3260) is the plain card, BUILT 2026-11-15 and dark. RESUME-1b (task 3434) is the question cards | A plain card of each listed write stores its tool, arguments, call id, card text and act keys. A rows card, an email card and a large call store none. The browser never reads it. Each listed tool writes nothing before its card. R8 holds the column under FORCE RLS | `tests/unit/test_resume_stored_call.py`, `tests/unit/test_resume_stored_call_r8.py`, `tests/unit/test_tenant_coverage.py` | HIGH (a migration, consent). AGENT-SAFE to build. The flag flip is OWNER-GATE (`enforcement-flip`) | none |
| RESUME-2a (task 3261) | 1 | A late Approve of a plain card claims its row once and runs the stored call once, in a NEW reply (case B): the claim, the 200 `resumed`, the stored-call yes, the prologue and the note path. **BUILT** 2026-11-16, dark. The follow-ups P2-1 to P2-3 (task 3467) are built: an atomic drop of the offer, the mark under either flag, and a claim of three answers | The sequence of 2026-11-14 on a real Postgres and a private Redis gives one card, one write, no 409 and no member turn. A changed record writes nothing and shows the new card. Another member, thread or org runs nothing. A second answer runs nothing. Flag off, the bytes are as before | `tests/unit/test_resume_continuation.py`, `tests/unit/test_resume_continuation_r8.py`, `tests/unit/test_late_approve_grant.py`, `tests/unit/test_late_approve_grant_r8.py` | HIGH (consent, the run loop). AGENT-SAFE to build. The flag flip is OWNER-GATE (`enforcement-flip`) | RESUME-1 |
| RESUME-2b (task 3460) | 1 | Case A: the same reply. The same run id, no stream reset, the XDEL of the parked `RUN_FINISHED`, the fold into the same row, and D-RES-7. **BUILT** 2026-11-17, branch `stu-resume2b`, dark | A thread whose last turn is the parked turn goes on in the same reply | `tests/unit/test_resume_continuation.py`, `tests/unit/test_resume_continuation_r8.py` | HIGH (the run loop) | RESUME-2a |
| RESUME-2c (task 3461) | 1 | Case C: a live run on the thread. The stored call runs at once, and its result goes into the live run as a steer note | An Approve during a live run acts once, and the live reply knows it | `tests/unit/test_resume_continuation.py` | HIGH (consent, the run loop) | RESUME-2a |
| RESUME-2d (task 3724) | 1 | **BUILT** 2026-11-19, branch `mt-82b507bd-resume2d`. The reconnect replay gives a reader the WHOLE run or none of it (D-RES-10, option B, owner 2026-11-19). `GET /agent/run/{thread_id}/reconnect` replays the run only when the chat is solo for the reader, or the reader matches a participant row (`join_message_ts`) whose join time is at or before the start of the run, with a margin of 2 s: `join_ms <= run_start_ms + 2000`. Any other reader gets no event of the run, and then the end frame with the marker `withheld: true`. The chat then rests, and it does not attach to that run again while the server lists it. One rule covers a live, a parked and an ended run. It closes the gap of round 2, P2-c, which D-RES-7 makes 24 h wide. It needs no migration, no new state and no flag | A late joiner reads no event of a run that started before the join, through the reconnect. A passer-by of an org-visible room reads none either. A solo chat replays as before, byte for byte. The saved reply stays readable by the saved-message view | `tests/unit/test_reconnect_waterline.py` (hermetic) and `tests/unit/test_reconnect_waterline_r8.py` (R8) | HIGH (who reads what: the waterline of a room). No flag: the fix leaves solo bytes unchanged | RESUME-2b |
| RESUME-2e (task 3818) | 1 | **BUILT** 2026-11-20, branch `mt-5c919acd-resume2e`. The memory extraction at the end of a case A run reads only the part after the park. The end of the park folds part one and extracts it. The fold of a case A run that writes the whole replay into the reply row now also gives `chat_fold.MEMORY_CONTENT_KEY`: the text of the events after the deleted parked end (`same_reply_own`). `build_extraction_conversation` reads that key in place of `content`. The row still gets both parts. The fallback row holds part two only, so it needs no key. A continuation sends no words, so its run end has no user turn and extracts nothing. A steer that the ENGAGE seam replays gives it a user turn, and memory then gets part two only. The watermark is the `parkedEndId` of the run record, which RESUME-2b already writes. No new state, no flag of its own and no migration | A case A run extracts each fact of part one once. A plain run and a case B run extract as before, byte for byte | `tests/unit/test_resume_continuation.py` (section 16) and `tests/unit/test_resume_continuation_r8.py` (R8) | STANDARD (the run end seam). It acts only where `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both name the org | RESUME-2b |
| RESUME-3a (task 3262) | 1 | The chat takes the resume of case B: no envelope, no notice, the BFF keeps `parked`, the `resumed` run event (C1), the replay target (C2), the 429 reason, the stale card, the Approve of another device, and the words of §4.12.4 item 5 that need no server code. **BUILT** 2026-11-16 | A parked card approved after a reload turns to "Approved", and ONE new reply goes on at the end. No "restarted" shows, before or after a reload. A park shows no "Done". Visual review of the card and the row | `src/lib/chatResume.test.ts`, `e2e/chat-card-restart.spec.ts` | HIGH (the attach loop) | RESUME-2a |
| RESUME-3b (task 3475) | 1 | The chat side of case A. (1) The BFF sends one frame `run_resumed` for a `RUN_STARTED` marked `resumed`. (2) The attach loop ends a replay at the last `done` after the last `run_resumed` (`chatResume.replayEndAfter`), and a `run_resumed` makes the reply stream again. The attach always read on past a `done` in the middle. The fault was its terminal flag, which nothing reset. (3) `resumedOf` reads `sameReply`, and `AgentChat` passes it in the `resumed` event. (4) `ServerRun.parkedRunId` and `RunStatus.parkedRunKey`: a listed run with the parked key is the same run, and the parked reply stays its target. (5) The "Approved" line of case A draws under the run's own reply. (6) `GET /chat/active-sessions` adds `parkedRunId` to a parked row, only with both resume flags on for the org. Non-goals: RESUME-3d (task 3785), RESUME-2d (task 3724) and RESUME-3c. **BUILT** 2026-11-19, branch `stu-resume3b`. The known gap of task 3818 (the memory extraction at the end of a case A run read the parked part again) is closed by RESUME-2e. | (a) Three tabs: the approving tab after a reload, the approving tab with no reload, and a second tab that showed the parked card. In each, the card leaves with "Approved · <title>" under the parked reply, and the reply goes on in THAT bubble. One bubble shows during the run and after a reload. (b) A replay that holds a `done` and then a `resumed` start keeps the reply open and shows no "Done". It ends as the last frame says, and a cut after that point reads as not finished. (c) A second `RUN_STARTED` with the same id adds no bubble. (d) With no `sameReply`, no `resumed` start and no `parkedRunId`, each frame and request is as before. Each state is as before, with one exception. After a park, a list that names the run key that the chat already holds is the same run going on. Then `doneMessageId` clears, the attach waits no gap, the tail is not stale, and `parkedRunKey` takes that key. (e) With a flag off, the list rows are as before | `src/lib/chatResume.test.ts`, `src/lib/runBinding.test.ts`, `src/lib/liveRuns.test.ts`, `e2e/chat-card-restart.spec.ts`, `tests/unit/test_pending_ask_flow.py` | HIGH (the attach loop). AGENT-SAFE to build. No flag of its own. The flip stays OWNER-GATE | RESUME-2b (PR 2583), RESUME-3a |
| RESUME-3d (task 3785) | 1 | The live row of a run names its reply row (`messageId`). A tab that did not get the 200 then replays a continuation into that row, with no read of the saved rows. (1) `start_detached(continuation=True)` gives the `messageId` of the run record to `register_live_run`. A re-register of the same run keeps it. (2) `list_live_runs` and `GET /chat/active-sessions` send it on the live row, only with both resume flags on for the org. (3) `LiveRun.messageId`, `ServerRun.messageId` and `RunStatus.listedReplyId`. (4) `chatResume.attachPickOf` picks the target of the attach. **BUILT** 2026-11-19, branch `mt-dbde72d3-resume3d`. No flag of its own, and no migration. It shows only where `CHAT_RESUME_ORGS` and `CHAT_LATE_APPROVE_ORGS` both name the org | A run that a tab lists after a reload replays into its own reply row, in case A and in case B | `src/lib/chatResume.test.ts`, `src/lib/liveRuns.test.ts`, `tests/unit/test_active_sessions_tenant.py` | HIGH (the attach loop) | RESUME-3b |
| RESUME-3c (task 3476) | 1 | The words of §4.12.4 item 5 that need a server code: the changed record, the expired card, and another tab or device first | Each moment shows its words from the server's code | `src/lib/chatResume.test.ts` | STANDARD | RESUME-3a, and the server codes |
| RESUME-D1 (task 3263) | 1 | Chat · Rung 1 demo · Approve a card an hour later, and the work goes on | The owner runs the script below on production and says so on the task | none | demo | RESUME-0a to RESUME-3, and RESUME-2d |
| RESUME-4a (task 3478) | 2 | Two parts. **4a-1, BUILT 2026-11-16, dark:** after each continuation on the note path, each stored native session of the thread goes stale. The prologue marks it before the stored call and before the model runs, so the next turn reads the text history that holds the reply. **4a-2, BUILT 2026-11-17, dark:** the late decline (D-RES-9, decided). A late "Not now" of a row with a stored call starts a short continuation that runs no tool. Server only: no client sends it until RESUME-7 | 4a-1: in case B with `MAF_NATIVE_SESSIONS` on, the turn after a continuation loads no session for any agent of the thread (`digest_drop`, reason `resume_stale`), and the next turn reads the text history (which holds the reply when the browser's copy holds it). Another org cannot mark a row. A thread with no row gets no row, and a second mark changes nothing. With the flags off or `MAF_NATIVE_SESSIONS` off, the run sends no statement. 4a-2: the row moves to `closed` once, the 200 says `resumed` and `declined`, no tool runs, and the note holds the `CARD_DECLINED` text. A row with no stored call, a flag off and each other miss take the late decline of before. A cap refusal or a refused start still closes the row with a 200 | `tests/unit/test_resume_continuation.py`, `tests/unit/test_resume_native_stale_r8.py` (R8), `tests/unit/test_resume_continuation_r8.py` (R8) | HIGH (the run loop, the native session store). AGENT-SAFE to build. The flag flip is OWNER-GATE (`enforcement-flip`) | RESUME-2a |
| RESUME-4b (task 3479) | 2 | The exact path: save the session at the park with call X and a placeholder result, swap the real result under X in `session.state`, and run with no new message | The model input of a continuation holds the call and its real result under one call id, and no orphan call. A run that is not a continuation keeps the preamble goldens byte for byte (`tests/unit/test_native_session_persistence.py`, `evals/trajectories`) | `tests/unit/test_resume_native_session.py`, `tests/unit/test_native_session_persistence.py` | HIGH (the run loop). AGENT-SAFE to build. The flag flip is OWNER-GATE (`enforcement-flip`) | RESUME-4a |
| RESUME-5 (task 3265) | 2 | Park at the stop. **BUILT** 2026-11-19, branch `resume5`, dark behind `CHAT_RESUME_ORGS` AND `CHAT_LATE_APPROVE_ORGS`, with `CHAT_DURABLE_ASKS` on (§4.12.5, "What RESUME-5 built") | A stop with a card open parks its run. The row is `parked`, the reply has no "interrupted" mark, and an Approve after the restart resumes it in the same reply (`sameReply` true). A card that opens after the call does not park. Flags off, the bytes are as before | `tests/unit/test_gateway_drain.py`, `tests/unit/test_pending_ask_flow.py`, `tests/unit/test_resume_continuation.py`, `tests/unit/test_resume_continuation_r8.py` (R8) | HIGH (the deploy path). AGENT-SAFE to build (dark). The flag flip is OWNER-GATE (`enforcement-flip`). The `gateway-drain.sh` edit is under the dated `deploy-write` grant | RESUME-1 |
| RESUME-6 (task 3266) | 2 | A cut run goes on by itself | A cut run with no open write goes on in the same reply with no notice. A run with an open write shows Continue, as today. Flag off, nothing goes on by itself | `tests/unit/test_resume_after_release.py` | HIGH (the run loop, the deploy path) | RESUME-2, RESUME-3 |
| RESUME-D2 (task 3267) | 2 | Chat · Rung 2 demo · A release does not stop the chat | The owner runs the script below at a release slot | none | demo | RESUME-4 to RESUME-6, RESUME-D1 |
| RESUME-7 (task 3268) | 3 | Approve and Not now on the My Day card | An Approve on My Day resumes the card with no chat open. It follows each consent rule of §4.12.4 | `tests/unit/test_my_day_card_approve.py` | HIGH (consent) | RESUME-2, WAIT-1 (1550), WAIT-2 (1551) |
| RESUME-8 (task 3269) | 3 | Retire the HRD-22 grant and the old words | No code path writes the HRD-22 grant or shows "The assistant restarted" | `tests/unit/test_resume_retired_paths.py` | STANDARD | RESUME-D1, and 7 days with RESUME on for each org |
| RESUME-D3 (task 3270) | 3 | Chat · Rung 3 demo · Approve tomorrow, from My Day | The owner runs the script below | none | demo | RESUME-7, WAIT-1, WAIT-2, H-289 |
| PARK-LINE (task 3595, slice 3595-a) | 0 | A parked reply says what it ran. **BUILT** 2026-11-17, no flag | See the rule below the table | `tests/unit/test_park_made_line.py` | HIGH (the run loop). AGENT-SAFE to build. No flag, so it is live at the merge | none |

**PARK-LINE (task 3595, slice 3595-a).** In run dd49a886, a reply made a
project, a child project, three tasks and a reminder on five approved cards.
Then it stopped at a card that nobody answered, and the run parked. The
member never heard what the reply had made. So these rules hold:

- The server sends one line at the park, not the model. The line is "Approved
  and run in this reply: «a» and «b». This reply stopped at the card «c» to
  wait for your answer." Three acts or more read "«a», «b», and «c»".
- The words tell history, so they stay true after a late Approve. The fixed
  words have no word "done", no link and no id. Each act is inert text from
  its card title (`card_act`), so a title such as "Mark this task done?"
  keeps its own words.
- An act ran only on positive proof from its own tool. The tool calls
  `ask_tools.act_ran()` at its success site, after the write landed in full.
- The gate wrapper `_tool_injection._agated` records the act after a normal
  return. It records only when the tool gave that proof and the member
  approved the card. A raise records nothing. A tool that delegates records
  nothing itself.
- The proof belongs to one call. The wrapper clears it before each call
  (`forget_card_marks`), and each new card clears it too.
- The rule fails safe. A tool that never calls `act_ran` names nothing. The
  wrapper reads no result text, because a text cannot prove an act.
- Two reviews found real texts of acts that did not run, and the old text
  check read them as run. So this change removed `result_says_not_run`, its
  word lists and `card_records_no_act`.
- These tools opt in, each at its success site only. `create_project` names
  a project and a child project when an id comes back. `create_task` names a
  task when each write after the create landed, with no chat file.
- `create_tasks`, `create_tags` and `create_types` name a full batch only.
  The member ticked each row, and each row landed with its follow-ups.
- `set_reminder` names a new reminder when an id comes back and its card showed (a held chat: in a normal chat the tool shows no card, so the line does not name it). The CRM tool
  `update_deal_status` names a move when the move landed.
- The line never names a partial batch or a row that was left out. It never
  names a reminder that was already set, or a deal move that the mirror
  refused.
- It never names a plan or a preview card, such as "Build X?" of
  `studio_plan`. It never names a tool that is not on the list.
- Examples of tools that are not on the list: `nudge_task`, `propose_edit`,
  `propose_page`, the profile writes, `bulk_update` and `manage_inbox`.
  `schedule_send` is not on it either, because its card only schedules.
- A new opt-in changes `ACT_RAN_SITES` in `test_park_made_line.py`, so a
  reviewer reads its success site. `packages/acb_skills/AGENTS.md` gives
  the rule to tool authors.
- The receipt of a batch says "To retry a row that is not in this list".
- Only a run that is not delegated opens the record
  (`executor._open_approved_acts`). So a child run writes into its parent's
  record, and one line names both.
- `pending_ask.park` sends the line after the "Not done" line of task 3130,
  and before `RUN_FINISHED` with `parked`. With no act, the park sends the
  bytes of before.
- Slice 3595-b keeps the steps of an open hand-off at a park, and it is not
  built. The timeout path that is not durable is out, and so are the words
  of the 7-day expiry (RESUME-3c).

**The demo scripts.**

- **Rung 1.** On production, turn `CHAT_RESUME_ORGS` on for the pilot org.
  Ask the chat to move a deal or a task. Close the chat when the card shows.
  After one hour, open the chat. The card shows in the same reply, with
  "Waiting for your answer". Tap Approve. The card reads "Approved", the same
  reply goes on, no new card comes, and no notice shows. The record changed
  once. Reload, and the reply is whole, with no member turn added.
- **Rung 2.** At 13:28 UTC, ask one chat for a long CRM read. Leave a card
  open in a second chat. After the 13:30 release, the first reply finishes
  with no notice and no Continue. Approve the card of the second chat, and it
  acts once.
- **Rung 3.** At 18:00, leave a card open. The next morning, the card shows
  on My Day on the phone. Approve it there. The chat then shows the finished
  reply.

**Findings for `chat_failure_modes.md`.** That spec stays their owner.

- HRD-16 takes the question card with no resolve event, and the losing answer
  that becomes a steer (scenarios 1 and 6).
- HRD-17 takes three findings of scenarios 5 and 7. They are the false
  "Done" after 20 s offline, and the "Metorite is updating" notice while
  offline. The third is the held send that lives in memory only.

#### 4.12.8 Proposals for the owner

**ACCEPTED, 2026-11-14.** The owner chose "Approve all 8 recommendations" on
task 3255 (answer `6d371aad`). Each recommendation below is now the rule.
None takes a D number in `work_plan.md` §3.

| # | Question | Recommendation |
|---|---|---|
| **D-RES-1** | Is a tap on Approve of a parked card the member's consent to run the stored call once, for the life of the row (7 days)? | Yes, under the equal-card rule. The tap is consent at the time of the tap, and the card shows what the tool will write now. D-HRD-7 proposes 30 minutes for the HRD-22 grant, which plans again and so needs a short window |
| **D-RES-2** | Which cards resume with no model? | A plain confirmation card of a write in `egress.HELD_CARD_WRITE_TOOLS`, and the question cards. Not a rows card, a reply form, a held send, an egress tool, an MCP tool or a generative UI. Those keep the resend path with the new words. A later slice can add the rows card |
| **D-RES-3** | When the record changed since the card, does the chat show the new card at once, or only a line? | The new card at once, with the line "This changed after the card was made, so nothing was done yet. Check the new card." |
| **D-RES-4** | At a release, may a waiting run park, and a cut run go on by itself with no tap and no notice? | Yes. A run with an open write never goes on by itself. D-1, a separate run worker, stays deferred |
| **D-RES-5** | May a member approve a waiting card on My Day, with no chat open? | Yes, with the same rules. Never from a push, because a push holds no content |
| **D-RES-6** | Does a parked card look the same as a live wait? | Yes: "Waiting for your answer" for both. A park is a server detail |
| **D-RES-7** | How long does the live stream of a parked run stay? | 24 hours. After that, an Approve shows the rest of the reply as a new reply directly below the parked one |
| **D-RES-8** | Turn HRD-22 on now, as the stop-gap of rung 0? | Yes, after its own review. RESUME-8 retires it 7 days after RESUME is on for each org |

**D-RES-9, DECIDED 2026-11-16 (task 3264, answer `3be3ea82`).** The owner
chose option 1: "Start a continuation: the model says nothing was done." A
late decline of a parked card starts a short continuation. The other option
was to record the answer only. RESUME-4a-2 builds the decision (§4.12.4,
"What RESUME-4a-2 built").

**D-RES-10, DECIDED 2026-11-19 (task 3724, answer `a078f5dc`).** The owner
chose option B, with no flag. A reader gets the whole run or none of it.

A reader gets the run in two cases. The first case is a solo chat. The second
case is a match with a participant row. Its join time is at or before the
start of the run (a margin of 2 s). Any other reader gets no event of the run, and then the normal end.

One rule covers a live, a parked and an ended run. It needs no migration and
no new state, because the run has no label to read today.

The rule is stricter than the saved-message view. A late joiner of a
full-history room loses the live view of that one run. So does a passer-by of
an org-visible room. That reader still reads the saved reply.

The other options were two. Option B behind a dark flag
`CHAT_REPLAY_GUARD_ORGS` was the first. Option A was the second. It keeps
parity with the saved-message view, and it needs new state for the label of a
live run. RESUME-2d builds the decision (§4.12.7).

**What RESUME-2d built (2026-11-19).** The rule is the pure function
`gateway.rooms.replay_allowed`. The route calls it once, through
`routes/agent.py` `_replay_ok`.

- **The start of a run.** It is the ms part of the id of the first stream
  entry (`stream_relay.stream_head_ms`). Each run resets the stream, so the
  first entry is the start of the run. No run record keeps a start after the
  run ends. `join_stream_id` is never written, so the rule reads
  `join_message_ts`. Both values are epoch ms. The join time comes from the
  gateway clock, and the start comes from the Redis clock. The margin of 2 s
  takes a small skew.
- **One stream holds one run.** A continuation of case A keeps the run id
  and the stream. So the start of the stream is the start of part one. A
  member who joined after part one gets no event of part one or part two.
- **The join row.** The row that names the reader wins. A NULL join on that
  row is the owner row, so it reads as "from the start". The creator of a
  chat with no participant row also reads as "from the start". A group or
  `org` row counts only with its own join time. A passer-by of an
  org-visible room has no row, and gets no event.
- **The join row is the direct row.** It wins over an earlier group or `org`
  row of the same reader. So a member who was named after the run started
  gets no event, even when a group row was there before. That is stricter
  than "a matching row", and it fails closed. A legacy group or `org` row
  with a NULL join also fails closed.
- **The withheld end.** A refused reader gets `RUN_FINISHED` with
  `withheld: true`. The chat reads it as `attach_withheld`, and it does not
  attach to that run again while the server lists it (review P1).
- **One room read.** The route reads the room once, for the guard and the
  rule. A solo chat reads no stream head, so its bytes do not change.
- **A fault fails closed** for a shared room. A failed room read and a Redis
  fault give only the end frame.
- **Known residual.** `STREAM_MAXLEN` trims the oldest entries of a very long
  run. The first entry that stays is then later than the true start. A
  member who joined between the two reads the rest of the run.
  `test_a_trimmed_head_is_the_known_residual` holds the behaviour.

#### 4.12.9 Anchors, and where the evidence and the code differ

**Anchors**, verified at `3040ac9e4`. RESUME-2a refreshed the rows that it touched on 2026-11-16, and RESUME-2b on 2026-11-17. RESUME-5 refreshed the rows of the park, the 409 and the stop on 2026-11-19, on `cb0c515e3` with its own edits:

| What | Where |
|---|---|
| The park, and the run cancel | `apps/services/orchestrator/orchestrator/pending_ask.py` `park` (1086), and `executor.py` `wait_user_future` (3219, the park at 3298-3304) |
| The park at the stop (RESUME-5) | `pending_ask.py` `stop_wait` (1232), `end_stop_wait` (1256), `parking_threads` (1272), `park_for_stop` (1286), `park_at_stop` (1302). `executor.py` `wait_user_future`: the signal at 3272, and the park in the run's own task at 3283-3290. `_wait_slice` (3325) and `_park_at_stop` (3347) |
| The row columns, the claim, and the reply row of a stored call | `pending_ask.py` `_COLUMNS` (213), `claim_stored_call` (493), `_run_facts` (774), `_record` (806) |
| The late answer, the resume, and the 409 | `apps/services/gateway/gateway/routes/agent.py` `_late_answer_from_row` (3813), `_resume_from_row` (3980), `_continuation_request` (4202), `_same_reply_of` (4223), `_same_label_as_park` (4281), and the `run_restarted` 409 (4598) |
| The last turn of a chat (RESUME-2b) | `apps/services/gateway/gateway/routes/chat.py` `_SHOWN_ROW_SQL` (681), `_LAST_TURN_SQL` (701), `label_of` (739), `reply_turn` (761). The fold of part one: `apps/services/gateway/gateway/chat_fold.py` `_same_label_as_row` (505), `same_reply_events` (556). The mark of a same-reply start: `executor.py` `_same_reply_run` (7037) |
| The parked stream (RESUME-2b, D-RES-7) | `stream_relay.py` `PARKED_STREAM_TTL_SECONDS` (95), `ParkedStreamGone` (154), `mark_inactive` (537), `keep_parked_stream` (567), `parked_end_of` (598), `part_one_at_head` (624) |
| The resend, and the note of a resume | `apps/services/gateway/gateway/chat_recovery.py` `card_answer_after_restart` (731), `card_answer_from_ask` (762), `compose_card_answer` (150), `compose_resumed_call_note` (194) |
| The stop | `apps/services/gateway/gateway/routes/drain.py` `_chat_runs` (109), `drain` (137), `drain_park` (157). `deploy/hostinger/gateway-drain.sh` (60 s, and 8 s for the park call). `deploy/hostinger/acb-gateway.service` (`TimeoutStopSec=75`). The sweep that writes the mark: `run_liveness.py` `sweep_dead_instances` (446) |
| The control bus, and the starter | `apps/services/orchestrator/orchestrator/stream_relay.py` `_deliver_control` (1274), `cancel_run` (1837), `mark_active` (397), `start_detached` (1453) |
| The card gate | `packages/acb_skills/acb_skills/ask_tools.py` `request_confirmation` (2332), `act_keys` (1923), `card_call` (1936). The gate wrapper is `apps/services/orchestrator/orchestrator/_tool_injection.py` `_agated` (1192) |
| The stored call of WhatsApp | `apps/services/gateway/gateway/routes/whatsapp_channel/acts.py` `_run` (710), and `packages/acb_skills/acb_skills/whatsapp_acts.py` `confirming` (287), `card_text` (202) |
| The native session, and the prologue | `apps/services/orchestrator/orchestrator/native_session_store.py` (rule 6, `begin_turn` 516, `finish_turn` 660). `executor.py` `_resume_prologue` (6847), `_native_session_skip` (10567) and `no_current_turn` (10604) |
| The words | `workbench/control_plane/src/lib/runErrors.ts` (120-148), `src/lib/runStatus.ts` (575-591), `src/lib/confirmationQueue.ts` (387-393) |

**Where the brief and the code differ.**

- The brief places the notice words in `src/lib/cardAnswer.ts`. They are in
  `src/lib/runErrors.ts` (`run_restarted`, lines 135-140). `cardAnswer.ts`
  (`resendPlan`) only picks the notice.
- The 409 carries its own `message`, "The assistant restarted, so your answer
  goes as a new message." The chat never shows it.
- A park is more than "the run ended". The drain saves the reply so far, and
  the card stays open with no `confirmation_resolved`. So the card can come
  back, and it does.
- The evidence for the D-1 deferral in §5 no longer holds, because of D194
  (§4.12.5).

---

## 5. Deferred — owner decisions

| Id | Decision | Recommendation | Gate |
|---|---|---|---|
| D-1 | A separate run-worker service, so a deploy never stops a run | **DEFERRED** 2026-10-29: the evidence below shows no need | Built only when the owner asks |
| D-2 | Opt-in web push for "needs input" | **DECIDED** 2026-10-10: opt-in per member. Slice S7, not built | AGENT-SAFE |
| D-3 | Caps on concurrent runs | **DECIDED** 2026-10-10: 5 per member, no org cap. **BUILT** (§4.8) | AGENT-SAFE |

An agent must refuse to build D-1 by name until the owner asks for it.

**Why the agent deferred D-1 (2026-10-29).** The owner asked an agent to settle the
open items on 2026-10-29. The agent measured production, and it found no
need for a second service:

- **No restart cut a run.** In 8 days, production started the gateway 19
  times. Each stop runs `gateway-drain.sh` first, and each of the 19 logged
  `gateway-drain: 0 runs`.
- **The release lands when nobody works.** In the 21 days to 2026-10-29,
  members sent no chat message and started no agent run from 22:00 to
  02:00 UTC. The daily release restarted the gateway between 23:49 and
  01:33 UTC. `staging_and_daily_release.md` §4.1a keeps it in that window.
- **A flag flip no longer restarts.** A `*_ORGS` or `*_MODES` flip reloads
  in place since #1448 (`docs/flag_reload.md`).

**Reopen D-1 when** a production stop logs a `gateway-drain:` line other than
`0 runs` or `every run ended`. The other lines say that the stop went ahead
while runs were live, or while the count did not answer. Since RESUME-5, the
`parked N run(s)` line and the `no park answer` line are not such lines. Run
`sudo journalctl -u acb-gateway --since "7 days ago" -o cat | grep gateway-drain`
on the box to see each stop.

**2026-11-14, the evidence is out of date (§4.12.5).** D194 moved the release
to 05:30, 13:30 and 21:30 UTC on 2026-11-07. Two of those slots land in the
working day of our members in India. §4.12 keeps D-1 deferred and hands the
work over inside one process (D-RES-4). RESUME-5 parks each waiting run at
the stop, and RESUME-6 lets a cut run go on by itself.

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
| `apps/services/gateway/gateway/routes/chat.py` | `GET /chat/active-sessions` (S2 adds `state`), and `reply_turn` (RESUME-2b) |
| `apps/services/gateway/gateway/routes/agent.py` | `POST /agent/respond-input` (S2), the grant write of a late approve (S2 item 8), `_resume_from_row` (RESUME-2a), and `_same_reply_of` and `_same_label_as_park` (RESUME-2b) |
| `packages/acb_skills/acb_skills/late_approve.py` | A late approve acts once: the flag, the offer, the grant and its one use (S2 item 8) |
| `packages/acb_skills/acb_skills/stored_call.py` | A card stores its call: the flag, the shape and the call id (§4.12 RESUME-1a), and the stored-call yes (RESUME-2a) |
| `apps/services/orchestrator/orchestrator/pending_ask.py` | The row of a waiting card, and its stored call: `bind_call`, `read_stored_call` (S2, §4.12 RESUME-1a), `claim_stored_call` (RESUME-2a), the reply row of the call and the 24 h of a parked stream (RESUME-2b), the retention `prune` and `prune_hook` (task 3435), and the park at the stop: `stop_wait`, `park_for_stop`, `park_at_stop` and `parking_threads` (RESUME-5) |
| `apps/services/orchestrator/orchestrator/executor.py` | `_pending_user_input` (S2), `_resume_prologue` (RESUME-2a), and the stop signal in `wait_user_future` (RESUME-5) |
| `apps/services/gateway/gateway/routes/drain.py` | The stop count `GET /internal/drain`, and `POST /internal/drain/park` (RESUME-5) |
| `deploy/hostinger/gateway-drain.sh` | The stop step: one park call, then the count (RESUME-5) |
| `apps/services/orchestrator/orchestrator/stream_relay.py` | `run_detached`, `start_detached` (RESUME-2a, `keep_stream` RESUME-2b), `keep_parked_stream`, `parked_end_of` (RESUME-2b), `list_live_runs`, `drop_run_slot` |
| `apps/services/orchestrator/orchestrator/run_cap.py` | The run cap: the count, the lock and the reservation (D-3) |
| `workbench/control_plane/src/lib/chatTurnFailure.ts` | The capped turn leaves the thread with one notice (D-3) |
| `workbench/control_plane/src/lib/chatSteer.ts` | A send during a run steers it: the one control, the steer send, the follow-up rule (§4.9) |
| `apps/services/orchestrator/orchestrator/steer.py` | `return_unconsumed`, `has_pending_steers`, `without_replayed` (§4.9) |
| `workbench/control_plane/src/lib/runStatus.ts` | The one run state: the reducer, the step, the view and the no-flash rule (§4.10) |
| `workbench/control_plane/src/components/RunStatusRow.tsx` | The status row and `useSteadyRunView` (§4.10) |
| `workbench/control_plane/src/lib/chatResume.ts` | The line of an Approve from another device, and the stale card set (§4.12 RESUME-3a). The frame `run_resumed`, the end of a replay (`replayEndAfter`) and the line of case A (RESUME-3b). The target of an attach (`attachPickOf`, RESUME-3d). Fence: `chatResume.test.ts` |
| `workbench/control_plane/src/lib/respondInput.ts` | The answer POST: `resumedOf` and the cap reason (§4.12 RESUME-3a), and `sameReply` (RESUME-3b) |
| `packages/acb_skills/acb_skills/ask_tools.py` | `ApprovedActs`, `act_ran`, `note_approved_act` and `park_line` (§4.12.7 PARK-LINE). The tools of `ACT_RAN_SITES` in `test_park_made_line.py` call `act_ran` |

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

For §4.9, from `workbench/control_plane`, then from the repo root:

```bash
npx vitest run src/lib/chatSteer.test.ts src/components/chatEditComposer.test.ts
npx playwright test e2e/chat-steer.spec.ts --project=ci
uv run pytest tests/unit/test_steer_routing.py tests/unit/test_steer_follow_up.py
uv run pytest tests/unit/test_steer_user_turn.py tests/unit/test_steer_unsent_card.py
```

For §4.10, from `workbench/control_plane`, then from the repo root:

```bash
npx vitest run src/lib/runStatus.test.ts src/lib/liveRuns.test.ts src/lib/chatStore.test.ts
npx playwright test e2e/chat-run-status.spec.ts --project=ci
uv run pytest tests/unit/test_pending_ask_flow.py
```

For §4.12 RESUME-3a, from `workbench/control_plane`. The e2e needs a free
port, because another server often holds 3101.

```bash
npx vitest run src/lib/chatResume.test.ts src/lib/runStatus.test.ts src/lib/respondInput.test.ts \
  src/lib/confirmationQueue.test.ts src/lib/confirmationReceipts.test.ts src/lib/chatRecovery.test.ts \
  src/lib/dismissibleNotice.test.ts src/lib/pendingAsks.test.ts src/lib/shell/assistant.test.ts
npx tsc --noEmit
npx playwright test e2e/chat-card-restart.spec.ts e2e/chat-run-status.spec.ts e2e/chat-steer.spec.ts --project=ci
```

For S2 item 8 (HRD-22), from the repo root. The R8 file needs a private
database and a private Redis. It runs FLUSHDB, so never point it at a shared
Redis. A skip is not a pass.

```bash
uv run pytest tests/unit/test_late_approve_grant.py tests/unit/test_pending_ask_flow.py \
  tests/unit/test_steer_unsent_card.py tests/unit/test_crm_questions_chat.py
docker run -d --rm --name late-approve-r8-redis -p 127.0.0.1:16398:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16398/0 \
  uv run pytest tests/unit/test_late_approve_grant_r8.py tests/unit/test_pending_ask_store.py -rs
```

For §4.12 RESUME-1a, from the repo root. Start the scratch database first
(`engineering_practice.md` §1.1). The R8 files need a real Postgres. A skip
is not a pass.

```bash
uv run pytest tests/unit/test_resume_stored_call.py tests/unit/test_pending_ask_flow.py \
  tests/unit/test_held_card_writes.py tests/unit/test_late_approve_grant.py \
  tests/unit/test_tenant_coverage.py tests/unit/test_flag_doc_sorted.py
uv run pytest tests/unit/test_resume_stored_call_r8.py -rs
uv run pytest tests/unit/test_pending_ask_store.py -rs
```

For §4.12 RESUME-2a, from the repo root. Each R8 file needs a private
database. The two files with a Redis need a private Redis, because they run
FLUSHDB. Run one R8 file at a time. A skip is not a pass.

```bash
uv run pytest tests/unit/test_resume_continuation.py tests/unit/test_resume_stored_call.py   tests/unit/test_pending_ask_flow.py tests/unit/test_late_approve_grant.py   tests/unit/test_held_card_writes.py tests/unit/test_wac_writes.py
uv run pytest tests/unit/test_run_started_producers.py tests/unit/test_maf_mcp_confirm.py   tests/unit/test_concurrent_run_guard.py tests/unit/test_chat_deploy_recovery.py   tests/unit/test_chat_fold_other_run.py tests/unit/test_steer_unsent_card.py
uv run pytest tests/unit/test_tenant_redis.py tests/unit/test_card_outcome_vocabulary.py   tests/unit/test_chat_supersede.py
uv run pytest tests/unit/test_native_session_persistence.py tests/unit/test_crm_questions_chat.py   tests/unit/test_fence_lists.py tests/unit/test_flag_doc_sorted.py tests/unit/test_tenant_coverage.py
docker run -d --rm --name resume2-r8-redis -p 127.0.0.1:16401:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16401/0   uv run pytest tests/unit/test_resume_continuation_r8.py -rs
HELD_R8_REDIS_URL=redis://127.0.0.1:16401/0   uv run pytest tests/unit/test_late_approve_grant_r8.py -rs
uv run pytest tests/unit/test_resume_stored_call_r8.py -rs
uv run pytest tests/unit/test_pending_ask_store.py -rs
uv run pytest tests/unit/test_wac_writes_r8.py -rs
```

For §4.12 RESUME-4a-1, from the repo root. The R8 file needs a private
database. Run one R8 file at a time. A skip is not a pass.

```bash
uv run pytest tests/unit/test_resume_continuation.py tests/unit/test_native_session_persistence.py \
  tests/unit/test_resume_stored_call.py tests/unit/test_pending_ask_flow.py
uv run pytest tests/unit/test_late_approve_grant.py tests/unit/test_fence_lists.py \
  tests/unit/test_flag_doc_sorted.py tests/unit/test_index_union.py tests/unit/test_tenant_coverage.py
uv run pytest evals/trajectories/test_native_structured_history_trajectory.py \
  evals/trajectories/test_hitl_trajectory.py -q
uv run pytest tests/unit/test_resume_native_stale_r8.py -rs
uv run pytest tests/unit/test_native_session_persistence.py -rs -k r8
docker run -d --rm --name resume4-r8-redis -p 127.0.0.1:16406:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16406/0 uv run pytest tests/unit/test_resume_continuation_r8.py -rs
```

For §4.12.7 PARK-LINE, from the repo root. The R8 file needs a private
database. A skip is not a pass.

```bash
uv run pytest tests/unit/test_park_made_line.py tests/unit/test_steer_unsent_card.py \
  tests/unit/test_pending_ask_flow.py tests/unit/test_card_outcome_vocabulary.py
uv run pytest tests/unit/test_late_approve_grant.py tests/unit/test_resume_continuation.py \
  tests/unit/test_fence_lists.py tests/unit/test_flag_doc_sorted.py tests/unit/test_index_union.py
uv run pytest evals/trajectories/test_chat_fold_trajectory.py evals/trajectories/test_hitl_trajectory.py -q
uv run pytest tests/unit/test_pending_ask_store.py -rs
```

For §4.12 RESUME-2b, from the repo root. The R8 file needs a private
database and a private Redis, because it runs FLUSHDB. The tests of case A
point the stream relay at that Redis too. A skip is not a pass.

```bash
uv run pytest tests/unit/test_resume_continuation.py tests/unit/test_resume_stored_call.py \
  tests/unit/test_pending_ask_flow.py tests/unit/test_late_approve_grant.py
uv run pytest tests/unit/test_concurrent_run_guard.py tests/unit/test_chat_deploy_recovery.py \
  tests/unit/test_chat_fold_other_run.py tests/unit/test_flag_doc_sorted.py
docker run -d --rm --name resume2b-r8-redis -p 127.0.0.1:16412:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16412/0 uv run pytest tests/unit/test_resume_continuation_r8.py -rs
```

For §4.12 RESUME-4a-2, from the repo root. Each R8 file needs a private
database. The two files with a Redis need a private Redis, because they run
FLUSHDB. Run one R8 file at a time. A skip is not a pass.

```bash
uv run pytest tests/unit/test_resume_continuation.py tests/unit/test_resume_stored_call.py \
  tests/unit/test_pending_ask_flow.py tests/unit/test_late_approve_grant.py
uv run pytest tests/unit/test_card_outcome_vocabulary.py tests/unit/test_held_card_writes.py \
  tests/unit/test_pending_ask_prune.py tests/unit/test_fence_lists.py \
  tests/unit/test_flag_doc_sorted.py tests/unit/test_index_union.py
uv run pytest tests/unit/test_resume_native_stale_r8.py -rs
docker run -d --rm --name resume4a2-r8-redis -p 127.0.0.1:16411:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16411/0 uv run pytest tests/unit/test_resume_continuation_r8.py -rs
HELD_R8_REDIS_URL=redis://127.0.0.1:16411/0 uv run pytest tests/unit/test_late_approve_grant_r8.py -rs
uv run pytest tests/unit/test_resume_stored_call_r8.py -rs
uv run pytest tests/unit/test_pending_ask_store.py -rs
```

For §4.12 RESUME-5, from the repo root. The script tests need a bash with
curl. The R8 file needs a private database and a private Redis, because it
runs FLUSHDB. Run one R8 file at a time. A skip is not a pass. Never run the
drain against production.

```bash
uv run pytest tests/unit/test_gateway_drain.py tests/unit/test_pending_ask_flow.py \
  tests/unit/test_resume_continuation.py tests/unit/test_chat_deploy_recovery.py \
  tests/unit/test_late_approve_grant.py
docker run -d --rm --name resume5-r8-redis -p 127.0.0.1:16415:6379 redis:7-alpine
HELD_R8_REDIS_URL=redis://127.0.0.1:16415/0 uv run pytest tests/unit/test_resume_continuation_r8.py \
  -rs -k "parked_at_the_stop or case_a_goes_on or real_park_keeps"
```

For §4.12 RESUME-3b, run these on the home box under `timeout`, never on
the owner's PC. The first four commands run from `workbench/control_plane`.
The last one runs from the repo root. The e2e needs a free port, and it
holds `flock ~/lanes/e2e.lock`. The last command needs no database.

```bash
npx vitest run src/lib/chatResume.test.ts src/lib/runStatus.test.ts src/lib/runBinding.test.ts \
  src/lib/respondInput.test.ts src/lib/liveRuns.test.ts src/lib/confirmationQueue.test.ts \
  src/lib/confirmationReceipts.test.ts src/lib/pendingAsks.test.ts
npx vitest run src/lib/sharedTaskUi.test.ts src/lib/theme/conformance.test.ts src/lib/boards.test.ts \
  src/lib/nav.test.ts
npx tsc --noEmit
npx playwright test e2e/chat-card-restart.spec.ts e2e/chat-run-status.spec.ts --project=ci
uv run pytest tests/unit/test_pending_ask_flow.py tests/unit/test_active_sessions_tenant.py \
  tests/unit/test_flag_doc_sorted.py
```

## 8. Waiting cards reach My Day (D171, 2026-11-03)

**Status:** 📋 SPEC. The parts exist, and they are dark. Metorite: task 1523.

**The ask.** A chat card that the member did not answer must not be lost. Today a card waits in memory for one hour (`ASK_USER_TIMEOUT`), and then it closes as TIMEOUT. Outside its chat, nothing shows it.

**What exists, dark.**
- `chat_pending_ask` (migration 237, `orchestrator/pending_ask.py`) keeps a waiting card. The flag is `CHAT_DURABLE_ASKS`, off. With it on, the run parks after 600 s, and the row stays answerable for 168 h.
- The My Day feed has an `agent_asks` card (`feed.py:957-1015`). Two gates hide it: `CHAT_DURABLE_ASKS`, and `MY_DAY_PROVIDERS`. In production that list holds `reminders,agent_questions` (read 2026-11-16, deployed `1f482db9e`). The card shows only when `chat` is on the list. Its title says only "{agent} asked you a question".
- The "AI waiting on you" group of My Day (`lib/shell/AiWaitingCard.tsx`) reads chats in the state `needs_input`. That state exists only with the flag on.
- A late answer goes to `POST /agent/respond-input`, and it starts a new run. For an approval, that run asks again on a new card. With `CHAT_LATE_APPROVE_ORGS` on (S2 item 8, HRD-22), the late tap is the member's own consent instead. It approves the same card once, in the same thread, within 30 minutes.

**The slices.**

| Slice | Task | What | Done when | Tier |
|---|---|---|---|---|
| WAIT-1 | 1550 | Turn on durable asks: `CHAT_DURABLE_ASKS` and the `agent_asks` provider. First on this PC, then in production for one org, then for all | An unanswered approval in a chat that the member closed shows in My Day after the park. A tap opens that chat at the card. The answer reaches the run, or a new run asks again on a new card | MEDIUM. The production flag flip is named in the PR, with the box and the evidence |
| WAIT-2 | 1551 | The My Day card names the ask: the approval title or the question, the chat, and the age | The card shows the text that the chat card showed, fenced as data. It never shows a payload that the member cannot read | LOW |
| WAIT-3 | — | The proactive agent (WS-AUTO) and decisions (ALN-2a) use the same group | A proposal of the proactive agent shows in "Needs you" beside the chat cards. WS-AUTO and WS-ALN build it on their own boards | — |

**Fences.** WAIT-1: `tests/unit/test_pending_ask_flow.py` holds the park and the late answer. A new test shows that the feed lists a parked card for its member only. WAIT-2: a test that the card text is fenced and names the chat. Advisory until each slice builds.

