# Chat failure modes: what goes wrong in the chat, and what we build against it

**Status: ACCEPTED, 2026-11-12 (D217). HRD-6 part 1 (the line of a slow tool) is BUILT
dark, 2026-11-13 (PR 2238). HRD-22 (a late approve acts once, task 3231) is
BUILT dark behind `CHAT_LATE_APPROVE_ORGS`, 2026-11-14, branch
`mt-1740009c-late-approve`.** Owner ask of 2026-11-12,
in chat. Epic task 2978 (project AI Chat Interface). It is part of WS-CHAT.

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
  statuses, but not tags, fields or types. The answer check covers CRM numbers
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
| C1 | The model misreports a card's outcome | Projects gives one text for a reject, a timeout and a cancel. Email has its own texts. Task 3130: when a chat message closes a write card, the server ends the answer with a "Not done" line (`chat_run_continuity.md` §4.9) | One outcome vocabulary: approved, declined, closed by your message, timed out, parked, changed since. HRD-7 starts from `ask_tools.CLOSED_BY_YOUR_MESSAGE` | HRD-7 |
| C2 | A redraft changes the act or the recipients | Email §18.11 links a redraft by To overlap only | A card carries its act. A changed act or changed recipients show on the new card | HRD-8 |
| C3 | The object changed after the card was drawn | Only the email reply card and the epic contract check it | A card holds its object's version, and the write checks it | HRD-10 |
| C4 | Two cards wait, and the member types | One card waits at a time (PAC §29.6). A steer closes the open card | No change. §29.6 stays the rule. HRD-16 covers cards parked after a restart | HRD-16 |
| C5 | A write reaches more than the member thinks | PST-4 guards shared statuses (on in production). Tags, custom fields and types have no guard | A shared vocabulary change says how many projects it changes | HRD-9 |
| C6 | A late approve opens nothing | The member approves a parked card after the chat stopped waiting. The answer starts a new run, which plans again and draws a new card. On 2026-11-14 one deal move needed three approvals | A late approve is the member's own consent. It approves the same card once, in the same thread, within 30 minutes. The fix lands in `chat_run_continuity.md` S2, item 8 | HRD-22 |

### 4.4 Claims and promises (D)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| D1 | The answer claims an act that did not happen, or doubts one that did | The answer check covers CRM numbers, in shadow | An act verb needs a matching tool outcome in the turn. A doubt needs a failed outcome | HRD-13 |
| D2 | A promise of a later act ("I'll check the thread") | No rule | A promise needs a reminder or a task made in the turn. Otherwise do it now, or ask | HRD-13 |

### 4.5 Losing track in the chat (E)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| E1 | "This", "it", "mark it done", "the next one" | Email has a working set of ids. Other apps send an app slug only | The turn carries the item in focus, from the last read or act and the open screen | HRD-11 |
| E2 | A decision made earlier in the chat does not hold | OA-4 keeps corrections and limits, and it is off | OA-4 keeps decisions too, from the member's typed words, with Undo | HRD-12 |
| E3 | A chat that went wrong | WT-1 and WT-2 are dark and carry the newest typed message only | "Start clean" carries the decisions, the item in focus and the open cards | HRD-19 |

### 4.6 Names, dates and language (F)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| F1 | A name spelled as people say it ("Swagata" for Sougata) | `resolve_person` matches exactly or as a substring. `find_contact` has a trigram fallback | One matcher folds spelling and transliteration variants, and asks to confirm | HRD-18 |
| F2 | Relative and past dates | Live: the Now line, the member's zone, past dates refused | No change. Each new date argument uses `acb_common.today` | §7 |
| F3 | Mixed language and voice transcripts | VOX puts a transcript in the box, and the member sends it | No change. The member checks it before Send | none |

### 4.7 Delivery and devices (G)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| G1 | A double tap or a retried answer | The prompt save is idempotent. No write key on a card answer. A steer can repeat | Each card answer has a one-use key. A steer seen twice counts once | HRD-16 |
| G2 | The phone sleeps during a stream | Live: detached runs, replay by `lastEventId`, durable asks on | No change | none |
| G3 and G4 | Two devices, a send while offline, a send while the history loads | No design | An outbox sends once. A send waits for the load. The server's copy wins the fold | HRD-17 |

### 4.8 Outbound safety and limits (H, I)

| Id | The class | State today | The rule | Slice |
|---|---|---|---|---|
| H1 | A customer copied by mistake, or the recipients change between drafts | Live: the held card classes each recipient and leaves a new address unticked | HRD-8 shows a change of recipients on a redraft | HRD-8 |
| H2 | A bulk act that fails part way | Projects stops and says what exists. Email and CRM are unchecked | §7 rule 6 binds each bulk tool | §7 |
| I1 | Credits run out, or the provider fails, during a run | Live: `runErrors.ts` names the credit and rate cases | No change | none |

## 5. The ladder (D211)

| Rung | Demo task | The demo | Slices |
|---|---|---|---|
| 0 | none (it outranks the ladder) | The failures that production shows today stop. A member approves a parked card once on production, and the act runs (HRD-22) | HRD-1, HRD-2, HRD-3, HRD-22 |
| 1 | 2999 | Ask for something hard to find: the chat finds it, or says what it tried | HRD-4, HRD-5, HRD-6 (and rung 0) |
| 2 | 3000 | Every card tells the truth, and a shared change says how far it reaches | HRD-7 to HRD-10 |
| 3 | 3001 | The chat keeps track: "mark it done", decisions, no empty promise | HRD-11 to HRD-13 |
| 4 | 3002 | A pasted email cannot steer the chat, and your own words always do | HRD-14, HRD-15 |
| 5 | 3003 | A double tap, a dead connection, a second phone or a misspelt name does no harm | HRD-16 to HRD-19 |

## 6. The slices

| Slice | Task | What | Fence (R7) | Tier |
|---|---|---|---|---|
| **HRD-0** | 2979 | This spec, the INDEX row and the WS-CHAT row | STE lint | LOW |
| **HRD-1** | 2980 | An unknown tool name gets a way through | `tests/unit/test_unknown_tool_answer.py` | STANDARD |
| **HRD-2** | 2981 | Print the hidden ids. Accept a task number and a `kind:` prefix | `tests/unit/test_tool_id_provenance.py` | STANDARD |
| **HRD-3** | 2982 | One wrapper on each pack tool: text for each failure, and each argument in its declared shape | `tests/unit/test_every_pack_tool_answers_text.py`, `tests/unit/test_tool_argument_shapes.py` | STANDARD |
| **HRD-4** | 2983 | Search that finds | `tests/unit/test_search_fallback.py` | STANDARD |
| **HRD-5** | 2984 | A per-turn budget for each tool | `tests/unit/test_tool_turn_budget.py` | STANDARD |
| **HRD-6** | 2985 | A slow read tells the member to wait (owner, 2026-11-13). Part 1, BUILT dark behind `CHAT_PROGRESS_NOTICE_ORGS`: one line for each tool call past 6 s, on the web chat and as WhatsApp's early message (`chat_run_continuity.md` §4.11). Part 2: the long mode of the CRM total | `tests/unit/test_progress_notice.py` (part 1), `tests/unit/test_crm_aggregate_timeout.py` (part 2) | STANDARD |
| **HRD-7** | 2986 | One vocabulary of card outcomes | `tests/unit/test_card_outcome_vocabulary.py` | STANDARD |
| **HRD-8** | 2987 | A redraft keeps the act | `tests/unit/test_redraft_keeps_act.py` | STANDARD |
| **HRD-9** | 2988 | Blast radius for tags, fields and types | `tests/unit/test_shared_vocab_guard.py` | STANDARD |
| **HRD-10** | 2989 | Changed since the card | `tests/unit/test_card_precondition.py` | HIGH |
| **HRD-11** | 2990 | The item in focus | `tests/unit/test_focus_item.py` | STANDARD |
| **HRD-12** | 2991 | Decisions in the session | `tests/unit/test_chat_constraints.py` | STANDARD |
| **HRD-13** | 2992 | Claims and promises, in shadow first | `tests/unit/test_answer_claims.py` | STANDARD |
| **HRD-14** | 2993 | A paste is framed as data | `tests/unit/test_paste_frame.py` | HIGH |
| **HRD-15** | 2994 | The member's own words are obeyed | `evals/member_words/` | STANDARD |
| **HRD-16** | 2995 | A card answer acts once | `tests/unit/test_card_answer_once.py` | HIGH |
| **HRD-17** | 2996 | Offline, loading and two devices | `src/lib/chatOutbox.test.ts` | STANDARD |
| **HRD-18** | 2997 | Names as people say them | `tests/unit/test_name_variants.py` | STANDARD |
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
| **D-HRD-7** | Is a late tap on Approve the member's own consent? | Yes, for one use: the same card, in the same thread, by the same member, within 30 minutes. Any other card asks again |

## 9. What this spec does not own

- The steer path stays in `chat_run_continuity.md` §4.9 (task 2494).
- The held email card and recipient safety stay in `email_app_master_plan.md` §21.
- The shared status guard (PST-4) stays in `projects_settings.md`. HRD-9 extends its pattern.
- The constraints store (OA-4) and the way through (WT-1 to WT-4) stay in `one_assistant_context.md`. HRD-12 and HRD-19 extend them.
- The card policy stays in `projects_ai_chat.md` §29.
- The tool packs stay in WS-67 (`one_assistant_context.md`).
- The model's behaviour fixes of 2026-11-11 stay in task 2874, and the speed in task 2875.
