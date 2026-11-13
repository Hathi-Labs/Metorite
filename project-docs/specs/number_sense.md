# Numbers that make sense — a plausibility check for every total

**Status: PROPOSED, 2026-11-13. Nothing is built.** Epic 3132. NUM-0 is
task 3133. The owner decides D-NUM-1 to D-NUM-5 (§7). The defaults hold
until the owner answers.

**Owner ask, 2026-11-13, in chat.** The words are the owner's, so they sit in
a block that the linter does not read:

```text
"when we're doing any work over a lot of records like this (like CRM or
accounts or books, or where we're doing a lot of totals), it might be useful
to have a sanity check of the numbers ... If they're not making sense, we
could tell the user: 'I did this, but this seems to be some issue with
such-and-such state. Do you want me to check out why this might be an
outlier or review if some of the data might be incorrect?' ... This kind of
critical thinking should be involved in the chat and percolate throughout
the other applications as well. Even when we are doing an external app
integration, like, say, Salesforce ... Do we leverage our existing systems
for tooling or standing instructions ... or something more innate in the
harnesses?"
```

## 1. The problem

**Evidence (production, 13 Nov, chat 7788f8c6, org DEWiN).** A member asked
for October sales. The chat answered ₹33.57 crore.

13,222 of the 14,313 October invoices carried one date, 17 Oct 2026, from a
mass update in the customer's Zoho. Most of them had no amount. Without
them, October was about ₹7.8 crore. Tasks 3127 and 3129 hold the details.

Three things went wrong:

1. The first answer gave the total with nothing to compare it with.
2. In the next answer, the model saw "about ten times the other months". It
   still led with the number.
3. The model had no tool to find the cause. A person found it by hand, with
   six read-only queries.

A rule in the prompt alone did not stop this. The model is not always
careful, and it cannot judge one number with no peers.

## 2. The answer: three layers, and the harness makes it sure

| Layer | What it does | Why it is needed | Where it lives |
|---|---|---|---|
| **1. Tools give evidence** | A totals tool returns its baseline (the periods before), its coverage (the records with no value) and its findings with the number | The tool has the data in the same scan at almost no cost. A model cannot make a baseline from one number | Each first-party totals tool. The CRM aggregate is first (§4.2) |
| **2. The harness checks and enforces** | The answer check runs the plausibility checks on each tool result with figures. An answer that leaves out a finding gets one more pass, then a server line | A prompt rule is a wish. Production showed that the model can see an outlier and still lead with it | `acb_skills/answer_check.py`, the one middleware that sees each tool result and holds the answer (§4.3) |
| **3. A short standing rule** | Before you state a total over many records, compare it with one other period or group. Say a finding first, then offer to check why | An outside app gives a bare number. Only the model can ask it for a comparison period | The orchestrator base, about 40 words (§4.7) |

**Recommendation (D-NUM-1): build all three.** The harness is the guarantee.
The tools make the check cheap and exact. The rule covers the outside apps
that give a bare number.

- **Standing instructions alone fail.** The DEWiN chat shows it.
- **Tools alone fail.** A tool that we do not own (Salesforce through MCP)
  cannot attach a baseline. A tool also cannot make the model say a finding.
- **The harness alone fails.** It can only check what a tool gave. A single
  total has no peers, so number memory (§4.5) and the rule fill that gap.

## 3. What a number that does not make sense is

Each check takes plain values and gives zero or more findings. A finding has
a kind and a severity (`info` or `warn`). It names its keys. It has one
sentence in Simplified Technical English, and it names the next check. The
thresholds are module constants (D-NUM-3).

| Kind | The test | Default threshold | Example sentence |
|---|---|---|---|
| `outlier` | A bucket is far from the median of its peers | Over 5 times, or under one fifth, of the median of at least 3 peers | "October is 6 times the median of May to September." |
| `concentration` | One key (a date, an owner, a record) holds most of a total | 30 % or more of the records or the value, while the median key holds under 5 % | "13,222 of 14,313 records (92 %) carry the date 17 Oct 2026." |
| `coverage` | Many records have no value in the measure, or a status that is usually left out | 20 % or more with no value, or with the status draft or cancelled | "13,219 of the records have no Total Tax Invoice Amount." |
| `parts` | Parts that must add up to a total do not | A gap over 1 % and over 1 unit | "Rent, IP and power add up to ₹6.1 crore, not ₹7.8 crore." |
| `jump` | A value against the remembered values of the same measure | Over 5 times, or under one fifth, of the last remembered value | "This is 10 times the pipeline that you asked about last week." |
| `part_period` | A period that is not complete is compared with full periods | The current month, week or quarter before its end | "November has only 13 days so far." |
| `duplicates` | The same record seems to count more than once | The same id, or the same amount, date and party, twice or more | "212 invoices repeat an earlier invoice number." |
| `grouping` | A figure in the answer has a wrong digit grouping | Indian or western grouping that does not parse to the source value | "INR 3,35,68,33,347 is not ₹33.57 crore." |

## 4. The design

### 4.1 One pure module (NUM-1)

`acb_common/number_sense.py` holds every check of §3. It has no I/O, no
settings and no model. The gateway, the tools and the harness all import it.

It is NOT the faithfulness check. `acb_common/number_check.py` (CRM-Q29)
checks that each figure of an answer matches a source. That stays the one
faithfulness check. `number_sense` checks that the source figures make sense.
Each kind has one module.

### 4.2 Tools give evidence (NUM-2, NUM-9)

A first-party tool that returns a total over many records also returns:

- **A baseline.** The same measure for up to 6 periods before the asked
  range, from the same scan.
- **Coverage.** The count of records with no value in the measure, and the
  count by status where the entity has one.
- **`checks`.** The findings of `number_sense` on those values.
- **`display`.** The value in the org's number system, so the model does not
  group the digits itself (task 3129).

The tool text tells the model to say a `warn` finding first. The CRM
aggregate is first (NUM-2, after task 3127). Then Projects reports and
board_stats, email counts, Studio object totals and WhatsApp stats (NUM-9,
one PR per app).

### 4.3 The harness makes sure (NUM-3, NUM-6)

`acb_skills/answer_check.py` already records each tool result of a run and
holds the answer before the member reads it (CRM-Q29). It gets a second
pass beside the number check: the plausibility pass.

- **When it arms.** A tool result with `checks` arms it (NUM-3). A tool
  result that holds a series of figures arms it too, from any app and from
  an outside app (NUM-6). A reader finds a series in a JSON result: a list
  of rows with one key field and one or more number fields. It takes numbers
  and keys only. It never follows words in an outside result.
- **What it checks.** Each `warn` finding of the run must show in the answer:
  its key (the date, the month, the owner) or its kind in words.
- **The modes.** `off` is the default. `shadow` logs the kind and the
  severity of each miss, and no number and no text. `on` asks the model for
  ONE more pass that names the finding. A second miss sends the answer with
  one server line: "Note: <the finding>. Do you want me to check why?"
- **Its limits.** It never changes a figure. It never hides or blocks an
  answer. A run makes at most one extra model request for the number check
  and the plausibility pass together.

HRD-13 (task 2992, the claim check) lives in the same seam. The three
checks share the one hold and the one retry.

### 4.4 Check why (NUM-4)

"Do you want me to check why?" needs a tool that finds the cause in one call.

- **First-party data.** A route and a tool do it, for example
  `crm_explain_bucket`. They take the filters and the bucket of the total. In one read-once
  scan they return the splits that found DEWiN's cause on 13 Nov. The
  splits are the top dates, the created months, the statuses and the
  owners. They also give the share with no value, and the repeated ids or
  amounts. `number_sense` turns the splits into
  one or two sentences of cause.
- **An outside app.** The skill text of its pack gives the same splits as
  the app's own filters (by date, by owner, by stage). The model runs them.

### 4.5 Number memory (NUM-7)

A single total has no peers. An outside app can cost a second call for a
baseline. So the harness keeps each total that a tool gave in a chat.

- **The table.** `answer_figures`, a tenant table with FORCE row level
  security (R5). A migration in expand form (R6).
- **What a row holds.** The source (an app or a connection), a measure key,
  the period, the value, the unit and the time. The measure key is the tool,
  the entity, the measure and the filters, without any value that names a
  person. A row holds no text and no member words.
- **How long.** 13 months (D-NUM-4). A purge of a connection removes its rows.
- **Its use.** `number_sense` reads the last values of the same measure as
  peers. That gives the `jump` check, for an outside app too.

### 4.6 A data issue becomes one question (NUM-5)

Sometimes "check why" finds a cause that a rule can fix. Examples are records
with no amount, one bulk date, or drafts. Then the chat offers to propose a
rule. It uses
the open questions and the org facts of CRM-Q32 (`routes/crm/questions.py`).
It adds no second seam.

- An admin's choice becomes an org fact. Each later total applies it, and
  says so in one line.
- A member's choice holds for that member's answer. It waits as a proposal
  for an admin (CRM-Q32-8).
- The chat never writes to an outside system (D-NUM-5). It tells the admin
  where to fix the data there.

### 4.7 The standing rule (NUM-8)

One short rule in the orchestrator base, about 40 words:

```text
Before you state a total over many records, compare it with one other
period or group. If a check or a tool flags a finding, say it first in one
line. Then give the total, and offer to check why.
```

The pack of each outside app adds its own steps. First, ask the app for one
comparison period, in the same call where the app allows it. Then split an
odd bucket with the app's filters. A change to the base text moves the preamble
goldens (`evals/trajectories`, `test_native_session_persistence.py`).

### 4.8 What it never does

- It never changes a figure that a tool gave.
- It never hides an answer or blocks it.
- It never writes to an outside system.
- It never follows words in an outside result.
- It never puts a figure or a text in a log line.

## 5. Outside apps (Salesforce and the rest)

Metorite does not mirror every outside app. The design still covers them:

1. An MCP tool or a connector read returns JSON. NUM-6 reads a series from
   it and runs the checks.
2. A bare total has no peers. Number memory gives the last values of the
   same measure. With an empty memory, the rule makes the model ask the app
   for one comparison period.
3. "Check why" runs the splits of the app's pack, as the app's own filters.
4. A finding about outside data names the outside app as the place to fix it.

## 6. The ladder (D211)

Rung 0 is in build: tasks 3127 and 3129 fix DEWiN's timeouts, and add the
first concentration note and the display value.

| Rung | Demo task | Slices | What the owner sees on the demo |
|---|---|---|---|
| 1 | 3137 | NUM-1 (3134), NUM-2 (3135), NUM-3 (3136) | "October sales" on DEWiN-like data names the 17 Oct batch first, and offers to check why |
| 2 | 3140 | NUM-4 (3138), NUM-5 (3139) | "Check why" names the cause in one answer, and one approval fixes each later total |
| 3 | 3144 | NUM-6 (3141), NUM-7 (3142), NUM-8 and NUM-9 (3143) | An outside app's 10 times jump is flagged, and a Projects or Studio total gets the same care |

Tiers: NUM-1, NUM-2, NUM-4, NUM-8 and NUM-9 are STANDARD. NUM-3, NUM-5,
NUM-6 and NUM-7 are HIGH (the run loop, an org fact, outside results and a
migration).

## 7. Decisions for the owner

| # | Question | Default |
|---|---|---|
| **D-NUM-1** | Standing instructions, tools or the harness? | All three (§2). The harness guarantees. The tools give evidence. The rule covers outside apps |
| **D-NUM-2** | May the plausibility pass change an answer? | `shadow` for 7 days on production, then `on` by an `enforcement-flip`. `on` gives one more pass, then a server line |
| **D-NUM-3** | Which thresholds? | The defaults of §3, as module constants. A change needs a test |
| **D-NUM-4** | What does number memory keep, and for how long? | Figures and measure keys only, no text, for each org, for 13 months |
| **D-NUM-5** | May the chat fix bad data? | In Metorite data, only through an org fact that an admin approves. In an outside system, never. The chat says where to fix it |

## 8. Seams and fences

| Seam | State | What changes |
|---|---|---|
| `acb_common/number_check.py` (CRM-Q29) | Built | Nothing. It stays the one faithfulness check |
| `acb_skills/answer_check.py` | Built, `shadow` on production | NUM-3 adds the plausibility pass. NUM-6 arms it for every tool with figures |
| `acb_common/number_sense.py` | New | NUM-1. Pure |
| CRM-Q32 open questions and org facts | Built | NUM-5 adds the kinds of a data issue |
| `answer_figures` | New table | NUM-7 |

Each slice names its fence:

- `tests/unit/test_number_sense.py` (NUM-1).
- `tests/unit/test_answer_plausibility.py` (NUM-3).
- An MCP fixture test of a 10 times jump (NUM-6).
- An R8 test of `answer_figures` on a private database (NUM-7). Until a slice lands, its rule is advisory (R7).
