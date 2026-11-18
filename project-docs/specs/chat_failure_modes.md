# Chat failure modes: what goes wrong in the chat, and what we build against it

**Status: ACCEPTED, 2026-11-12 (D217). BUILT: HRD-6 part 1 (the line of a slow
tool), dark, 2026-11-13 (PR 2238). HRD-1 (an unknown tool name), 2026-11-14:
the answer is live, and the auto-load is dark behind `CHAT_TOOL_AUTOLOAD`.
HRD-1 follow-up (task 3599), 2026-11-17, with no flag: the trail shows a plain
line in place of MAF's text, and a run tries one load for each name.
HRD-4 (search in parallel, `find_tasks` on most of the words, task 2983), 2026-11-16.
HRD-18a (names as people say them), 2026-11-14. HRD-22 (a late approve acts
once, task 3231) is BUILT dark, 2026-11-14. Its flag is
`CHAT_LATE_APPROVE_ORGS`. CS-1 (the first line, client only) and CS-2 (the
prompt cache marks of the Router, dark) are BUILT, 2026-11-15. CS-3a (the
server half of the run phase, task 3438) is BUILT dark, 2026-11-16. Its flag
is `CHAT_RUN_PHASE_ORGS`. HRD-17a spec row rewritten 2026-11-17. HRD-17a built
dark, 2026-11-17, behind `CHAT_SEND_ONCE_ORGS`.**

**HRD-7 (one vocabulary of card outcomes, task 2986) is BUILT, 2026-11-15,
with no flag. Projects, CRM and email take each card outcome text from
`ask_tools.CARD_OUTCOMES`.**

**HRD-8a (a closed email card keeps its act, task 2987) is BUILT, 2026-11-16,
with no flag. HRD-8b (task 3459) is open.**

**HRD-9a (task 2988) is BUILT, 2026-11-16, with no flag. A card of a
root-local tag, custom field or task type names the space and N, the count
of projects that the change reaches. HRD-9b and HRD-9c are not built.**

**HRD-11a (task 2990) is BUILT dark, 2026-11-16, behind the new flag
`CHAT_FOCUS_ORGS`. The chat keeps the email or the Projects task that a tool
call read or acted on last, for each chat and member. The per-turn context
shows its kind, its id and its link after the OA-4 block, with the rule for
"it", "this" and "mark it done". HRD-11b ("the next one") and the flag flip
are not built.**

**HRD-11a fix round 1 (2026-11-17) makes the item safe to lose.** A run whose
calls name two or more different items keeps no item. A tool that makes,
archives or moves a task or a project clears the item. An item older than 2
hours shows no block. The rule yields to the member's words and to a newer
item. §8 Q7 (a bare "this" and the page item) is OPEN and not built.

**HRD-12a (task 2991) is BUILT dark, 2026-11-16, under the existing flag
`CHAT_CONSTRAINTS`. The OA-4 store keeps a `decision` kind beside the limits,
from the member's typed words only. The per-turn block and each delegated
task show the decisions on their own line. HRD-12b (the Undo chip) and
HRD-12c (the flag flip) are not built.**

Owner ask of 2026-11-12, in chat. Epic task 2978 (project AI Chat Interface).
It is part of WS-CHAT.

HRD-0 is task 2979. The owner accepted D-HRD-1 to D-HRD-6 of §8 as D217.
D-HRD-7 of §8 is PROPOSED, and the owner has not decided it.

It owns the catalogue of chat failure classes, the contract of §7 for every
chat tool, and the slices HRD-1 to HRD-19 and HRD-22. Each fix lands in the seam that
owns it, as §9 lists. This spec adds no second seam.

## 1. The owner's ask

On 2026-11-11 the owner reviewed a morning chat. Three edits typed over open
cards were lost (task 2494). The keyboard opened again on Send (task 2867). A
tool needed an id that no read tool shows (task 2873). The owner then asked:
which other failures of these kinds will a member meet, that we have not met
yet? Build against them before a member meets them, and put them in the plan
first.

## 2. The answer in one screen

- **Most of the risk is in the tool contract, not in the model.** A tool asks
  for an id that the model cannot see, or refuses a near form of an id. A tool
  refuses an argument shape, or fails with no text that the model can act on.
  Production shows each of these.
- **Cards must tell the truth.** One outcome text covers a reject, a timeout
  and a cancel in Projects. A redraft can change the act. A shared change can
  reach many projects. A card does not check that its object is unchanged.
- **The chat loses track.** "Mark it done" has no item in focus outside
  email. A decision such as "TEDx is done" does not hold. A promise of a later
  act has no mechanism.
- **Trust is not calibrated.** Text that the member pasted is the member's
  order. The member's own correction was refused as an injection. No eval
  checks either way.
- **Some guards exist and are dark, or cover one app.** PST-4 guards shared
  statuses. Tags, fields and types have a part guard: a refusal of an org-wide
  delete or merge, and the root name on a create. No card gives a project count. The answer check covers CRM numbers
  only. OA-4 keeps corrections, but not decisions, and it is off.
- **The plan is a ladder (D211).** Rung 0 fixes what fails in production now.
  Each later rung ends in a demo that a member sees. Nineteen slices, five
  demos.

## 3. The evidence (production, 2026-10-29 to 2026-11-12)

Measured on 2026-11-12 from `chat_message` (108 sessions, 665 messages, 8
orgs, 24 members). Text was read only from the owner's own sessions. The other
orgs gave counts only.

| Signal | Count | Class |
|---|---|---|
| `Function "create_task" not found.` (also `find_tasks`, `create_tasks`, `create_status`, `update_status`, `search_email`, `load_skill_tools`) | 12 | B1 |
| `No task matches «…»` for a phrase of several words | about 16 | B2 |
| `ask_questions`: "questions must be a JSON object" | 6 | B3 |
| "task_id must be a UUID … Got '244'" | 2 | B3 |
| `crm_aggregate`: 503 "took longer than 10 seconds", and "Error: Function failed." | 9 and 2 | B6, B4 |
| One turn called one tool 3 times or more (`search_company` 15, `find_tasks` 9, `read_email` 6) | 60 turns | B5 |
| Card answers: APPROVE 215, REJECT 10, REVISED 8. Steers: 15 | | C1 |
| Member messages that repair the chat ("I meant", "what happened", "never mind") | 17 of 317 | all |
| A stage change for one sub-project changed every project (2026-11-05) | 1 | C5 |
| "Let's retry from a fresh chat in case something is going wrong" (2026-11-05) | 1 | E3 |
| The same message sent twice within 10 s | 0 | G1 |
| An answer that claims an act, in a turn whose tools did not do it | 0 of 15 | D1 |

The flags on production on 2026-11-12: `STATUS_SET_GUARD_ORGS=*`,
`EMAIL_HELD_CARD_SEND=true`, `CHAT_DURABLE_ASKS=true`,
`CRM_ANSWER_CHECK=shadow`, `MAF_NATIVE_SESSIONS=true`. `CHAT_CONSTRAINTS` and
`CHAT_CONTINUE_ORGS` are not set, so they are off.

## 4. The failure classes

Each row gives the state on `main` on 2026-11-12, the rule that we build, and
the slice. "Dark" means that the code exists behind a flag that is off.

### 4.1 Whose words are these (A)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| A1 | A pill, a card chip, a starter or a retry that the member pressed | D-PZ-37 marks typed words for the profile only. Nothing says whether a pill is an order to act | A press of the member is the member's order for that act. Its words still carry no typed mark | HRD-15 |
| A2 | The member pastes a mail or a document | A paste is a typed row with no frame. WT-1b is specified only | A paste is framed as data. An instruction inside it acts only when the member's own words ask for it | HRD-14 |
| A3 | Over-distrust: the model refuses the member's own words as an injection | Task 2494 fixed the steer path. No eval checks that the model obeys the member | An eval set of the member's own risky-looking orders, which the model must act on | HRD-15 |

### 4.2 Tools the model cannot use (B)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| B1 | A call to a tool that the turn has not loaded | MAF's own "Function … not found". `PACKS_LEFT` hints only after a pack left a sticky chat | An unknown name gets the closest real name, or the pack that holds it. The turn loads an allowed pack | HRD-1 |
| B2 | A search that finds nothing | `find_tasks` needs every word. `search_company` asks the apps in turn in 4 s, and skips email | Rank by the words matched when all words miss. Ask the apps in parallel | HRD-4 |
| B3 | An id or an argument that the model cannot give | No read tool prints Projects comment, activity or attachment ids, or the CRM activity id. `uuid_of` refuses a task number and a `kind:` prefix. No layer coerces a JSON string | Each id that a tool needs is printed or resolved. One layer gives each argument the shape that its schema declares | HRD-2, HRD-3 |
| B4 | A failure with no text that the model can act on | `refusals_as_text` covers Projects, CRM and email only. Other exceptions give "Error: Function failed." | Each pack tool answers a failure as text with a next step | HRD-3 |
| B5 | A loop with new arguments each time | `LoopDetector` stops 5 identical calls only | After 3 empty or refused answers from one tool in a turn, the chat says what it tried and asks | HRD-5 |
| B6 | A slow backend | `crm_aggregate` returns a 503 with "Narrow the filter". A slow tool runs with no word to the member | Owner, 2026-11-13: a large read can take minutes. The member hears so at once, and waits. A tool call that runs past 6 s sends ONE line. A read sends: "This is a large read. It can take a few minutes to get all the data. Please wait." Any other tool sends: "This is taking longer than usual. Please wait." A card is the sign of a wait for the member, so a call that shows one sends no line. The CRM total gets a long mode, and the read finishes | HRD-6 |

### 4.3 Cards that do not tell the truth (C)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| C1 | The model misreports a card's outcome | Projects gives one text for a reject, a timeout and a cancel. Email has its own texts. Task 3130: when a chat message closes a write card, the server ends the answer with a "Not done" line (`chat_run_continuity.md` §4.9) | One outcome vocabulary: approved, declined, timed out, closed by your message, no card, another member answered, parked, changed since. "No card" and "another member answered" were on `main` already (a WhatsApp, batch or no-person run, and PZ-32). HRD-7 starts from `ask_tools.CLOSED_BY_YOUR_MESSAGE` | HRD-7 |
| C2 | A redraft changes the act or the recipients | Email §18.11 links a redraft by To overlap only | A card carries its act. A changed act or changed recipients show on the new card | HRD-8a, HRD-8b |
| C3 | The object changed after the card was drawn | Only the email reply card and the epic contract check it | A card holds its object's version, and the write checks it | HRD-10 |
| C4 | Two cards wait, and the member types | One card waits at a time (PAC §29.6). A steer closes the open card | No change. §29.6 stays the rule. HRD-16 covers cards parked after a restart | HRD-16 |
| C5 | A write reaches more than the member thinks | PST-4 guards shared statuses (on in production). Tags, custom fields and types have a part guard today: the org-wide delete and merge refusal (`guarded.py` ~:176), the root name on a create (`writes.py` ~:4715), and the task count on a tag rename, delete and merge. The gap: no card gives a PROJECT count. The update and delete cards name the subproject, not the space. Note: `GET /tags/{id}/impact` and `GET /vocabulary/{kind}/{id}/impact` return a key `projects`. It counts distinct trees, NOT projects. Do not reuse it as N | A shared vocabulary change says how many projects it changes | HRD-9a, HRD-9b, HRD-9c |
| C6 | A late approve opens nothing | The member approves a parked card after the chat stopped waiting. The answer starts a new run, which plans again and draws a new card. On 2026-11-14 one deal move needed three approvals | A late approve is the member's own consent. It approves the same card once, in the same thread, within 30 minutes. The fix lands in `chat_run_continuity.md` S2, item 8 | HRD-22 |

### 4.4 Claims and promises (D)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| D1 | The answer claims an act that did not happen, or doubts one that did | The answer check covers CRM numbers, in shadow | An act verb needs a matching tool outcome in the turn. A doubt needs a failed outcome | HRD-13 |
| D2 | A promise of a later act ("I'll check the thread") | No rule | A promise needs a reminder or a task made in the turn. Otherwise do it now, or ask | HRD-13 |

### 4.5 Losing track in the chat (E)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| E1 | "This", "it", "mark it done", "the next one" | Since D99 (commit 3c9442b2a, 2026-10-13) the dock sends the open item of the page as `system_context` on each turn (`src/lib/shell/AssistantDock.tsx` ~:925-933, `useAgentChat.ts` ~:621, `route.ts` ~:891). It carries the id and a "means it" line for the Projects, Tasks, Calendar, Email and Knowledge pages (`src/app/<app>/lib/assistantFacts.ts`). CRM sends none. The gap: outside email, nothing holds the item that the chat last read or acted on, and no rule maps "it" or "mark it done" to an item. A list read (`find_priority`, `query_inbox`) sets no `email_id`, so after a list "it" has no id | The turn carries the item in focus, from the last read or act and the open screen | HRD-11a, HRD-11b |
| E2 | A decision made earlier in the chat does not hold | OA-4 keeps corrections and limits, and it is off | OA-4 keeps decisions too, from the member's typed words, with Undo | HRD-12a, HRD-12b, HRD-12c |
| E3 | A chat that went wrong | WT-1 and WT-2 are dark and carry the newest typed message only | "Start clean" carries the decisions, the item in focus and the open cards | HRD-19 |

### 4.6 Names, dates and language (F)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| F1 | A name spelled as people say it ("Swagata" for Sougata) | HRD-18a: `acb_common.name_match` is the one matcher. `resolve_person`, `find_contact` and `search_crm` use it, mark a near name and ask to confirm it. The company search and the mail sender match wait for HRD-18b | One matcher folds spelling and transliteration variants, and asks to confirm | HRD-18 |
| F2 | Relative and past dates | Live: the Now line, the member's zone, past dates refused | No change. Each new date argument uses `acb_common.today` | §7 |
| F3 | Mixed language and voice transcripts | VOX puts a transcript in the box, and the member sends it | No change. The member checks it before Send | none |

### 4.7 Delivery and devices (G)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| G1 | A double tap or a retried answer | The prompt save is idempotent. No write key on a card answer. A steer can repeat | Each card answer has a one-use key. A steer seen twice counts once | HRD-16 |
| G2 | The phone sleeps during a stream | Live: detached runs, replay by `lastEventId`, durable asks on | No change | none |
| G3 and G4 | Two devices, a send while offline, a send while the history loads | Designed in §4.7a (2026-11-16). Not built | An outbox sends once. A send waits for the load. The server's copy wins the fold | HRD-17a to HRD-17f |

### 4.7a The design of G3 and G4 (HRD-17, PROPOSED 2026-11-16)

**What the code does today** (read on `origin/main`, 2026-11-16):

- The browser makes the id of the member's message. It sends it as `userMessageId`, and the gateway reads it as `user_message_id` (`routes/agent.py`, near line 2984).
- The server saves the member's turn under that id at run start. The browser's later save updates the same row. So the saved row is already written once.
- The run is not deduped. The stream is per thread (`cc:stream:{thread_id}`), not per run. A second send with the same id during a live run takes the steer route. It adds the same words twice. A second send after the run is final starts a second run. That run repeats the answer, the model cost and the tool acts. Another member gets a 409 during a live run.
- A browser network error settles the send as sent (`isDisconnect` in `useAgentChat.ts`). The send may never have reached the server, and nothing sends it again.
- A send while the history loads is dropped. The send wrapper returns false (`AgentChat.tsx`, `loadingHistory`).
- The history load keeps the server list when it has more messages. The slow poll lets content only grow. Nothing says that the server's copy of a message wins.
- `projects_ai_chat.md` §19.6 says that a second tab can write older content before the fold.

**The rules.**

1. **One key for each send.** The key is the existing `user_message_id`. No second id exists. The server keys a send on organization, member, thread and `user_message_id`. The member in the key is PROPOSED. A repeat of a live run replays that run and starts no run. A repeat of a final run answers from the saved row. A send with no id works as it does today. The key comes from the session and the run binding, never from the body alone. Slice HRD-17a.
2. **The outbox.** A send that the server did not acknowledge waits in `src/lib/chatOutbox.ts`. The server acknowledges a send with its first stream event, or when a poll finds the row by id. The outbox releases entries in order, once, on the `online` event and after the history fold. Slices HRD-17b and HRD-17d.
3. **Where it lives.** The outbox lives in memory, and `sessionStorage` keeps a copy for a reload of the same tab. It never uses `localStorage`. It clears on sign-out and on a member switch. A fence reads the source and refuses a `localStorage` write of message text. This is the proposed default of D-HRD-17-1.
4. **The waiting state.** A waiting entry shows in the existing run-status line (`runStatus.ts`) with a Cancel. It adds no banner. A send while a run is live is a steer, and it stays on the steer path (`chatSteer.ts`). It never enters the outbox.
5. **A send while the history loads** goes to the outbox. It goes out after the load settles. The composer stays open for typing. Slice HRD-17c.
6. **The fold rule.** The client merges by message id. For an id on the server, the server copy wins. A local row whose id is not on the server stays only as an outbox entry. Order stays as the server sets it. Slice HRD-17e.
7. **A second device** sees a sent message through the slow poll. It needs no new channel. A thread revision is not needed while the merge is by id.
8. **No shrink before the seal.** HRD-17e first reads `_upsert_messages`. It checks whether a client write can shorten a stored row before the seal (`run_final_at`). If it can, the slice refuses the shorter write. That change is HIGH.

**What this does not change.** The card-answer key stays in HRD-16. The steer path stays in `chat_run_continuity.md` §4.9. Replay by `lastEventId` stays as it is (G2).

**Order and builders.** HRD-17a is a server slice. It is HIGH, because it changes the write path of the run start. It ships dark behind `CHAT_SEND_ONCE_ORGS` (PROPOSED). The client slices HRD-17b to HRD-17f are STANDARD, and lane-ui builds them. They wait for RESUME-3a (PR 2443), which edits the same hook and stream. HRD-17d waits for HRD-17a, so that a resend is safe.

A client slice that changes the look needs the owner's Visual review: light mode, compact density, a changed accent and 390 px.

### 4.8 Outbound safety and limits (H, I)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| H1 | A customer copied by mistake, or the recipients change between drafts | Live: the held card classes each recipient and leaves a new address unticked | HRD-8b shows a change of recipients on a redraft | HRD-8b |
| H2 | A bulk act that fails part way | Projects stops and says what exists. Email and CRM are unchecked | §7 rule 6 binds each bulk tool | §7 |
| I1 | Credits run out, or the provider fails, during a run | Live: `runErrors.ts` names the credit and rate cases | No change | none |

## 5. The ladder (D211)

| Rung | Demo task | The demo | Slices |
|---|---|---|---|
| 0 | none (it outranks the ladder) | The failures that production shows today stop. A member approves a parked card once on production, and the act runs (HRD-22) | HRD-1, HRD-2, HRD-3, HRD-22 |
| 1 | 2999 | Ask for something hard to find: the chat finds it, or says what it tried | HRD-4, HRD-5, HRD-6 (and rung 0) |
| 2 | 3000 | Every card tells the truth, and a shared change says how far it reaches | HRD-7 to HRD-10 |
| 3 | 3001 | The chat keeps track: "mark it done", decisions, no empty promise | HRD-11a to HRD-13 |
| 4 | 3002 | A pasted email cannot steer the chat, and your own words always do | HRD-14, HRD-15 |
| 5 | 3003 | A double tap, a dead connection, a second phone or a misspelt name does no harm | HRD-16 to HRD-19 |

## 6. The slices

| Slice | Task | What | Fence (R7) | Tier |
|---|---|---|---|---|
| **HRD-0** | 2979 | This spec, the INDEX row and the WS-CHAT row | STE lint | LOW |
| **HRD-1** | 2980 | An unknown tool name gets a way through. **BUILT 2026-11-14:** the answer is live. D217 allows the auto-load, and it ships dark behind `CHAT_TOOL_AUTOLOAD` (default off). **Task 3599, BUILT 2026-11-17, with no flag:** a member saw MAF's text for `query_inbox` in the chat. The stream event of a step sent the `exception` of MAF's result, `'Function "query_inbox" not found.'`, before the HRD-1 rewrite. The saved step kept it. `event_translator.tool_result_fields` is now the one mapping of a result for the stream and for a batch sub-run. MAF's result shows `unknown_tool.MEMBER_LINE`, and the step stays failed. A run asks the loader once for each name. A turn with no unknown call is byte-identical. A new step saves no MAF text, so a count of B1 reads the saved steps for `MEMBER_LINE`, which also holds the sub-run calls. The log line `unknown_tool.answered` of the orchestrator misses them. **Not built:** the HRD-1 answer for the model of a sub-run (the worker, a delegated agent), and a client map for steps saved before the fix | `tests/unit/test_unknown_tool_answer.py`, `tests/unit/test_unloaded_pack_tool.py` | STANDARD |
| **HRD-2** | 2981 | Print the hidden ids. Accept a task number and a `kind:` prefix | `tests/unit/test_tool_id_provenance.py` | STANDARD |
| **HRD-3** | 2982 | One wrapper on each pack tool: text for each failure, and each argument in its declared shape | `tests/unit/test_every_pack_tool_answers_text.py`, `tests/unit/test_tool_argument_shapes.py` | STANDARD |
| **HRD-4** | 2983 | Search that finds | `tests/unit/test_search_fallback.py` | STANDARD |
| **HRD-5** | 2984 | A per-turn budget for each tool | `tests/unit/test_tool_turn_budget.py` | STANDARD |
| **HRD-6** | 2985 | A slow read tells the member to wait (owner, 2026-11-13). Part 1, BUILT dark behind `CHAT_PROGRESS_NOTICE_ORGS`: one line for each tool call past 6 s, on the web chat and as WhatsApp's early message (`chat_run_continuity.md` §4.11). Part 2: the long mode of the CRM total | `tests/unit/test_progress_notice.py` (part 1), `tests/unit/test_crm_aggregate_timeout.py` (part 2) | STANDARD |
| **CS-1** | 2898 | **The first words in 2 seconds** (Rung 1 of the chat ladder, D211). BUILT 2026-11-15, client only. Task 2875 measured production on 2026-11-11: the setup is 0.5 to 1.7 s and the first model request is 5 to 10 s before its first tool, so the chat cannot wait for the model. The line shows from the moment of Send: the run row and the header of the thinking trail both say `FIRST_STEP` ("Reading your message", `lib/runStatus.ts`), one fixed phrase and never model text, where they said "Starting" and "Thinking…". The first real step takes it over. Text before and between tools already streams per chunk from the executor to the screen, and stays in the answer body (the fence pins it). The tool names in plain words are `lib/toolSteps.ts` (`describeToolStep`), the one vocabulary. **Not built, and why:** a line of real server phases ("Setting up", "Asking the model") would touch the output accounting of the executor (`_n_emitted`, `_tier_run.seal()`) and the live and replay fold together, so it is its own HIGH slice; a persona rule for a lead-in sentence needs an eval run. `RUN_STARTED` is still dropped by the BFF route. | `src/lib/firstLine.test.ts` | STANDARD |
| **HRD-7** | 2986 | One vocabulary of card outcomes. **BUILT 2026-11-15**, with no flag. `ask_tools.CARD_OUTCOMES` holds eight outcomes. Each has one lead, what happened and a next step. `request_confirmation` keeps its answer, and `last_card_outcome` tells why a card did nothing. Projects (`writes._not_confirmed`), CRM (`_not_written`) and email (`_not_done`, `_closed_card_line`, the heads of the redraft texts) take their words from the table. The chat cards draw by the lead (`src/lib/cardOutcomes.ts`), and each keeps its chip of before. A receipt shows a line in the second person and the act (`memberLineOf`), so the member never reads the text for the model. A parked card never draws as "Cancelled". Known edge, on `main` before HRD-7: the step trail labels a declined `create_lead` step "Created a lead". Not built here: a caller of "parked" (RESUME-4) and of "changed since" (HRD-10). The steps of the email redraft texts stay with HRD-8a. Lane cal-tasks owns six persona and pack lines that still call each card that did nothing a decline or a cancel: `agent-crm/instructions.md` lines 94 to 95, `agent-email-assistant/instructions.md` line 291, and `tool_packs.py` lines 224, 322, 419 and 3214. Three more lines quote a text that no tool gives now: `agent-email-assistant/instructions.md` lines 323 to 325 and 456 to 459 look for "Nothing changed. This needs your approval on a card", and the text is now "Not shown: ...". `orchestrator/agents.py` lines 70 to 73 tell the model to repeat "Reply discarded ..." | `tests/unit/test_card_outcome_vocabulary.py`, `src/lib/cardOutcomes.test.ts` | STANDARD |
| **HRD-8a** | 2987 | The text of a closed email card names its act and its tool. **BUILT 2026-11-16**, text only, with no flag. It sits on HRD-7, because both edit the same texts. `agents._closed_by_message(act)` builds each text: the HRD-7 head, then the steps of the act, with "Its tool is" and the tool. The acts are a reply (`compose_reply`), a new mail and a send (`send_email`), a forward (`forward_email`), a send later (`schedule_send`), a draft send (`send_draft`) and an unsubscribe (`unsubscribe_sender`). A forward names `forward_email` and no other send tool, and it keeps the forward of the same email to the people that the member names. `send_email`, `forward_email` and `schedule_send` no longer return `_CARD_REVISED`, and only `compose_reply` gives it. Each held card takes the act of its kind (`_HELD_KIND_ACTS`), and `_HELD_CARD_REVISED` is gone. The chat still draws each text as "Draft being changed", with the member line alone. **Not built here:** the act kind on the held-card record, the revise link of a forward and a reply, and the line on the new card (HRD-8b). No live eval ran, so the fence proves the text, not what the model does with it | `tests/unit/test_redraft_keeps_act.py` | STANDARD |
| **HRD-8b** | 3459 | The held-card record keeps its act kind. The revise link (`routes/email/automation/redraft.py`, `link_fields`, near :333 to :391) now covers a forward and a reply, and not only a new mail by To overlap. A new card whose act kind, or whose To and Cc, differ from the card it replaces shows a plain line (`ConfirmationCard.tsx` near :414 to :420, and `src/lib/redraftCard.ts`). **The owner decides two things:** the words of that line, and whether a card with a changed act still takes the revise link. The safe default, with no answer, is that it takes the link and shows the line. The Redis case lives in `TestTheOrderOnARealRedis`, which skips in pr-check until task 1856 adds the Redis service. HIGH, because `email_app_master_plan.md` §18.11 rates the send path HIGH | `tests/unit/test_redraft_keeps_act.py` (the Redis case in `TestTheOrderOnARealRedis`) | HIGH |
| **HRD-9a** | 2988 | Root-local tags, custom fields and task types: one helper, one read, one card line. AGENT-SAFE, no flag. An Opus builder builds it, because the R8 count goes into the shared seam `core.py`. **BUILT 2026-11-16, with no flag.** `core.vocabulary_reach` and `core._TREE_PROJECTS_SQL` count N. The walk goes down from the root to `MAX_DEPTH`, through each folder, and does not stop at `owns_statuses`. Each row of the walk names the organization of the root, beside RLS. A hidden project counts with no bypass. The tenant session reads each row of the tenant, and the count adds no visibility clause. A sealed project and an archived project count, because a change reaches their tasks. `GET /projects/nodes/{project_id}/vocabulary/reach` answers `{root_id, root_name, root_kind, projects}`. `root_kind` is `space` or `personal`. `root_name` is null when the member cannot see the root, and the card then says "the space above it" (Q5). The read is class A (`vocabulary`) in both manifests, and `off_door` `LATER` in the door table. The seven tools name the space in the detail. The three update cards name it in the `project` row of the body too, and the delete cards name it in the impact line. The twelve tools add the line for N of 2 or more, after the detail, as PST-4 does. A failed read gives the space from the walk up the tree, and no N. A walk that fails too gives "the space above it". The rename count of `update_tag` reads `/tags/{id}/impact`, and a failed read gives the count of the list. An org-wide row reads no reach, and its card does not change. Not built here: the count of an org-wide row (HRD-9b). **Done when:** (1) a read returns, for any visible node, the id, name and kind of the root, plus N. N is the count of projects in the tree of the root that hold tasks. A folder and a team space do not count. A hidden project counts. A subproject that owns its statuses still counts, so do NOT reuse `_STATUS_USERS_SQL`, because the status rule stops at `owns_statuses`. The helper walks down over `parent_project_id`, bounded by `MAX_DEPTH`. It adds the root only when the root holds tasks (the `holds_lanes` rule). (2) The seven tools `update_tag`, `update_field`, `update_type`, `delete_tag`, `delete_field`, `delete_type` and `merge_tags` name the SPACE (the root), not the node, when they change a root-local row. When N is 2 or more, the card detail adds "Changes the {tags\|custom fields\|task types} of {space} for {N} projects". (3) The five create cards (`create_type`, `create_field`, `create_tag`, `create_tags`, `create_types`) show the same N. Their receipts do not change, and `test_projects_create_vocab.py` ~:144 pins them. (4) The rename count of `update_tag` comes from `/tags/{id}/impact`, so archived tasks count, as the rewrite does (`tags.py` ~:697). (5) An org-wide row keeps its card of today. A failed read gives the card with the space and no N. (6) No tool gains an argument, and no call that passes today is refused. (7) A node of org A answers 404 to org B, and the trees of org B change no count of org A. **Files:** `apps/services/gateway/gateway/routes/projects/core.py` (a helper beside `status_users` ~:3111), one read route (default `GET /projects/nodes/{project_id}/vocabulary/reach` in `routes/projects/vocabulary.py`), `packages/acb_common/acb_common/app_manifests/projects.py` (a class A row), `apps/skills/skill-projects/skill_projects/writes.py` (~:4698, ~:4715, ~:5074, ~:5224, ~:5321), `guarded.py` (~:1604, ~:1609, ~:1634, ~:1639, ~:1670, ~:1706), `forms.py` (~:2216) and `tests/fences.json`. **Verify:** `uv run pytest tests/unit/test_shared_vocab_guard.py tests/unit/test_projects_agent_writes.py tests/unit/test_projects_create_vocab.py tests/unit/test_projects_chat_coverage.py tests/unit/test_projects_door_coverage.py tests/unit/test_app_door_coverage.py tests/unit/test_egress_card_bound_pin.py tests/unit/test_card_outcome_vocabulary.py -q`. Then, with a real database, `bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)" && uv run pytest tests/unit/test_shared_vocab_reach_r8.py tests/unit/test_projects_status_set_read_r8.py -v -rs`. Open PRs 2071 and 2094 also change `core.py`, so merge main before the merge. | `tests/unit/test_shared_vocab_guard.py` (hermetic tool-card cases), `tests/unit/test_shared_vocab_reach_r8.py` (R8, two orgs through `tenant_engine_scope`: a folder does not count, the team-space root does not count, a personal root counts, a project the member cannot see counts, a subproject with its own statuses counts, org B adds nothing to org A with a superuser control, and a node of another org answers 404) | STANDARD |
| **HRD-9b** | 3465 | Org-wide rows get the count. AGENT-SAFE, no flag, after HRD-9a. A tag uses `/tags/{id}/impact`, which the chat already reads. Fields and types need the class X impact read opened to the chat. No "only this space" copy. **Done when:** the card of an org-wide tag, field or type change shows the count. **Verify:** the HRD-9a commands, plus the new test of this slice | `tests/unit/test_shared_vocab_guard.py` | STANDARD |
| **HRD-9c** | 3466 | A refusal for tags, fields and types, in the style of PST-4. Built only if the owner rules for a refusal (§8, Q1). It runs behind a NEW flag, `SHARED_VOCAB_GUARD_ORGS`. The default is empty, which is off. Do NOT extend `STATUS_SET_GUARD_ORGS`. It is `*` on production, so an extension would switch on at merge with no owner preview. Needs new lines in `tool_packs.py` and `instructions.md` (lane cal-tasks owns them) and new egress pins (`egress.py` ~:1464). The flag flip is OWNER-GATE (`enforcement-flip`). The build is AGENT-SAFE. **Verify:** the HRD-9a commands, plus the new test of this slice | `tests/unit/test_shared_vocab_guard.py`, `tests/unit/test_egress_card_bound_pin.py` | STANDARD |
| **CS-2** | 2875 | **The Router marks each Anthropic chat step for the prompt cache.** BUILT 2026-11-15, dark behind `CUSTOMER_CONSOLE_PROMPT_CACHE` (the env of the Console unit, default off). Measured in `usage_event`: since the chat moved to Anthropic on 2026-11-04, 0 of 1,766 Claude requests reported a cached token, and the orchestrator's prompt averages 47,000 tokens (DeepSeek, which caches by itself, hit 18,709 of 18,953 before). Cause: the gateway applies `acb_llm.prompt_cache` (the explicit `cache_control` marks) only after the Router hop, and a routed call leaves before that. The gateway cannot name the model, and the Console Router, which can, had no cache code. Now `customer_console/prompt_cache.py` marks, once for each step in `main._kwargs_for`, the stable system block (split at the `CACHE BREAK` sentinel) and the last tool, and takes the sentinel out for every other vendor. The module is a copy of the gateway rule, because the Console imports no `acb_*` package, and a parity test pins the two. **Billing:** litellm reports Anthropic cache reads inside `prompt_tokens`, so the arithmetic holds (pinned). Cached reads bill at the cached price (Haiku 5.5: $0.01 against $0.10 per 1M input). A cache WRITE bills as plain input in our arithmetic while the vendor charges 1.25 times, about $0.0012 for each write of a 46,000-token prefix. **Not built:** a third mark on the last message, which would cache the later rounds of one turn; the flip, which is the owner's. | `tests/unit/test_customer_console_prompt_cache.py`, `tests/unit/test_customer_console_router.py::TestPromptCachePerStep` | HIGH |
| **CS-3** | 3438 | **The server sends the phase of a run** (Rung 1 of the chat ladder, demo task 2926, D211). CS-3a, BUILT 2026-11-16, dark behind `CHAT_RUN_PHASE_ORGS`: the server half, with no visible change. A streamed run sends the CUSTOM event `run_phase`. Its value is `{"phase": …}` and holds no other field. `gathering` goes right after `RUN_STARTED`, so it covers the setup of 0.5 to 1.7 s: the load, the tier wait, the session load and the compose. `asking` goes when the model stream of a native run opens. A run on the Tier 2 batch path or on a Copilot SDK agent sends `gathering` only. `acb_skills.run_phase` holds the closed set and the one flag reader. Only `executor._RunPhases` calls them, and only `run_agent_stream` uses it. Each phase goes by a direct yield, never by the run queue. So a phase is no output: `_n_emitted` stays False, and nothing seals the tier. The fold, `_is_answer_event`, the BFF route and `withCustomEvent` drop the name. The BFF route forwarded no phase in CS-3a. CS-3b opens that forward. AGENT-SAFE for the build. The flag flip is OWNER-GATE (`enforcement-flip`). **Done when:** with the flag on, a streamed run emits `run_phase` in order: `RUN_STARTED`, `gathering`, the setup, `asking`, then the first `TOOL_CALL_START` or `TEXT_MESSAGE_CONTENT`. It emits each phase at most once. `_n_emitted`, `_tier_run.seal()` and the usage rows are the same as with the flag off. No saved message holds the event. The flag off emits nothing. **Verify:** `uv run pytest tests/unit/test_run_phase_event.py tests/unit/test_progress_notice.py tests/unit/test_profile_used_event.py tests/unit/test_setup_laps.py tests/unit/test_chat_speed_slice2.py tests/unit/test_flag_doc_sorted.py`, and in `workbench/control_plane` `npx tsc --noEmit && npx vitest run src/lib/firstLine.test.ts src/lib/runStatus.test.ts src/lib/runPhaseDrop.test.ts src/lib/progressNotice.test.ts`. **CS-3b** (task 3462), the client half, is BUILT 2026-11-16: see the next row. | `tests/unit/test_run_phase_event.py`, `src/lib/runPhaseDrop.test.ts` | HIGH |
| **CS-3b** | 3462 | **The client draws the run phase** (client only, dark because `CHAT_RUN_PHASE_ORGS` stays off). BUILT 2026-11-16. The BFF route forwards `run_phase` as a custom event and still saves nothing for it. The hook turns the value into a FIXED word from ONE table, `RUN_PHASE_WORDS` in `src/lib/runStep.ts` (`phaseWordOf`): `gathering` is "Gathering what I need" and `asking` is "Asking the model". An unknown name, or a value that is no string, draws nothing, and the client never draws a server string. The run row (`RunStatus.phaseWord`, the new `phase` action of `reduceRun`) and the header of the thinking trail (`ChatMessage.phaseWord`, live only) say the word in place of "Reading your message". The phase action does not move the run from `starting` to `working`, and the hook skips the run event after a phase. The first real step takes over, and a run with no phase keeps CS-1 exactly. A re-attach, the end of the run and the first event clear the word. No save names it. AGENT-SAFE. **Done when:** on the scripted turn of task 2875, gathering, then asking, show their words inside 2 s on the row and the header. The first tool or text replaces the word. A phase never sets `working`, never joins `customEvents`, and never reaches a saved row. A joined run shows no stale phase. **Verify:** in `workbench/control_plane` `npx vitest run src/lib/runPhaseWords.test.ts src/lib/runPhaseDrop.test.ts src/lib/firstLine.test.ts src/lib/runStatus.test.ts`. | `src/lib/runPhaseWords.test.ts`, `src/lib/runPhaseDrop.test.ts` | STANDARD |
| **HRD-10** | 2989 | Changed since the card | `tests/unit/test_card_precondition.py` | HIGH |
| **HRD-11a** | 2990 | **The item that the chat last read or acted on.** **BUILT 2026-11-16, dark behind `CHAT_FOCUS_ORGS`.** `acb_skills.chat_focus` holds the closed table `FOCUS_TOOLS`: the six tools of `_EMAIL_ID_TOOLS` (`email_id`), and nine tools of the core `projects` pack that read or act on one task (`task_id`). A tool sets the item only in a call of its own owner. A task id must also read as a UUID, and it is kept in the canonical form. The read of the stored item starts beside the load (`executor._EarlyReads`). `_prime_chat_focus` binds it with the run id after the OA-4 prime, in the new lap `focus`. A delegated run drops the binding (`_without_focus_key`). The block holds no title. **Fix round 1 (2026-11-17):** the binding holds a `RunFocus` record of the run. When the calls of one run name two or more different items, the run deletes the stored item and writes none, so calls in parallel never keep a random one. The closed table `CLEARS` names 15 Projects tools that make, archive, delete or move a task or a project, and a call of one that acted deletes the item. The stored value holds the time of its write. A read gives the item for `FOCUS_FRESH_S` (2 hours) only, and the TTL is the same. `NO_ACT` adds three leads: the refusal of the permission gate, the date check of `defer` and the body check of `comment`. The rule now says that "it", "this" and "mark it done" CAN mean the item, only when the member's words name no other item, and that a newer item wins. No live eval ran, so the fences prove the text, not what the model does with it. **Not built here:** "the next one" (HRD-11b), the CRM, Calendar and Knowledge kinds, the new task of `create_task` as the item (its id is in the result, not in an argument, so it clears the item), the email tools that make a new mail, §8 Q7, and the flag flip. STANDARD. An Opus builder builds it, because it edits a shared seam. AGENT-SAFE, dark behind a NEW flag, `CHAT_FOCUS_ORGS`. The default is empty, which is off. The flag FLIP is OWNER-GATE (`enforcement-flip`, `work_plan.md` §6.0 D4, D218 step 4). Scope: the server tracks the item that the chat last READ or ACTED on, for emails and Projects tasks only. "The next one" is HRD-11b. **Done when:** (1) a new module `packages/acb_skills/acb_skills/chat_focus.py` keeps ONE record for each chat and member at the key `cc:<org>:chat-focus:<session>:<member hash>`. It writes the record through `tenant_redis.key` inside `organization_scope`. The org comes from `current_tenant()`. The session and the member come from the artifact context. No part comes from a request field or a tool argument. (2) A closed table maps each tool to (kind, id argument). It starts with the email and task read and act tools. A fence fails when a tool in the table lacks that argument in its real signature. (3) Only a call that succeeded writes the record, and only when its result does not start with a `_NO_ACT` lead. An id that fails `_ok_id` writes nothing. A list read writes nothing. (4) `_record_pack_call` and the sub-run hook beside `_record_email_working_set` write the record. The text of the model never writes it. (5) With the flag on, the per-turn context of the orchestrator shows the kind, the id argument and the in-app link. It comes after the OA-4 block, with the rule for "it", "this" and "mark it done". (6) With the flag off, the preamble, the tools and the persona stay byte-identical. (7) A shared room, a batch (WhatsApp) run and a delegated run read and write nothing. (8) A scripted trajectory costs no spend. Turn 1 reads an email or a task. The next turn says "mark it done", "it" or "this". The model request of that turn holds the block with the correct kind and id. A live-model eval that costs more than USD 0.50 waits for the owner's OK. **Files:** `packages/acb_skills/acb_skills/email_chat_context.py` (`_ok_id` ~:113, `_EMAIL_ID_TOOLS` ~:69, `update_working_set` ~:271, `_data` ~:565, `orchestrator_block` ~:680), `apps/services/orchestrator/orchestrator/_tool_injection.py` (`_pack_call` ~:3696, `_record_pack_call` ~:3876), `apps/services/orchestrator/orchestrator/executor.py` (`_prime_email_context` ~:1174, `_record_email_working_set` ~:3187, the primes ~:7636, `_context_preamble_parts` ~:11523), `tests/unit/test_setup_laps.py` ~:29 (pins the lap names), new `tests/unit/test_focus_item.py` and new `evals/trajectories/test_focus_item_trajectory.py`. **Verify:** `uv run pytest tests/unit/test_focus_item.py tests/unit/test_email_working_set.py tests/unit/test_one_assistant_email.py tests/unit/test_chat_constraints.py tests/unit/test_pack_guards.py tests/unit/test_every_pack_tool_answers_text.py tests/unit/test_setup_laps.py tests/unit/test_native_session_persistence.py tests/unit/test_orchestrator_one_source.py tests/unit/test_flag_doc_sorted.py tests/unit/test_tenant_redis.py evals/trajectories/test_focus_item_trajectory.py evals/trajectories/test_context_assembly_trajectory.py evals/trajectories/test_pack_turn_trajectory.py -q`. Then `uv run ruff check <changed files>`. **Notes:** HRD-5 (lane chat-reach, in progress) edits `_tool_injection.py` near ~:2999 and ~:3061, `settings.py`, `flag_reload.md` and this spec, so merge main first. Open PRs 2439 (TOF-1b), 1923, 1902 and 2034 touch the same files. Lane cal-tasks owns the persona and tool-text lines. This slice keeps its rule in its own per-turn block, so it needs no persona line | `tests/unit/test_focus_item.py`, `evals/trajectories/test_focus_item_trajectory.py` | STANDARD |
| **HRD-11b** | 3480 | **"The next one."** STANDARD, AGENT-SAFE, after HRD-11a, under the same flag, `CHAT_FOCUS_ORGS`. The record keeps the last list that the chat read and a cursor in it. "The next one" moves the cursor and names the item. Email and tasks come first. **Verify:** the HRD-11a commands, plus the new test of this slice | `tests/unit/test_focus_item.py` | STANDARD |
| **HRD-12a** | 2991 | **Decisions in the session, server only.** STANDARD. An Opus builder builds it, because of the persona swap. AGENT-SAFE, dark under the EXISTING `CHAT_CONSTRAINTS` flag. The store gains a `decision` kind. The card drop stays as it is, and no Undo UI is built. **BUILT 2026-11-16, dark under `CHAT_CONSTRAINTS`.** `keep_correction` takes `kind`, `limit` by default or `decision`, so the tool count stays two and an old call keeps a limit. A bad kind gets `BAD_KIND`. The store writes a limit as a plain string, the old shape, and only a decision as `{kind, text}`. `clean` reads both shapes. After a flip and a roll back, a reader from before HRD-12a keeps each limit and loses only the decisions. It does not raise. The per-turn block and the task head tell the model that a decision is not a request, so the model does not act on it. The two kinds share `MAX_ITEMS`. `handoff.DECISIONS_LINE` puts the decisions after the limits, in the per-turn block and in the task head. With no decision, each text is the OA-4 text, byte for byte. The drop card of a decision has its own lead (`DROP_CARD_LEAD_DECISION`). The persona gains one line in `_CONSTRAINTS_SWAP`. No live eval ran, so the fences prove the text, not what the model does with it. **Not built here:** the Undo chip (HRD-12b) and the flag flip (HRD-12c). **Done when:** (1) a decision is kept only when `said_by_member(text, typed_words())` is true (`chat_constraints.py` ~:227, ~:252). Text from a tool result, a mail, a paste or the model is refused with NOT_SAID. (2) The store keeps `{kind, text}`. A stored list of plain strings in the old shape reads as kind `limit`. (3) `orchestrator_block` (~:439-451) shows the decisions on their own line. They hold for the rest of this chat. (4) `handoff.head_task` carries the decisions to each delegated task. (5) The card drop removes a decision. (6) With the flag off, the tools, the persona, the preamble and the task head stay byte-identical. (7) In the trajectory the member types "TEDx is done." in turn N. Turns N+1 and N+5 show it. A kept stranger line is refused. The member drops it on a card, and turn N+7 does not show it. **Files:** `packages/acb_skills/acb_skills/chat_constraints.py` (NAMESPACE ~:70, MAX_ITEMS ~:78, flag reader ~:117, clean ~:153-170 which drops any item that is not a string, keep ~:501-537, drop ~:540-589), `packages/acb_skills/acb_skills/handoff.py` (CONSTRAINTS_MARK ~:58, `head_task` ~:235), `apps/services/orchestrator/orchestrator/agents.py` (`_CONSTRAINTS_SWAP` ~:360-370, tools ~:1199-1207), `apps/services/orchestrator/orchestrator/executor.py` (`_prime_chat_constraints` ~:1217, block ~:1254, inheritance ~:1267), `packages/acb_common/acb_common/settings.py` ~:1342. **Verify:** `uv run pytest tests/unit/test_chat_constraints.py tests/unit/test_handoff_words.py tests/unit/test_no_unasked_draft.py tests/unit/test_tool_packs_off.py tests/unit/test_orchestrator_one_source.py tests/unit/test_native_session_persistence.py tests/unit/test_executor_briefing_block.py evals/trajectories/test_chat_constraints_trajectory.py evals/trajectories/test_handoff_words_trajectory.py -q`. **Notes:** lane cal-tasks owns the persona lines in `agents.py` ~:360-370 and ~:178-199, `chat_constraints.py` ~:444-451, ~:461-489 and ~:501-515, and `tool_packs.py` ~:205-206, ~:228-229 and ~:403 | `tests/unit/test_chat_constraints.py`, `evals/trajectories/test_chat_constraints_trajectory.py` | STANDARD |
| **HRD-12b** | 3481 | **An Undo chip for a decision.** STANDARD, AGENT-SAFE, after HRD-12a. The chip calls a member route on the pattern of `/me/assistant-profile/undo` (`gateway/routes/admin/assistant_profile.py` ~:1306). **Verify:** the HRD-12a commands, plus the new test of this slice | `tests/unit/test_chat_constraints.py` | STANDARD |
| **HRD-12c** | 3482 | **Turn on `CHAT_CONSTRAINTS`.** OWNER-GATE (`enforcement-flip`). The flag is a global bool. It is not an `*_ORGS` list and is not in `docs/flag_reload.md`, so the flip needs a restart of the orchestrator service. Take the evidence before and after, as the task says. The grant window ends 2026-11-30 | none (a flip, no code) | STANDARD |
| **HRD-13** | 2992 | Claims and promises, in shadow first | `tests/unit/test_answer_claims.py` | STANDARD |
| **HRD-14** | 2993 | A paste is framed as data | `tests/unit/test_paste_frame.py` | HIGH |
| **HRD-15** | 2994 | The member's own words are obeyed | `evals/member_words/` | STANDARD |
| **HRD-16** | 2995 | A card answer acts once | `tests/unit/test_card_answer_once.py` | HIGH |
| **HRD-17** | 2996 | Offline, loading and two devices. The design is in §4.7a. The slices are HRD-17a to HRD-17f below. Not built | none (this is the parent row) | STANDARD |
| **HRD-17a** | 3483 | **The server runs one send once.** **BUILT dark, 2026-11-17, behind `CHAT_SEND_ONCE_ORGS` (default empty).** `orchestrator/send_once.py` holds the key and the rules. `send_once_on` is the one reader of the flag. The key is `cc:<org>:send-once:<thread>:<member hash>:<id>`. The value holds `run_id`, `member`, `sha256`, `claim` (a token of the request), `state` and `at`. The state is `claim`, then `pending`, then `run`. Each move is ONE compare and set (`TenantRedis.replace_if`) from the value of the same request. PROPOSED, decided at the build: (e) the route moves the claim to `pending` right before `run_detached`, not at the mint of the run id. A request that loses the move starts nothing. The `RUN_STARTED` of the run moves `pending` to `run`, and a run that loses that move stops before its work. (f) After the 2 s wait, a claim with no live run on the thread is stale, so the repeat takes it and runs as new, with no 409. A claim while a run is live, and a pending run younger than 15 s that nothing names, get 409 `send_in_progress`. (g) A steer, a stop and a dropped turn delete the claim, so a repeat of a steer steers again. (h) A run written less than 10 s ago gets the wait before FINISHED, because its stream may not show it yet. (i) R3 also covers a run id: a pending run that no `RUN_STARTED` and no live run names for 15 s never began, so a repeat takes it. (j) A release deletes each value that its request wrote or was about to write, so a cancel during a move leaves no key. A stream read is ONE `XRANGE` (`stream_relay.stream_head`), which never blocks. Each Redis step has a hard timeout of 3 s, and `begin` has 10 s, which ends in 409 and never in a run. `replay_events` sends no `BLOCK 0` now, so a read past the last entry returns at once. That also ends the old hang of `/reconnect` on a stream of 500 times N events, and of the fold on 10,000 times N. `TenantRedis` gains two fixed scripts, `delete_if` and `replace_if`. `/reconnect` and a repeat share `_replay_run_events`, and a repeat passes `expect_run_id`, so it never sends a newer run. **Known side effects:** (1) ⚠️ A REPLAY makes the BFF write a second assistant row under the new `assistantMessageId` of the resend (`route.ts` near :214 and :616, `useAgentChat.ts` near :496). This BLOCKS the flag flip until HRD-17d meets its condition. (2) A slow first request that loses the move can leave a second room event and one hidden empty reply row. (3) The request that loses the move keeps the cap reservation, because the thread is the key of the reservation, so the reservation is the winner's too. It lapses after 60 s. (4) A run that dies with its process keeps its key, so a resend replays the cut run with its interrupted marker. ⚠️ When the move to `run` at `RUN_STARTED` hits a Redis fault, the value stays `pending`. `_is_stale` counts its age from the `at` of the pending write. The stream names a finished run only until a newer run deletes the stream, or until 1 hour passes. After that, nothing names the run, and the pending value is older than 15 s. So a resend runs the send AGAIN after the 2 s wait. A short run runs again too, not only a run longer than 13 s. This BLOCKS the flag flip. (5) ⚠️ F-C. In `begin`, a stall or a refusal on the first SET NX of a send gives OPEN. The send then takes the old path. After that first SET NX, a stall in any step of `begin` gives 409 `send_in_progress`. The limit is 3 s for one step and 10 s for all of `begin`. A refusal in any of those steps fails open, and the send takes the old path. Then a repeat can steer the same words again or start a second run. `start` and `confirm` fail open on a stall and on a refusal, and the run goes on. This BLOCKS the flag flip until the owner accepts it. (6) ⚠️ F-E. The code now refuses a move into an empty key. A slow request can lose its claim in two ways. Its claim can run out after 60 s. Or a taker can run it, and a Stop can release it. Then the slow request starts nothing. It sends `RUN_ERROR` with the code `send_in_progress`, and a resend of the same id runs. One case remains. The move of a slow first request can stall, or Redis can refuse it. Then the move fails open. That request still runs after a Stop of the taker's run. This BLOCKS the flag flip. The owner must also accept that a request slower than 60 s starts nothing. (7) Notes. After a key was found, a refusal fails open, and a stall gives 409 (R10). The `TimeoutError` of redis-py is not the builtin one. `send_once` catches both through `tenant_redis.TIMEOUT_ERRORS`. So a socket timeout below 3 s still counts as a stall, and gives 409 after a key was found. Fences: `tests/unit/test_send_once.py`, and `tests/unit/test_send_once_real_redis.py` with a private real Redis. HIGH. An Opus builder builds it. An independent verifier and an Opus diff review follow. AGENT-SAFE, dark behind a NEW flag, `CHAT_SEND_ONCE_ORGS` (PROPOSED). The default is empty, which is off. The flag is per org and reloads on SIGHUP. The flag FLIP is OWNER-GATE (`enforcement-flip`). The dated grant in `.claude/OWNER_GRANTS.md` runs to 2026-11-30, so it covers a flip until then. **Why.** The stream is per thread, not per run (`stream_relay.py`: `cc:stream:{thread_id}` and `cc:active:{thread_id}`). A repeat during a live run takes the steer route and adds the same words twice. A repeat after the run is final starts a second run, with a second answer, cost and tool acts. **Done when:** a repeat of the same send starts no second run, mints no second row, and saves no second user row, in every case below. The old behaviour holds for no id, a bad id, the flag off and Redis down. **The key.** (1) The key is organization, thread, member and `user_message_id`. The member in the key is PROPOSED, beyond the words "organization, thread and id" of §4.7a. A room member who learns another member's id cannot swallow or join that run. A different member with the same id is no repeat, and takes the old path. The org comes from `user.organization_id`, never from the body. (2) The value is `{run_id, member, sha256(text)}`. (3) A new module keeps the key through `acb_common.tenant_redis` (`TenantKey`, `get_tenant_redis`). It adds no entry to `_ALLOWED_DIRECT_REDIS`. It reuses the `answer_once.py` idiom of HRD-16 (PR 2491), so the build waits for that merge. (4) Lifetimes: SET NX writes a placeholder with a 60 s TTL BEFORE the route decision. When the run id exists, the value is rewritten with the run id and a 24 h TTL. The stream lives 1 hour (`STREAM_TTL_SECONDS`). **The rules.** (R1) A repeat of a LIVE run replays it through ONE shared function. The build extracts that function from the `_event_generator` closure of `reconnect_agent_stream` (`routes/agent.py`, GET `/run/{thread_id}/reconnect`). It does not copy the closure. The answer carries the header `X-Run-Id` with the stored run id, because the BFF binds its reader to that header (`runBinding.ts`). The repeat starts no run, mints no row, takes no cap slot (`admit_member_run`), fires no room event and saves no second user row. (R2) A repeat of a FINAL run checks that the `runId` of the stream RUN_STARTED event equals the stored run id. On a mismatch (a newer run reset the stream) or a missing stream (after 1 hour), the answer is the synthetic RUN_FINISHED with `X-Run-Id` set to the stored id. The client then reads the saved row. (R3) A STALE key has no RUN_STARTED with that run id and no active run. The process died between the SET and the run start. The server does a compare-and-delete and runs the send as new. (R4) A run that ends in RUN_ERROR, or that the Stop route stops (`cancel_agent_run`), deletes the key. A deliberate resend can then run. The client mints a new id on a deliberate resend anyway. (R5) Two repeats at once: SET NX picks one winner. The loser polls for the run id for a bounded time (PROPOSED 2 s) and then replays. If no run id appears, the loser follows R3. (R6) The SAME id with a DIFFERENT body (the digest differs) gets 409 `send_id_reused`. The server logs it and never runs the new text. This is PROPOSED. (R7) An id outside `^[A-Za-z0-9_-]{8,64}$` counts as no id, so the old behaviour holds. The server logs it. A send with no id keeps the old behaviour. (R8) A request with `supersedes` set, a RESUME-2a continuation (`_resume_launch`) and a `steer_followup` (`_followup`) SKIP the key. Edit-and-resend and a steer use a new id, so they never meet the key. (R9) A tier fallback and the parallel-tier restart happen inside one `run_agent_stream`, with the same run id and thread. They never reach the route, so they never touch the key. (R10) With Redis down, the send FAILS OPEN to the old behaviour and logs a warning. With the flag off, the code makes NO Redis call. (R11) The direct path (no relay, `_serve_run` direct when Redis is down at the start) deletes the key. **Where.** The key code sits AFTER `room.can_send`, `_resolve_room` and the `supersedes` guard. It sits BEFORE `_route_incoming_turn` and `admit_member_run`. **Order.** This docs PR comes first. Then the module and the flag, with the route unwired. Then the route wiring. The build waits for PR 2491 (HRD-16). **Files:** `apps/services/gateway/gateway/routes/agent.py` (the route, the `/reconnect` extraction, `cancel_agent_run`), `apps/services/orchestrator/orchestrator/stream_relay.py` (read only, unless the extraction needs a helper), a new module beside `orchestrator/answer_once.py`, `packages/acb_common/acb_common/settings.py` (`chat_send_once_orgs: str = ""`), a reader like `run_phase_on(org)` in `packages/acb_skills/acb_skills/run_phase.py`, `.env.example`, `docs/flag_reload.md`, `packages/acb_common/acb_common/env_guard.py` (add a line only if the build finds one is needed), an `AGENTS.md` entry and new `tests/unit/test_send_once.py`. **Verify:** `uv run pytest tests/unit/test_send_once.py tests/unit/test_flag_doc_sorted.py tests/unit/test_tenant_redis.py tests/unit/test_flag_reload.py -q`. Add the tests that read `routes/agent.py` by AST (`git grep -l "routes/agent.py" -- tests/unit`). Then `uv run ruff check` and `uv run mypy` on the touched files. `test_send_once.py` holds a default-empty flag test. R8 does not apply, because the slice uses Redis and no SQL. If the build touches the SQL of `_mint_run_row`, R8 applies to that part. **Rung.** §5 has no HRD-17 line. HRD-17a sits in rung 5 with HRD-16 to HRD-19. **Open choices** (all PROPOSED, the owner may change them, and the build goes on dark behind the flag): (a) the member in the key, (b) the 409 on a changed body, (c) the 2 s wait of the loser, (d) the 60 s placeholder. | `tests/unit/test_send_once.py` | HIGH |
| **HRD-17b** | 3484 | The chat outbox: in memory, with `sessionStorage` for a reload. It releases once and in order. Builder: lane-ui | `src/lib/chatOutbox.test.ts` | STANDARD |
| **HRD-17c** | 3485 | A send while the history loads waits for the load. Builder: lane-ui | `src/lib/chatOutbox.test.ts` | STANDARD |
| **HRD-17d** | 3486 | A send that lost its connection before the server answered is not marked sent. Waits for HRD-17a. Builder: lane-ui. **Condition (from the review of HRD-17a, 2026-11-18):** a resend must reuse the `assistantMessageId` of its first try, or the BFF must save nothing for a replay. Today a replay writes a second assistant row. `CHAT_SEND_ONCE_ORGS` stays empty until this holds | `src/hooks/useAgentChatDisconnect.test.ts` | STANDARD |
| **HRD-17e** | 3487 | The server's copy wins the fold of a second device, and a shorter write before the seal is refused if the check says so. Builder: lane-ui, with a HIGH server part | `src/lib/chatFold.test.ts`, `tests/unit/test_chat_upsert_no_shrink_r8.py` | STANDARD |
| **HRD-17f** | 3488 | The e2e test with the network off, and the Visual review. Builder: lane-ui | `e2e/chat_outbox.spec.ts` | STANDARD |
| **HRD-18** | 2997 | Names as people say them. HRD-18a, BUILT 2026-11-14: the one matcher, the CRM search word by word with its near pass (`crm_platform.md` §19.5), `resolve_person` and `find_contact`. HRD-18b: the company search and the mail sender match | `tests/unit/test_name_variants.py`, `tests/unit/test_crm_near_search_r8.py` (R8) | STANDARD |
| **HRD-19** | 2998 | Start clean | `tests/unit/test_continue_carries.py` | STANDARD |
| **HRD-22** | 3231 | A late approve acts once. BUILT dark behind `CHAT_LATE_APPROVE_ORGS`, 2026-11-14 (`chat_run_continuity.md` S2, item 8). The flag flip on production is OWNER-GATE (`enforcement-flip`) | `tests/unit/test_late_approve_grant.py`, `tests/unit/test_late_approve_grant_r8.py` (R8 and a private real Redis) | HIGH |

An eval with a live model costs money. Each eval above USD 0.50 waits for the
owner's OK.

## 7. The contract of every chat tool

Each new or changed chat tool meets these rules. HRD-2 and HRD-3 add the
fences of rules 1 to 3. Until those land, the rules are advisory (R7).

1. **Each id that the tool needs is one the model can hold.** A read tool
   prints it, or the tool resolves a near form. A near form is a number, a
   name, a `kind:` prefix, or an email id for its thread.
2. **Each argument works in the shape that the model sends.** The seam of
   HRD-3 gives a JSON string or a list the shape of the schema.
3. **Each failure is text with a next step.** No raw exception reaches the
   model. A refusal names a tool or a step that works.
4. **A card names its reach.** A change to something shared says how many
   objects it changes.
5. **A card outcome uses the words of HRD-7.**
6. **A bulk act stops at the first failure,** and says what exists and what
   does not.
7. **A date argument reads the member's day** from `acb_common.today`.

## 8. Decisions

The owner accepted each default on 2026-11-11 21:28 UTC on epic 2978, and confirmed it in chat on 2026-11-12. They are recorded as **D217**.

| # | Question | Decision |
|---|---|---|
| **D-HRD-1** | When the model calls a tool of a pack that is not loaded, may the turn load that pack? | Yes, when the member may use that pack. The answer says so |
| **D-HRD-2** | When no task has all the words, may a search show tasks with most of them? | Yes, with the line "No task has all the words. These have most of them" |
| **D-HRD-3** | How many empty or refused answers from one tool end a search in one turn? | 3 |
| **D-HRD-4** | Is a pill, a chip or a retry that the member pressed the member's order to act? | Yes. Its words still carry no typed mark for the profile (D-PZ-37) |
| **D-HRD-5** | Which text is a paste, framed as data? | A block of 8 lines or more, or text that matches a stored mail body |
| **D-HRD-6** | May the claim check of HRD-13 change an answer? | No. It logs in shadow until the owner turns it on |

**D-HRD-7 is PROPOSED** (HRD-22, 2026-11-14). It is not part of D217, and the
owner has not decided it. HRD-22 builds it dark, so the flag flip waits for
the owner. It takes no D number in `work_plan.md` §3.

| # | Question | Proposed default |
|---|---|---|
| **D-HRD-7** | Is a late tap on Approve the member's own consent? | Yes, for one use: the same card, in the same thread, by the same member, within 30 minutes. Any other card asks again. An act that already ran in this chat does not run again on a late tap. Across chats, and after a Redis stall, task 3294 (HRD-22b) closes the gap before the flag flips |

**The questions of HRD-9 are PROPOSED** (2026-11-16). The owner has not
decided them. They take no D number in `work_plan.md` §3.

| # | Question | Proposed default |
|---|---|---|
| **Q1** | Does a change to a shared tag, field or type refuse, or only name its reach? | Name the reach on the card. No copy for one project exists, so a refusal would offer only "say yes again" |
| **Q2** | What is N? | The projects of the space tree that hold tasks. Hidden projects count |
| **Q3** | Does the card show a line when N is 1? | No, as in PST-4 |
| **Q4** | What do org-wide rows show? | HRD-9b adds the count only. A copy for one space waits for an owner ruling |
| **Q5** | What shows when the member cannot see the space? | No name. The card says "the space above it", plus N |

**The questions of HRD-11a are PROPOSED** (2026-11-16). The owner has not
decided them. They take no D number in `work_plan.md` §3.

| # | Question | Proposed default |
|---|---|---|
| **Q1** | Does the focus carry a title? | No. It holds ids, kind and link only, as the email working set does. For a title, use `email_chat_context._data` (60 chars) and UntrustedFrame |
| **Q2** | Which item wins? | An item that the member names wins. Then the last read or act of the chat answers "it", "this" and "mark it done". The open page item wins for "this task" and "this email" |
| **Q3** | What does "the next one" do? | HRD-11b builds it |
| **Q4** | Which kinds come first? | Email and task |
| **Q5** | Does WhatsApp keep a focus? | No. A batch run reads and writes nothing |
| **Q6** | How does the flag reload? | `CHAT_FOCUS_ORGS` reloads on SIGHUP, as the other `*_ORGS` flags do |
| **Q7** | Does a bare "this" go to the stored item or to the open page item? | **OPEN** (review of HRD-11a, 2026-11-17). Safe default: the open page item wins for a bare "this" when the page facts name one. **NOT built:** the block still lets a bare "this" mean the stored item. Only "this task" and "this email" go to the page item |

**The questions of HRD-12a are PROPOSED** (2026-11-16). The owner has not
decided them. They take no D number in `work_plan.md` §3.

| # | Question | Proposed default |
|---|---|---|
| **Q1** | How does a member undo a decision? | A chip with a member route (HRD-12b). Until then, the card drop |
| **Q2** | What shape does the tool take? | A `kind` argument on `keep_correction`, default `limit`. The tool count stays two, and old calls work |
| **Q3** | What is the cap? | One shared list of 10 |
| **Q4** | What if a decision names no item? | Keep the sentence, and the model asks. Tie it to the focus item only after HRD-11a |
| **Q5** | What is the flag? | Keep the global bool `CHAT_CONSTRAINTS`. The owner decides at the flip whether to add `CHAT_CONSTRAINTS_ORGS` |

**D-HRD-17-1 is PROPOSED** (HRD-17, 2026-11-16). The owner has not decided it.
It takes no D number in `work_plan.md` §3.

| # | Question | Proposed default |
|---|---|---|
| **D-HRD-17-1** | Does an unsent message survive a closed tab? | No. It survives a reload of the same tab, in `sessionStorage`, and it clears on sign-out. A closed tab keeps no message text on a shared computer. The cost: a message typed offline is lost when the member closes the tab |

Two limits wait for the owner, and the build keeps them as they are
(`chat_run_continuity.md` S2 item 8). The 30 minutes start at the tap, not at
the card. A parked card stays answerable for 7 days, so a card that parked 6
days ago can still act when its text and its act keys are still equal.

## 9. What this spec does not own

- The steer path stays in `chat_run_continuity.md` §4.9 (task 2494).
- The held email card and recipient safety stay in `email_app_master_plan.md` §21.
- The shared status guard (PST-4) stays in `projects_settings.md`. HRD-9a to HRD-9c extend its pattern.
- The constraints store (OA-4) and the way through (WT-1 to WT-4) stay in `one_assistant_context.md`. HRD-12a and HRD-19 extend them.
- The card policy stays in `projects_ai_chat.md` §29.
- The tool packs stay in WS-67 (`one_assistant_context.md`).
- The model's behaviour fixes of 2026-11-11 stay in task 2874, and the speed in task 2875.
