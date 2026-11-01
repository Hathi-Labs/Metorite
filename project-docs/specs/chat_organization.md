# Chat organization — topics, picking up a chat, and one search

> **Status:** ACTIVE (D151, 2026-11-02). Nothing is built. Verified against
> code on 2026-11-02 at `origin/main` `ce29836ea`.
> **Board:** WS-CO. **Owner of the file half:** `artifact_management.md` §6.0
> (D150). This spec owns topics, the join, and the full-screen chat.
> **Extends:** `personalization.md` §15 (PZ-33, D142), `navigation_shell.md`
> §7.1 and `knowledge_base.md` §13 (KB-9a). It replaces none of them.

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

## 1. What the owner asked for (2026-11-02)

"Categorize all the different kinds of chats ... into high-level buckets like
sales, finance, marketing." "This auto categorisation should be automatic."
"Instead of starting a new session, if it's ... a continuation of an old
session, we should append to the older session ... and smartly convey to the
customer ... and be able to override." The owner accepted §4 in chat: "I'm
happy with all of these."

## 2. Scope and non-goals

**In scope:** the full-screen chat, the idle-chat pass, topics, the join, one
search, and three company features (§8).

**Not in scope:**
- The file library. `artifact_management.md` owns it.
- Moving or splitting turns between chats. No split exists, and this spec adds
  none. §7.3 undoes a join another way.
- A join in a group chat. Only a solo chat joins.
- A second index. Search lands in MEM-2 (D132.3).
- Reading a private chat for a company view. §8.3 reads no chat.

## 3. What exists (verified 2026-11-02)

| Thing | Where | What this spec does with it |
|---|---|---|
| `chat_session`, `chat_message`, `member_summary` | `infra/postgres/02_chat_history.sql`, migration 319 | Adds the columns of §10 |
| The summary store. The job (PZ-33b) is not built | `gateway/assistant_summaries.py` | CO-1 builds the job as the one pass |
| Grouping the chat list | `splitSessions`, `src/lib/shell/chatHistory.ts:92` | Date groups, then topics |
| Client search of title and preview | `matchesQuery`, `chatHistory.ts:74` | Replaced by the server search |
| The rule for bar words | `barMayJoin:604`, `startsFresh:587`, `STALE_CHAT_MS:579`, `src/lib/shell/assistant.ts` | §7 widens it, only on a confident match |
| The solo test | `SOLO_ROOM_SQL`, `gateway/rooms.py:590` | The join filter |
| Removing the last turn | `POST /chat/sessions/{id}/supersede`, `gateway/routes/chat.py` | The undo of §7.3 |
| A label job, shadow mode, a change check, a daily cap | `gateway/crm_sync/note_labels.py` | The pattern for CO-1 and CO-4 |
| The recurring job | `gateway/jobs/worker.py` | CO-1 runs on it. `JOB_QUEUE` is off |
| Knowledge gaps: different askers, masked text, no model | `gateway/routes/knowledge/gaps.py`, `kb_gaps` (293) | §8.3 shows it |
| Teams | `src/lib/centers.ts` | Seeds the company list |

## 4. The decisions (D151)

`work_plan.md` §3 records these. The owner accepted the agent's proposal.

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

## 5. The idle-chat pass

- **When.** A chat with at least 2 turns, idle for 30 minutes, with no live run
  (PZ-33's idle rule). A change check skips a chat that has not changed.
- **What.** One small-model call returns the summary, the topic, a title and the
  records named. One embedding goes through `gateway/kb_index/embed.py`.
- **The member's switch.** With the D142 switch off, the pass skips the chat. It
  then has no topic, sits under the date groups, and never joins.
- **Forget and offboarding** delete the topic row and the embedding with the
  summary (D142.3).
- **Blocker.** PZ-33b has an open question on numbers in a summary. CO-1 waits
  for that answer.

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
| Related, older, or a chat that may not join | **Carry.** A new chat holds the old summary and shows "Related: <title>" |
| Unsure between join and carry | **Ask.** "Same as <title>? Continue there" |
| Nothing | **Clean.** Nothing shows |

The 14 days and the score bands are defaults. CO-4 tunes them.

### 7.2 What may join

A chat joins only when all of these hold: it is solo (`SOLO_ROOM_SQL`), it is not
archived, it has no live run and no open `chat_pending_ask`, and it fills less
than 75 % of the context window. Any other match carries.

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

- **One header row.** Title, topic chip, Files, Share and the window controls.
  The "● Metorite" row goes.
- **A new chat** centres the composer, with the suggestions under it.
- **The composer** has one model picker with a plain name. The key hint moves to
  a tooltip, and the context ring shows from 60 %.
- **A reading column** of 720 px.
- **The chat rail** groups by date until topics ship. A dot marks a live run only.

The prototype of 2026-11-02 shows this layout.

## 10. Data

Next free migration numbers at build time (R1). Expand only (R6).

- `chat_topic`: `id`, `organization_id`, `name`, `scope` (`company` or `member`),
  `owner_email`, `hidden_at`. FORCE RLS (R5). A member reads company topics and
  their own.
- `chat_session_topic`: `(session_id, member_email)` PK, `organization_id`,
  `topic_id`, `set_by` (`auto` or `member`).
- `chat_session_digest`: `session_id` PK, `organization_id`, `content_hash`,
  `entities JSONB`, `embedding`, `digested_at`. It must reconcile with CX-4's
  planned `chat_session_digests` first. One table serves both.
- `chat_join_log`: the shadow record of CO-4: the session, the candidate, the
  outcome and the override.

## 11. The slices

| Slice | Work | Gate | Done when |
|---|---|---|---|
| CO-0 | The full-screen chat (§9) | AGENT-SAFE | The four captures pass, and `/chat` and the dock's full view match |
| CO-1 | The pass (§5), metered to the platform | **OWNER-GATE** for the flip. HIGH tier: money | A test run on Fracktal reports the real cost per chat, and no `usage_event` debits the company |
| CO-2 | Topics store, rail and chip (§6, §10) | AGENT-SAFE, dark | A member's move survives the next pass, and a private topic never reaches a colleague (R8 test) |
| CO-3 | One search of chats and files, on MEM-2 | AGENT-SAFE, dark | A word inside a chat and a word inside a file both return, and a colleague's private chat does not |
| CO-4 | Shadow matching: no UI, `chat_join_log` | AGENT-SAFE, dark | One week of Fracktal data shows the outcomes it would have taken |
| CO-5 | The chip, join, carry and undo (§7) | **OWNER-GATE** for the flip | Each row of §7.1 and each bar of §7.2 has a test, and the override rate is under 10 % on Fracktal |
| CO-6 | Promotion to company topics | AGENT-SAFE, dark | The admin card shows the count and no name |
| CO-7 | The shared chat pointer | AGENT-SAFE, dark | A private chat never shows (R8 test) |
| CO-8 | The Save to Knowledge suggestion | AGENT-SAFE, dark | The suggestion saves nothing by itself |
| CO-9 | The gap count (§8.3) | AGENT-SAFE. The flag is `KNOWLEDGE_GAPS` | A gap under 5 askers does not show |

**Order.** CO-0 first, as it needs no decision and spends nothing. AM-2 can ship
beside it. CO-1 waits for the PZ-33b answer. CO-2 to CO-5 follow CO-1.

## 12. Verification commands

```bash
# Run the DB first (R8)
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run python scripts/affected_tests.py --run

# Frontend, in workbench/control_plane
npx tsc --noEmit && npx vitest run src/lib/shell

# Visual: the visual-review skill over /chat and the dock's full view
```

## 13. Fences

Named at build time, per slice (R7). Until each slice builds, its rule is
advisory. Each new table joins `test_tenant_coverage.py`.

## 14. Risks

- **Context cost.** Each joined turn resends the window, so §7.2 bars a full chat.
- **Compaction is client-only**, and the route has no Router metering. A join
  must not depend on it.
- **Sticky tool packs.** A join inherits them (`tool_packs.py`), which suits a
  join and needs a test.
- **A wrong join after a reply** costs a second run. CO-4 sets the bar to keep
  these rare.
