# Find: one search engine for the chat, as a ladder of rungs

**Status: PROPOSED, 2026-11-14. Verified against the code on 2026-11-14
(`origin/main` at `ed2274b1c`).** Epic WS-FIND, task 3232. FIND-0 is task
3233. This spec builds nothing. It maps five slices that are in flight or
planned (§9.2). **FIND-1 is built (branch `mt-45237431-find-misses-eval`, task
3238):** the misses set is in `evals/search_company/fixtures/misses.json`, and
`--fixture` prints its score. **HRD-4 is built (task 2983, 2026-11-16):** the sources run at once, 4 at most, and `find_tasks` ranks by the words held on a miss. The owner accepted D-FIND-1 to D-FIND-8 as
recommended on 2026-11-14 (task 3233, answer `30dfa0d6`, §10).

**HRD-18b is built (task 3226, 2026-11-16).** Behind `FIND_ORGS`, a miss of
§3.3 (D-FIND-4) starts rung 2 and rung 3. `query_inbox` asks for the near
spelling of a sender (`near_sender`). The Mail UI never asks.

**FIND-3 is built** (task 3240, 2026-11-16). Behind `FIND_ORGS`, rung 4 runs
after the other passes, on the first 5 hits that are a deal, lead, contact,
person or company. The CRM, People and Email sources have `related` and
`open`. The records go to the answer key `related`: 3 for each hit, 15 at
most, inside one second. A record that the member cannot open leaves no trace.

**FIND-4 is built** (task 3241, 2026-11-16, see "As built" in §3.6 and
§3.7). Behind `FIND_ORGS`, the answer names why the route skipped each app
(`skipped_why`). No line says that a record does not exist: the switch
`find.ABSENCE_CLAIMS` is off. A zero answer names the near spellings that it
tried, and at most 3 "Where else" lines. `search_company(explain=true)`
answers the trace of §3.7 with no record. Each search writes one
`search.company.ladder` line.

**FIND-7 is built** (task 3244, 2026-11-17, see "As built" in §5.5). Behind
`FIND_CLAIM_CHECK`, the harness reads the final answer of each chat run, for
an org of `FIND_ORGS` only. An answer that says a record does not exist
needs a `search_company` answer of the same run that shows pass 2. An app's
search tool alone does not count.

In `shadow`, a miss writes one `answer_check.claim` line with no text. In
`on`, it gets one more pass. The flag is `off` on every box.

**HRD-21 is built** (task 3225, 2026-11-16, see "As built" in §3.1 and §6).
Behind `CHAT_SEARCH_ORGS`, rung 6 reads the member's own solo chats with
Mira, and never the current chat. A hit names the title, the date of the last
change of the chat and the link that opens the chat. With the flag empty, the
answer is the answer of before, byte for byte, and so is the 422 of an unknown
app, `chat` too. The flip waits for the owner's preview.

**FIND-5 is built** (task 3242, 2026-11-16). Behind `FIND_ORGS`, the core tool
`resolve_name` reads the new route `GET /search/names`. It gives each person,
contact, lead, company and mail sender that holds one name, exact or near.
§5.2 holds the shape as built.

**FIND-6 is built** (task 3243, 2026-11-17, see "As built" in §5.3 and
§5.4). Behind `FIND_ORGS`, with company search on, the core list holds
`search_company` and `resolve_name`. Each find tool of the core list answers
a failure as text. With `CONTEXT_RECENCY` on too, a zero of `search_crm`,
`query_inbox` and `find_tasks` ends with the pointer, and the persona holds
the standing rule. `resolve_person` stays in the core list, because the
parity of D-FIND-3 did not hold then (§5.3). FIND-6b closed it.

**FIND-6b is built** (task 3665, 2026-11-17, see "As built (FIND-6b)" in
§5.2). Behind `FIND_ORGS`, `GET /search/names` also gives a member of the
org, the member's saved aliases (tier 1, match `alias`) and the address of a
colleague. Each follows the rules of `resolve_person`. `resolve_person` stays
in the core list. The next PR takes it out (D-FIND-3).

**The seam owner.** `knowledge_base.md` §20 (D105) owns the retrieval seam:
`gateway/retrieval/`, the source, the hit and `GET /search/company`. This spec
extends §20 and replaces nothing in it. It adds no second read path and no
central index (`building_blocks.md`, the row "Knowledge and retrieval").

**Owner ask, 2026-11-14, in chat.** The words are the owner's, so they sit in
a block that the linter does not read:

```text
"We need to have a comprehensive engine that also can pick up similar names
or similar companies, or help debug when some particular search query
returns nothing. So that particular tool set for smartly searching should be
there. We also overhauled our tooling system, so this can become a part of it
for the chat."

"Similar names, same names, typos, etc., should also work with getting the
right details and getting out. Even if we do not find exact searches
somewhere, we should be able to know where else to search to get that
information and data."
```

## 0. The answer in one screen

- **One engine on the one seam.** `GET /search/company` becomes a ladder. Each
  rung asks the apps through their own search functions, as the member. No
  rung writes SQL in `gateway/retrieval/`.
- **Eight rungs.** Exact words, each word, near spelling, related records,
  meaning, past chats, outside sources, and an honest zero.
- **Three passes in time.** Pass 1 asks every source at once. Pass 2 runs only
  on a miss. Pass 3 adds related records, and asks an outside source on a
  miss. The answer stops at 6 s, or 8 s with an outside source.
- **Each hit says how it matched** (`matched_by`) and how many of the words it
  holds. The hit that holds the most words comes first.
- **Each app declares its search** in its manifest (`contributes.search`):
  exact search, near names, related records and a read by id. No manifest
  declares it today (§4.1).
- **Two core tools for the chat.** `search_company` runs the ladder.
  `resolve_name` finds one name in every app. The app search tools keep their
  filters and share their app's one predicate.
- **The pattern of `number_sense.md`.** The engine gives evidence, the harness
  checks a "not found" claim, and one short standing rule covers the rest.
- **Three rungs of delivery (D211).** Rung 1 (demo task 3234) finds
  "Sharath's printer order" in one call.

## 1. The evidence

### 1.1 The Sharath chat (Fracktal, 2026-11-14)

The member asked about an order that Sharath placed for a 3D printer. The CRM
held the deal «Neuropeak - Snowflake 3D printer». Its lead was «Sharat
Satyanarayana», which the chat converted on 2026-11-12. A mail thread of 12
messages from that lead held the order and the payment.

The first answer took 23 s, 12 tool calls and 7 model requests. It said that
the CRM held no such order. It also pointed at the wrong deal, «Twin Dragon
300», because a different contact named Sharath is on it.

| The call | The answer | Why it missed |
|---|---|---|
| `search_crm("Sharath")` | One lead and one contact at BMS School of Architecture | The CRM matches the whole query with `ILIKE` on each column. «Sharat» does not hold "Sharath". The converted lead is hidden |
| `search_company("Sharath 3D printer")` | No records. "Skipped: email" | Each app needs every word in one record. The sources ran one after another in one 4 s budget, and Email ran out of time (HRD-4 now runs them at once) |
| `search_crm("3D printer")` | "Deals (16, showing 10)" | The deal was one of the 6 that the list did not show. The list sorts by the last activity, not by the words |
| `query_inbox("Sharath")` | 2 old mails from BMS | Email full text has no near spelling |
| `search_company("Neuropeak Sharath")` | No records | The deal holds «Neuropeak», and its lead holds «Sharat». No one record holds both words |
| `query_inbox("Neuropeak")` | No mail | The sender domain is `neuropeek.com`, and the mails name «Bitnib Technologies» |
| The past chats | Not searched | On 2026-11-12 the same member and the chat discussed this order and made the deal. A past chat is not a source |

The member then wrote "nuropeak or Sharath" and "an email conversation". The
chat found the deal by an id that it saw before, and the thread by the word
"Snowflake". Asked why it failed, the chat named three causes: the spelling,
the wrong company, and the list of 10 out of 16.

### 1.2 Across all organizations

Measured on production on 2026-11-14, over the last 3 days (task 3232):

- 52 of 85 `search_company` calls answered zero. Most of them held more than
  one word.
- 12 of 36 `query_inbox` calls answered zero.
- One call said "Skipped: email", because the sources share one budget.
- On 2026-11-11 the meaning half timed out on 20 of 20 searches. The embed
  limit is 0.8 s (`QUERY_EMBED_TIMEOUT_S` in `kb_index/query.py`).
- The median chat answer took 12 seconds, and the 90th percentile took 39
  seconds.

### 1.3 What the code does today

| Search | Where | On `main` | In flight |
|---|---|---|---|
| CRM | `routes/crm/core.py` `search_where`, `records.search_records` | One `ILIKE '%query%'` for each column. A deal also matches its company name (`search_sql`). A converted lead is hidden (`OPEN_LEAD_SQL`) | HRD-18a: word by word, a deal matches its contacts and source lead, and `?near=true` |
| Email | `routes/email/transport/search.py` | `websearch_to_tsquery('english', q)` over the subject, the body and the sender. Every word must be in one mail | HRD-18b |
| Tasks | `routes/projects/search.py` `search_tasks` | Every word. When no task has all of them, the tasks that hold most of the words (HRD-4, built) | HRD-4 |
| Company | `retrieval/company.py` | The sources at once, 4 at most, 1.5 s each from its start and 4 s for all (HRD-4, built) | KR-ZERO, HRD-4 |
| Colleagues | `routes/admin/assistant_profile_people.py` | An exact fold of the name, or of its first word | HRD-18a |
| Contact typeahead | `routes/email/transport/contacts.py` | A `pg_trgm` fallback at 0.3, over members and People | HRD-18a |
| Knowledge | `routes/knowledge/search.py`, `kb_index/query.py` | Any of the words (CX-1b), and a vector half within 0.8 s | KR-ZERO |
| Past chats | none | `company_context.md` §8.2 plans the `chat` source (CX-4). `TAU_DAYS` holds `chat` already | HRD-21 |

## 2. Scope and non-goals

**In scope.**

- The ladder of `GET /search/company`, and the shape `matched_by`.
- The honest zero, and the explain mode.
- The source contract `contributes.search`.
- The chat's find tools, and the harness check of a "not found" claim.
- The slices that build them.

**Non-goals.**

- No central index and no copy of app data (D105, §20.2).
- No new fuzzy index and no migration for near names. `pg_trgm` is installed
  (migration 170). The chat search index of HRD-21 belongs to CX-4.
- No search inside attachments. KG-12 owns it.
- No chat model inside the engine. The embed of the meaning rung stays the one
  model call, behind its owner flags.
- No change to the command bar. It runs rung 1 only, inside its 1.5 s, and
  `test_shell_search.py` stays unchanged.
- No write. A near match never counts as a match for a write (HRD-18a).

## 3. The ladder

### 3.1 The rungs

| # | Rung | What it does | When it runs | Budget | `matched_by` | Slices |
|---|---|---|---|---|---|---|
| 1 | Exact words | Each app's own search, with the whole query. Inside one app, each word may match a different field (HRD-18a) | Always, pass 1 | 1.5 s for a source. Pass 1 ends at 2.0 s | `exact` or `words` | KR-1, HRD-4, HRD-18a |
| 2 | Each word | Each content word alone, against each app that answered | On a miss, pass 2 | Shares 2.5 s with rung 3 | `word` | KR-ZERO, HRD-4 |
| 3 | Near spelling | The near names of up to 3 words, from records the member can open. Then the query again with the best spelling | On a miss, pass 2 | 0.5 s for the lookup, inside the 2.5 s | `near` | HRD-18a, HRD-18b |
| 4 | Related records | The records that link to the best hits: the contacts and the lead of a deal, the deals of a company, the mail from and to an address, and the mail of a company's own domain (no free-mail domain, the rule of KG-5) | When a pass found a person, contact, lead, deal or company | 1.0 s, at most 3 for each hit | `related` | FIND-3, then KG-6 |
| 5 | Meaning | The vector half of each source that has one | The embed starts at 0 s, beside rung 1. A source waits at most 0.8 s for it | 3.0 s for the embed | `meaning` | KR-ZERO, KG-3b |
| 6 | Past chats | The member's own chat sessions | Pass 1, when `CHAT_SEARCH_ORGS` names the org | 1.5 s, as each source of pass 1. A LOCAL `statement_timeout` of 1.6 s stops its statement | `chat` | HRD-21 |
| 7 | Outside sources | An outside app that an admin made a find source | On a miss after pass 2, or when the member names it | 3.0 s. The answer cap rises to 8 s | `outside` | FIND-10 |
| 8 | Honest zero | The answer says what it searched, what it tried and where else to look | Each answer with a miss | None. Code writes it | none | KR-ZERO, FIND-4 |

**As built (HRD-21), rung 6.**

- The source has the key `chat`, and it is last in `REGISTRY`. The command
  bar never asks it.
- The route asks it only when `feature:chat` and `CHAT_SEARCH_ORGS` let the
  member in, and the request names the current chat (`current_chat`). Else
  the source is not asked at all. The tool sends the thread of its own run.
  A run with no chat (a batch run, with a thread that the executor made)
  sends no thread, so it shows no past chat. A run that is not solo sends
  none either.
- It runs in pass 1, in the 1.5 s of a source. Its hits leave the ladder
  before the miss rule, the words alone and the near step, and join the one
  list before the sort. So a past chat that holds every content word does
  not stop the search for the records.
- The search proves each content word of the query as a whole word, by the
  stem rule of `find.coverage` (`find.whole_word_pattern`). So "AI" never
  matches inside "email", "print" never matches "printer", and "printers"
  holds "printer". The cut end of a long row is no end of a word.
- With `FIND_ORGS` on, a past chat says `matched_by` `chat`. It holds every
  content word as a whole word, because its search proved each one in the
  title or in one row. So `covers` is `of`, whatever its snippet and its hint
  show. On a tie of words, a record comes first, because rung 1 is lower
  than rung 6.
- The second pass starts on a miss of the records only. The `miss` of the
  log line reads the final answer, so a past chat with every word makes no
  miss there.
- `find.chat_words` is the one rule of the words, for the route and the
  reader. A query with no content word, or with more than 8, does not ask
  the source. Then `searched` has no `chat`, "Where else" names the past
  chats, and rung 6 of the trace reads `closed`. A stop word does not count.
- The reader is `gateway/chat_search.py`, in the chat's own code. The caps
  bound the word match. It reads the newest 100 chats that pass every rule,
  the newest 80 rows of each chat, and 4,000 characters of each row. The rules run
  before that cut, and the agent rule reads each row of each chat that it
  tests. Each word arm is a `LIKE` of the stem, then a regular expression,
  and no index serves either. A LOCAL `statement_timeout` of 1.6 s bounds the
  whole statement. HRD-21 measured no plan on a large chat history. Measure
  one before the flip.

### 3.2 Three passes in time

```text
0 s     pass 1: rung 1 on every source (4 at a time), rung 6, and the embed of rung 5
2.0 s   pass 2, on a miss only: the near lookup of rung 3, then rungs 2 and 3 ask again
4.5 s   pass 3: rung 4 on the best 5 hits, and rung 7 on a miss
5.5 s   the merge and the answer. The cap is 6 s, or 8 s with rung 7
```

These are limits, not the usual times. On staging the p50 of the route was
0.87 s after KG-3a (`knowledge_graph.md` §7.2). A pass 1 whose best hit holds
every word goes straight to pass 3.

1. Give each pass what is left of the answer cap.
2. Name each late source in `skipped`, with its reason: timed out, failed,
   refused or closed.
3. Run at most 4 sources at once in one gateway process (`FIND_SESSIONS`,
   D-FIND-6).
4. Keep the command bar on rung 1 only.
5. Call no chat model in any rung.

**Why a cap of 4.** One gateway process has 7 async sessions and 2 more
(`db_pool_size`, `db_max_overflow` in `acb_common/settings.py`). Each source
holds one session while it runs. Eight sources of one search at once would
take the pool from every other request.

### 3.3 What a miss is

A **content word** is a query word after the KR-ZERO fold: no stop word, no
one-letter word and no final `'s`. So "Sharath's" gives "sharath".

A **miss** is a pass whose best hit holds fewer than all the content words.
Zero hits is one kind of miss (D-FIND-4).

The reason is the Sharath chat. «Twin Dragon 300» held one word of three,
through a different Sharath, and the chat took it as the answer. A pass 2 that
ran on zero hits only would not have run.

**The near lookup takes at most 3 words.** First the words with no exact hit,
then the words that start with a capital letter, then the longest words. A
word needs 4 letters or more (`NEAR_MIN_LETTERS` of `acb_common.name_match`).

### 3.4 The order of hits

The merge makes one list, sorted by three keys:

1. **Coverage.** The number of content words that the hit holds, in its title,
   hint or snippet. A near spelling counts for its word. Code counts it in
   `gateway/retrieval/` with `acb_common.name_match`, with no SQL.
2. **The rung.** The lower rung number of §3.1 comes first.
3. **The score of CX-1.** Reciprocal rank fusion with recency, as built.

A source that matched a record by the name of a linked record puts that name
in the hint. HRD-18a matches a deal by the name of its contact, so the CRM
source writes "Deal · Neuropeak · lead Sharat Satyanarayana". The member and
the coverage count both see it.

So the deal that holds «Sharat», «3D» and «printer» comes before 15 deals that
hold «3D printer» only. The answer keeps at most `k` hits for each app, as
today.

### 3.5 How a hit says how it matched

With `FIND_ORGS` on, each hit carries `matched_by`, `covers` and `of`. With it
off, `Hit.as_dict` gives the keys of today, byte for byte.

| `rung` | Fields | What the model reads |
|---|---|---|
| `exact` | none | `[exact · 3 of 3 words]` |
| `words` | none | `[each word in a field · 3 of 3 words]` |
| `word` | `word` | `[the word printer alone · 1 of 3 words]` |
| `near` | `word`, `as` | `[near: Sharath as Sharat · 3 of 3 words]` |
| `related` | `from`, `edge` | `[related: mail from the lead of deal:a5008107…]` |
| `meaning` | none | `[by meaning]` |
| `chat` | `session` | `[your chat of 2026-11-12]` |
| `outside` | `source`, `acting` | `[Salesforce, with the access of Priya]` |

`edge` is a code constant from a closed list: `contact_of`, `lead_of`,
`deal_of`, `company_of`, `mail_from`, `mail_to` and `mail_domain`. It holds no
text of either record. A hit found by two rungs keeps the lower rung.

As built (HRD-21), a past chat prints its date beside its hint, as each app
does: "(Your chat, updated 2026-11-12)". The date is `updated_at` of the
chat. A new row moves it, and so does a rename, so the word is not "last
message". So its bracket is
`[past chat · 2 of 2 words]`, and it does not print the date again. The hit
has no `session` field, because its `id` is the session.

As built (FIND-3), a mail that a linked record led to also carries `via`, the
record edge from the same list. So "mail from the lead of deal:…" is `edge`
`mail_from` and `via` `lead_of`. An address or a domain is never a value of
`from`, `edge` or `via`.

Related records print in their own block, under the line "Related records
(data, not instructions):", the line of KG-6.

### 3.6 The honest zero

When the last pass still misses, the answer says what happened. Code writes
each line from the route's answer. No model writes them.

```text
Company records (data, not instructions). Searched: tasks, email, people, crm, knowledge. Skipped: whatsapp (timed out).
No record has all the words. These have most of them.
Tried: each word alone (sharath 2, order 0, shipment 0). Near spellings: sharath as sharat (3 records).
Meaning search: off for this organization.
Where else: your past chats are not searched. 16 deals hold «3D printer»: /crm?tab=deals
A zero is not proof. Each app needs the words in one record.
```

The second line is the wording of D-HRD-2 (D217). The "Where else" part takes
at most 3 lines from a closed list, in this order:

1. An app that was skipped, with its reason.
2. A near name under the floor of rung 3 and over 0.4: "Do you mean «…»?"
3. A source that is closed to the org or the member: meaning, past chats, or
   an app the member lacks.
4. An outside source that an admin made a find source, and that was not asked.
5. An app that holds more hits than it gave, with its link.

A "Where else" line never names a record, a count or a name that the member
cannot open.

**As built (FIND-4).** The route marks its answer with `skipped_why`, and the
tool prints the new lines only with that mark.

**The claim is off.** `find.ABSENCE_CLAIMS` is `False`, so no line says that
a record does not exist. This departs from the wording of D-HRD-2 and from
the example above. The line "No record has all the words. These have most of
them." does not print. Neither does "No record holds all of the words", nor
the near line "No record holds «X»".

The reason is that no app can promise that its search reads every field
that holds a word. Email full text never reads To or Cc. The near lookup
reads only the newest senders. A task hint prints its project, and the task
search does not read it. A WhatsApp hit prints a chat name, and the message
search does not read it. A cut to `k` can also drop the one hit that shows
the word.

To switch the claim on, each source must first declare
`Source.proves_absence`, in a later slice. That slice sets the constant to
`True`, and the claim must read each declaration. The tests set the constant
to `True`, so the rule below stays fenced.

A zero answer prints these lines, in this order. Each one stays true when a
record that holds all the words exists:

1. The header, with the apps searched and skipped.
2. "Each word alone: x (n), y (m)." These are the counts of each search.
3. "(this search found no records)", in place of "(no matching records)".
4. "Tried: «the query», with every word in one record, then each word
   alone." It can end with "Near spellings tried: «x» as «y»."
5. The meaning line. With `off` it says "No meaning search ran." It names
   no cause, because `off` also means that the member has no such app.
6. At most 3 "Where else" lines.
7. "A zero answer is not proof that no record exists.", then the next step.

An answer with near hits opens with "Near spellings: «x» as «y» (n
records)." It names the spelling and its records, and nothing about the
word itself. In `near`, `held` is `true` or `null`, and never `false`.

The rule behind the switch is as follows:

- The line "No record has all the words. These have most of them." needs the
  proof `none_has_all`, and so does "No record holds all of the words". The
  proof is the rule of `held` false: one word of 4 letters or more, searched
  alone in each app that answered, found nothing. The route skipped no app,
  and no date or verified filter applied. An app may answer nothing for a
  shorter word, with no error.
- `find.proves_absence` is the one rule of that word, for `held` false and
  for `none_has_all`. Three parts must hold.
- First, the member typed the word as it folds, in ASCII letters and digits
  (`find.typed_alone`). "Müller" folds to "muller", "acme.com" folds to
  "acme" and "com", and "Strauß" folds to "strauss". An app with no
  unaccent, or a full text that keeps "acme.com" as one token, finds nothing
  for the fold.
- Second, no hit of any rung and no related record shows the word in its
  title, hint or snippet. An app can print a field that its search does not
  read: a preferred name in People, a chat name in WhatsApp.
- Third, the near lookup ran for the word in each near source, and gave no
  name that is the word itself. A sender address is such a field: "acme"
  alone never matches inside «billing@acme.com». A lookup that did not run,
  failed or ran out of time proves nothing.
- The answer must also miss: no hit shows every word.
- The near lookup reads the newest 2000 names of a source, and its scoring
  stops at 0.5 s with no error. These limits are why the switch is off.
- A first pass with no hit is no proof. People matches the whole query as
  one phrase, so a person who holds the words in another order is no hit. A
  low `covers` is no proof either. A hit can hold a word in its body that its
  snippet does not show.
- With no proof, the counts of each word alone print with no claim.
- A zero answer with no related record prints the honest zero lines. Item 1
  of "Where else" names each skipped app with its reason. Item 3 names the
  past chats and each app that is not open to the member. A dark Studio
  source is never named.
- As built (HRD-21), the line of the past chats prints only when the search
  did not ask the `chat` source. The flag does not name the org, or the run
  named no current chat. So the line is true each time it prints. The `chat`
  key is never a `not_open` entry, because the past chats have their own
  line.
- Item 2 is not built. `name_match.near_names` keeps no name under the
  floor, so the route holds no name between 0.4 and the floor.
- Item 4 waits for FIND-10.
- Item 5 is not built. The route reads no total of an app, and a list of `k`
  hits proves no more hits.
- The meaning search keeps its own line. It is not also a "Where else" line.
- The last line keeps the words of KR-ZERO: "A zero answer is not proof that
  no record exists." The tool does not print "Each app needs the words in
  one record". Knowledge matches any of the words, so that sentence is false
  for it.

### 3.7 The explain mode, and the log line

`search_company(query, explain=true)` runs every rung, with no early stop. It
answers the trace in place of the hits. For each rung and each source, the
trace gives whether it ran, the hits, the time in ms and the outcome. It names
each near spelling that it weighed, as over or under the floor. It gives the
outcome of the embed, and the flags that the org has off.

The trace holds no snippet, and it stays under about 600 tokens. The model
calls it when the member asks why a search found nothing (D-FIND-2).

Each search writes one log line, `search.company.ladder`. The line holds the
hits and the time of each rung, and `miss` true or false. It holds no word, no
name, no address and no id. The weekly delivery review reads the zero rate of
§1.2 from it.

**As built (FIND-4).**

- Only the dated tool takes `explain`. The plain tool runs with
  `CONTEXT_RECENCY` off, and there the route never reads `explain`.
- The trace has four keys: `rungs`, `near`, `meaning` and `flags_off`. Each
  row of `rungs` holds a rung, a source key, `ran`, `hits`, `ms` and an
  outcome from `find.OUTCOMES`. A closed app has one row, in rung 1.
- Rung 4 gives a row for each source too. Its hits are the related records
  that each app gave. Rung 6 has one row, with the key `chat`. As built
  (HRD-21), the `chat` source fills that row from its pass 1. It has no row
  in rungs 1 to 4. With the source closed or not asked, the row stays
  `closed`, so a dark source changes no trace.
- `near` holds at most 3 spellings for each word. `name_match` keeps no name
  under the floor, so `over_floor` is true for each spelling today.
- `flags_off` asks the one reader of each flag: `KNOWLEDGE_EMBED`,
  `EMAIL_SEMANTIC_SEARCH_ENABLED` and `CHAT_SEARCH_ORGS`.
- An explain call records no Knowledge gap, because it asks the same words
  again.
- The log line has seven fields: `hits`, `exact_ms`, `word_ms`, `near_ms`,
  `related_ms`, `miss` and `explain`. `miss` reads the hits of the final
  answer. `explain` lets the zero rate leave out an explain call.

## 4. The source contract: `contributes.search`

### 4.1 What exists today

- `package_host.md` §4 lists the point `search`: "Records in Find and in
  `search_company`". `studio_manifest.py` accepts the key in
  `CONTRIBUTION_POINTS`, with no shape.
- **No manifest declares it.** The 22 files of `app_manifests/shell/` hold
  `scope` (a scope link to a source key), and none holds `search`. The
  `building_blocks.md` §3.1 row marks it V. For `search`, that is wrong.
- The sources are code: `REGISTRY` in `retrieval/sources.py`, with 7 rows.
  `BAR_APPS` in `routes/shell/search.py` and `TAU_DAYS` in
  `retrieval/company.py` are two more lists in code.
- A Studio manifest has `shell.search`, a list of object names that
  `validate_manifest` checks. No code reads it for a search.

So FIND-8 is the slice that makes the point real.

### 4.2 The declaration

A system app declares its search in its file of `app_manifests/shell/`:

```json
"search": [{
  "source": "crm",
  "label": "CRM",
  "feature": "crm",
  "kinds": ["deal", "lead", "contact", "company"],
  "can": ["exact", "near", "related", "open"],
  "names": ["contact", "lead", "company"],
  "bar": true,
  "half_life_days": 180
}]
```

| Field | Meaning |
|---|---|
| `source` | The source key. One owner for each key (`package_host.md` §4) |
| `label`, `feature` | The group name, and the feature that the app's router demands |
| `kinds` | The kinds of hit that it gives |
| `can` | Its abilities, from the closed list of §4.3. `exact` is required |
| `names` | The kinds whose names its near lookup reads, for `resolve_name` |
| `bar` | True when the command bar asks it. It replaces `BAR_APPS` |
| `half_life_days` | The half-life of CX-1, or null. It replaces `TAU_DAYS` |

The file holds data, not code. Code registers the functions under the same key
in `retrieval/sources.py`, as `needs.register(Provider)` does for a bell item.

### 4.3 The abilities

| Ability | The function on `Source` | What it gives | The rule |
|---|---|---|---|
| `exact` | `find(user, q, k)`, as built | Hits | Required |
| `near` | `near(user, words, k)` | For each word, names with a score, from rows that the member can open. One read serves every word of a lookup | The floor comes from `acb_common.name_match` |
| `related` | `related(user, refs)` | Triples of a ref, an `edge` and a linked ref, from stored ids and addresses | Ids only, no text |
| `open` | `open(user, refs)` | Hits for the refs that the member can open | The rule of `knowledge_graph.md` §6 |
| `meaning` | `find(..., hybrid=True)`, as built | Hits | One embed, through the one embed door |
| `dated` | `find(..., after=...)`, as built | Hits | The app's own SQL filters the date |

### 4.4 Five rules

1. **One predicate for each app.** The engine's source and the app's own
   search tool call the same function. So `search_crm`, the CRM list and the
   engine agree on what matches.
2. **No SQL in `gateway/retrieval/`.** Each function lives in the app's
   module, and runs as the member in the bound tenant.
3. **One matcher.** Each near test reads its floor from
   `acb_common.name_match` (HRD-18a). No module keeps a copy of a floor or a
   fold.
4. **Lockstep.** Each manifest entry has one registered source with the same
   abilities. Each registered source has one entry.
5. **One owner for each key.** The install refuses a second claim of a source
   key (`package_host.md` §4).

### 4.5 A Studio app

A Studio package declares `contributes.search` with an `object` and the fields
that hold its names. FIND-9 registers one source for the install,
`studio:<package id>`. Its `exact` and `near` call the object query of C2a as
the member, with the grants of the install. Its `open` reads by id. The app
writes no code for it. A2's `shell.search` reads as the same list until
`package_host.md` folds the old `shell` block.

### 4.6 An outside source

Metorite does not mirror every outside app (`number_sense.md` §5). An outside
app joins the ladder on these terms:

1. **A connection declares it.** An admin marks one read-only tool of an org
   MCP server (`mcp:<name>`, `connections/mcp.py`) as its find tool, in
   `provider_meta`. A connector of `crm_sources/` may declare a search
   function in the same way.
2. **It only reads.** A server with `always_confirm` on is never a find
   source, because a search cannot wait for a card.
3. **It runs on a miss, or when named.** Rung 7 runs when pass 2 still misses,
   or when `apps` names the source.
4. **It says whose access it used.** An org connection acts as the admin who
   connected it (D98). Each hit names the app and that person.
5. **It keeps nothing.** The engine stores no outside hit and logs no word of
   it (D105). Its text is data under the header of the answer.
6. **A link goes to the app's own host only.** A hit keeps an `https` link
   when its host is the host of the connection.

The engine registers the source at request time from the connection rows, with
the key `outside:<label>` and the ability `exact` only.

## 5. The chat tool family

### 5.1 Where the tools live

`tool_packs.py` holds the packs of the owner agents: email, CRM, Projects and
Studio. A pack loads when the words of the turn or the screen name its app. A
search comes before the chat knows the app, and a pack load costs one more
model round.

So the find tools sit in the orchestrator's core list (`core_tools` in
`orchestrator/agents.py`), on the one path of KR-2. `search_company` is there
today. `resolve_person` is there when the profile is on. Neither is in a skill
family, in `tool_scope` of `config.json`, or in the tool floor.

The contract of the tool system still binds them. Each failure is text with a
next step (HRD-3, `chat_failure_modes.md` §7). Each id that they print is one
the model can use. Each risk is in `TOOL_ANNOTATIONS`.

### 5.2 The two tools

| Tool | Route | What it answers | Flag |
|---|---|---|---|
| `search_company(query, apps, after, verified_only, explain)` | `GET /search/company` | The ladder of §3, with `matched_by`, related records and the honest zero. `explain` gives the trace of §3.7 | `FIND_ORGS` for the new parts |
| `resolve_name(name, kinds)` | `GET /search/names`, new, in `gateway/retrieval/` | Each person, member, contact, lead, company and mail sender that holds the name, exact or near, with its app, kind, link and match. One exact match: use it. Several, or near only: ask the member | `FIND_ORGS` |

`GET /search/names` asks each source that can `near`, with the lookups of rung
3. So a near name is the same in the ladder and in the tool.

**As built (FIND-5, task 3242, 2026-11-16, with its two fix rounds).**

- **The route.** `gateway/retrieval/names.py` holds it. An org off
  `FIND_ORGS` gets 404 before any check of the input. The route takes `name`
  and `kinds` only, and the org and the member come from the session.
- **The name.** One name of 2 to 60 characters and at most 3 words. `kinds` is
  a comma list from the closed list alias, person, member, contact, lead,
  company and sender (FIND-6b added alias and member). An unknown kind gets a
  422 with fixed text.
- **A word that the matcher reads.** The name must hold one word of 2 letters
  or more in the letters a to z or the digits 0 to 9. Else the route answers
  a 422 with its own fixed sentence, before any read. Initials alone ("A B")
  and a name in another script get it. The tool tells the model to ask the
  member for the name in those letters.
- **The sources.** The CRM gives contact, lead and company. People gives
  person. Email gives sender. FIND-6b adds the member and the saved alias
  (see "As built (FIND-6b)" below).
- **The reads.** Each source has the ability `named` (`Source.named_records`).
  It calls the app's own search. Each source runs `named` with the name and
  `Source.near_names` at once. Then it runs `named` once more, with each word
  replaced by its best near spelling.
- **One word for People and Email.** Their own search matches its text as one
  phrase, and the directory never reads the preferred name. So the route asks
  them with one word: the longest word of the name. The near read asks the
  longest word of the respelled name. When that word is the word of the name
  read, the app gets no second read, and the near test reads the same page.
  The CRM matches word by word, so it gets the whole name. "Kumar Sharath"
  finds «Sharath Kumar» in each app.
- **The name test is code.** A record is `exact` when one form of its name
  holds each word of the name. It is `near` when one form holds each word of
  the near spelling. Each form counts on its own, never the union of two
  forms. Each asked word takes its own stored word of that form, once. A word
  of one letter takes a stored word that starts with it, after the full words
  take theirs. A record that matched by another field gives no row.
- **The forms of a name.** A CRM record has its name. A person with a preferred
  name has two forms: the full name, and the full name with its first word
  replaced by the preferred name. So «Gitanjali Rao», preferred «Gita», holds
  "Gitanjali Rao", "Gita Rao" and "Gita". It does not hold "Gitanjali G". A
  sender has two forms: the display name, and the part of the address before
  the `@`. `sources.person_forms` builds the forms of a person.
- **An initial reads the first form only (round 4).** The invariant: no
  initial is met by the second spelling of one given name. The second form of
  a person guesses that the first word of the full name is the given name.
  A full name can start with an initial, a title or a surname. Then that form
  holds two spellings of one given name: «K Sharath Kumar», preferred
  «Sharath», gives "Sharath Sharath Kumar". So an ask that holds a word of one
  letter reads the full name only, and a sender's display name only. "Sharath
  S" is then no exact match for «K Sharath Kumar». An ask with no initial
  reads each form. The fence is `test_resolve_name_tool.py`.
- **An apostrophe joins.** An apostrophe between two letters joins them into
  one word, in the name and in each form. So "O'Brien" is one word, and
  «Oscar Brien» does not hold it.
- **A sender row.** The Email app's own read `search.named_mail` gives it. The
  read uses the mailbox scope of the search, drops the outbound mail (sent,
  drafts, outbox) and sets a LOCAL `statement_timeout` of 1.5 s first. A `%`
  or a `_` in the word is plain text. The id of the row is the address, and
  its link opens the newest mail of the sender. An address that holds a
  hidden character gives no row, and a joiner counts as one.
- **A person row** says the status of the person (active, contractor, alumni
  or invited), because the directory shows each status.
- **The time.** One deadline of 1 s for every read, and each read takes a
  slot of `FIND_SESSIONS`.
- **A failed read.** A failed name read gives no row, and `skipped` names the
  app with `timed_out`, `failed` or `refused`. `refused` is a 401 or a 403 of
  the app only. A statement that Postgres cancelled (SQLSTATE 57014) reads
  `timed_out`. A failed near read keeps the exact rows, and `near_skipped`
  names the app. `full` names each app whose name read or near read gave a
  full page of 20. A none answer does not count such an app as one where no
  name matched.
- **The caps.** At most 5 rows of each kind and 15 in all, the exact rows
  first. `cut` says when a cap left a row out.
- **The tool.** `resolve_name(name, kinds)` in `company_search_tools.py`
  answers with the header of `search_company`. Two values come from data: a
  stored name in guillemets, and a sender address that passed the address
  check. Every other word comes from a closed list. The last line is the
  fixed instruction of the verdict (`NAME_NEXT`).
- **The flag.** The orchestrator holds the tool only for an org of
  `FIND_ORGS`, with `COMPANY_SEARCH_ENABLED` on. So the tool list of each
  other org does not change.

**The verdict (fix round, P2-c).** The rule "One exact match: use it" holds
only for a sure match. One exact row beside a near row repeats the failure of
§1.1. There «Sharath BMS» is exact, and the near «Sharat» is the man that the
member means. A mail sender writes its own display name, so it is no known
person.

| Verdict | When | The last line of the tool |
|---|---|---|
| `one` | One exact record that is no mail sender, no near row, and each app answered in full | "One exact match: use it." |
| `partial` | The same, but an app gave no answer or a full page, did not try its near spellings, or a cap cut a row | "Ask the member to confirm it before you act." |
| `several` | Two exact records or more, or one exact record beside a near row | "Ask the member which one they mean." |
| `near` | Near rows only, or one exact mail sender alone | "Ask the member to confirm one, and never act on it without a yes." |
| `none` | No row | "Ask the member for the full name or the address." |

**Limits of FIND-5.** Each limit below is known, and no test hides it.

- **A preferred name that the full name does not hold.** The People read asks
  the longest word of the name, and the directory matches it in the full
  name only. When that word is a preferred name that the full name does not
  hold, the read misses the person. "Sharu Kumar" asks "Sharu" (the first of
  two words of 5 letters), and «Sharath Kumar», preferred «Sharu», gives no
  row. The search does not know that it missed. So a `one` from another app
  can then be wrong. When the full name holds the longest word, the read
  finds the person, and the forms test the name.
- **The page of Email holds the newest 20 mails, not 20 senders.** Twenty
  newer mails from other senders that hold the word can hide a sender. Then
  `full` names Email, and the answer is no `one`.
- **The page of People holds the first 20 rows of the directory read**, in
  the order of the department, before the name test. A full page reads in
  `full` too.
- A sender with 20 mails or more fills the page of Email, so the answer reads
  `partial` and not `one`.
- A sender that is only in the sender rollup, with no mail, gives no row.
- An address with an apostrophe gives no row, because the address check
  refuses quote marks. An address with a joiner of an Indian script gives no
  row either, because the check refuses each character of category C.
- An apostrophe joins, so "D'Souza" does not hold «D Souza», written with no
  apostrophe.
- **An initial loses some recall, by choice (round 4).** "Gita R" does not
  hold «Gitanjali Rao», preferred «Gita», because an ask with an initial
  reads the full name only. "Gitanjali R" holds it. A sender loses the same
  way: "Sharath K" does not hold «S Kumar» <sharath.kumar@…>. No answer
  claims a wrong match for it. The answer can only miss a record.
- **A display name that is the address itself.** Some mail gives the address
  as the display name. Then the first form of the sender holds the words of
  the domain too, so a word of the domain can match the sender exactly. A
  sender alone never gives `one`.
- **The cap of a request.** The pack cap counts the orchestrator's own tools.
  For an org of `FIND_ORGS`, a Studio turn with the email words drops the pack
  `studio-more`, and keeps email and studio. Nothing fails. FIND-6 kept
  `resolve_person` in the core list (§5.3), so this trade holds. Its exit
  gives a place back only to an org with the profile on. The fence is
  `test_resolve_name_tool.py`.

**As built (FIND-6b, task 3665, 2026-11-17).** It closes the three gaps of
parity with `resolve_person`: the member, the saved alias and the address.
`resolve_person` stays in the core list. The next PR takes it out
(D-FIND-3).

- **The member.** People gives the kind `member` too. The read is the read of
  `resolve_person`: `assistant_profile_people.members_named` runs
  `MEMBERS_SQL`, the active members of the bound org. It needs
  `feature:people`, the gate of the directory tier of `resolve_person`.
- **The member row.** Its id is its address, as for a sender, and it has no
  link. It has two forms: the display name, and the part of the address
  before the `@`. People gets one word, so the read gives each member whose
  display name or address part holds that word. The name test checks the
  other words.
- **The saved alias.** `assistant_profile_people.saved_names` runs tier 1 of
  `resolve_person` (`_alias_tier`). An alias of the member matches when the
  whole name equals it, after the fold. An alias never matches nearly. Each
  address must still be in the org (`email_in_org`).
- **The gate of the alias.** The read needs no feature, as tier 1 needs none.
  It runs only when the assistant profile is on for the org (`enabled_for`),
  the switch of `resolve_person`. The row has the app `profile`, the kind
  `alias` and the match `alias`. The key `profile` is not in `REGISTRY`, so
  `/search/company` and the command bar do not change.
- **The field.** The match is the field `match` of the row, from the closed
  list `names.MATCHES`. `find.RUNGS` is the ladder of `search_company`, and
  it does not change.
- **Tier 1 wins.** When an alias matched, no person or member row stays, as
  in `resolve_person`. A CRM record stays, because `resolve_person` does not
  read the CRM.
- **A colleague is no mail sender.** A sender row whose address an alias, a
  person or a member of the answer holds gives no row. The mail tier of
  `resolve_person` drops each address of the org (`email_in_org`). With
  `kinds=sender`, no colleague is in the answer, so the sender row stays.
- **An alumnus is no colleague there (fix round).** `email_in_org` refuses
  a person with the status `alumni`, and accepts each other status. So the
  mail of an alumnus keeps its sender row, and the answer is `several`, as
  before FIND-6b (`names.in_org`). The alumnus row prints no address.
- **Each member record reaches the name test (fix round 3).** The People
  read keeps each member record, also beside a People row of the same name
  and address. Only the member record holds the part of its address before
  the `@`, and `resolve_person` matches that part. So "Chris" finds the
  member «Christopher Lee» <chris@…>, also with `kinds=member`.
- **One address is one colleague.** A member with a login has a People row
  too (migration 206). A person or member row whose address an earlier row
  holds gives no row. The exact rows come first, and a person comes before a
  member. The row that stays prints the address when a row of that address
  may print it.
- **The address.** A row prints the address (`email`) only where
  `resolve_person` prints it: a saved alias, an active member, and a person
  with the status `active` (`PEOPLE_SQL`). A contractor, an alumnus or an
  invited person prints no address. A near row prints it too, as the near
  tier of `resolve_person` does.
- **The check of the address.** The tool prints the address in angle
  brackets after the name. An address with a hidden character, or a
  character of Unicode category C, prints no address. The tool never folds an
  address, because the address prints outside the quotes. A member or an
  alias whose address fails the check gives no row.
- **The verdict.** The verdict table above stays closed. A saved alias is an
  exact record for the member who saved it. A member row is a person, not a
  mail sender. So "Raj", the alias of one colleague, is `one`, also beside a
  People row of another Raj. An alias and a CRM contact of the name give
  `several`.
- **The tool.** A member prints "(People member)". A saved alias prints
  "(colleague)" and "[your saved name]". The lines of the skipped and the
  searched apps name the alias read "Your saved names".

**Limits of FIND-6b.** Each limit below is known, and no test hides it.

- An alias beside a People read that failed or gave a full page reads
  `partial`. `resolve_person` acts on the alias, because its tier 1 does not
  read the directory.
- The near spelling of a member comes from the names of the People directory.
  The near lookup of People reads `people` only, and rung 3 shares it. A
  member with no People row, or with another People name, gets no near row.
- The member read reads at most 2000 members (`DIRECTORY_CAP`). In a larger
  org, People reads `full`.
- **A colleague takes two places of the People page.** Each member also has
  a People row (migration 206), and the page holds 20 records, people and
  members together. So about 11 matches fill the page. Then People reads
  `full`, and a `one` becomes `partial`. It fails safe: the answer asks the
  member to confirm. A later slice can count a colleague once for the test of
  a full page, and keep the member record. `rows_of` already joins one
  address into one row. The fences are
  `test_the_limit_each_member_record_takes_a_place_of_the_page` and its R8
  twin.
- The drop of a sender row is local to the answer. A sender row of a
  colleague stays when the answer holds no alias, person or member row of
  that address. That happens without `feature:people`, when the People read
  fails or runs late, or when the People name does not hold the asked words. FIND-5
  gave the same row, so no new text shows.
- Migration 206 never rewrites the People row of a member. A member who was
  active and is now suspended keeps an `active` People row, and that row
  prints the address. `PEOPLE_SQL` reads the same row, so `resolve_person`
  prints it too.
- An alias matches the whole name only. "Raj K" does not match the alias
  «Raj».
- `resolve_person` matches an address with an alias. `resolve_name` takes a
  name, so an address matches no alias there.
- The address check of a sender refuses an apostrophe. So a person whose
  address holds one prints no address, and a member or an alias with one
  gives no row. `resolve_person` prints such an address.

### 5.3 How the app search tools wrap the engine

| Tool (where) | What stays | What changes | Slice |
|---|---|---|---|
| `search_crm` (pack `crm-records`) | The kind filter, the lists and the counts | Word by word and near, through `core.search_where`, the predicate of the engine's CRM source. A zero ends with "search_company also tries near spellings, linked records and the other apps." | HRD-18a, FIND-6 |
| `crm_list_records` (family `crm`) | The list, the sort and the filters | The same predicate | HRD-18a |
| `query_inbox` (pack `email`) | Each filter | A sender name tries its near spelling, in the email app's own function. The same zero line | HRD-18b, FIND-6 |
| `find_contact` (pack `email`) | The address lookup | The one matcher | HRD-18a |
| `find_tasks` (pack `projects`) | The task filters | Most of the words on a miss. The same zero line | HRD-4, FIND-6 |
| `resolve_person` (core) | The colleague address for an act | The one matcher. It leaves the core list one release after `resolve_name` passes its cases (D-FIND-3). FIND-6b closes the parity gaps. The removal is the next PR (D-FIND-3) | HRD-18a, FIND-6, FIND-6b |

No app tool gets a second matcher, and no tool reads a second store. So no
second read path exists.

**As built (FIND-6, task 3243, 2026-11-17).**

- **The zero line.** `company_search_tools.with_zero_pointer` ends a zero
  answer with `FIND_POINTER`, the sentence of the table, on its own line.
  `search_crm` gives it after "Nothing in … matches". `find_tasks` gives it
  after "No task matches". `query_inbox` gives it only when the route sends
  the integer 0 as the total. A page past the end of a total gets none. The
  tool also gives no pointer for a missing total, `None`, a text or a bool.
- **The gates.** Each gate fails closed. The call is a pack call of the one
  assistant (`egress._in_pack_call`). `COMPANY_SEARCH_ENABLED` is on.
  `CONTEXT_RECENCY` is on (`company_search_tools.context_recency_enabled`).
  `FIND_ORGS` names the org of the run (`company_search_tools.find_on`, which
  asks `find.find_allowed`). A sub-run of a specialist holds no
  `search_company`, so it gets no line. A spawn child is the one exception
  (see the limits).
- **Why `CONTEXT_RECENCY`.** The route runs the ladder only with it on
  (`company.py`, `find_on`). With it off, `search_company` tries no near
  spelling and no linked record, so the pointer would not be true.
- **A failure is no zero.** A read that fails raises before the zero branch.
  The HRD-3 guard of the pack call then answers it, with no pointer.
- **A near answer is no zero.** A `search_crm` answer with near spellings
  only shows rows that the member can open, so it gets no pointer.
- **The failure text.** For an org of `FIND_ORGS`, `build_orchestrator_agent`
  wraps `search_company`, `resolve_name` and `resolve_person` in
  `pack_tool_guard.guard`, the one guard of HRD-3. A failure then reads "The
  tool failed (<class>). Nothing changed.", with a next step. The text names
  the class of the error, never its message. The guard needs no
  `CONTEXT_RECENCY`, and neither does `resolve_name`: `GET /search/names`
  does not read it. Each other org keeps the tool objects of before, so MAF
  answers its failure as before.
- **`resolve_person` stays (D-FIND-3).** When FIND-6 merged, the parity did
  not hold. `resolve_name` had no source that gave a member (`app_user`). It
  did not read the member's saved aliases. A person row printed no address.
  `resolve_person` gives all three. So FIND-6 kept it in the core list, with
  the guard. FIND-6b closed the three gaps. The removal is the next PR.
- **How `resolve_person` leaves (decided by the planner, 2026-11-17).** Option
  (a). FIND-6b (task 3665) gives `GET /search/names` the kind `member`, the
  saved aliases (tier 1, match `alias`) and the address of a person row.
  One release after FIND-6b merges, a second PR takes `resolve_person` out of
  `core_tools` and gives the pack cap its place back. The owner can override.
  `test_find_tools.py` names the gaps, so the slice that closes them must
  update the fence.

**Limits of FIND-6.**

- A spawn child (OA-9c) holds the read tools of its packs and no
  `search_company`. Its pack call still opens the gate, so its zero line
  names a tool that it does not hold. The parent reads the answer, and the
  parent holds the tool.
- The row FIND-6 says that no find tool answers "Error: Function failed.".
  As built, the text covers the core find tools of the one assistant, and its
  pack calls through the HRD-3 guard. A delegated sub-run of a specialist
  keeps the text of MAF for an exception that is no refusal, because its
  tools have `refusals_as_text` only. An org off `FIND_ORGS` also keeps the
  text of MAF for the find tools of its core list.
- `failure_text` names the class of the error, such as `RuntimeError`. That
  is the choice of HRD-3.

### 5.4 The standing rule

One rule joins the grounding paragraph of `knowledge_base.md` §8.2, in about 45
words:

```text
When the member names a person, a company or a record, call search_company
once, with all their words. Read how each hit matched. A near or related hit
is a lead: say the spelling or the link you used. Before you say that a record
does not exist, read the "Where else" line.
```

A change to the persona moves its fences in the same PR.

**As built (FIND-6).** `orchestrator/agents.py` holds the rule as
`FIND_STANDING_RULE`, in the words above. One exact swap (`_FIND_SWAP`) puts
it at the end of rule 1, right before rule 2. So it closes the grounding
paragraph, after the sentences of CX-1. It needs `FIND_ORGS`,
`COMPANY_SEARCH_ENABLED` and `CONTEXT_RECENCY`. With one of them off, the
persona is the persona of before, byte for byte, and `test_find_tools.py`
pins its hash.

- **The goldens.** The preamble goldens did not move. `test_native_session_persistence.py`
  and `evals/trajectories/test_native_structured_history_trajectory.py` pin
  the per-turn context with a stand-in persona, not this persona. The new
  `evals/trajectories/test_find_family_trajectory.py` pins the rule and the
  tool list of the real run.
- **Why `CONTEXT_RECENCY`.** The route runs the ladder only with it on, and
  only the dated tool prints how a hit matched and the "Where else" line.
  With it off, the rule would name lines that the answer does not print. So
  an org of `FIND_ORGS` with it off gets `resolve_name` and no rule.
- **Two texts differ.** The docstring of `search_company` says "put one name
  in each call". The rule says to call it once, with all the words. The
  slice kept the docstring, because every org reads it.

### 5.5 The harness checks a "not found" claim

`acb_skills/answer_check.py` holds each answer before the member reads it
(CRM-Q29, NUM-3, HRD-13). FIND-7 adds one more check to the same hold.

Some answers say that a record does not exist, or that the chat cannot find
it. Such an answer needs a `search_company` answer in the same turn, with pass
2 run. When the turn has none, the chat gets one more pass that asks it to
search with all the words.
The check shares the one retry of NUM-3 and HRD-13. It logs in `shadow` first
(D-FIND-8).

**As built (FIND-7, task 3244).**

- **The home.** `CheckClaims` in `acb_skills/answer_check.py` is a second
  chat middleware on the one provider, `AnswerCheckProvider`. The one seam,
  `_tool_injection._apply_answer_check`, attaches it to an agent whose own
  tools hold `search_company`. A spawn child gets no check. It sits inside the
  number check, so it reads the model's own text before a note joins it.
- **Only an org of `FIND_ORGS`.** Pass 2 exists only for such an org, so the
  seam attaches the check only there. It asks
  `gateway.retrieval.find.find_allowed`, the one reader of `FIND_ORGS`, for
  the org of the run binding. No org, or a fault of the reader, reads as off.
  For any other org the seam adds no middleware and writes no log line, as
  with the flag `off`.
- **The final answer only.** The check reads a model response that calls no
  tool. A step that calls a tool is no answer, because the run goes on.
- **The mark of pass 2.** `company_search_tools.ran_second_pass` is the one
  reader, beside the code that writes each line. A line of rung 2 starts with
  "Each word alone:". A line of rung 3 starts with "Near spellings:" or
  "No record holds «". A trace counts too, because the explain mode runs
  each rung.
- **No record can fake the mark.** The tool folds each line break of a
  title, a hint, a snippet and the query, for each boundary of
  `str.splitlines`. The "Tried:" line quotes the query, so the reader looks
  for "Near spellings tried:" only after the quote of the query closes.
- **Why not the log line.** `search.company.ladder` is a log line of the
  gateway. The harness reads only the text of the tool answer.
- **The matcher.** `not_found_claim` has 7 rules with closed codes:
  `no_such`, `could_not_find`, `no_record_of`, `there_is_no_named`,
  `does_not_exist`, `found_nothing` and `not_in_place`. Each rule needs a
  record noun from a closed list, or a name. A name has a capital letter or
  a digit on each word, or it starts with a quote mark.
- **What the matcher reads as no claim.** The tests hold each form below,
  and the matcher reads no other form as no claim.
  - A question, a condition ("if", "unless", "whether"), a quote, a quoted
    block and code.
  - A preposition or a list word between "find" and the noun: "any errors
    in the invoice", "any overdue tasks", "any duplicate contacts".
  - Reported speech, when a person is the subject right before the verb:
    "Priya replied that there is", "Ravi says he could". A tool or a store as
    the subject keeps the claim: "The CRM says there is no such order".
  - The state of one record right before the claim: "The invoice shows no
    record of". A store keeps the claim: "Our records show no record of".
  - A record that the answer makes, for the rule `does_not_exist` only:
    "does not exist yet", "did not exist, so I created it".
  - A list filter right after the claim: "no matches for your filter".
- **The bounded work.** The matcher runs on the event loop of the gateway.
  It reads the first 20,000 characters of an answer (`MAX_CLAIM_CHARS`), and
  each quote is 400 characters at most (`MAX_QUOTE_CHARS`). Each regex runs
  once over a sentence. So the cap of 20,000 characters bounds the work. On
  2,000,000 characters of each shape of the reviews, the best of 3 calls
  took at most 0.25 s of CPU here.
- **The flag.** `FIND_CLAIM_CHECK` takes `off`, `shadow` or `on`. The flag
  is global, but the check acts only for an org of `FIND_ORGS`.
  `acb_common.settings.find_claim_check_mode` is its one reader. It is not a
  `*_ORGS` or `*_MODES` key, so a change needs a restart.
- **`shadow`.** The check writes at most one `answer_check.claim` line for a
  run, and changes nothing. The outcome is `shadow_miss` or `shadow_pass`.
- **`on`.** The check holds each answer once any find tool of §5.2 or §5.3
  answered in the run (`FIND_TOOLS`). A claim with no pass 2 gets one more
  model request, with the fixed text `CLAIM_RETRY_NOTE`. After it, the answer
  goes as it is. An app's search tool alone (`search_crm`, `query_inbox`,
  `find_tasks`, `crm_list_records`, `find_contact`) has no pass 2, so a claim
  after it alone gets the retry too. The planner decided this on task 3244:
  §5.5 binds.
- **A failed retry.** When the one more request raises, the member reads the
  held draft, as with the check `off`. The line says `retry_failed`. A
  cancel is not caught, so it stops the run as before. The failed request
  still spends the one retry of the run. So the number check then adds its
  note, and it makes no request of its own.
- **The one retry.** NUM-3 and HRD-13 are not built. So the check shares
  `RunLedger.retried` with the number check of CRM-Q29. The check that
  finds a fault first spends it. A later claim logs `retry_spent`.
- **The log line.** `answer_check.claim` holds `mode`, `outcome`, `rule`,
  `searches`, `finds` and `armed`. `finds` counts the results of every find
  tool of §5.2 and §5.3. `armed` is true when `search_company` or
  `resolve_name` answered before the claim, so the retry is a fair request to
  run pass 2. It is false when only an app's search tool ran, or none
  (`finds` 0). The line holds no word of the answer, of a record or of the
  member. The run context adds the run id.
- **Off.** With the flag `off`, and for an org off `FIND_ORGS`, the seam
  attaches what it attached before. The log line `executor.answer_check_on`
  keeps its fields. Fence: `tests/unit/test_answer_not_found.py`.

**The limits of FIND-7.**

1. The matcher reads English only.
2. The matcher reads the first 20,000 characters of an answer. It does not
   read a claim after them. It reads a quote of more than 400 characters as
   prose, and not as a quote.
3. The matcher misses these claims, and the tests keep each miss:
   - "Sharath has no orders" and "I could not find anything like that
     order".
   - "Unfortunately, no order for Sharath turns up in the records".
   - "Your inbox has no mails from Sharath" and "Our records show no order
     for Sharath".
   - A name in lower case, such as "nuropeak in the CRM".
   - "Sharath is not in the CRM yet" and "Sharath is not in the CRM or the
     inbox". The rule `not_in_place` needs the clause to end after the place.
   - A claim in a question, in a condition, and in reported speech with a
     person as the subject.
4. The matcher does not read "no mention of" or "no trace of". A summary of
   a mail uses them for what the mail holds.
5. The matcher reads these sentences as a claim, a false positive. The
   tests keep each one.
   - An empty list: "I could not find any tasks for this week", "I could
     not find any tasks due today", "No results for this week".
   - Reported speech in other forms: "Priya replied: we could not find the
     payment", "In his mail, Ravi asks why he could not find the order",
     "The client mentioned that the deal does not exist".
   - More reported speech: "Ravi's mail says that the order does not exist",
     "According to Ravi, there is no such order", "The customer could not
     find the invoice", "Mr. Rao could not find the invoice".
   - Reported speech that the rule does not see, because no person and verb
     of speech come right before the claim: "Ravi: I could not find the
     invoice", "Ravi wrote back and could not find the invoice", "Your note
     says you could not locate the shipment".
   - The state of a record in other forms: "No record of a payment yet, so
     the invoice is open", "There is no record of a payment on this invoice".
   - A word that names no record: "I could not find Settings", "Python is not
     in the system", "PDF is not in the inbox", "I found nothing for Q4".
   - A match that is no record: "I could not find a match between the two
     invoices".
   - An old fault that the answer reports: "Earlier I could not find the
     order, but here it is".
6. The matcher reads "I could not find the order, so I created it" as a
   claim. The same is true for a lead. The new record may be a second copy.
7. The check reads the text of the answer, not the text of a card.
8. Pass 2 shows no line for a query of one word whose near lookup found no
   name. So the check fires after such a search.
9. With `CONTEXT_RECENCY` off, the route returns after pass 1. The plain
   tool then shows no mark. See `retrieval/company.py`, the return before the
   words alone. So in `on`, each claim after a search takes the retry, and no
   claim can pass. On production `CONTEXT_RECENCY` is true: the planner read
   it on the box at 08:40 UTC on 2026-11-17.
10. Pass 2 does not run when the first pass has a hit with every content
    word, or for a query with an operator. With `FIND_ORGS` off, it runs only
    when the first pass finds no hit. In each case the check fires on a
    claim.
11. In `on`, the check holds no answer before the first find result of the
    turn. A claim with no find tool at all streams as it is, and the check
    logs `unheld_miss` with `finds` 0. `shadow` logs it as `shadow_miss` with
    `finds` 0. The planner accepted this gap: such an answer comes with no
    search at all.
12. In `on`, after the first find result of a turn, the member sees each
    later answer only when the model has drafted all of it. The text does not
    stream. This is true for each answer, not only for a claim.
13. A search of a sub-agent does not count for the run of its parent.
14. The check does not compare the words of the search with the words of the
    member. Any `search_company` answer of the run that shows pass 2 passes.
15. `shadow` logs no text. So the line cannot tell a false positive from a
    true miss. `armed` and `finds` show which claims came after a lookup.
16. In `on`, a claim after `resolve_name` alone always takes the retry. A
    `resolve_name` answer never shows pass 2, so it cannot pass the check.
    The cost is one more model request in each such turn, and one more
    search when the model obeys the retry text.
17. In `on`, a true empty list after an app's search tool costs the retry,
    for example "I couldn't find any tasks due today" after `find_tasks`. The
    line then says `armed` false. The planner accepted this cost, and the
    owner reads 20 shadow lines before the flip (decision 1).
18. The check acts only for an org of `FIND_ORGS`. In any other org it adds
    nothing, in each mode.

**Decisions for the owner before `on`.** The planner decided these on task
3244, and the owner can change them.

1. **Read 20 shadow lines before the flip.** Read 20 `answer_check.claim`
   lines with their turns, from the HathiLabs org only, with no chat of a
   customer. Count the false holds, and write the count on task 3244. With
   more than 2 false holds in 20, do not flip to `on`.
2. **`CONTEXT_RECENCY` on production is true.** The planner read it on the
   box at 08:40 UTC on 2026-11-17. On that box, `FIND_ORGS` and
   `FIND_CLAIM_CHECK` are not set, so both are off. Limit 9 does not apply
   there.
3. **The precision of the matcher.** Shadow cannot measure it, because the
   line holds no text (limit 15). The planner accepted this. Decision 1
   answers it.

## 6. Privacy and tenancy

- **Tenancy is a row (D15).** Each lookup runs in the bound tenant session. No
  org and no member comes from input (R5).
- **Each rung runs as the member (D12).** It goes through the app's own read.
  The engine adds no visibility rule.
- **A near name comes from rows that the member can open.** CRM names come
  after the CRM gates. People names need `feature:people`. Mail senders come
  from the mailboxes that the member can read, by the email app's account
  filter. A name from the mailbox of another member never shows, as a hit, a
  count or a "Do you mean".
- **A related record goes through `open`** (`knowledge_graph.md` §6). A record
  that the member cannot open leaves no trace.
- **Past chats are the member's own (D-FIND-5).** Only the solo sessions of the
  member in this org. Never the chat of another member, a room, a `no_egress`
  session or the current chat. HRD-21 is HIGH.

**As built (HRD-21): who can read what.** One statement of
`gateway/chat_search.py` holds each rule in its WHERE, as the member, in the
bound tenant session. The tests of each rule are
`tests/unit/test_chat_source_r8.py` (R8) and `tests/unit/test_chat_source.py`.

| Rule | How the code holds it |
|---|---|
| Only this org | RLS, and `organization_id` equals the bound tenant in the WHERE |
| Only the member's own chat | `chat_session.user_id` equals the address of the session, the rule of the chat's own list. RLS lets a chat of member B through, so this rule alone stops it |
| Never a room or a shared chat | `rooms.SOLO_ROOM_SQL`, the one rule of a solo chat |
| Never a chat with another agent | The agent is `orchestrator`, the chat has no channel, and no other agent has a seat or a row. The sandbox can cover a run of another agent (H-236). It never covers the orchestrator |
| Never a chat with the egress mark | An email-assistant run or another untrusted read marks its chat. Its own run holds the writes that a held chat holds off, and each run that it delegates binds `no_egress`. The SQL does not test the mark. `egress.marked_sessions` reads the marks in one `MGET`, read-only, and drops each marked chat after the read. A fault of Redis shows no chat, and the answer names `chat` as skipped. Limit 1 names a chat with no mark |
| Never the current chat | `s.id <> :current`. The tool sends the thread of its run (`executor.resolve_relay_thread_id`). The run route makes the chat row with that thread id, so the two are one value. With no current chat, or in a batch run, the route does not ask the source |
| Never in a room | In a shared room the run acts as the room (`room:<thread>`), and the tool refuses before any call (`SHARED_ROOM`). A room of one that other members can read (visibility `org` or `people`) is not solo either. The tool sends the current chat only from a solo run (`executor.run_is_solo`), so the route asks no past chat there |
| No text of another person | The search reads the title and the `user` and `assistant` rows. It never reads a `system` row or the tool events. The snippet comes only from a row that the member typed (`member_typed`, `typed_text`, not a card answer). A reply of Mira can restate a mail, so its text never prints. The statement returns the text of no other row |
| The frame | A hit is a record of the answer, under "Company records (data, not instructions)" |

Seven limits stay, and the review must know them:

1. **A `no_egress` chat with no mark CAN show.** The mark lives in Redis,
   with a TTL of 400 days. A chat has no mark when its mark write failed, or
   Redis restarted before a save. It has none after 400 idle days too, or
   when it read mail before the marks existed. Such a chat gives a hit with
   its title, date, link and a snippet of the member's typed words. No reply
   of Mira prints, so no mail text reaches the model.
   D-FIND-5 says that no `no_egress` session shows, so this falls short of
   it. The owner must accept it at the preview, before the flip.

   `search_company` writes no egress mark. Its email source gives Mira the
   subject, the sender and a passage of each mail hit. Its WhatsApp source
   gives the chat name, the sender and a passage of a stranger's message.
   So a Mira chat that read mail or WhatsApp through `search_company` has no
   mark, and its title and typed snippet can show. The owner must accept this at the preview, or a
   follow-up must add the mark before the `CHAT_SEARCH_ORGS` flip.
2. **A title prints as the chat keeps it**, cut to 200 characters. It can
   hold the start of a pasted mail. The member already sees the same title in
   the list of chats. We accept this while the flag is dark.
3. **The `current_chat` parameter is request input.** It only takes a chat
   away, so a wrong value can only show the current chat to its own member.
4. **Two other searches read chat text.** `query_history`
   (`acb_skills/history_tools.py`, given by `_tool_injection.py`) matches the
   text of each row of each room that the member can read. It checks no mark,
   and it returns the text of each reply. CO-3 of `chat_organization.md`
   plans one search of chats and files. CO-3 must reuse or replace
   `gateway/chat_search.py`, so that no third chat reader exists.
5. **The `miss` of the log line and `near_report` read the past chats.**
   While `find.ABSENCE_CLAIMS` is off, no line reads them as a claim. The
   slice that sets it on must decide how a past chat counts.
6. **The cost before the cut.** `ONLY_AGENT_SQL` reads each row of each chat
   that it tests, before the cut to 100 chats. Each word arm runs a regular
   expression that no index serves. Run EXPLAIN ANALYZE on a large chat
   history before the flip.
7. **A word with an accent finds less.** The whole-word pattern ends a word
   only at an ASCII mark or a few typographic marks. Any other character stays
   inside a word, so the SQL never finds more than `find.coverage` counts.
   The cost: "muller" does not find «Müller», and "Müller" finds no chat.
- **An outside source names whose access it used** (D98). It stores nothing
  and logs no word.
- **The answer is data.** It keeps the header and the quotes of KR-2, and the
  closed lists of `matched_by` and `edge`. A search closes the profile writes
  of the run (PZ-6a), and `resolve_name` does too.
- **Logs hold counts, times and source keys only.** No query, name, address or
  id.

## 7. Cost and speed

- **No chat model runs in the engine.** The meaning rung makes at most one
  embed call, behind `KNOWLEDGE_EMBED` and the email semantic flag. Both stay
  owner flips.
- **The later passes run only when they can help.** Pass 2 runs on a miss.
  Rung 4 runs on hits of five kinds. Rung 7 runs on a miss, or when named.
- **The answer stays under about 3,200 tokens.** The hits take about 1,500,
  the related records at most 1,500 (`knowledge_graph.md` §7.1), and the honest
  zero about 200. The explain trace takes about 600.
- **`resolve_name` adds about 150 tokens** of schema to the cached prefix of
  each request.
- **The chat saves model rounds.** The first Sharath answer made 7 model
  requests and 12 tool calls in 23 seconds. We expect one call of at most 6
  seconds, and one answer. That saves about 5 model requests, of 1.5 to 3
  seconds each.
- **The database load stays bounded.** Pass 1 reads each source once. Pass 2
  makes at most 3 near lookups for each name source, and asks at most 5 words
  again (the cap of KR-ZERO). The cap of 4 keeps 5 of the 9 sessions of a
  process free.

## 8. Flags

| Flag | Default | What it opens | Gate |
|---|---|---|---|
| `FIND_ORGS` | empty | `matched_by`, the coverage order, rung 3 in the engine, rung 4, the honest zero lines, the explain mode, `resolve_name` and the standing rule. In the engine it acts only with `CONTEXT_RECENCY` on. It also opens the sender near spelling of `query_inbox` (HRD-18b), which needs no `CONTEXT_RECENCY`. Empty gives the answer of KR-ZERO, byte for byte. It reloads with no restart (`docs/flag_reload.md`) | AGENT-SAFE. It spends nothing. Flip it under the `enforcement-flip` grant, after the FIND-1 eval |
| `CHAT_SEARCH_ORGS` | empty | Rung 6, the `chat` source (HRD-21). Empty gives the answer of before HRD-21, byte for byte, and the 422 of before for `apps=chat` and each other unknown app. Its one reader is `find.chat_search_named`. It reloads with no restart (`docs/flag_reload.md`) | The owner sees the preview before the flip (task 3225). Owner-only in `env_guard` |
| `FIND_OUTSIDE_ORGS` | empty | Rung 7 (FIND-10) | **OWNER-GATE.** A read of a third party |
| `FIND_CLAIM_CHECK` | `off` | The "not found" check of §5.5: `off`, `shadow` or `on` | `shadow` is AGENT-SAFE. `on` is an `enforcement-flip` after 7 days of shadow |
| `KNOWLEDGE_EMBED`, `EMAIL_SEMANTIC_SEARCH_ENABLED`, `GRAPH_SEARCH_ORGS` | As built | Unchanged | Unchanged |

## 9. The ladder of slices (D211)

### 9.1 Rungs and demos

| Rung | Demo task | The demo on production | Slices |
|---|---|---|---|
| 1 | 3234 | "Sharath's printer order" finds the deal «Neuropeak - Snowflake 3D printer» and the 12-message thread in one call. "nuropeak printer" finds them too. Each hit names the spelling it used | FIND-0, FIND-1, KR-ZERO, HRD-18a, HRD-4, FIND-2, HRD-18b, FIND-3 |
| 2 | 3245 (FIND-D2) | An order that only a past chat holds shows that chat. A search for a thing that does not exist names what it searched, the spellings it tried and where else to look. "Why could you not find it?" gets the trace. "Who is Sharat?" gives the lead, the contact and the mail sender in one call | FIND-4, HRD-21, FIND-5, FIND-6, FIND-7 |
| 3 | 3249 (FIND-D3) | A Studio app's records answer a near name through its manifest. A connected outside app answers a miss and names whose access it used | FIND-8, FIND-9, FIND-10 |

Rung 0 is empty. No leak is known, and the board tags the false "no order"
answer as rung 1 work.

HRD-21 moves from rung 1, its tag on the board, to rung 2. Demo 3234 needs no
past chat, and HRD-21 is HIGH with an owner preview.

### 9.2 The slices

| Slice | Task | Rung | What | Done when | Fence (R7) | Tier · gate | Depends on |
|---|---|---|---|---|---|---|---|
| **FIND-0** | 3233 | 1 | This spec, its INDEX row, the §2.1 row and the pointer in `knowledge_base.md` §20.10 | The lint passes, and INDEX lists the spec | `test_index_completeness.py`, `test_doc_names_resolve.py`, ste-lint | LOW · AGENT-SAFE | none |
| **FIND-1** (built) | 3238 | 1 | A misses set for the KG-1 eval: 20 synthetic misses in `evals/search_company/fixtures/misses.json`. Each names its kind (near name, words spread over records, related only, converted lead, past chat, outside) and the rung it needs. The real misses of production stay off the repo, in `~/.metorite/evals/kg1/` | `--fixture` prints the misses score, found in the top 5 of one call. The baseline is a comment on epic 3232. Each later slice writes its numbers before and after, and a drop does not merge. The end of rung 2 scores 18 of 20 or more | `test_search_company_eval.py`, extended | LOW · AGENT-SAFE | none |
| **KR-ZERO** | 2974 | 1 | Rung 2 (each word alone), the embed of rung 5 at 0 s, and `meaning` and `broadened` in the answer | As its task. Built on `mt-bdf0d8ea-kr-zero`, not merged | `test_company_search_zero.py`, `test_company_search_r8.py` | STANDARD · AGENT-SAFE | none |
| **HRD-18a** | 2997 | 1 | The one matcher `acb_common/name_match.py`. CRM search word by word, a deal that matches its contacts and source lead, and `?near=true`. `resolve_person` and `find_contact` use the matcher | As its task. Built on `mt-53eeea95-near-names`, not merged | `test_name_variants.py`, `test_crm_near_search_r8.py` | STANDARD · AGENT-SAFE | none |
| **HRD-4** | 2983 | 1 | Rung 1 at once: each source of pass 1 in parallel, at most `FIND_SESSIONS` (4) at a time. `find_tasks` ranks by most of the words on a miss | "Sharath 3D printer" names no skipped source on the fixture. A slow source does not hold a fast one. A fifth source waits for a slot | `test_search_fallback.py` | STANDARD · AGENT-SAFE | KR-ZERO |
| **FIND-2** | 3239 | 1 | `matched_by`, `covers` and `of` on each hit, the order of §3.4, the fold of §3.3, the print of §3.5, and the flag `FIND_ORGS` | On the fixture, a deal that holds 3 of 3 words comes before 15 that hold 2. "Sharath's" folds to "sharath". With `FIND_ORGS` empty, the answer and the tool text are byte-identical | `test_find_matched_by.py`, new | STANDARD · AGENT-SAFE | KR-ZERO |
| **HRD-18b** (built) | 3226 | 1 | Rung 3 in the engine: the `near` ability of the CRM, people and email sources, the near lookup of pass 2, the query again with the best spelling, and `matched_by` `near`. `query_inbox` tries the near spelling of a sender | "Sharath 3D printer" finds the Neuropeak deal and the mail from «Sharat Satyanarayana». "nuropeak" finds the deal and the mail from `neuropeek.com`. A sender of member B never shows for member A | `test_find_near.py`, new. `test_find_near_visibility_r8.py`, new. `test_one_name_matcher.py`, new: each `pg_trgm` floor comes from `name_match` | STANDARD · AGENT-SAFE | KR-ZERO, HRD-18a, FIND-2 |
| **FIND-3** (built) | 3240 | 1 | Rung 4: the `related` and `open` abilities of the CRM, people and email sources, and the step on the best 5 hits, 3 for each hit, in 1.0 s. It builds the step and `Source.open` that KG-6 names. KG-6 then adds the `kb_links` edges to the same step | "Neuropeak order" finds the deal, and the thread from the address of its lead, in one call. A contact's mail in the mailbox of another member never shows. A record that the member cannot open leaves no trace. Org B never shows | `test_find_related.py`, new. `test_find_related_r8.py`, new | HIGH · AGENT-SAFE | FIND-2 |
| **FIND-D2** | 3245 | 2 | The demo of rung 2 (§9.1) | The owner uses it on production | none | demo | FIND-4 to FIND-7, HRD-21 |
| **FIND-4** (built) | 3241 | 2 | The honest zero and "Where else" of §3.6, the explain mode of §3.7, and the log line | Each line comes from code, from a closed list. No line names a record that the member cannot open. `explain=true` gives the trace with no snippet. The log line holds no word | `test_find_honest_zero.py`, new | STANDARD · AGENT-SAFE | FIND-2, HRD-4, HRD-18b |
| **HRD-21** (built) | 3225 | 2 | Rung 6: the `chat` source of `company_context.md` §8.2, the first part of CX-4, with no digest. It reads the titles and messages of the member's own solo sessions in this org. A hit gives the title, the date and a link. Item 3 of its task, the next places to try, moves to FIND-4 | Member B never sees a chat of member A. A room, a chat with the egress mark and the current chat never show. A `no_egress` chat with no mark can show (§6, limit 1). The key is `chat` | `test_chat_source_r8.py`, new. `test_company_search_sources.py` | HIGH · The flip of `CHAT_SEARCH_ORGS` waits for the owner's preview | KR-ZERO, FIND-2 |
| **FIND-5** (built) | 3242 | 2 | `resolve_name` and `GET /search/names`, over the `near` ability of each source | "Sharat" gives the lead, the contact and the mail sender, each with its link and its match. A near name says so. The tool refuses with no member, and answers each failure as text | `test_resolve_name_tool.py`, new. `test_find_near_visibility_r8.py` | STANDARD · AGENT-SAFE | HRD-18b |
| **FIND-6** (built, `resolve_person` stays until parity, §5.3) | 3243 | 2 | The family of find tools. It adds the zero line of `search_crm`, `query_inbox` and `find_tasks`, and the standing rule of §5.4. It adds failure text to the core find tools. After parity, `resolve_person` leaves the core list | With `FIND_ORGS` on, the core list holds `search_company` and `resolve_name`. Each zero of an app search tool ends with the pointer. No find tool answers "Error: Function failed." Its fences move in the same PR | `test_find_tools.py`, new. `evals/trajectories`, `test_native_session_persistence.py` | STANDARD · AGENT-SAFE | FIND-4, FIND-5 |
| **FIND-6b** (built) | 3665 | 2 | `GET /search/names` gives the kind `member`, the saved aliases (tier 1, match `alias`) and the address of a person row. One release later, `resolve_person` leaves `core_tools` and the pack cap gets its place back (D-FIND-3) | A name of a colleague gives the member, the alias match and the address, as `resolve_person` does. FIND-6b changed the pins of the gaps in `test_find_tools.py` in the same PR | `test_resolve_name_tool.py`, `test_find_near_visibility_r8.py` (R8), `test_find_tools.py` | STANDARD | FIND-5, FIND-6 |
| **FIND-7** (built) | 3244 | 2 | The "not found" check of §5.5 in `answer_check.py`, behind `FIND_CLAIM_CHECK` | In `shadow`, a "no such order" answer with no pass 2 in the turn logs one miss, with no text. In `on`, it gets one more pass, which shares the retry of NUM-3 and HRD-13 | `test_answer_not_found.py`, new | HIGH, the run loop · `on` is an `enforcement-flip` | FIND-4 |
| **FIND-D3** | 3249 | 3 | The demo of rung 3 (§9.1) | The owner uses it on production | none | demo | FIND-8 to FIND-10 |
| **FIND-8** | 3246 | 3 | `contributes.search` made real: one entry in each shell manifest of an app with a source, `load_search()` in `shell_manifest.py`, the lockstep of §4.4, `BAR_APPS` and `TAU_DAYS` read from the manifest, and the `building_blocks.md` §3.1 row set right | No behaviour changes: the search suites pass unchanged. A source with no entry, or an entry with no source, fails the fence | `test_search_contributions.py`, new. `test_shell_manifests.py`, `test_shell_search.py` | STANDARD · AGENT-SAFE | FIND-3, HRD-18b |
| **FIND-9** | 3247 | 3 | The Studio source of §4.5: one source for each install that declares `contributes.search`, over the object query of C2a, as the member | A near name finds a Studio record. A member outside the scope of the install gets nothing. Org B gets nothing | `test_find_studio_source_r8.py`, new | HIGH · AGENT-SAFE. It follows `STUDIO_ORGS` | FIND-8 |
| **FIND-10** | 3248 | 3 | Rung 7 of §4.6: the find tool of a connection, its admin switch in `provider_meta`, the source at request time, and the cap of 8 s | On a miss, the find tool of a test MCP server answers. Each hit names the app and whose access. A server with `always_confirm` is never asked. Nothing is stored. With `FIND_OUTSIDE_ORGS` empty, nothing is asked | `test_find_outside_source.py`, new | HIGH · The flip is **OWNER-GATE** | FIND-8, FIND-4 |

**The order.** First FIND-1 and the merges of KR-ZERO and HRD-18a. Then HRD-4
and FIND-2 in parallel. Then HRD-18b and FIND-3, and then demo 3234. In rung 2,
FIND-4 and HRD-21 run in parallel, then FIND-5, FIND-6 and FIND-7. In rung 3,
FIND-8 comes first, then FIND-9 and FIND-10.

A test file marked "new" does not exist yet. The slice that names it writes
it, red first.

## 10. Decisions for the owner

**ACCEPTED, 2026-11-14.** The owner chose "Approve all 8 recommendations" on
task 3233 (answer `30dfa0d6`). Each recommendation below is now the rule.

| # | Question | Recommendation | Why |
|---|---|---|---|
| **D-FIND-1** | Does the ladder tool keep the name `search_company`, or take the name `find`? | Keep `search_company` | The persona, the goldens, KG-8 and five specs use the name. The model already calls it first, 85 times in 3 days. The gain comes from the ladder, not from the name |
| **D-FIND-2** | Is the debug tool a third tool `why_nothing`, or a mode of `search_company`? | A mode: `explain=true` | Each core tool rides in every request. One tool with one more argument costs less, and the model has one name to learn |
| **D-FIND-3** | How many tools take a name? | One: `resolve_name`. `resolve_person` leaves the core list one release after `resolve_name` passes its cases. KG-7 `get_entity` becomes the card mode of `resolve_name`, not a second tool | Three tools that take a name are three ways to do one thing (CLAUDE.md §5) |
| **D-FIND-4** | What starts pass 2? | A miss: the best hit holds fewer than all the content words | The Sharath chat took a hit with one word of three. A rule of zero hits only would not have run |
| **D-FIND-5** | Which past chats does rung 6 read first? | The member's own solo sessions in this org, with the key `chat`. Not a room, not a `no_egress` session and not the current chat. Rooms come later with the `since_join` rule of CX-4 | The member already sees these sessions. A room needs the rule of who joined when |
| **D-FIND-6** | How many sources run at once? | At most 4 in one gateway process | A process has 7 sessions and 2 more. A search must leave room for every other request |
| **D-FIND-7** | May an outside app answer a search? | Yes, on these terms: an admin marks one read-only tool for each connection. It runs on a miss or when named. Each hit names whose access it used. Nothing is stored. The flag is the owner's | It follows `number_sense.md` §5 for outside apps, and D98 for the identity of a connection |
| **D-FIND-8** | May the harness hold a "not found" answer? | `shadow` for 7 days on production, then `on` by an `enforcement-flip`. `on` gives one more pass, then the answer goes as it is | The Sharath answer said "no order" after searches that never tried a near spelling. A prompt rule alone did not stop it |

## 11. The fences that must stay green

- `tests/unit/test_company_search_sources.py`: no SQL in
  `gateway/retrieval/`, and each source checks its gate first.
- `tests/unit/test_company_search_r8.py`: each source answers its own org
  only.
- `tests/unit/test_shell_search.py`: the command bar does not change.
- `tests/unit/test_company_search_tool.py` and
  `tests/unit/test_company_search_recency.py`: the flag-off answer stays
  byte-identical.
- `tests/unit/test_search_company_eval.py`: the eval and its gate.

## 12. Verification commands

```bash
# FIND-0, the docs
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py -q
node .claude/hooks/ste-lint.mjs project-docs/specs/find_engine.md

# The search suites that every slice keeps green (hermetic)
uv run pytest tests/unit/test_company_search_sources.py tests/unit/test_company_search_tool.py \
  tests/unit/test_company_search_recency.py tests/unit/test_shell_search.py \
  tests/unit/test_search_company_eval.py -q

# The R8 suites and the eval, with a real database. A skip is not a pass
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_company_search_r8.py tests/unit/test_search_company_eval_r8.py -q -p no:xdist -rs
uv run python -m evals.search_company.run --fixture

# Each slice adds its own fence from §9.2, for example FIND-2
uv run pytest tests/unit/test_find_matched_by.py -q

# FIND-7, the "not found" check, with the number check that shares its retry
uv run pytest tests/unit/test_answer_not_found.py tests/unit/test_crm_answer_check.py -q
```

## 13. What this spec does not own

- `knowledge_base.md` §20 keeps the seam, the source, the hit, the route and
  the fences of §20.9.
- `company_context.md` keeps the stamp, recency, the digest and the design of
  the `chat` source (§8). D-FIND-5 sets the scope of its first slice.
- `knowledge_graph.md` keeps the edges, their provenance, the graph half of
  KG-6 and the rule of `open` (§6).
- `chat_failure_modes.md` keeps the classes B2 and F1, the HRD rows and the
  tool contract of §7.
- `number_sense.md` and `answer_check.py` keep the hold and the one retry.
- `package_host.md` keeps the envelope of the manifest and the conflict rule.
- `navigation_shell.md` keeps the command bar.
- `external_agent_access.md` keeps the MCP door, and KG-8 puts
  `search_company` on it.
- `integrations_console.md` keeps the connection rows and their switches.

## 14. Findings: the code against the evidence

1. **The evidence holds on `main`.** The CRM matches the whole query with
   `ILIKE` (`core.py`, `search_where`). A converted lead is hidden
   (`OPEN_LEAD_SQL`). Email uses `websearch_to_tsquery`. The sources share one
   budget of 4 seconds. `QUERY_EMBED_TIMEOUT_S` is 0.8.
2. **A deal matches its company name today**, through `search_sql`. It does
   not match its contacts until HRD-18a merges.
3. **`contributes.search` is a name only** (§4.1). `building_blocks.md` §3.1
   marks the row V, and no manifest declares the point.
4. **The find tools are not in `tool_packs.py`.** `search_company` and
   `resolve_person` are in `core_tools` of `orchestrator/agents.py` (§5.1).
5. **Two names for one source.** Task 3225 names it `chats`. CX-4 (D106) and
   `TAU_DAYS` name it `chat`. Use `chat`.
6. **Two scopes for past chats.** CX-4 reads every session where the member
   takes part, rooms included. Task 3225 reads the member's own sessions only
   (D-FIND-5).
7. **Two related steps.** KG-6 and FIND-3 both build a step and `Source.open`.
   FIND-3 builds them once, and KG-6 adds its edges.
8. **Two name tools.** KG-7 `get_entity` and `resolve_name` both take a name
   (D-FIND-3).
9. **"Every source at once" meets a small pool.** HRD-4 needs the cap of
   D-FIND-6.
10. **The first wrong answer came from an exact hit.** It held a different
    Sharath. A pass 2 that runs on zero hits only would not help (D-FIND-4).
11. **The mails never say «Neuropeak».** The sender domain is `neuropeek.com`.
    Only a near domain (rung 3) or the address of the lead (rung 4) finds the
    thread from "Neuropeak".
12. **The CRM near pass of HRD-18a is narrow.** It runs for 1 to 3 words, and
    only when the exact read finds no row. A query of 5 words gets no near
    pass inside the CRM, so pass 2 of the engine asks each word alone.
