# Context — how every app feeds the AI, and keeps it right

**Status.** ✅ ACTIVE, owner directive 2026-10-18, decision **D106**. Board:
**WS-61**. This spec extends `knowledge_base.md` §20 (D105), which owns the
read seam. This spec owns what goes INTO the seam: the stamp, the recency
rule, the digest, the connectors, their health and the sweep.

**Read first.** `knowledge_base.md` §0, §6 and §20. `work_plan.md` D104 and
D105. This spec changes neither decision. It adds the rules that make the
answers of the seam current and findable.

---

## 0. The answer in one screen

1. **Retrieval, never stuffing.** The assistant asks `search_company` when
   it needs company facts. Nothing pastes the company into a prompt. This
   was true before this spec (§20.8), and it stays true.
2. **Every record carries a stamp.** A hit says when its record last changed
   in substance. That date is `stamp`. It is not the due date of a task.
3. **Newer outranks older, inside each app.** The seam lowers an old hit by
   a half-life that suits its kind. An old exact match still shows, but a
   new record on the same subject goes first. The model reads the stamp,
   and the grounding rule tells it that the newer record wins.
4. **A cheap model writes a digest for each record that earns one.** A
   digest holds a short summary and the questions a person types to find the
   record. It also holds the **atoms**. An atom is an exact number, a
   decision and its reason, an error and its fix, or a command. Code keeps an
   atom only when its exact text is in the record.
5. **The digest lives with its record, in the owning app.** Knowledge keeps
   it in `kb_chunks`. The email app keeps it in `email_insights`. A chat
   session keeps it in its own table. D105 stands: no central copy.
6. **Search the digest, then open the record.** The lexical index reads the
   digest, so a question in other words still finds the record with no
   vector. The hit points to the record, and the model reads it with the
   app's own get tool.
7. **Lexical first. Vectors are a second half that an eval must earn.** One
   golden-question eval measures every change to ranking, the digest and
   the vectors. A change that lowers its score does not merge.
8. **Every connector pulls only what is new, and reports its health.** A
   connector holds a cursor, reads with read-only scopes, and stamps what
   it brings. A connector that brings nothing for too long makes a card for
   an admin.
9. **Past AI conversations are a source.** A member can find what they
   decided with the assistant last month. A session digest makes that cheap,
   and the session's own participants are the only readers.
10. **The brain grows with a card, never in silence (D104).** An atom that
    records a decision becomes a proposal for a page. A person accepts it.

---

## 1. What the owner asked for (2026-10-18)

The owner pointed at a talk on personal knowledge bases. It is Nick Saraev,
"Build Your Personal Knowledge Base With AI (Step by Step)", YouTube, viewed
on 2026-10-18. The owner wrote that its concepts should guide the knowledge
base and the management of knowledge across all of Metorite. The owner asked
for a detailed document to build to.

### 1.1 The ideas in the talk, in our words

The talk describes the author's own system, which he calls Engram. It holds
some 35 000 to 50 000 records: git history, AI chat sessions and video
transcripts. These are its ideas, as this spec reads them.

| # | Idea | Why it matters |
|---|---|---|
| T1 | **Retrieval beats a full context window.** A model treats all context as equally true, and its accuracy falls as the context grows ("context rot"). A long context also costs more and runs slower | The answer is better and cheaper when the model searches first and reads only what it finds |
| T2 | **Recency weighting.** Stamp each record with a date when it arrives. Raise new records and lower old ones | An old price and a new price must not look equally true |
| T3 | **A periodic sweep** removes records that no longer have value | The pile stays useful as it grows |
| T4 | **A fast model picks the search words** before the search runs, and the top hits go into the prompt | One cheap step, then a search in under a second |
| T5 | **A connector for each source.** It reads only what is new since its last run, stamps it, and runs on a schedule. It has read-only access | The pile stays current with no manual work, and the reader cannot damage the source |
| T6 | **Index the AI sessions.** Chat tools delete them after a time | Decisions made in a conversation stay findable |
| T7 | **A cheap model writes metadata for each record**: a short summary that includes the question a person would ask, and "atoms", which are exact errors, fixes, commands and decisions with their reasons | Raw text is hard to search. The metadata makes implicit decisions explicit, which is institutional memory |
| T8 | **Search the metadata, then follow it to the raw record** | The model reads the whole record only when the summary matched |
| T9 | **Plain text search (BM25) is often enough.** The author reports that vectors cost more and did not answer better for his data | Start lexical, and add vectors only where they earn their cost |
| T10 | **A freshness check.** Keys rotate and permissions change, so a connector stops in silence | A stale pile gives stale answers with full confidence |

### 1.2 Where Metorite differs from a personal system

Engram is one person's pile. Metorite is a company with many members, and
each app owns who may read each row. So two ideas change shape here.

- **No central pile (D105).** Engram copies every source into one store.
  Metorite asks each app through its own search and its own visibility rule.
  The digest of T7 therefore lives in the owning app's table, not in a pile.
- **No silent writes into the company's knowledge (D104).** Engram files
  what it extracts. In Metorite, an extracted decision is a proposal that a
  person accepts.

Everything else in §1.1 applies as written.

---

## 2. What exists today — measured 2026-10-18

The survey read `main` at `a51f6e97e`. Each row names the principle of
§1.1 it serves.

| Principle | What exists | Where | Gap |
|---|---|---|---|
| T1 retrieval | `search_company` over the seam, called when needed. The index block of §6.5 is about 400 tokens | `gateway/retrieval/`, `acb_skills/company_search_tools.py` | The tool is dark behind `COMPANY_SEARCH_ENABLED` on production. Mem0, when on, injects a memory block on each turn |
| T2 recency | `kb_index.query.fuse` lowers a score by 10 % for each year since `valid_from`. Email and tasks use the date as a tie-break only. CRM sorts by `last_activity_at` only | `kb_index/query.py:46`, `routes/email/transport/search.py`, `routes/projects/search.py`, `routes/crm/records.py:605` | The seam's fusion ignores dates. The task hit's `when` is `due_at`, not a change date. People carry no date |
| T3 sweep | The KB sweep backfills vectors. My Day deletes judgments after 30 days | `kb_index/sweep.py`, `my_day_loop.py` | Nothing demotes or removes a stale record from retrieval |
| T4 query words | The assistant writes the tool's arguments itself | the tool call | None. This step costs no extra model call |
| T5 connectors | Gmail `history_id`, Outlook `deltaLink`, IMAP cursors, `crm_sync_cursors`, the WhatsApp `covered_through` watermark | `email_ingestion/scheduler.py`, `crm_sync/engine.py`, `routes/whatsapp/automation/groups.py` | No shared contract. KB-7 (Drive, OneDrive, Dropbox) is not built |
| T6 sessions | `chat_session` and `chat_message`, folded from the stream when a run ends. `query_history` is an ILIKE over the member's own messages | `gateway/chat_fold.py`, `acb_skills/history_tools.py` | No full-text index, no digest, not a source of the seam. Only the orchestrator has the tool |
| T7 metadata | `kb_chunks.distilled` exists and is always NULL. `email_insights` stores facts with a verbatim quote, and no job writes it. `crm_note_labels` is shadow only. `crm_digests` and `wa_group_summaries` summarise in their own tables | migrations 273, 231, 278, 281, 108 | Nothing writes a summary with questions and atoms for a record. KB-8 names `tier-cheap`, which does not exist |
| T8 summary then raw | The hit has a snippet and a link. `read_page` reads one section | `retrieval/sources.py`, `acb_skills/knowledge_tools.py` | No digest to search yet |
| T9 lexical first | Every source is lexical by default. Vectors are opt-in by flag | all sources | Postgres `ts_rank_cd` is not BM25, and it is good enough to start. The email embedder is broken (H-329) |
| T10 freshness | `integration_connections.status`, `last_sync_at` and `last_error`. Email accounts have `last_synced_at` and `sync_error`. CRM runs have a heartbeat | migrations 245, 17, `crm_sync/runs.py` | Nothing alerts when a connected source brings nothing new |

**Two hazards the survey found.** `mem0_memories` has no `organization_id`
and no RLS, and the scopes `org:global` and `agent:<name>` are the same in
every tenant (`saas_multitenancy.md` records it). Mem0 is off on every box.
It must stay off until WS-9 makes it tenant-safe. And `email_embeddings`
holds 0 rows, because its writer sends a key the gateway refuses (H-329).

---

## 3. The stamp

**A stamp is the date a record last changed in substance.** A hit carries
it as `stamp`, an ISO timestamp, beside `when`. `when` keeps its meaning: the
date a member cares about, such as a due date. The two can differ.

| Source | `stamp` | Note |
|---|---|---|
| tasks | `updated_at` | `when` stays `due_at` |
| email | `received_at` | |
| people | none | A directory entry does not age. The seam does not decay it |
| crm | `last_activity_at` | |
| artifacts | `last_activity_at` | |
| whatsapp | `sent_at` | |
| knowledge | the page's `updated_at` | §4.2 adds the review state |
| chat (CX-4) | the session's last message | |

**A source reads the stamp from the app's own row.** It adds no query. An
app search that does not return the column gains it in its own SELECT, in
the same slice.

**Knowledge stamps a chunk with `valid_from`.** The indexer sets it to the
day it writes a new chunk. A chunk whose text did not change keeps its row
and its date, so `valid_from` is the day that text was written. A document
from a source (KB-7) sets `valid_from` to the provider's modified date.

---

## 4. Recency — newer outranks older

### 4.1 Inside one app

The seam keeps each app's own order, and lowers old hits inside it. For the
hit at rank *r* (from 1) with age *a* days:

```
score = 1 / (60 + r) × max(FLOOR, 0.5 ^ (a / HALF_LIFE[app]))
```

The seam sorts each app's hits by `score`, then interleaves the apps as
D105 already does. Scores of two apps still never compare.

| App | `HALF_LIFE` (days) | Why |
|---|---|---|
| email | 90 | A thread is current for weeks |
| whatsapp | 60 | Chat moves faster than mail |
| tasks | 180 | A project lives for months |
| crm | 180 | A deal cycle is months |
| artifacts | 90 | |
| chat | 90 | |
| knowledge | none, see §4.2 | A page is true until someone changes it |
| people | none | |

`FLOOR` is 0.35. So an old exact match drops behind a new match, but stays
in the list. These numbers are defaults. The eval of §9 can change them, and
nothing else may.

**The command bar does not decay.** It is a projection of the registry for
titles, and `test_shell_search.py` stays unchanged. Recency applies to
`GET /search/company` and to its consumers.

### 4.2 Knowledge — authority, not age

A published page is the company's statement until someone changes it. Age
alone must not bury a policy that nobody needed to edit. So Knowledge
replaces the age decay with the review state.

| The page | Factor |
|---|---|
| Published, `review_at` in the future or empty | 1.0 |
| Published, `review_at` passed | 0.7 |
| From a source (KB-7), by the document's modified date | the age decay of `fuse` |

`fuse` keeps its 10 % a year for source documents only. A page that a
person published keeps 1.0 until its review date passes. KB-9 makes the
stale page a card for its author, which is the fix. The lower rank is only
the signal.

### 4.3 What the model reads

`search_company` prints the stamp on each hit, in the line it already
prints:

```
- «Price list 2026» (Page · Sales, changed 2026-10-02) /knowledge/pages/…
```

The grounding rule of `knowledge_base.md` §8.2 gains one sentence: "When two
records disagree, trust the newer one, and say which one you used." The
model sees the dates, so it can apply the rule. No code guesses which
record is true.

---

## 5. The digest — summary, questions and atoms

### 5.1 The shape

A digest is one JSON object that a cheap model writes for one record.

| Field | Content | Limit |
|---|---|---|
| `summary` | What the record is about, in plain words | 60 words |
| `questions` | Questions a member would type to find this record | 3 to 6, 15 words each |
| `atoms` | Exact facts, each with a kind | 0 to 12 |
| `model`, `made_at`, `source_sha` | Provenance | |

An atom has a `kind`, a `text` and, for a decision, a `why`.

| Kind | Example |
|---|---|
| `number` | "The refund window is 30 days" |
| `decision` | "We bill in INR only", why: "GST invoices need one currency" |
| `procedure` | "Restart the printer queue from Settings, then Devices" |
| `error_fix` | "Error E-204 on export: clear the cache, then export again" |
| `command` | `systemctl restart acb-gateway` |
| `contact` | "Meera owns renewals over 40L" |

### 5.2 The rules

1. **Code checks every atom against the record.** An atom of kind `number`,
   `error_fix` or `command` must quote text that is in the record, after
   the spaces fold. Code drops an atom that fails. This is rule D-EM-41 of
   the email insights, made general.
2. **The digest never replaces the record.** Search reads the digest. The
   answer quotes the record. `raw` stays.
3. **The digest follows its record.** The record's visibility is the
   digest's visibility. A digest of a draft is never written. When the
   record changes, its `source_sha` changes, and the next pass writes a new
   digest. When the record goes, the digest goes in the same transaction.
4. **The model is `tier-fast`.** It is the cheap tier that exists today. A
   `tier-cheap` alias may replace it later through the Router, and no caller
   changes. KB-8's text names `tier-cheap`, and this spec corrects it.
5. **One call per record version, and a daily cap per org.** The digest
   waits until the record has been still for 10 minutes. So a page in active
   edit costs one call, not twenty. A record under 80 words gets no
   digest, because its body is already short enough to search.
6. **The prompt is data, not instructions.** The record's text enters the
   prompt inside a marked block. The output is validated as JSON against
   the shape. A digest that fails validation is dropped, and the record
   stays searchable by its raw text.

### 5.3 Where each app keeps its digest

D105 rule 2 says an app adds recall in its own table. The digest follows it.

| App | Where | State |
|---|---|---|
| knowledge | One more row of `kb_chunks` for the page, marked `metadata.kind = 'digest'`, with the digest text in `raw`. The reindex of a page body leaves it alone | CX-3 |
| chat | `chat_session_digests`, one row per session, tenant RLS, readers from `chat_session_participant` | CX-4 |
| email | `email_insights` holds the atoms already, quote-checked. Its job (EM-T14b-2) belongs to the email workstream | email app |
| crm | `crm_digests` (weekly, per org) and `crm_note_labels` exist | CRM app |
| whatsapp | `wa_group_summaries`, one per group, with its `covered_through` cursor | WhatsApp app |

A source of the seam prefers the digest's summary as its `snippet` when the
row has one. So the model reads the summary first, and opens the record
only when it needs more (T8).

### 5.4 Why the digest comes before vectors

The questions in a digest put the member's likely words into the lexical
index. A query in other words then finds the record with no vector at all.
One `tier-fast` call per record version buys that. A vector costs one
`tier-embed` call per chunk and per question, and it still misses exact
names and numbers. So the order on production is: digests first, then
vectors where the eval shows they still help. The owner flips each one,
because each spends credits.

---

## 6. Connectors — only what is new, and healthy

### 6.1 The contract

Every reader that brings data from outside Metorite follows five rules.

1. **A cursor.** The connector stores where it stopped: a history id, a
   delta link, a page token or a watermark. A run reads only what came
   after it.
2. **Read-only scopes.** A connector asks for the narrowest read scope the
   provider has. A write scope belongs to an act a member asked for, never
   to a sync.
3. **A stamp on arrival.** Each new row records the provider's own date and
   the time Metorite received it.
4. **A schedule and a lock.** One run at a time per connection, on a fixed
   interval. The CRM run lock is the model (`crm_sync/runs.py`).
5. **A health record.** Each run writes its result to the connection's row:
   `last_sync_at`, `last_error` and `last_new_at`. `last_new_at` is the last
   time a run brought one or more new records.

The connectors that exist keep their own tables. This contract names what
each row must hold, and CX-5 adds the missing columns.

| Connector | Cursor | Health today |
|---|---|---|
| Gmail, Outlook, IMAP | `history_id`, `deltaLink`, IMAP UID | `email_accounts.last_synced_at`, `sync_error` |
| Zoho CRM | `crm_sync_cursors.cursor_state` | `crm_sync_runs` and `integration_connections` |
| WhatsApp | the webhook, and `covered_through` for summaries | none |
| Drive, OneDrive, Dropbox (KB-7) | the provider's change token | `integration_connections` |
| GitHub (WS-54) | the webhook, and the reconcile loop | `integration_connections` |

### 6.2 The freshness check

A connected source that brings nothing new for too long has usually broken
in silence: a rotated key, a revoked consent, a moved folder. The check is
code, with no model.

- Each connector type declares `expected_every`: the longest normal gap
  between new records. A mailbox is 2 days. A CRM is 7 days. A Drive folder
  is 30 days.
- The My Day loop's daily sweep reads each connection. A `connected`
  connection is quiet when `last_new_at` is older than 3 × `expected_every`.
  It is broken when its last 3 runs each wrote a `last_error`. Either state
  makes one card for the org's admins: "Gmail for meera@… has brought
  nothing new since 12 October."
- The card rides the WS-58 and WS-59 contracts as a new kind,
  `connector_quiet`. Reach delivers it at most once a week per connection.

---

## 7. The sweep — the pile stays useful

One sweep per app, each in the loop the app already runs. No new unit.

| What | Rule | Owner |
|---|---|---|
| Knowledge chunks of a deleted or archived page | Deleted in the page's transaction today. The sweep also deletes any chunk whose page row is gone | `kb_index/sweep.py` |
| Knowledge digests | Rewritten when the page's published body changes. Deleted with the page | CX-3 |
| Stale pages | Not deleted. Ranked down by §4.2, and made a card by KB-9 | KB-9 |
| Chat session digests | Deleted with their session. Follow the chat retention | CX-4 |
| My Day judgments | 30 days, as today | WS-58 |
| Artifacts | The retention sweep of `artifact_management.md` §7.4 | WS-60 |

A sweep never deletes a record a person wrote. It deletes only what was
derived from one: chunks, digests and judgments.

---

## 8. Past AI conversations as a source

### 8.1 The session digest

A pass writes a session's digest, with the shape of §5.1, when the session
is idle for 30 minutes and holds at least 4 messages. The atoms catch what
the member decided with the assistant. They also catch the fixes that worked
and the numbers that were agreed.

**What it skips.** It skips a session that ran with `no_egress` and a
session whose turns read mail bodies. It also skips each session of an org
that has not turned the digest on. These are the rules `_extract_run_memory` already applies to
Mem0 (`routes/agent.py`).

### 8.2 The source

`chat` joins the registry of `gateway/retrieval/sources.py`. Its search reads
`chat_session_digests` and a new full-text index on `chat_message.content`,
over the sessions where the member is a participant. In a room, a member who
joined later sees only what `query_history` lets them see today
(`since_join`). The hit links to the session.

The `chat` source is not in the command bar. A member finds a conversation
by its title in the sessions list, as today.

### 8.3 From atom to page

An atom of kind `decision` or `procedure` in a session digest is a
candidate for knowledge. When KB-5 is built, the pass may offer it as a
proposal: "You decided this with the assistant on 3 October. Make it a
page?" The member accepts, edits or dismisses it. Nothing is published
without them (D104).

---

## 9. The eval — the fence for every ranking change

**R7 needs a fence, and a ranking has no other honest one.** The eval is a
fixed set of questions over a seeded org, each with the record that must
come back.

- **The seed.** A test org on the R8 database, with pages, tasks, CRM
  records, emails and chat sessions written by the suite. No production data.
- **The questions.** At least 40. A third use the record's exact words, a
  third use other words, and a third ask for the newer of two conflicting
  records.
- **The measure.** Hit@5: the share of questions whose record is in the top
  5 of `GET /search/company`.
- **The rule.** A slice can change the stamp, the decay, the digest or the
  vectors. Then it runs the eval before and after, and writes both numbers in
  the pull request. A change that lowers hit@5 does not merge.
- **The fake.** The digest model and the embedder are deterministic fakes
  in the suite. The eval measures ranking, not a model's mood.

---

## 10. Tokens and cost

| Step | Model | Cost driver | Control |
|---|---|---|---|
| Search | none | database reads | D105 rule 3 |
| Recency | none | arithmetic | |
| Digest of a page | `tier-fast` | one call per published version, after 10 still minutes | `KNOWLEDGE_DISTILL`, owner flip, daily cap |
| Digest of a chat session | `tier-fast` | one call per idle session of 4 or more messages | `CHAT_DIGEST_ORGS`, owner flip, daily cap |
| Vectors | `tier-embed` | one call per new chunk and per question | `KNOWLEDGE_EMBED`, owner flip |
| Freshness | none | one read per connection per day | |
| The answer | the chat model | the hits it reads, about 1500 tokens at most | §20.8 |

The assistant's prompt grows by nothing. The digest moves work from every
question to one cheap call per record version.

---

## 11. Flags

| Flag | Default | What it opens | Gate |
|---|---|---|---|
| `CONTEXT_RECENCY` | on | §4 in `GET /search/company`. Off gives the D105 interleave unchanged | AGENT-SAFE. It spends nothing |
| `KNOWLEDGE_DISTILL` | off | §5 for Knowledge pages. Already named by `knowledge_base.md` §13 | owner, it spends credits |
| `CHAT_DIGEST_ORGS` | empty | §8 for the named orgs. A star is every org | owner, it spends credits and reads conversations |
| `CONNECTOR_FRESHNESS` | off | the `connector_quiet` card | AGENT-SAFE to build. MD-10's rule for the card kind |

---

## 12. Tickets

Each ticket ships dark behind §11, on its own branch.

| Ticket | What | Done when | Gate |
|---|---|---|---|
| **CX-1** | The stamp of §3 on every source, the decay of §4.1 in `company.py`, the review factor of §4.2 in `kb_index.query`, the stamp in the `search_company` line, and the one sentence in the grounding rule. Behind `CONTEXT_RECENCY` | Two tasks match a query with the same rank, one changed last week and one a year ago, and the new one comes first. An old exact match still shows. A page past its review date ranks below a current page with the same text. `test_shell_search.py` is unchanged and green. The flag off gives byte-identical output | AGENT-SAFE |
| **CX-2** | The eval of §9: the seed, 40 questions, the hit@5 report, as an R8 suite | The suite prints hit@5 for the seam with `CONTEXT_RECENCY` off and on, and fails when "on" scores lower than "off" | AGENT-SAFE |
| **CX-3** | Knowledge digests (replaces the text of KB-8). The digest pass in `kb_index/sweep.py`, the digest row of §5.3, the atom check of §5.2, the cap, on `tier-fast` | A published page of 300 words gets one digest row after 10 still minutes. A question in other words, which shares no word with the body, finds the page lexically. An atom whose quote is not in the page is dropped. A draft gets no digest. An edit writes a new digest and removes the old one. With the flag off, nothing calls the model | AGENT-SAFE to build. The flip: owner |
| **CX-4** | Session digests and the `chat` source of §8: the migration for `chat_session_digests` and the full-text index, the idle pass, the source, the skip rules | A member finds last month's decision by a question in other words. A member who is not a participant finds nothing. A `no_egress` session gets no digest | AGENT-SAFE to build. The flip: owner |
| **CX-5** | The connector contract of §6: `last_new_at` on the connection rows, the `expected_every` table, the freshness check and the `connector_quiet` card | A connected mailbox with no new mail for 7 days makes one card for an admin, and no second card that week | AGENT-SAFE to build. The card kind: MD-10's rule |
| **CX-6** | Atom to proposal (§8.3), after KB-5 | A `decision` atom in a session digest offers a proposal card. Accepting it writes a draft page with the session as its origin | AGENT-SAFE |
| **CX-7** | Sources prefer a digest's summary as the snippet (§5.3), app by app, as each app's digest ships | The email source shows the insight's summary when the message has one | AGENT-SAFE, in each app's slice |

The order is CX-1, CX-2, CX-3, then CX-5, CX-4, CX-6. CX-7 follows each app.

---

## 13. Fences

- `tests/unit/test_company_search_recency.py` (CX-1): the decay formula, the
  floor, the review factor, and the flag-off identity.
- `tests/unit/test_shell_search.py`: unchanged, and green.
- `tests/unit/test_context_eval_r8.py` (CX-2): the hit@5 rule of §9.
- `tests/unit/test_kb_digest.py` (CX-3): the atom check, the draft rule, the
  rewrite on change, and no model call with the flag off.
- `tests/unit/test_company_search_sources.py`: every source, `chat`
  included, checks its gate before it calls the app.

---

## 14. Decision D106 (accepted 2026-10-18, owner directive)

Recorded in `work_plan.md` §3 as D106.

1. **Every hit carries a stamp**, the date its record last changed in
   substance, beside `when`.
2. **The seam ranks newer above older inside each app**, by a half-life per
   app with a floor. A Knowledge page decays by its review state, not its
   age. The model reads the stamp, and the newer record wins a conflict.
3. **A cheap model writes a digest per record**: a summary, the likely
   questions and the atoms. Code checks each atom against the record. The
   digest lives in the owning app's table, under the record's visibility.
4. **Lexical first.** A digest comes before a vector. An eval of fixed
   questions is the fence for every ranking change.
5. **Every connector holds a cursor, reads only, and reports
   `last_new_at`.** A quiet connector makes a card for an admin.
6. **Past AI sessions are a source**, for their participants only.

It changes no price and no product surface. D104 and D105 stand unchanged.

---

## 15. Owner questions — each with its default

| # | Question | Default |
|---|---|---|
| CQ1 | Should the digest run on `tier-fast`? | Yes. It is the cheap tier that exists |
| CQ2 | Should session digests be on for every org? | No. Off until the owner names orgs, because they read conversations and spend credits |
| CQ3 | On production, turn on digests before vectors? | Yes. Digests first, then vectors where the eval shows a gain |
| CQ4 | The half-lives of §4.1 | As written, until the eval of CX-2 says otherwise |
