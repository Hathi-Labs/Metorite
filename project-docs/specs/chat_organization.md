# Chat organization — topics, picking up a chat, condense, and one search

**Status:** ACTIVE (D163, 2026-11-02). CO-0 is BUILT (PR 1607, 2026-11-02).
CO-11, the pin, is BUILT and dark behind `CHAT_PIN_ORGS` (task 3510,
2026-11-16). Its chat tool is not built (§18). CO-2a, the topic store and
its routes, is BUILT and dark behind `CHAT_TOPICS_ORGS` (task 3501,
2026-11-16). CO-2b adds `topic_id` and `set_by`, the move, the pass that
never overrides a member, the rail and the chip. The rest of D163 is not
built.
CO-13, the continue mark, is BUILT too (task 3512, 2026-11-16).

**Proposed:** §15 to §20 (task 3491, owner ask of 2026-11-16). The owner has not
decided them, so they add no decision number. §19 holds the questions. Some
slices of them are built since then, and §11 marks each one.

**Verified** against the code on 2026-11-16 at `origin/main` `471704477`.

**Board:** WS-CO, epic task 3495. §11 lists a task for each slice.
**Owner of the file half:** `artifact_management.md` §6.0 (D162). This spec owns
topics, the join, the condense note, pin, and the full-screen chat.
**Extends:** `personalization.md` §15 (PZ-33, PZ-34, D142), `navigation_shell.md`
§7.1 and `knowledge_base.md` §13 (KB-9a). It replaces none of them.

## 0. The answer in one screen

The assistant remembers, and nobody manages anything. It speaks up only when it
is unsure, and a wrong guess costs one click.

- **One pass.** When a chat goes idle, one small model call writes its summary,
  topic, title and the records it is about. Every feature below reads that row.
- **Topics.** The company has one short list. Which chats sit in a topic stays
  private. A topic that the AI makes for one member stays with that member.
- **Three outcomes for a new message:** join the old chat, start a new chat that
  carries the old chat's summary, or start clean.
- **A chip shows the destination before the send.** That is the cheap override.
- **Search** covers chats and files together.
- **Many chats made easy (§15 to §20, PROPOSED).** A member condenses chats into
  one note, pins a chat, and sees an old chat offered with a preview.

## 1. What the owner asked for (2026-11-02)

```text
"Categorize all the different kinds of chats ... into high-level buckets like
sales, finance, marketing." "This auto categorisation should be automatic."
"Instead of starting a new session, if it's ... a continuation of an old
session, we should append to the older session ... and smartly convey to the
customer ... and be able to override."
```

The owner accepted §4 in chat: "I'm happy with all of these." §15.1 holds the
ask of 2026-11-16.

## 2. Scope and non-goals

**In scope:** the full-screen chat, the idle-chat pass, topics, the join, one
search, and three company features (§8). The plan of task 3491 adds the condense
note (§16), the teaching of many chats (§17) and pin (§18).

**Not in scope:**
- The file library. `artifact_management.md` owns it.
- Moving or splitting turns between chats. No split exists, and this spec adds
  none. §7.3 undoes a join another way.
- A join in a group chat. Only a solo chat joins.
- A second index. Search lands in MEM-2 (D132.3).
- Reading a private chat for a company view. §8.3 reads no chat.
- RESUME. It continues one run that waits at an approval card, and it is not
  "pick up an old chat" (§17.1).

## 3. What exists (verified 2026-11-16)

A gateway path is under `apps/services/gateway/gateway/`. A `src/` path is under
`workbench/control_plane/`.

| Thing | Where | What this spec does with it |
|---|---|---|
| `chat_session`, `chat_message`, `member_summary` | `infra/postgres/02_chat_history.sql`, migration 319 | Adds the tables of §10 |
| The summary store, the one reader and writer of `member_summary` | `assistant_summaries.py` | §16 reads typed words through it |
| The summary job (PZ-33b). **BUILT, dark** | `chat_summary_job.py`, kind `assistant.chat_summary` | CO-1 extends it as the one pass |
| The typed mark | `chat_message.member_typed`, migration 337 (D-PZ-37) | Each summary and each note reads typed rows only |
| The briefing (PZ-34). **BUILT** | `assistant_summaries.recent`, flag `ASSISTANT_BRIEFING_ORGS` | Rung 1 fills it. CO-18 loads the note beside it |
| The job worker | `jobs/worker.py`. `job_queue` defaults to False (`packages/acb_common/acb_common/settings.py:525`) | CO-10 turns it on for HathiLabs |
| The continue route (WT-1). **BUILT, dark** | `POST /chat/sessions/{id}/continue`, `continue_session`, `routes/chat.py:1510`. Flag `CHAT_CONTINUE_ORGS` | CO-15 adds a carry mode (§17.1) |
| The link of a continued chat | `chat_session.continued_from`, migration 345, `ON DELETE SET NULL`. Fence `test_continued_from_readers.py` | CO-5 and CO-13 read it. Only the WT-1 route writes it |
| The pill of a held chat (WT-2). **BUILT, dark** | `src/lib/chatContinue.ts`, `src/components/ContinueOffer.tsx`, drawn in `AgentChat.tsx` | The one caller of the route today |
| Grouping the chat list (CO-0) | `dateGroups:78`, `DATE_GROUPS:71`, `splitSessions:151`, `src/lib/shell/chatHistory.ts`. `src/lib/shell/SessionPicker.tsx` draws it | Pinned, then topics, then dates |
| Client search of title and preview | `matchesQuery`, `chatHistory.ts:133` | Replaced by the server search (CO-3) |
| Rename and archive | `PATCH /chat/sessions/{id}` (`routes/chat.py:1157`), `archived_at` (migration 254) | Pin sits beside these acts in the list |
| The pin (CO-11). **BUILT, dark** | `chat_session_member` (migration 450), `gateway/chat_session_member.py`, `PUT` and `DELETE /chat/sessions/{id}/pin`. Flag `CHAT_PIN_ORGS` | CO-2 adds the topic to the same row |
| Delete | `DELETE /chat/sessions/{id}` (`routes/chat.py:1230`). The picker offers Delete on an archived chat only. The route has no such check | §16.5 clears a note |
| The title | The client sets it once: the title that a chat brings, such as the day's brief, or else the first typed message. It cuts it to 60 characters (`src/lib/sessions.ts:762`) | CO-1 writes a better title |
| The rule for bar words | `barMayJoin:768`, `startsFresh:751`, `STALE_CHAT_MS:743`, `src/lib/shell/assistant.ts` | §7 widens it, only on a confident match |
| The solo test | `SOLO_ROOM_SQL`, `rooms.py:590` | The join filter |
| Removing the last turn | `POST /chat/sessions/{id}/supersede`, `routes/chat.py:1394` | The undo of §7.3 |
| Card outcomes (HRD-7) | `ask_tools.CARD_OUTCOMES`, `packages/acb_skills/acb_skills/ask_tools.py:1473` | CO-17 stores an approved card (§16.3) |
| Records that name their chat | `artifacts.session_id` (migration 268), `reminders.source_session_id` (migration 307) | §16.2 links them |
| The context ring | `src/components/ContextRing.tsx`. Its colour changes at 60 % | CO-14 puts the length cue beside it |
| The guide | `packages/acb_common/acb_common/app_manifests/guides/*.json`, `src/lib/shell/guide.ts` (D167) | CO-12 adds the hints |
| A label job, shadow mode, a change check, a daily cap | `crm_sync/note_labels.py` | The pattern for CO-1 and CO-4 |
| Knowledge gaps: different askers, masked text, no model | `routes/knowledge/gaps.py`, `kb_gaps` (293) | §8.3 shows it |
| Teams | `src/lib/centers.ts` | Seeds the company list |

## 4. The decisions (D163)

`work_plan.md` §3 records these. The owner accepted the agent's proposal.
`work_plan.md` numbers D163 in 9 items, and this list groups them in 8. So a
reference "D163.n" in this spec means item n in `work_plan.md`.

1. **Company list, private contents.** One short list for the organization,
   seeded from its teams. Which chats sit in a topic is private to whoever can
   see the chat.
2. **A new topic starts private.** It belongs to the one member. A name made
   from a private chat must not reach a colleague.
3. **Three outcomes.** Join, a new chat that carries context, or a clean chat.
4. **Records first.** The same project, client or task outranks similar words.
5. **Corrections teach.** A member who often presses ✕ gets fewer joins.
6. **Metorite absorbs the pass.** It never debits a company's credits, and a
   member sees no charge. This changes the credit note of D142.9. The flip stays
   owner-only.
7. **Measure first.** Shadow, then Fracktal, then every organization.
8. **Three company features:** a pointer to a shared chat, a suggestion to save
   to Knowledge, and the gap count (§8).

D163 does not cover §15 to §20. They stay PROPOSED until the owner answers §19.

## 5. The idle-chat pass

- **When.** A chat with at least 2 turns, idle for 30 minutes, with no live run
  (PZ-33's idle rule). A change check skips a chat that has not changed.
- **What.** One small-model call returns the summary, the topic, a title and the
  records named. One embedding goes through `gateway/kb_index/embed.py`.
- **The member's switch.** With the D142 switch off, the pass skips the chat. It
  then has no topic, sits under the date groups, and never joins.
- **Forget and offboarding** delete the topic row and the embedding with the
  summary (D142.3).
- **No blocker on numbers.** PZ-33b built the strict digit rule, and its
  question on numbers has a default that stands (`personalization.md` §15.12
  item 6). CO-1 waits only for rung 1, because the job worker must run first.

## 6. Topics

- **The list.** It starts from the teams in `centers.ts`, plus General. An admin
  can rename, merge or hide a company topic.
- **Sorting.** Every chat gets a topic with no question. A chat that fits nothing
  gets a private topic, or General.
- **Correcting.** The chip in the chat header moves a chat. A moved chat stays
  moved, and the pass never overrides the member.
- **Promotion (CO-6).** When 3 or more members hold a similar private topic, an
  admin sees "Add to company topics". The admin sees the count, and never who.
- **A shared chat** carries its topic to each member who can see it.

## 7. Picking up a chat

### 7.1 The outcomes

| Match | Outcome |
|---|---|
| Same record or same subject, last message within 14 days, a chat that may join | **Join.** The chip reads "Continuing: <title> ✕" |
| Related, older, or a chat that may not join | **Carry.** A new chat holds the old summary and shows "Related: <title>". A carry from a HELD chat copies no summary (`one_assistant_context.md` §9). CO-5 reads `chat_session.continued_from` and never writes it. |
| Unsure between join and carry | **Ask.** "Same as <title>? Continue there" |
| Nothing | **Clean.** Nothing shows |

The 14 days and the score bands are defaults. CO-4 tunes them. §17.1 adds the
preview to the chip.

### 7.2 What may join

A chat joins only when all of these hold:

- It is solo (`SOLO_ROOM_SQL`).
- It is not archived.
- It has no live run and no open `chat_pending_ask`.
- It fills less than 75 % of the context window.

Any other match carries.

The match runs only on the first message of a new chat. It reads stored data and
calls no model. A message typed into an open chat never moves.

### 7.3 The undo

- **Before the send.** ✕ on the chip. This costs nothing.
- **After the reply.** "Start a new chat instead" removes the turn with
  `supersede`, then sends the same words to a new chat. It costs a second run.
  What the first run did stays done, and the notice names any file it made.

The server writes the join notice (D-PM-39).

## 8. For the whole company

1. **A shared chat (CO-7).** "Priya has a shared chat on this" shows only for a
   chat already shared with the member. A private chat never shows.
2. **Save to Knowledge (CO-8).** When a chat ends in a decision or a finished
   file, the assistant suggests the act. A person does it (D104).
3. **Knowledge gaps (CO-9).** KB-9a already counts different askers and masks
   figures. It reads no chat and no summary, so D142.2 stands. This slice shows
   a gap at 5 askers or more, with no names. An admin sees it in Knowledge.

## 9. The full-screen chat (CO-0)

**BUILT** (PR 1607, 2026-11-02). The PR shipped the header row, the centred new
chat, the key hint in the tooltip of Send, and the date groups. Its commit names
no change to the model picker, the context ring or the reading column.

- **One header row.** Title, topic chip, Files, Share and the window controls.
  The "● Metorite" row goes.
- **A new chat** centres the composer, with the suggestions under it.
- **The composer** has one model picker with a plain name. The key hint moves to
  a tooltip, and the context ring shows from 60 %.
- **A reading column** of 720 px.
- **The chat rail** groups by date until topics ship. A dot marks a live run or
  an unread reply.

The prototype of 2026-11-02 shows this layout.

## 10. Data

Next free migration numbers at build time (R1). Expand only (R6). Each table
holds `organization_id`, has FORCE RLS (R5), and joins `test_tenant_coverage.py`.

- `chat_topic`: `id`, `organization_id`, `name`, `scope` (`company` or `member`),
  `owner_email`, `hidden_at`. A member reads company topics and their own.
  CO-2a built it (migration 451) with `created_at` and UNIQUE
  `(organization_id, id)`, the key of the CO-2b foreign key. One name is unique
  in each list, in any case: the org, the scope and the owner. RLS covers the
  org. `gateway/chat_topic.py` is the one module that runs SQL on it, and it
  holds the member rule in each statement. The member purge deletes the member
  topics of a leaver.
- `chat_session_member`: `(session_id, member_email)` PK, `organization_id`,
  `topic_id` (nullable), `set_by` (`auto` or `member`), `pinned_at` (nullable).
  It is the per-member row of a chat: its topic (CO-2) and its pin (CO-11). The
  D163 draft named it `chat_session_topic`. CO-11 built the table (migration
  450) with `pinned_at` only. CO-2 adds `topic_id` and `set_by` as nullable
  columns, in its own migration (an expand step, R6). `member_email` is the
  address in lower case. `gateway/chat_session_member.py` is the one module
  that runs SQL on it, and the member purge deletes the rows of a leaver.
- `chat_session_digest`: `session_id` PK, `organization_id`, `content_hash`,
  `entities JSONB`, `embedding`, `digested_at`. §10.1 reconciles it with CX-4.
- `chat_join_log`: the shadow record of CO-4: the session, the candidate, the
  outcome and the override.
- `chat_topic_note` (CO-16): one row for each version of a note. `id`,
  `organization_id`, `member_email`, `topic_id`, `version`, `focus`, `body`,
  `sources` (session ids only, never a title), `source_deleted_at`,
  `created_at`. The newest version is the note. A member reads only their own.
- `chat_card_record` (CO-17): `id`, `organization_id`, `session_id`,
  `member_email`, `outcome`, `act_kind`, `record_link`, `created_at`. It holds
  no text that a model wrote. A Delete of its chat deletes it.

### 10.1 Reconcile before CO-1, CO-3 or CX-4 builds

- **One idle pass.** Three plans run a pass over an idle chat. They are PZ-33b
  (built), CO-1 (§5) and CX-4 (`company_context.md` §8.1). CO-1 extends the
  PZ-33b job and is the one pass. CX-4 reads its row and adds no second pass.
- **One digest table.** `chat_session_digest` here and `chat_session_digests` in
  CX-4 are one table. The first slice that builds it names it for both specs.
- **One source rule.** The summary job reads typed words only (D-PZ-37), after
  5 review rounds of PZ-33b. CX-4 §8.1 says that its atoms catch what the member
  decided with the assistant. A CX-4 digest that reads assistant turns needs an
  owner decision first.
- **`chat_join_log` has no twin.** It is the shadow record of CO-4 only. CX-4
  plans no join log, so nothing reconciles with it.
- **One search.** CO-3, HRD-21 (D-FIND-5) and the `chat` source of CX-4 all read
  past chats. HRD-21 builds first and stays narrow. CO-3 builds on the same
  source and adds no second index (D132.3). If CO-3 needs rooms or chats that
  are not solo, that is the wider scope of CX-4 (`find_engine.md` §14 item 6),
  not a wider HRD-21. `find_engine.md` gives the chat search index of HRD-21
  to CX-4 already.

## 11. The slices

| Slice | Task | Rung | Work | Gate | Done when |
|---|---|---|---|---|---|
| CO-0 | none | — | The full-screen chat (§9). **BUILT**, PR 1607 | AGENT-SAFE | The four captures pass, and `/chat` and the dock's full view match |
| CO-1 | 3500 | 2 | The pass (§5), metered to the platform | **OWNER-GATE** for the flip. HIGH tier: money | A test run on Fracktal reports the real cost per chat, and no `usage_event` debits the company |
| CO-2 | 3501 | 2 | Topics store, rail and chip (§6, §10). **CO-2a BUILT, dark** (2026-11-16): the table, `/chat/topics` and the purge, flag `CHAT_TOPICS_ORGS`. CO-2b: the topic of a chat, the move, the rail, the chip and the seed. It waits for CO-11 (task 3510, not merged) | AGENT-SAFE, dark. HIGH tier: new tables | A member's move survives the next pass, and a private topic never reaches a colleague (R8 test) |
| CO-3 | 3502 | Later | One search of chats and files, on MEM-2. Reconcile first (§10.1) | AGENT-SAFE, dark | A word inside a chat and a word inside a file both return, and a colleague's private chat does not |
| CO-4 | 3503 | 3 | Shadow matching: no UI, `chat_join_log` | AGENT-SAFE, dark | One week of Fracktal data shows the outcomes it would have taken |
| CO-5 | 3504 | 3 | The chip with a preview, join, carry and undo (§7, §17.1) | **OWNER-GATE** for the flip | Each row of §7.1 and each bar of §7.2 has a test, and the override rate is under 10 % on Fracktal |
| CO-6 | 3505 | Later | Promotion to company topics | AGENT-SAFE, dark | The admin card shows the count and no name |
| CO-7 | 3506 | Later | The shared chat pointer | AGENT-SAFE, dark | A private chat never shows (R8 test) |
| CO-8 | 3507 | Later | The Save to Knowledge suggestion | AGENT-SAFE, dark | The suggestion saves nothing by itself |
| CO-9 | 3508 | Later | The gap count (§8.3) | AGENT-SAFE. The flag is `KNOWLEDGE_GAPS` | A gap under 5 askers does not show |
| CO-10 | 3509 | 1 | Turn on the job worker and chat summaries for HathiLabs (§19 question 1) | **OWNER-GATE**. `ASSISTANT_CHAT_SUMMARY_ORGS` is owner-only (D-PZ-35) | An idle chat on production has a stored summary, and the next new chat shows it |
| CO-11 | 3510 | 2 | Pin (§18). **BUILT, dark** (2026-11-16), flag `CHAT_PIN_ORGS`. Chat parity: a follow-up slice, CO-11b (task 3563), needs cal-tasks. The flip waits for §18, §19 question 8 and the parity ceiling (§18) | AGENT-SAFE, dark | A pin shows on a second device, a colleague sees none, and an archive unpins |
| CO-12 | 3511 | 2 | First-run hints and the empty state (§17.2, §17.3) | AGENT-SAFE | Each hint shows once per member, and the empty state shows no private topic of a colleague |
| CO-13 | 3512 | 3 | The continue mark (§17.5). **BUILT** (task 3512, 2026-11-16) | AGENT-SAFE | A continued chat shows its source, and `test_continued_from_readers.py` stays green. That fence pins three things. No Python file outside the allow list names the column. No Python file outside `routes/chat.py` names `continuedFrom`. The dock names `continuedFrom` once |
| CO-14 | 3513 | Later | The length cue (§17.4). It waits for HRD-19 (task 2998) | AGENT-SAFE | The cue shows at 60 % and not below, and a tap runs Start clean |
| CO-15 | 3514 | 3 | The carry door: a carry mode of the WT-1 route (§17.1) | HIGH. AGENT-SAFE, dark. The §9 owner agrees first | The R8 cases of `test_continue_carry.py` pass for both modes, and a held source carries no summary |
| CO-16 | 3515 | 4 | The condense note: pick, build, preview, edit, versions, Delete (§16.1, §16.2, §16.5) | HIGH: a new table, chat words to a model. AGENT-SAFE, dark | A pasted mail and an assistant reply never reach a note. A room and an `off` chat are never a source. A deleted source leaves no text in any version |
| CO-17 | 3516 | 4 | The card record (§16.3) | HIGH: a migration. AGENT-SAFE, dark | An approved card writes one row with no model text, a declined card writes none, and a colleague reads none (R8) |
| CO-18 | 3517 | 4 | The note in a new chat of its topic (§16.4) | AGENT-SAFE, dark | A new chat in the topic answers from the note and cites a source by its link only. A chat in another topic does not load it. No session store holds the note |

### 11.1 The ladder (D211)

Each rung ends in a demo on production, for HathiLabs. Each slice blocks the demo
task of its rung, and each demo task blocks the next one.

- **Rung 0** (task 3491): this epic, the tasks, and the drift fixes of this
  spec.
- **Rung 1, "new chats remember my recent chats"** (demo task 3496): CO-10. The
  briefing is live already, so it starts to carry real summaries. Demo: a new
  chat knows what the owner discussed yesterday.
- **Rung 2, "my chats sort themselves"** (demo task 3497): CO-1, CO-2, CO-11 and
  CO-12. Demo: the list shows topics and pins.
- **Rung 3, "it offers to continue"** (demo task 3498): CO-4, CO-15, CO-5 and
  CO-13. Demo: a first message about an old topic offers its chat.
- **Rung 4, "condense and summarize"** (demo task 3499): CO-17, CO-16 and CO-18.
  Demo: 4 chats condense into a note, and a new chat in that topic uses it.
- **Rung 5, "find any past chat":** HRD-21 (task 3225) and its demo FIND-D2 (task
  3245), owned by `find_engine.md`. The note and the offer cite its hits.
- **Later:** CO-3, CO-6, CO-7, CO-8, CO-9 and CO-14.

D163.8 still binds a flip for every organization: shadow, then Fracktal, then
everyone.

## 12. Verification commands

```bash
# Run the DB first (R8)
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run python scripts/affected_tests.py --run

# The seams that the new slices touch
uv run pytest tests/unit/test_continued_from_readers.py tests/unit/test_chat_summary_private.py tests/unit/test_continue_carry.py tests/unit/test_tenant_coverage.py -q

# Frontend, in workbench/control_plane
npx tsc --noEmit && npx vitest run src/lib/shell

# Visual: the visual-review skill over /chat and the dock's full view
```

## 13. Fences

Named at build time, per slice (R7). Until each slice builds, its rule is
advisory. Each new table joins `test_tenant_coverage.py`.

These fences exist on `main` and bind the new slices:

- `tests/unit/test_continued_from_readers.py`: only the WT-1 route, the session
  list and the CO-5 chip may name `continued_from`. No TypeScript file may name
  it. CO-5 adds its row. CO-13 reads through the session list.
- `tests/unit/test_chat_summary_private.py`: only `assistant_summaries.py` names
  `member_summary`, and only the modules on its `IMPORTERS` list import that
  module. CO-1, CO-15, CO-16 and CO-18 add their entries there, in review.
  `routes/chat.py` never joins the list.
- `tests/unit/test_continue_carry.py` (R8): the carry of WT-1. CO-15 adds the
  cases of its carry mode.

CO-11 adds two fences:

- `tests/unit/test_chat_pin.py`: only `gateway/chat_session_member.py` and
  the member purge name `chat_session_member` in Python, and no file of the
  control plane names it. The pin selects a chat that the member can read and
  that is not archived. Each statement filters on the member. With the flag
  off, the routes answer 404 before any SQL.
- `tests/unit/test_chat_pin_r8.py` (R8) runs the cases on a real database.
  They are a second device, a colleague in the same room, an archive and a
  chat that the member cannot read. They are also two orgs, an unpin, a
  Delete and the member purge.

CO-2a adds two fences:

- `tests/unit/test_chat_topic.py`: only `chat_topic.py` and the member purge
  name `chat_topic` in Python, and no TypeScript file names it. Each read holds
  the member rule, and each write holds the write rule. The seed names are the
  teams of `centers.ts` plus General. With `CHAT_TOPICS_ORGS` off, each route
  answers 404 before any SQL. A body that names an owner, a scope or an org
  gets 422.
- `tests/unit/test_chat_topic_r8.py` (R8): no colleague and no admin reads the
  topic of a member. Org B never reads org A, and only an admin changes a
  company topic. The store refuses a duplicate in one list, and the purge
  deletes the member topics. FORCE RLS holds, and the flag off gives 404.

## 14. Risks

- **Context cost.** Each joined turn resends the window, so §7.2 bars a full chat.
- **Compaction is client-only**, and the route has no Router metering. A join
  must not depend on it.
- **Sticky tool packs.** A join inherits them (`tool_packs.py`), which suits a
  join and needs a test.
- **A wrong join after a reply** costs a second run. CO-4 sets the bar to keep
  these rare.
- **Summary drift.** A summary of summaries drifts. The note keeps its sources,
  and each re-run shows a diff (§16.1).
- **A deleted chat that lives on.** In other products, a deleted chat keeps its
  memory, and members expect otherwise (§20). §16.5 clears the note.

## 15. Many chats made easy (PROPOSED, task 3491)

### 15.1 The owner's ask (2026-11-16)

Task 3491 holds the ask, from chat:

- Chats sort under headings when a member has many.
- A member knows, with no lesson, to use several chats, and to pick up an old
  chat when that is better than a new one.
- The assistant learns from all chats and uses them as context for new chats.
- A member can say "condense and summarize" over a series of chats, and the
  result helps later chats.

The agent researched how other chat apps do this (§20). This plan extends D163
and adds no second spec of the same topic.

### 15.2 What exists (audit of 2026-11-16)

| Capability | State | Where |
|---|---|---|
| Chat list by date, rename, archive, Delete of an archived chat, client search | LIVE (CO-0) | §3 |
| A summary of each solo chat (PZ-33a, PZ-33b), and its Settings controls (PZ-35) | BUILT, dark | `personalization.md` §15.3 and §15.5 |
| The briefing: the newest 3 summaries and the due tasks (PZ-34) | LIVE, with no summaries | `personalization.md` §15.4 |
| The continue route and its pill (WT-1, WT-2) | BUILT, dark | `one_assistant_context.md` §9 |
| Topics, the chip, join or carry, the idle pass, server search | SPEC ONLY | §5 to §7 |
| Past chats as a search source (HRD-21, task 3225) | SPEC ONLY | `find_engine.md` D-FIND-5 |
| Start clean (HRD-19, task 2998) | SPEC ONLY | `chat_failure_modes.md` E3 |
| Pin (CO-11) | BUILT, dark (task 3510) | §18 |
| First-run hints, an offer with a preview, condense | ABSENT | §16 and §17 |

The audit of task 3491 read production on 2026-11-16. `ASSISTANT_BRIEFING_ORGS`
is `*` there. `JOB_QUEUE` and `ASSISTANT_CHAT_SUMMARY_ORGS` are absent, so no job
worker runs and no summary exists. So the briefing shows only due tasks.

### 15.3 What this plan adds

1. The condense note (§16).
2. The teaching of many chats (§17). It holds the offer with a preview, the
   first-run hints, the empty state, the length cue and the continue mark.
3. Pin (§18).
4. The ladder (§11.1), the slices CO-10 to CO-18 (§11), and the owner questions
   (§19).

## 16. The condense note ("condense and summarize these chats")

### 16.1 What the member does

- The member picks chats from the chat list, from a topic, or by asking in chat.
  The picker shows only the chats that §16.2 allows. An optional focus line
  says what matters.
- The result is ONE note, visible and editable. Its shape is fixed: goal,
  decisions, open questions, constraints, next steps, and the source chats with
  links.
- The member sees a preview before the save. The member can edit the note, run
  it again and see a diff, and open each earlier version.
- The note is the member's private brief of one topic (CO-2). Only that member
  reads it (D142.2). It is not a Knowledge page (§19 question 4).
- The originals stay. A condense never deletes, archives or moves a chat.

### 16.2 The source rule

This rule binds CO-16 and CO-18. It says which chats may be sources, and what
a note reads from them.

**Which chats.** Each source chat passes `ELIGIBLE_SQL`
(`assistant_summaries.py:1022`), the rule of the summary job today:

- It is a chat with Mira on the web (`channel IS NULL`), so no WhatsApp chat.
- It is not archived.
- It is not `off` (PZ-35).
- No other agent is in the chat.
- It is solo (`SOLO_ROOM_SQL`).

The reason is `_is_member_row` (`assistant_summaries.py:206`). It assumes a
solo chat, and it counts a row with an empty `author_email` as the member's
own. So in a room, a note could take the words of another member. Until the
owner answers §19 question 7, an explicit pick does not override `off`.

**What a note reads.** Three sources and nothing else:

1. **The member's own typed words.** These are the rows with the typed mark
   (`chat_message.member_typed`, D-PZ-37), read through `assistant_summaries.py`.
   Its `member_texts` and `prompt_rows` choose the rows, and `typed_text` drops
   a pasted mail.
2. **The cards that the member approved** (§16.3). The note uses the outcome
   key, the act kind and the record link. It never uses a card title or detail.
3. **The records created: the link and the kind only.** Today two tables name
   their chat: `artifacts.session_id` and `reminders.source_session_id`. The
   note never reads the title or the body of a record. The assistant writes the
   title and the body of an artifact. A reminder made from a mail can hold the
   subject of its sender. The member opens each link as the member. A
   record with no chat link stays out until its app writes one.

Later, the decisions store of HRD-12 (task 2991) joins as a fourth source.

A note **never** reads the assistant's free text, a tool output, or text that a
third party wrote. The evidence is PZ-33b. Its reviews held the slice for 5
rounds, because stranger text could reach a stored summary. One case was a
pasted mail that a reply restated. A note over many chats keeps the rule of one
summary.

The SQL that reads the source rows lives in `assistant_summaries.py`, beside
`read_job_chat`. So no second reader of chat rows starts. HRD-21 is search, not
condense (§17.6).

### 16.3 The card record (CO-17)

Main has no durable record of a card outcome:

- `ask_tools.last_card_outcome()` (`ask_tools.py:1556`) reads a context variable
  of one run.
- The outcome text of HRD-7 reaches the chat only inside a tool result, in
  `chat_message.tool_events`.
- The card event `confirmation_requested` in `custom_events` holds a title and
  a detail that the model can shape. `assistant_summaries.py` drops a card
  answer for the same reason.

So CO-17 writes one row when a write card closes as `approved`. The row holds
the session, the member, the outcome key, the act kind and the record link, and
no text. The note reads the row again as the member. CO-17 checks its overlap
with HRD-12 before it builds.

### 16.4 Later chats in the topic (CO-18)

- A new chat in the topic loads the note, beside the PZ-34 briefing. The note
  holds about 400 words at most.
- The note goes in as instructions, through `RunContextProvider`
  (`apps/services/orchestrator/orchestrator/_native_run_context.py`). It never
  goes in as an input message, because `native_session_store.py:180` stores
  the inputs of a run. No history provider stores instructions, and no chat
  row stores the note.
- **A source on demand is a link only.** The assistant cites a source chat by
  its link, and the member opens that chat. No tool reads the text of a source
  chat into a later run. A tool result stays in the later chat, in
  `chat_message.tool_events` (`chat_fold.py:268`) and in its stored native
  session, and a Delete of the source reaches neither.
- With the member's switch off (PZ-35), a new chat loads no note. The member can
  still condense and read the note.

### 16.5 Delete, Forget and offboarding

- **A Delete of a source chat** clears the body of each note that names it, and
  of each earlier version. A note is a blend, so no code can take out one
  chat's part.
- **What the note keeps.** Its focus line and the links of the other sources. It
  shows "A source was deleted", offers Re-run, and keeps NO text of the deleted
  chat (§19 question 3).
- **The Delete dialog** names each note that clears, as it names the files that
  stay (D162.8).
- **What a Delete reaches.** The note and each of its versions, at once. CO-18
  loads the note with no cache, so the next new chat reads the cleared note. A
  stored PZ-34 block can live up to 24 hours after a Delete (D-PZ-38), and the
  note takes no such store. The note goes in as instructions, which no session
  store keeps. The read on demand carries no source text (§16.4).
- **What a Delete does not reach.** A later chat that already used the note
  keeps what it wrote. That is its reply, and the reasoning of that turn
  (`chat_message.reasoning`), which can quote the note. `agent_run.trace` also
  keeps the reply and the reasoning of an errored, cancelled or flagged run
  (`run_trace.py`). These belong to the later chat, as a quote does (§19
  question 3).
- **Forget and offboarding** delete each note of the member (D142.3).

### 16.6 Cost

A condense is one model call that the member asks for. It runs as the member
through the Router, as a chat turn does, and it draws the org's credits. D163.7
covers the idle pass only. §19 question 6 asks the owner.

## 17. Teaching many chats

### 17.1 The continue offer

The offer is the chip of CO-5 (§7.1) with a preview. It recommends and shows,
and the member decides. A message never moves before the member sees where it
goes.

- **The trigger.** The first message of a new chat, typed and not yet sent. The
  match of §7 runs on stored data, with no model call.
- **The preview.** The chip names the old chat: "Continue your earlier chat
  about <title>?". A tap shows its date, its topic and its PZ-33 summary. The
  summary shows only to its member (D142).
- **The send.** D163.5 stands. On a confident match the chip shows the
  destination before the send, and ✕ costs nothing. An unsure match asks.
- **Dismiss.** ✕ sends to a new chat. "Don't suggest for this topic" stops the
  offer for that topic, for this member. CO-4 counts each ✕ (D163.6).
- **Join** sends the words to the old chat. It needs no new route.
- **Carry** opens a new chat through the carry door, below.

**The carry door (CO-15).** The WT-1 route (`continue_session`,
`routes/chat.py:1510`) is built and dark. Its one caller today is the pill of a
held chat. The route does four things today (`one_assistant_context.md` §9.3):

1. It answers 404 unless `CHAT_CONTINUE_ORGS` holds the org. That flip is
   owner-only, because the new chat runs without the egress hold.
2. It puts the newest typed row of the old chat in the composer (item 2).
3. It copies no `member_summary` (item 5).
4. It is the only writer of `continued_from` (item 8).

So a CO-5 carry cannot use the route as it is. CO-15 adds a `carry` mode, and
the owner of `one_assistant_context.md` §9 agrees it first:

- The route makes the new chat and writes the link. It returns no text, so the
  member's new words stay in the composer.
- The old summary goes across once, at the carry, through
  `assistant_summaries.py`. It goes only from a source that is not held.
- The copy does not live in `routes/chat.py`. `test_chat_summary_private.py`
  fails when that module imports `assistant_summaries`, because it serves the
  chat list. CO-15 puts the copy in its own module and adds that module to
  `IMPORTERS`, in review.
- No context builder reads the old chat through the link after the carry.
- The flag of the mode is open: the CO-5 flag, or `CHAT_CONTINUE_ORGS`.

**RESUME is not this offer.** RESUME continues ONE run that waits at a card.
That is an approval card today. RESUME-1b (task 3434) adds the question cards.
RESUME-4a-2 adds a late "Not now", and it waits for D-RES-9 (both in
`chat_run_continuity.md`, the second on the branch of PR 2451). Reopening an old session and its history is native session persistence
(`maf_coding_engine.md` §15.9, `MAF_NATIVE_SESSIONS`).

### 17.2 First-run hints (CO-12)

Two hints show once per member. They use the guide seam of
`assistant_guide.md` (D167): a guide file `app_manifests/guides/chat.json` and
the app chapter of its §4.6. No second hint store starts.

1. One chat for one outcome: start a new chat for a new piece of work.
2. A topic for work that goes on over weeks.

The first-visit chip of an app chapter is GD-10 (`assistant_guide.md` §6), and
it is not built. Task 1464 (GD-10c) holds the chat chapter. CO-12 builds that
chapter for the chat, or waits for GD-10.

The words are product copy, so the user-facing copy rules bind them, not STE
(`docs/style_ste.md` §1). The slice sets the final words.

### 17.3 The empty state (CO-12)

An empty chat list shows the topics that the member can see, each as a place to
start a chat. These are the company topics and the member's own. No private
topic of a colleague shows (D163.2).

### 17.4 The length cue (CO-14)

When the context ring reaches 60 %, one plain line beside it offers "Start a
fresh chat that carries the decisions". The act is HRD-19 Start clean (task
2998). The line is information, so it uses no warning colour. CO-14 waits for
HRD-19 and adds no carry of its own.

### 17.5 The continue mark (CO-13)

A chat that continues another one says so in its header: "Continued from
<title>", with a link. The session list (`_get_sessions`, `routes/chat.py:129`)
returns the link. That function is on the allow list of
`test_continued_from_readers.py` already, and no TypeScript file names the
column. A source that the member cannot read shows "Continued from an earlier
chat", with no title and no link. A deleted source leaves no link.

**As built** (task 3512, 2026-11-16). `_get_sessions` adds `s.continued_from` to
its one statement. It returns `continuedFrom`, which is `null` for a chat that
continues nothing and for a deleted source. Otherwise it is
`{id, title}`.

A source is readable when it is a row of the same list, so the
rule is `SESSION_VISIBLE_SQL` and no second rule exists. A source that is not
in the list gives `{id: null, title: null}`. This covers a chat that the member
cannot read and a chat of another org. An archived source stays in the list, so
it keeps its title and its link.

The mark is a row under the header
(`src/lib/shell/ContinuedMark.tsx`). The link opens the source in the same
chat view. Tests: `test_continue_mark.py`, `test_continue_mark_r8.py` and
`ContinuedMark.test.tsx`.

### 17.6 Past chats in search (HRD-21)

HRD-21 (task 3225) is the `chat` source of `search_company`. `find_engine.md`
owns it. It reads only the member's own solo sessions in this org (D-FIND-5). It
never reads a room, a `no_egress` session, the current chat or the chat of
another member. A hit gives the title, the date and a link.

The continue offer and the note may reuse the link of a hit. This spec does
not widen HRD-21. Rooms belong to the wider scope of CX-4 (`find_engine.md` §14
item 6).

## 18. Pin (CO-11)

- Pin and Unpin sit on a chat row and in the chat header.
- A Pinned group shows over the topic and date groups.
- A pin is per member: `pinned_at` on `chat_session_member` (§10). A colleague
  in the same room sees no pin of the member.
- An archive takes the chat out of Pinned.
- Chat parity (task 1238): the same act works from chat.

**Built (CO-11, task 3510, 2026-11-16), dark.** `CHAT_PIN_ORGS` lists the orgs,
and the default is empty.

- **The store.** `chat_session_member` (migration 450). A pin inserts the row
  of the member, or sets `pinned_at` on it. A second pin keeps the first time.
  An unpin sets `pinned_at` to NULL and keeps the row, so the topic of CO-2
  can live in it.
- **Who may pin.** Any member who can read the chat, a room guest too. The pin
  selects the chat under the bound tenant and the visibility rule of
  `gateway/rooms.py`. A chat that the member cannot read answers 404.
- **The read.** `GET /chat/sessions` sends `pinned` and `pinnedAt` for the
  caller only. A colleague in the same room reads no pin of the member. With
  the flag off, the list sends no pin key.
- **The archive.** The read leaves out an archived chat, and a pin of an
  archived chat answers 409. So an archive of a room takes it out of Pinned
  for each member at once. The archive writes no pin row, so a restore shows
  the pin again (§19 question 8).
- **The UI.** The chat list shows Pin and Unpin on a row, and a Pinned group
  over the date groups, newest pin first. The list shows a pinned chat once. The
  header of the dock shows Pin on a desktop. On a phone with the chat-first
  flag, the chat's "…" control holds it. Without that flag, a phone has the
  act on the row only. The shell read serves the flag as `chat_pin`.
- **The purge.** The member purge deletes each row of the member in the
  bound org (`chat_member_rows`). A Delete of the chat deletes its rows.
- **Chat parity: not built.** No chat tool lists or opens chats today. The
  chat tool of the pin is a follow-up slice of P-7: CO-11b, task 3563. It
  can need a line in `tool_packs.py`, which the cal-tasks session owns. The
  two acts are debt rows of P-7 in `acb_skills/chat_parity.py`, and the debt
  ceiling of the chat goes from 25 to 27.

**Before the flag flip.** The owner does three things first:

1. Accept §18. It is still PROPOSED (§15 to §20).
2. Answer §19 question 8: does a restore show the pin again?
3. Accept the raise of the parity debt ceiling of the chat from 25 to 27.

## 19. Owner questions

Each question has the agent's recommendation. Nothing here is decided until the
owner answers on task 3491 or on the slice.

1. **Turn on the job worker and chat summaries for HathiLabs (CO-10)?**
   Recommend YES, after task 1640 gives the first-turn cost. Today the job
   draws the org's credits through the Router (`chat_summary_job._ask`). D163.7
   moves the pass to the platform only with CO-1.
2. **May a note ever use the assistant's text?** Recommend NO. Decisions come
   from structured records: approved cards and records created. PZ-33b is the
   evidence (§16.2).
3. **A source chat is deleted. Clear the note and offer a re-run, with no text
   of that chat?** Recommend YES. It matches D-PZ-33. The member loses their
   edits of that note, and the Delete dialog says so first. A later chat that
   already used the note keeps its words. §16.5 lists them: the reply, its
   reasoning, and the trace of a failed or flagged run.
4. **Is the note a private topic brief, or also a Knowledge page to share?**
   Recommend a private topic brief first. Sharing comes later, as an act of the
   member (D104).
5. **Is the continue offer on by default, and does a confident match join on
   the send?** Recommend on by default, with one-tap dismiss and "Don't suggest
   for this topic". Keep D163.5, so the chip shows before the send and ✕ is
   free. A tap in place of the send changes D163.5, so it needs the owner.
6. **Who pays for a condense?** Recommend the org, as a chat turn, because the
   member asks for it each time (§16.6).
7. **Does an explicit pick override `off`?** A chat that is `off` (PZ-35) is
   out of the summaries. Recommend NO. Such a chat cannot be a source, and the
   picker does not show it. A yes would change what the switch means (§16.2).
8. **Does a restore show the pin again?** CO-11 keeps the pin row through an
   archive, so a restore shows the pin again. Recommend YES. The archive is
   an act of the owner of the chat, and the pin is an act of each member. A no
   needs one more statement: the archive clears the pin of each member.

## 20. Sources

The research of 2026-11-16 (task 3491). "Read" means that the agent read the
page in full. "Snippet" means that the agent saw only a search result.

| Source | What it gave | How |
|---|---|---|
| ChatGPT Projects (`learn.chatgpt.com`) | A project holds instructions, files and chats | Read |
| Simon Willison on project-only memory | ChatGPT's memory mode for one project | Read |
| Claude help: Projects, memory, chat search, length limits | Memory for each project, search that cites chats, plain length cues | Read |
| Claude Code sessions (`code.claude.com`) | Resume or start new with a cost estimate, `/compact [focus]`, `/branch` | Read |
| Gemini notebooks, and the personal-context post | Notebooks, and Workspace accounts left out | Read |
| Microsoft Copilot chat history | "Move to notebook". A deleted chat keeps its memory | Read |
| Slack AI summaries | "More details" shows what a summary used | Read |
| Notion agent memories | Memory as an editable database | Read |
| Cloudscape conversational history | Date groups and the history pattern | Read |
| OpenAI help pages | "Branch in new chat" | Snippet. The site answered 403 |

**What we take.** Date groups and a container for work that goes on. An
editable note with its sources. A short brief that loads first, and a plain
length cue.

**What we do not take.** No product ships "condense these chats into a note"
from end to end, so §16 builds it from parts. No product found shipped a
"continue there?" offer, so §17.1 makes it a recommendation with a preview,
never a silent move.
