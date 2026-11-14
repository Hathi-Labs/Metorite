# Find: one search engine for the chat, as a ladder of rungs

**Status: PROPOSED, 2026-11-14. Verified against the code on 2026-11-14
(`origin/main` at `ed2274b1c`).** Epic WS-FIND, task 3232. FIND-0 is task
3233. This spec builds nothing. It maps five slices that are in flight or
planned (§9.2). **FIND-1 is built (branch `mt-45237431-find-misses-eval`, task
3238):** the misses set is in `evals/search_company/fixtures/misses.json`, and
`--fixture` prints its score. The owner decides D-FIND-1 to D-FIND-8 (§10). The defaults
hold until the owner answers.

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
| `search_company("Sharath 3D printer")` | No records. "Skipped: email" | Each app needs every word in one record. The sources run one after another in one 4 s budget, and Email ran out of time |
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
| Tasks | `routes/projects/search.py` `search_tasks` | Every word | HRD-4 |
| Company | `retrieval/company.py` | The sources one after another, 1.5 s each and 4 s for all | KR-ZERO, HRD-4 |
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
| 6 | Past chats | The member's own chat sessions | Pass 1, when `CHAT_SEARCH_ORGS` names the org | 1.0 s | `chat` | HRD-21 |
| 7 | Outside sources | An outside app that an admin made a find source | On a miss after pass 2, or when the member names it | 3.0 s. The answer cap rises to 8 s | `outside` | FIND-10 |
| 8 | Honest zero | The answer says what it searched, what it tried and where else to look | Each answer with a miss | None. Code writes it | none | KR-ZERO, FIND-4 |

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
| `near` | `near(user, word, k)` | Names with a score, from rows that the member can open | The floor comes from `acb_common.name_match` |
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

### 5.3 How the app search tools wrap the engine

| Tool (where) | What stays | What changes | Slice |
|---|---|---|---|
| `search_crm` (pack `crm-records`) | The kind filter, the lists and the counts | Word by word and near, through `core.search_where`, the predicate of the engine's CRM source. A zero ends with "search_company also tries near spellings, linked records and the other apps." | HRD-18a, FIND-6 |
| `crm_list_records` (family `crm`) | The list, the sort and the filters | The same predicate | HRD-18a |
| `query_inbox` (pack `email`) | Each filter | A sender name tries its near spelling, in the email app's own function. The same zero line | HRD-18b, FIND-6 |
| `find_contact` (pack `email`) | The address lookup | The one matcher | HRD-18a |
| `find_tasks` (pack `projects`) | The task filters | Most of the words on a miss. The same zero line | HRD-4, FIND-6 |
| `resolve_person` (core) | The colleague address for an act | The one matcher. It leaves the core list one release after `resolve_name` passes its cases (D-FIND-3) | HRD-18a, FIND-6 |

No app tool gets a second matcher, and no tool reads a second store. So no
second read path exists.

### 5.4 The standing rule

One rule joins the grounding paragraph of `knowledge_base.md` §8.2, in about 45
words:

```text
When the member names a person, a company or a record, call search_company
once, with all their words. Read how each hit matched. A near or related hit
is a lead: say the spelling or the link you used. Before you say that a record
does not exist, read the "Where else" line.
```

A change to the persona moves the preamble goldens (`evals/trajectories`,
`test_native_session_persistence.py`).

### 5.5 The harness checks a "not found" claim

`acb_skills/answer_check.py` holds each answer before the member reads it
(CRM-Q29, NUM-3, HRD-13). FIND-7 adds one more check to the same hold.

Some answers say that a record does not exist, or that the chat cannot find
it. Such an answer needs a `search_company` answer in the same turn, with pass
2 run. When the turn has none, the chat gets one more pass that asks it to
search with all the words.
The check shares the one retry of NUM-3 and HRD-13. It logs in `shadow` first
(D-FIND-8).

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
| `FIND_ORGS` | empty | `matched_by`, the coverage order, rung 3 in the engine, rung 4, the honest zero lines, the explain mode, `resolve_name` and the standing rule. It acts only with `CONTEXT_RECENCY` on. Empty gives the answer of KR-ZERO, byte for byte. It reloads with no restart (`docs/flag_reload.md`) | AGENT-SAFE. It spends nothing. Flip it under the `enforcement-flip` grant, after the FIND-1 eval |
| `CHAT_SEARCH_ORGS` | empty | Rung 6, the `chat` source (HRD-21) | The owner sees the preview before the flip (task 3225) |
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
| **HRD-18b** | 3226 | 1 | Rung 3 in the engine: the `near` ability of the CRM, people and email sources, the near lookup of pass 2, the query again with the best spelling, and `matched_by` `near`. `query_inbox` tries the near spelling of a sender | "Sharath 3D printer" finds the Neuropeak deal and the mail from «Sharat Satyanarayana». "nuropeak" finds the deal and the mail from `neuropeek.com`. A sender of member B never shows for member A | `test_find_near.py`, new. `test_find_near_visibility_r8.py`, new. `test_one_name_matcher.py`, new: each `pg_trgm` floor comes from `name_match` | STANDARD · AGENT-SAFE | KR-ZERO, HRD-18a, FIND-2 |
| **FIND-3** | 3240 | 1 | Rung 4: the `related` and `open` abilities of the CRM, people and email sources, and the step on the best 5 hits, 3 for each hit, in 1.0 s. It builds the step and `Source.open` that KG-6 names. KG-6 then adds the `kb_links` edges to the same step | "Neuropeak order" finds the deal, and the thread from the address of its lead, in one call. A contact's mail in the mailbox of another member never shows. A record that the member cannot open leaves no trace. Org B never shows | `test_find_related.py`, new. `test_find_related_r8.py`, new | HIGH · AGENT-SAFE | FIND-2 |
| **FIND-D2** | 3245 | 2 | The demo of rung 2 (§9.1) | The owner uses it on production | none | demo | FIND-4 to FIND-7, HRD-21 |
| **FIND-4** | 3241 | 2 | The honest zero and "Where else" of §3.6, the explain mode of §3.7, and the log line | Each line comes from code, from a closed list. No line names a record that the member cannot open. `explain=true` gives the trace with no snippet. The log line holds no word | `test_find_honest_zero.py`, new | STANDARD · AGENT-SAFE | FIND-2, HRD-4, HRD-18b |
| **HRD-21** | 3225 | 2 | Rung 6: the `chat` source of `company_context.md` §8.2, the first part of CX-4, with no digest. It reads the titles and messages of the member's own solo sessions in this org. A hit gives the title, the date and a link. Item 3 of its task, the next places to try, moves to FIND-4 | Member B never sees a chat of member A. A room, a `no_egress` session and the current chat never show. The key is `chat` | `test_chat_source_r8.py`, new. `test_company_search_sources.py` | HIGH · The flip of `CHAT_SEARCH_ORGS` waits for the owner's preview | KR-ZERO, FIND-2 |
| **FIND-5** | 3242 | 2 | `resolve_name` and `GET /search/names`, over the `near` ability of each source | "Sharat" gives the lead, the contact and the mail sender, each with its link and its match. A near name says so. The tool refuses with no member, and answers each failure as text | `test_resolve_name_tool.py`, new. `test_find_near_visibility_r8.py` | STANDARD · AGENT-SAFE | HRD-18b |
| **FIND-6** | 3243 | 2 | The family: the zero line of `search_crm`, `query_inbox` and `find_tasks`, the standing rule of §5.4, failure text on the core find tools, and `resolve_person` out of the core list after parity | With `FIND_ORGS` on, the core list holds `search_company` and `resolve_name`. Each zero of an app search tool ends with the pointer. No find tool answers "Error: Function failed." The preamble goldens move in the same PR | `test_find_tools.py`, new. `evals/trajectories`, `test_native_session_persistence.py` | STANDARD · AGENT-SAFE | FIND-4, FIND-5 |
| **FIND-7** | 3244 | 2 | The "not found" check of §5.5 in `answer_check.py`, behind `FIND_CLAIM_CHECK` | In `shadow`, a "no such order" answer with no pass 2 in the turn logs one miss, with no text. In `on`, it gets one more pass, which shares the retry of NUM-3 and HRD-13 | `test_answer_not_found.py`, new | HIGH, the run loop · `on` is an `enforcement-flip` | FIND-4 |
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
