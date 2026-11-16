# Chat failure modes: what goes wrong in the chat, and what we build against it

**Status: ACCEPTED, 2026-11-12 (D217). BUILT: HRD-6 part 1 (the line of a slow
tool), dark, 2026-11-13 (PR 2238). HRD-1 (an unknown tool name), 2026-11-14:
the answer is live, and the auto-load is dark behind `CHAT_TOOL_AUTOLOAD`.
HRD-4 (search in parallel, `find_tasks` on most of the words, task 2983), 2026-11-16.
HRD-18a (names as people say them), 2026-11-14. HRD-22 (a late approve acts
once, task 3231) is BUILT dark, 2026-11-14. Its flag is
`CHAT_LATE_APPROVE_ORGS`. CS-1 (the first line, client only) and CS-2 (the
prompt cache marks of the Router, dark) are BUILT, 2026-11-15. CS-3a (the
server half of the run phase, task 3438) is BUILT dark, 2026-11-16. Its flag
is `CHAT_RUN_PHASE_ORGS`.**

**HRD-7 (one vocabulary of card outcomes, task 2986) is BUILT, 2026-11-15,
with no flag. Projects, CRM and email take each card outcome text from
`ask_tools.CARD_OUTCOMES`.**

**HRD-8a (a closed email card keeps its act, task 2987) is BUILT, 2026-11-16,
with no flag. HRD-8b (task 3459) is open.**

**HRD-9a (task 2988) is BUILT, 2026-11-16, with no flag. A card of a
root-local tag, custom field or task type names the space and N, the count
of projects that the change reaches. HRD-9b and HRD-9c are not built.**

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
| E1 | "This", "it", "mark it done", "the next one" | Email has a working set of ids. Other apps send an app slug only | The turn carries the item in focus, from the last read or act and the open screen | HRD-11 |
| E2 | A decision made earlier in the chat does not hold | OA-4 keeps corrections and limits, and it is off | OA-4 keeps decisions too, from the member's typed words, with Undo | HRD-12 |
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
| G3 and G4 | Two devices, a send while offline, a send while the history loads | No design | An outbox sends once. A send waits for the load. The server's copy wins the fold | HRD-17 |

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
| 3 | 3001 | The chat keeps track: "mark it done", decisions, no empty promise | HRD-11 to HRD-13 |
| 4 | 3002 | A pasted email cannot steer the chat, and your own words always do | HRD-14, HRD-15 |
| 5 | 3003 | A double tap, a dead connection, a second phone or a misspelt name does no harm | HRD-16 to HRD-19 |

## 6. The slices

| Slice | Task | What | Fence (R7) | Tier |
|---|---|---|---|---|
| **HRD-0** | 2979 | This spec, the INDEX row and the WS-CHAT row | STE lint | LOW |
| **HRD-1** | 2980 | An unknown tool name gets a way through. **BUILT 2026-11-14:** the answer is live. D217 allows the auto-load, and it ships dark behind `CHAT_TOOL_AUTOLOAD` (default off) | `tests/unit/test_unknown_tool_answer.py` | STANDARD |
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
| **HRD-11** | 2990 | The item in focus | `tests/unit/test_focus_item.py` | STANDARD |
| **HRD-12** | 2991 | Decisions in the session | `tests/unit/test_chat_constraints.py` | STANDARD |
| **HRD-13** | 2992 | Claims and promises, in shadow first | `tests/unit/test_answer_claims.py` | STANDARD |
| **HRD-14** | 2993 | A paste is framed as data | `tests/unit/test_paste_frame.py` | HIGH |
| **HRD-15** | 2994 | The member's own words are obeyed | `evals/member_words/` | STANDARD |
| **HRD-16** | 2995 | A card answer acts once | `tests/unit/test_card_answer_once.py` | HIGH |
| **HRD-17** | 2996 | Offline, loading and two devices | `src/lib/chatOutbox.test.ts` | STANDARD |
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

Two limits wait for the owner, and the build keeps them as they are
(`chat_run_continuity.md` S2 item 8). The 30 minutes start at the tap, not at
the card. A parked card stays answerable for 7 days, so a card that parked 6
days ago can still act when its text and its act keys are still equal.

## 9. What this spec does not own

- The steer path stays in `chat_run_continuity.md` §4.9 (task 2494).
- The held email card and recipient safety stay in `email_app_master_plan.md` §21.
- The shared status guard (PST-4) stays in `projects_settings.md`. HRD-9a to HRD-9c extend its pattern.
- The constraints store (OA-4) and the way through (WT-1 to WT-4) stay in `one_assistant_context.md`. HRD-12 and HRD-19 extend them.
- The card policy stays in `projects_ai_chat.md` §29.
- The tool packs stay in WS-67 (`one_assistant_context.md`).
- The model's behaviour fixes of 2026-11-11 stay in task 2874, and the speed in task 2875.
