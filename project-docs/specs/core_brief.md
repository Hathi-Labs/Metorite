# Core brief — the 07:30 brief, the 18:30 close-out, and silence between

**Status: ACTIVE. Built: BR-2a-1, BR-2c-1, BR-2c-2, BR-4b, BR-2d, BR-7a,
BR-5 slices 1 and 2a, BR-2a-2 slice 1 and BR-2b-1 (dark), 2026-10-29 to
2026-11-06.**
Verified against code on 2026-11-05, at `origin/main` `1e63f33d8`.

**BR-2b-1 BUILT 2026-11-06, dark** behind `BRIEF_CLOSEOUT` (OFF): the
template close-out at 18:30, with the sections `done` and `moved` (§4.3).
`brief.run` takes a `kind` in its payload. A payload with no `kind` is a
`brief`. The close-out makes no model call, even with `BRIEF_COMPOSE` on.

No migration. The section `acted` waits for BR-7b. "Mail sent" stays out until
an authorized read exists (§8). The phrase of the close-out lead waits for a
later slice.

**BR-2a-2 slice 1 BUILT 2026-11-05, dark** behind `BRIEF_COMPOSE` (OFF):
the phrase step of the 07:30 brief (§4.2, §4.4). `gateway/brief/phrase.py`
is the one model site of the package. The run commits its claims and the
intent mark `phrasing`, then makes one call with no session open. A retry
of a run in `phrasing` makes no call. No close-out, no 3-minute fallback,
no UI and no migration.

**BR-4b BUILT 2026-10-30** (`gateway/brief/fence.py`), the claim fence.
**BR-2d BUILT 2026-11-04, dark** (`gateway/brief/monitor.py`), the monitor.
**BR-7a BUILT 2026-11-04**, the effect registry and its fence. **BR-5
slice 1 BUILT 2026-11-04, dark** (`reach/exceptions.py`), the four
interrupt rules behind `BRIEF_EXCEPTIONS`.

Five slices are built and dark. BR-1a is the job queue
(`docs/job_queue.md`). BR-1b-a is its detached mode and `jobs.prune`
(Metorite #642). AUTO-2 is the ledger of unattended runs (Metorite #814).

**BR-4 slice 1 BUILT 2026-10-28** (migration 315, `gateway/brief/store.py`,
dark, no producer). The member purge deletes the runs of the member.

**BR-2a-1 BUILT 2026-10-29** (Metorite #583): the planner, the gather and
the template lead, in `gateway/brief/{flags,planner,gather,run}.py`, behind
`BRIEF_ENABLED` and `BRIEF_ORGS` (both OFF). The seed hooks of
`docs/job_queue.md` §5c carry the 15-minute planner. The prune hook keeps 90
days. No model call, no send, no UI. The member sets the brief through
`PUT /auth/me/assistant-profile` (key `apps`). The owner answered all seven
questions of §9 on 2026-10-29 (epic #479, "Owner decisions"), and §9 holds
the answers.

**BR-2c-1 BUILT 2026-10-29** (Metorite #585, repair round 1), dark behind
`BRIEF_IN_APP` (OFF). `GET /brief/today` (`routes/brief_today.py`, gate
`feature:chat`) serves the stored run and its claims to the member of the
session. The GET is a pure read. With the switch on, the run itself moves
`ready` to `delivered` (§5). The chat's first turn shows the stored lead
and lines. No card, no bell, no push, and no UI.

**Board.** Workstream **WS-BRIEF** (`work_plan.md` §2.1). Metorite epic
**#479**, items BR-2a to BR-7b. AUTO-2 is Metorite **#814**.

**Single owner.** This spec owns the brief record, the claims, the schedule
and the phrase step. It also owns the delivery monitor, the exception rules
and the policy fence. Every other doc links here and adds nothing.

**The input brief.** The owner's brief "Metorite Core Function Tasks"
(2026-10-27) is not in the repository. Only its summary is here: D119, the
critical path of `work_plan.md` §2.0 (items 9 and 10), and the item text of
epic #479. This spec works from that summary.

---

## 1. What the brief is

Each **brief member** gets two moments on each working day, and silence
between them.

| Moment | Default time | What it says |
|---|---|---|
| **The brief** | 07:30, the member's time zone | What needs the member today, from email, calendar, tasks and CRM, with the numbers that matter |
| **Silence** | 07:30 to 18:30 | Nothing interrupts, unless a rule of §6 says so |
| **The close-out** | 18:30, the member's time zone | What got done, what Mira did, and what moved to tomorrow |

**Who is a brief member.** DECIDED (§9 Q1, owner 2026-10-29): every active
member of the org, with an opt-out. The brief is on by default, and each
member can turn it off (`apps.brief.enabled`).

**Every claim has a source (BR-4).** Each line and each number opens its
source record in one click. A sentence that names no source does not ship.

### 1.1 Scope

- The brief record and the claim record (§3).
- The schedule, the run and the at-most-once rule (§4).
- The gather, the selection and the phrase step (§4).
- Delivery: the My Day card, the chat's first turn, the bell entry, and web
  push through Reach last (§5).
- The delivery monitor (§7), the exception rules (§6) and the policy fence (§8).

### 1.2 Non-goals

- **No second feed.** `my_day_feed.md` (WS-58) keeps the signals, the cards
  and the `decide` judge. The brief reads its cards.
- **No second outbound seam.** `proactive_reach.md` (WS-59, D103) keeps every
  message outside the app. A channel outside the app is a later slice, and
  Reach sends it.
- **No act.** The brief prepares and reports. It never sends, writes or
  deletes. D131 governs every act, and §8 fences it.
- **No new calendar sync.** External calendars arrive with WS-21-P4, read
  only first (D119.6). Until then the brief reads the Metorite calendar.
- **No WhatsApp work.** D115 holds, except D122.3.

---

## 2. The seams it reuses

The brief adds a record and a schedule. Every read goes through a seam that
exists today.

| Need | Seam | Anchor |
|---|---|---|
| The member's cards | `feed_cards`, the one compute path of My Day | `routes/shell/feed.py:1729` |
| One read of a member and the cards | `reach.loop.resolve` and `reach.loop.snapshot` | `reach/loop.py:134`, `:121` |
| The brief lines | `plan.brief_items`, `plan.line`, `plan.brief_parts` | `reach/plan.py:324`, `:189`, `:351` |
| The lead from the counts, with no model | `plan.brief_lead` | `reach/plan.py:362` |
| The reach class of each kind | `REACH_CLASS` | `reach/classes.py:22` |
| The attention budget: cap, quiet hours, batch | `proactive_reach.md` §6.1, `flags.daily_cap` | `reach/flags.py:64` |
| What closed today | `handled_run` (tasks, email, approvals) | `routes/shell/handled.py:105` |
| Mail numbers | `needs_reply_count`, `promise_tasks` | `routes/email/digest.py:279`, `:495` |
| The member's day and zone | `Day`, `feed_day` | `routes/shell/state.py:115` |
| The job queue | `queue.enqueue`, `worker.register`, the rules of §5a | `jobs/queue.py:221`, `jobs/worker.py:112` |
| At most once, the run ledger, incidents | AUTO-2, `jobs/unattended.py` | on its branch, not merged |
| The chat's first turn | `POST /chat/sessions/{id}/brief` (CF-5) | `routes/chat_brief.py:248` |
| Links that draw chips | The hrefs of `search_company`, `linkChips.ts` | `knowledge_base.md` §8.3 (KB-4b) |
| Cross-app retrieval | `company_search` | `retrieval/company.py:252` |
| The member's preferences | `render_block` | `acb_common/assistant_profile.py:1272` |
| The house voice | `voice_prompt` | `acb_llm/voice.py:158` |
| The model call and its bill | `acompletion_with_fallback`, the Console Router (H-171) | `acb_llm/context.py:243` |

All gateway paths are under `apps/services/gateway/gateway/`. The packages
are under `packages/`.

**One composition (D-CF-6).** The brief's lines are the lines of
`plan.brief_parts`. So the chat, My Day and the WhatsApp brief show the same
words. Only the phrased lead (§4.4) is new.

---

## 3. The data model (BR-4)

### 3.1 The decision: two new tables

No table that exists today fits.

- **`reach_outbox` does not fit.** It holds one message on one channel. Its
  CHECKs allow only `whatsapp` and the kinds `brief` and `interrupt`
  (`infra/postgres/266_reach_outbox.sql`). A brief is one record that many
  surfaces show. Later, an outbox row points at a brief run.
- **`my_day_card_state` does not fit.** It holds the state of one card, not a
  composition (`my_day_feed.md` §13.1).
- **The AUTO-2 ledger does not fit.** `unattended_runs` records that a run
  happened and how it ended. It holds no content. A brief run points at it.

### 3.2 `brief_runs`

One row for each member, kind and local date.

| Column | Type | Meaning |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID NOT NULL | DEFAULT the bound tenant, as in `reach_outbox` |
| `member_email` | TEXT NOT NULL | |
| `kind` | TEXT NOT NULL | CHECK `brief` or `closeout` |
| `local_date` | DATE NOT NULL | The member's date of the slot |
| `slot_at` | TIMESTAMPTZ NOT NULL | 07:30 or 18:30 local, as an instant |
| `state` | TEXT NOT NULL | CHECK `planned`, `gathered`, `phrasing`, `ready`, `delivered`, `empty`, `failed` |
| `lead` | TEXT NULL | The first sentence |
| `lead_source` | TEXT NULL | CHECK `template` or `model` |
| `lead_claim_ids` | UUID[] NOT NULL DEFAULT `{}` | The claims that the lead cites |
| `model_request_id` | TEXT NULL | The `client_ref` of the phrase call. It joins `usage_event.client_ref` |
| `unattended_run_id` | UUID NULL | The AUTO-2 run |
| `error` | TEXT NULL | The error type only, never a payload |
| `created_at`, `ready_at`, `delivered_at` | TIMESTAMPTZ | |

UNIQUE `(organization_id, member_email, kind, local_date)`. CHECK: when
`lead_source = 'model'`, `lead_claim_ids` is not empty.

### 3.3 `brief_claims`

One row for each line and each number of a run.

| Column | Type | Meaning |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID NOT NULL | DEFAULT the bound tenant |
| `run_id` | UUID NOT NULL | FK `brief_runs`, ON DELETE CASCADE |
| `position` | INT NOT NULL | The order in the brief |
| `section` | TEXT NOT NULL | CHECK `now`, `needs_you`, `numbers`, `done`, `moved`, `acted` |
| `text` | TEXT NOT NULL | The line that code wrote, from the card |
| `source_app` | TEXT NOT NULL | `tasks`, `email`, `calendar`, `crm`, `approvals`, `knowledge`, `chat`. The prefix of `source_id` sets it, except that `calendar:<date>` keeps `calendar` |
| `source_kind` | TEXT NOT NULL | The card kind, or `count` for a number over a list |
| `source_id` | TEXT NOT NULL | The `signal_id` shape `<app>:<ref>` (`my_day_feed.md` §4.1). Split it on the first colon only |
| `source_ids` | TEXT[] NOT NULL DEFAULT `{}` | For a `count`: the counted ids, at most 50 |
| `href` | TEXT NOT NULL | The deep link. Code builds it (`my_day_feed.md` §12) |
| `value` | NUMERIC NULL | The number that the line states |
| `value_path` | TEXT NULL | The field of the facts with the number, for example `facts.waited_hours` |
| `fingerprint` | TEXT NULL | The card's fingerprint at gather time |

CHECK: each source column is not empty. CHECK: `source_id` matches
`^[a-z_]+:.+`. CHECK: `value IS NULL OR value_path IS NOT NULL`.

No validator of the `<app>:<ref>` shape exists in code.
`permissions.normalize_grant_subject` checks the grant subject (`org`,
`group:<slug>`, an address), which is a different grammar. So the SQL CHECK
is the fence of the shape, and no second grammar module is added.

**One click.** The `href` uses the grammar of the KB-4b chips, so the
existing chip draws it. The click opens the record in its own app, and that
app checks the member's access again. Each claim keeps one line, never the
record.

### 3.4 Tenancy and the migration

- Both tables are tenant-scoped, with FORCE ROW LEVEL SECURITY through the
  generated RLS migration (R5a). `tests/unit/test_tenant_coverage.py` is the
  fence.
- Every read filters on the member of the session too. A member reads only
  their own runs.
- One expand-only migration adds both tables, with the next free number at
  build time (R1, R6). Nothing existing changes.
- Retention: runs older than 90 days go (§9 Q6, owner 2026-10-29). BR-1b-a
  built `jobs.prune` and its hook list, `worker.prune_hooks`
  (`docs/job_queue.md` §5b). The job deletes old `jobs` rows only. BR-2a-1
  appends a hook that calls `brief.store.prune_runs` with a 90 day cut-off.
- A claim position holds one claim. A retry of the same claims writes no
  second set.

---

## 4. The pipeline

### 4.1 The schedule

1. **The planner** is one job kind, `brief.plan`, that runs every 15 minutes
   for each org of `BRIEF_ORGS`.
2. For each brief member, it finds the slot in its window. The window
   starts 2 hours back (`LOOKBACK`) and ends 30 minutes ahead (`HORIZON`).
   It reads the zone and the working days from `Day` and `work_schedule`. A
   member with no zone takes the org's `default_timezone`. So a planner
   outage shorter than 2 hours loses no brief. An older slot with no row gets
   one `failed` row, with the error `slot_missed`. The plan then logs
   `brief.slot_missed` (warning).
3. For each slot, it enqueues `brief.run`. `run_at` is the slot minus 5
   minutes. The dedupe key is `brief:<member>:<kind>:<local_date>`.
4. **No manual trigger** (BR-2c). No route enqueues a brief run. "Send my
   brief now" of Reach stays a Reach act.

### 4.2 The run, at most once

`brief.run` follows `docs/job_queue.md` §5a, because the gather reads many
providers and the phrase step calls a model. It uses the detached mode that
BR-1b builds.

1. AUTO-2 `begin` takes the slot `(brief.<kind>, <member>:<local_date>)`. A
   taken slot ends the run (`fresh=False`).
2. It resolves the member, and reads the brief preferences again. A member
   who is gone, turned the brief off or moved its time gets no row. A
   `planned` row of an earlier try turns `failed`, with the reason. A moved
   time is the exception: that row goes, so the planner plans the new time
   on the same day. It then
   writes the `brief_runs` row in state `planned`. A run that gathers
   nothing, a cancel included, gives the ledger slot back.
3. **Gather**, with no session held (§4.3). It writes the claims and sets
   `gathered`, or `empty` when no claim exists.
4. **Phrase** (§4.4). It sets `phrasing` first. That is the intent mark.
5. It sets `ready` with the lead. Then, when the in-app surface is on
   (`flags.in_app`, BR-2c-1), it **delivers** (§5). It moves `ready` to
   `delivered` with a guard, and stamps `delivered_at`. Off, the run stays
   `ready`.
6. On a retry, a run in `phrasing` with no lead skips the model and takes the
   template lead. A model call is never made twice for one run.

### 4.3 Gather and select: rules, no model

- **The brief** reads `reach.loop.snapshot`, then `plan.brief_items` and
  `plan.line`. Every card makes one claim, with its `signal_id`, `href` and
  fingerprint. At most 7 lines, as `BRIEF_LINES`.
- **The numbers section** adds at most 3 counts: threads that wait for a
  reply, tasks due today and promises due (`needs_reply_count`,
  `promise_tasks`). Each count stores its ids.
- **The close-out** reads `handled_run` for `done`, the open cards of today
  for `moved`, and BR-7b's executed acts for `acted`. BR-2b-1 builds `done`
  and `moved` only. The rules of BR-2b-1:
  - **`done`.** One claim for each `Closed` row of `handled_run`, in its
    order (newest first). `source_id` is `Closed.key`, `source_app` is
    `Closed.app`, `source_kind` is `handled`, `text` is `Closed.title`, and
    `href` is `Closed.href` when it is an in-app path. A row with no in-app
    href gets no claim. The cut is `BRIEF_LINES`. The run calls
    `handled_run` itself, because `loop.snapshot` does not run Handled. A
    Handled read that is not ok (`HandledRun.ok` is False) raises, and the
    queue retries the run, as it does for `SnapshotIncomplete`. So a lost
    source never reads as a quiet day.
  - **`moved`.** The `plan.brief_items` of the 18:30 snapshot, cut to
    `BRIEF_LINES`, in the feed's order. Each makes one claim in the section
    `moved`, with the text `plan.line`, as the brief builds it. These are
    the open cards that the day leaves for tomorrow. A card that is done
    since the snapshot is not open, so it is not here.
  - **The order.** `done` first, then `moved`. The positions are continuous
    from 0.
  - **The lead (template).** `Your close-out, <date words>.` Then, in
    words and counts, `<N> done` and `<M> moved to tomorrow`, joined with
    "and", and a stop. A count of 0 is left out. Example: `Your close-out,
    Thu 15 Oct. 2 done and 1 moved to tomorrow.` The counts are the claim
    counts. The lead holds no name and no title of a card.
  - **Empty.** No claim in both sections gives the state `empty`, with no
    lead.
  - **"Mail sent" is not in BR-2b-1.** The Email Handled read lists the
    rules that applied. Mail that an agent sent stays out until an authorized read exists (§8).
- **Each read runs as the member**, through the app's own authorized read.
  The brief adds no SQL against the table of another app.
- **The order** is the feed's order. No model ranks, picks or drops a claim.

### 4.4 Phrase: one model call, bound to sources

- **When.** `BRIEF_COMPOSE` is on, and the run has 4 claims or more. Else the
  lead is `plan.brief_lead`, from the counts.
- **The input.** The claims as a numbered list `[c1] … [cN]`, with their
  values. The member's block from `render_block`. The house voice from
  `voice_prompt("summary", json=True)`.
- **The answer.** JSON: at most 3 sentences, each with the ids of the claims
  that it uses.
- **The check (BR-4b).** A sentence with no known claim id fails. A number in
  a sentence that no cited claim holds fails. The number check uses the shape
  of `_numbers` (`assistant_profile.py:612`). BR-4b made it one public
  helper, `numbers_in`, and copies nothing. A failure keeps the template lead, and the run
  logs `brief.phrase_refused`.
- **The words of the lines never change.** The model writes the lead only.

### 4.5 Cost and credits (D123)

| Item | Estimate for each member and working day |
|---|---|
| Calls | 2 at most: one brief, one close-out |
| Input | About 2,500 tokens for each call: the claims, the profile block and the voice |
| Output | About 200 tokens for each call |
| Tier | `tier-fast`, through `acompletion_with_fallback` with `source="brief"` |

The cost is an estimate. The measured burn sets the rate card (D123.1). The
call draws credits through the Console Router when H-171 and H-69 hold. When
the CP-6 balance gate refuses, the run takes the template lead and still
delivers. A brief with no AI is free, as My Day is (`my_day_feed.md` §9.3).

With the Router off (`acb_llm.routed.routing_is_on()` is False), the run
makes no call and takes the template lead. A call off the Router writes no
usage row.

---

## 5. Delivery (BR-2c)

In the app first. Every surface reads the stored run, so all show the same
words.

1. **My Day.** A standalone card of the brief reads today's run through
   `GET /brief/today`. It is a component of its own, not a kind of the MD-7
   feed. BR-2c-2 built it (`BriefCard.tsx`, flag `NEXT_PUBLIC_BRIEF_CARD`). This spec owns its data.
2. **The chat's first turn.** BUILT in BR-2c-1. With `BRIEF_IN_APP` on,
   `POST /chat/sessions/{id}/brief` reads the stored run through the read
   of item 3. When the run is `ready` or `delivered`, the chat reads no
   feed. Its stamp and its row id are the run's `local_date`, so one run
   makes one post. The composition stays as the fallback, also when the
   stored read fails. The chat does not post a run that is dated before the
   feed day, and composes from the feed instead. So a member whose day
   starts early never gets the brief of the day before. The rules of once a
   day and phone only stay too (D-CF-2, D-CF-3). The chat writes nothing to
   the run.
3. **A read route.** BUILT in BR-2c-1. `GET /brief/today?kind=brief|closeout`
   answers the run and its claims, for the member of the session only. The
   route takes the newest run whose `slot_at` has passed. Its date is
   today or the day before, by the member's calendar date in the planner's
   zone (`planner.member_zone`). The day before covers a zone change, and a
   brief time near midnight that the look-back ran after midnight. It does
   not use the feed day, because the feed day moves with the day-start
   hour. The route is a pure read, and it writes nothing. No route
   enqueues a run. A close-out run is served by the same route (BR-2b-1). Before the slot
   of today, the route serves the run of the day before, with its own
   `local_date`. The My Day card (BR-2c-2) must show that date.
4. **The bell.** One entry in v1 (§9 Q2, owner 2026-10-29). The entry says
   that the brief is ready, and it opens the brief.
5. **Outside the app.** Web push (PN-1) is in v1, and it ships last (§9 Q2
   and Q7). It sends through Reach, behind `BRIEF_PUSH` and its owner gate.
   WhatsApp stays later (D115).

"Delivered" means that the run is available on an in-app surface. The
system sets it, not a read: the run moves `ready` to `delivered` when
`BRIEF_IN_APP` is on, and stamps `delivered_at`. So a member who never
opens the app still has `delivered_at`. A future "seen" signal is a
separate `seen_at` column. Only an explicit POST from the card writes it,
and a GET never does.

---

## 6. The exception engine v1 (BR-5)

**Silent by default.** Between the brief and the close-out, a card reaches
the member only when a rule says so. Every other card waits in My Day for the
next moment.

**The rule shape.** A rule is a row of code in v1, beside `REACH_CLASS`:

```python
Rule(id="approval_waiting", kind="approval",
     when={"waited_hours": (">=", 2)}, action="interrupt",
     reason="An approval waits on you")
```

- `action` is `interrupt` or `hold`. No rule matches means `hold`.
- `when` tests only the card's `facts`, with `==`, `>=`, `<=` and `in`.
- A member can mute a rule in `my_day_prefs`. A member cannot add a rule in
  v1.

**The rules of v1** (DECIDED, §9 Q4, owner 2026-10-29). Four interrupts:

1. An approval waits on the member for more than 2 hours (`approval`).
2. An agent asks the member a question (`agent_asks`).
3. A block starts soon, and the member asked to be told (`block_soon` with
   `notify_me`).
4. A source sync failed (`source_sync_failed`).

**The limits.** The budget of `proactive_reach.md` §6.1, unchanged: 3 a day,
a 15-minute batch window, and quiet hours. A held card is a claim of the next
moment. It never drops.

**In the app.** An interrupt sets the card to the top of My Day, with the
unread mark of the shell. A send outside the app is Reach, behind its own
flags.

**The rating of an alert (slice 2b-1, DECIDED 2026-11-06 07:17 UTC).** The
owner chose to store the rating on `reach_signal_log`, with the rule id.
The migration (its number is taken at build time, R1) adds three nullable columns: `rule_id`, `feedback` and
`feedback_at`. An interrupt writes the `rule_id` of the rule that matched, with
one log row for each rule. `feedback` holds
`useful` or `not_useful`. One member gives one rating for each card and rule,
and a second rating replaces the first.

A rating only stores. It mutes no
rule and changes no budget. 

`proactive_reach.md` §7.1 and RE-6 (WS-59) keep
the rating of a whole message on `reach_outbox.feedback`. The two do not
overlap: the log rates one card for one rule, and the outbox rates a message.
RE-6 reads the log rating, and it adds no second table.

**The `notify_me` fact (slice 2b-2).** Rule 3 never fires, because no
provider sets `notify_me`. The owner has not decided what "the member asked to
be told" means (Q8 in §9). This row waits on that answer.

**The mute screen (slice 2b-3).** A member sets a rule mute in Settings. The
screen is STANDARD tier and needs a D145 preview. It writes `rule_mutes` in
`my_day_prefs` through the route of slice 2a.

---

## 7. Delivery monitoring (BR-2d)

**The target.** 99% of runs are `delivered` within 5 minutes of `slot_at`,
over 7 days, for each org. The lag is `delivered_at` minus `slot_at`: the
time until the brief is available in the app, not the time until the
member reads it (§5).

- **Retry.** The queue retries a failed try with its backoff
  (`docs/job_queue.md` §4). The queue never retries the model call (§4.2 step 6).
- **The fallback.** At `slot_at + 3 minutes`, a run that is not `ready`
  delivers the template brief, with no model.
- **The signals.** `brief.delivered` (info, with the lag), `brief.late`
  (warning), `brief.failed` (error). AUTO-2 groups failures into one incident
  for each job and error.
- **The check.** A job `brief.slo` runs each hour. It logs `brief.slo_missed`
  at error when an org falls under 99%.
- **The view.** `GET /admin/brief/health` answers the counts and the lag
  percentiles for the org. An org admin reads it.

---

## 8. The policy fence (BR-7a, BR-7b)

**BR-7a: no code path sends or changes anything without a policy.**

1. **The brief does not act.** A structural test reads the `gateway/brief/`
   package. It fails on an import of a send seam or of a write route of
   another app. The allowed writes are the brief's own tables, the queue and
   the AUTO-2 ledger.
2. **Each job kind states its effect.** `worker.register` takes `effect`:
   `read`, `own_rows`, `send` or `write`. A kind with `send` or `write` also
   names a policy, that is, a D131 level and a scope.
3. **The fence.** A test lists every registered kind. It fails on a kind with
   no `effect`, and on a `send` or `write` kind with no policy.

**BR-7b: every executed act is in the close-out and the audit log.** The
`acted` section lists each act that Mira or an automation ran today. It
reads two sources: `audit_event` rows on the allow-list of §8.1, and
`reach_outbox` rows in state `sent`. Each line names the policy that allowed
it. Mail that an agent sent is OUT of BR-7b until an authorized read of it
exists (§8.4). BR-7b has three slices: BR-7b-1 is the reader, BR-7b-2 is the
`acted` section, and BR-7b-3 holds the findings (§10).

### 8.1 What counts as an act (proposed default, owner may change)

The reader uses an allow-list of `audit_event.action` values. A new action
is not an act until a person adds it to the list. The builder derived this
list on 2026-11-07 from every writer in `apps/` and `packages/`.

| Action | Writer | Today |
|---|---|---|
| `agent_door_call` | `acb_auth/deps.py:691` | Has `organization_id` and `payload.member`. An external agent called a door for a member |
| `agent_push_by_reviewer` | `orchestrator/executor.py:4438` | Has the org and `payload.member`. Its `reviewed_by` is `chat:autopush` |
| `mutation_commit_auto_pushed` | `orchestrator/mutation.py:527` | No org and no member on the row. It gives no row for a tenant until a finding of BR-7b-3 is fixed |
| `execute:<action>` | `action_broker/broker.py:205` | No org on the row. Same limit as the line above |

The reader excludes these rows by name, and every action not in the table
is out by default. `llm_completion` (`acb_llm/client.py:699`) is a usage
row. `agent_run_complete` and `agent_run_error` (`orchestrator/executor.py`)
are run bookkeeping. `agent_self_commit_detected` (same file) is a warning.
`email_triage` (`orchestrator/triage/email.py:312`) is a label and not an
act. A human actor is also out, such as `mutation_monorepo_pr_opened`.

**D131 "Act and report" (AUTO-8) is not built.** So almost no unattended act
exists to list today. The `acted` section is mostly empty until AUTO-8
lands. Mira's acts of a member's own chat run are the main source now.

### 8.2 The member, the policy and the claim

1. **The member.** The key is `payload.member`. The writer sets it from the
   run's user, and the reader never takes it from request input. A row with no
   `payload.member` is not listed, and a finding of BR-7b-3 tracks the writer.
2. **The policy.** The writer stores it in `payload.policy`. For a `reach_outbox`
   row the policy is `reach_signal_log.rule_id` (the exception engine, BR-5
   slice 2b-1), or `brief` for the brief send. For a broker act it is the
   decision of `reviewed_by`. A row with no policy is listed as "no policy
   recorded", and a fence test counts it.
3. **The claim.** `brief_claims.href` is NOT NULL, and it must pass
   `gather.in_app_href` (PR 1767 branch `br-2b-closeout`). The new prefixes
   `audit` and `reach` join `gather.SOURCE_APPS`. The `source_id` is
   `audit:<audit_event.id>` or `reach:<reach_outbox.id>`. The `source_app` is
   the prefix, and the `source_kind` is the action or `outbox`.
4. **The link (proposed default, owner may change).** No in-app page shows
   one audit row. The claim contract has no "no link" case. A claim with no
   href is skipped. A `reach` claim takes the href of its first signal card.
   An `audit` claim takes the in-app path of its target, if the target has one.
   Otherwise it takes the My Day path, where the Handled view lives. The builder
   confirms both paths in the code.

### 8.3 Tenancy of `audit_event` and the R8 plan

Production `audit_event` is tenant-scoped: `organization_id` is NOT NULL, with
FORCE RLS. The phase-4 promotion did it. The evidence is
`infra/postgres/generated/04_policies.sql:148-153`,
`routes/agent.py:4218-4260`, `packages/acb_audit/acb_audit/log.py:103-155` and
`artifact_management.md:811-815`. **No migration is needed.** The CI ladder and
`schema.generated.sql` lack the column, because the ladder never replays the
generated files. The R8 test of BR-7b uses the `promoted` fixture of
`tests/unit/test_h3_rls_promotion_rehearsal.py`.

Before BR-7b-1, the builder runs one read-only production check: the column
`audit_event.organization_id` in `information_schema.columns`, and
`pg_class.relforcerowsecurity` for the table. It needs the box, so it goes
through the owner-gated path. The spec writer did not run it. A tenant-less
event lands in the operator org (`log.py:131`), so the reader never sees it
for another org.

### 8.4 Findings (BR-7b-3) and what is out of BR-7b-1

**Out of BR-7b-1 (proposed default, owner may change).** The `crm_change_log`
acts, the workflow runs and the mail sent by an agent are not in the first
reader. Each is a finding with an owner workstream. CRM acts go to WS-CRM.
Workflow runs go to the Workflows workstream. Agent mail goes to WS-59 (Reach)
and the Email workstream.

Mail sent stays out until an authorized read exists.

**Act paths that write no `audit_event` row.** Each is a finding:

- the `skill-projects` and `skill-my-tasks` tools,
- `gateway/routes/workflows/*` and `gateway/routes/email/*`,
- the calendar write routes,
- MCP tool calls in agent runs, except `routes/integrations.py:2006`,
- CRM acts, which go to `crm_change_log`.

**Defective writers.** `routes/notes/dispatch.py:469` (`_audit`) is a raw
insert outside the seam. `action_broker/broker.py` sets no org on any event.
`orchestrator/triage/email.py:312` writes across tenants (Metorite task 2173).
`acb_llm/client.py:699` sets no org.

**BR-6c.** BR-6c is not defined in the repo. `work_plan.md:140` names it in
critical-path item 9, and it belongs to epic 480. The text "Blocked by BR-6c"
on a task stays open until the owner or the dispatcher finds its source.

---

## 9. The owner's decisions

The owner decided Q1 to Q7 on 2026-10-29 (epic #479, the comment
"Owner decisions").

| # | Question | DECIDED 2026-10-29 |
|---|---|---|
| Q1 | Who gets a brief? | Every active member of the org, with an opt-out. The eligible set is the active members minus the members who turned the brief off. It is not for the admins only |
| Q2 | Which surfaces in v1? | The My Day card, the chat's first turn, a bell entry, and web push. Web push sends through Reach, and it ships last |
| Q3 | The credit budget for each brief? | At most 2 `tier-fast` calls for each member and working day. With no credit, the template brief |
| Q4 | What may interrupt? | All four of §6: an approval that waits more than 2 hours, an agent that asks, a block that starts soon (with notify-me), and a source sync that failed |
| Q5 | Fixed times, or the member's? | 07:30 and 18:30 in the member's zone by default. Each member can change both. The brief stores three preferences (`enabled`, `brief_time`, `closeout_time`) through `acb_common.assistant_profile` (`app_prefs`, and `PUT /auth/me/assistant-profile` with the key `apps`). A later slice adds the settings UI |
| Q6 | How long to keep a brief? | 90 days. The prune hook of `brief/run.py` deletes older runs |
| Q7 | Outside the app, when? | Web push is in v1, and it ships last, after the in-app surfaces |
| Q8 | What does "the member asked to be told" mean for rule 3? | **OPEN, asked on task 590 on 2026-11-06.** Options: (a) one boolean in `my_day_prefs` that sets `notify_me` on every `block_soon` card, no migration (recommended), (b) only blocks that have a reminder, (c) drop rule 3, because the Notify me request (RE-5) already interrupts |
| Q9 | What counts as an act in the `acted` section, and how does a claim link to it? | **OPEN, asked on task 592.** Proposed default (§8.1 to §8.4, owner may change): the four-action allow-list, the member in `payload.member`, the policy in `payload.policy`, the prefixes `audit` and `reach`, and CRM, workflow and agent-mail acts out of BR-7b-1 |

### 9.1 Gaps that the BR-2a-1 audit found

1. **The 15-minute seed.** §4.1 names a planner every 15 minutes, and no
   recurring seam existed: only `jobs.prune` was seeded, once a day. BR-2a-1
   added `worker.seed_hooks` (`docs/job_queue.md` §5c), one additive list
   beside the prune seed, and started no second loop.
2. **The admin source.** The old recommendation of Q1 named "org admins",
   and no spec said which column held the role. Q1 as decided makes it
   moot. The planner reads every active member from `app_user`, minus the
   opt-outs, through `assistant_profile.members_app_prefs`.
3. **A stored UTC means no zone.** The DEFAULT of `user_settings.timezone`
   is UTC, so a stored UTC and no zone look the same. The planner gives both
   the org's `default_timezone`. A member who chose UTC in an org with
   another default gets the org's zone.
4. **A missed row on the first day.** An org can turn on after its slots
   passed. A slot from 2 to 8 hours before the first plan then gets a
   `slot_missed` row. BR-2d must not count those rows against its target.
5. **The settings and a removal.** Forget-all keeps `apps`. A removal
   (`personalization.md` §7 point 6) still forgets the whole profile. So a
   member who comes back gets the defaults, with the brief on.
6. **No chat write of `apps`.** In v1 only Settings sets the brief. A chat
   tool for "move my brief to 8" is a later slice.

---

## 10. Slices, in build order

Each slice ships dark. Each flag defaults OFF. The queue needs `JOB_QUEUE`,
and the at-most-once rule needs AUTO-2's `UNATTENDED_LEDGER`.

| # | Slice | Needs | Flag | Gate |
|---|---|---|---|---|
| 1 | BR-4: the two tables and the store module | BR-1a | none | AGENT-SAFE |
| 2 | BR-2a-1: the planner and the gather, with the template lead. **BUILT 2026-10-29, dark** | 1, AUTO-2 (#814), the BR-1b detached mode (#642) | `BRIEF_ENABLED`, `BRIEF_ORGS` | AGENT-SAFE to build. The production flip: `enforcement-flip` |
| 3 | BR-4b: the claim fence. **BUILT 2026-10-30, not wired. BR-2a-2 calls it** | 2 | none | AGENT-SAFE |
| 4 | BR-2a-2: the phrase step. **Slice 1 BUILT 2026-11-05, dark** (`brief/phrase.py`, the brief at 07:30 only). The close-out call waits for BR-2b | 3 | `BRIEF_COMPOSE` | AGENT-SAFE to build. **OWNER-GATE** to flip in production (credits, §3a rule 3) |
| 5 | BR-2c: in-app delivery and the read route. **BR-2c-1 (the route and the chat turn) BUILT 2026-10-29, dark. BR-2c-2 (the My Day card) BUILT 2026-10-30, dark,** in `src/lib/shell/BriefCard.tsx` behind `NEXT_PUBLIC_BRIEF_CARD`. A 404 or a failed read draws nothing, and the brief of the day before wears its date | 2 | `BRIEF_IN_APP`, `NEXT_PUBLIC_BRIEF_CARD` | AGENT-SAFE. The production flip: `enforcement-flip` |
| 6 | BR-2d: the monitor. **BUILT 2026-11-04, dark** (`brief/monitor.py`, the hourly `brief.slo` check and `GET /admin/brief/health`). The 3-minute fallback waits for BR-2a-2, because a template run is `ready` at once | 2, AUTO-2 | none | AGENT-SAFE |
| 7 | BR-2b with BR-7b: the close-out. **BR-2b-1 BUILT 2026-11-06, dark** (the template close-out with `done` and `moved`, no model call). `BRIEF_CLOSEOUT` is the planner's switch for the close-out kind: off, the planner plans no close-out and the brief path is as before. **Still open:** the `acted` section moves to rows 12 and 13 (BR-7b), and the phrase of the close-out lead | 2 | `BRIEF_CLOSEOUT` | AGENT-SAFE. The production flip: `enforcement-flip` |
| 8 | BR-7a: the effect registry and its fence. **BUILT 2026-11-04** (`worker.register(effect=, policy=)`, `tests/unit/test_job_effects_fence.py`). A kind with no `effect` is a finding in the fence; `register` takes the argument as optional, so the fence is the gate | BR-1a | none | AGENT-SAFE |
| 9 | BR-5: the exception rules. **Slice 1 BUILT 2026-11-04, dark** (`reach/exceptions.py`: the four rules, `plan.interrupt_candidates(rules=)`, `BRIEF_EXCEPTIONS`). **Slice 2a BUILT 2026-11-05, dark:** a member's mute of a rule (the `my_day_prefs` key `rule_mutes`, read by `loop.muted_rules`). **Still open, in three slices (spec rows 12 to 14):** 2b-1 the useful or not-useful rating (migration 348, HIGH tier), 2b-2 the `notify_me` fact that rule 3 needs (waits on Q8), and 2b-3 the mute screen (STANDARD, D145 preview). The move of an interrupted card to the top of My Day (§6) is not built, and no module under `routes/shell/` reads `BRIEF_EXCEPTIONS` | 2 | `BRIEF_EXCEPTIONS` | AGENT-SAFE to build. The production flip: `enforcement-flip` (Q4 is decided) |
| 10 | BR-3a: one week on pilot data | 5 | none | **OWNER-GATE**: the pilot orgs and their inboxes are the owner's |
| 11 | Web push of the brief, through Reach | 6 | `BRIEF_PUSH` | **OWNER-GATE**: it reaches a person through a third party |
| 12 | BR-7b-1 (Metorite 2171): the reader of acts. It reads the allow-listed `audit_event` rows and the `reach_outbox` rows in state `sent`, and returns claims with source, policy and href. **HIGH tier**, no migration (§8.3). Waits for Q9 and for the read-only production check | 7 | none | AGENT-SAFE to build |
| 13 | BR-7b-2 (Metorite 2172): the `acted` section of the close-out, built on row 12. **STANDARD tier** | 12 | `BRIEF_CLOSEOUT` | AGENT-SAFE. The production flip: `enforcement-flip` |
| 14 | BR-7b-3: the findings of §8.4, one task for each. The first is Metorite 2173 (the cross-tenant write of `triage/email.py`). The other tasks come in the same session as the owner's answer to Q9 | none | none | AGENT-SAFE |

The BR-1b detached mode is the part of BR-1b (#642) that slice 2 needs, and
it can ship before the rest of BR-1b. BR-1b-a built it, dark, on
2026-10-28. `docs/job_queue.md` §5a holds its rules.

BR-1b acceptance: BR-1b's `jobs.prune` calls `brief.store.prune_runs` with a
90-day cut-off (Q6).

### Acceptance

Each item is a "done when". Each fence runs red first, then green.

| # | Done when | Fence |
|---|---|---|
| 1 | The migration applies on a real database, with FORCE RLS on both tables. An empty `source_id` or `href` fails its CHECK. A member reads no run of another member, and org B reads no row of org A | `test_brief_store_r8.py` |
| 2 | On a fake clock, a member in `Asia/Kolkata` gets one run at 07:30 local, and none on a day off. Two planner ticks and a restart in a run give one row and one ledger entry. A timeout of the handler gives the slot back, and the retry gathers once. A planner outage under 2 hours loses no brief, and an older slot leaves a `slot_missed` row. Each line equals `plan.line` of its card, with its `signal_id` and `href`. With `BRIEF_COMPOSE` off, the run calls no model | `test_brief_run.py`, `test_brief_run_r8.py` |
| 3 | The check refuses a sentence that cites no claim, and a sentence with a number that no cited claim holds. A `delivered` run with a line that has no claim row fails the R8 test | `test_brief_claim_fence.py` |
| 4 | With 4 claims, one call goes out with `source="brief"`. A refused answer, a model error or a balance refusal keeps the template lead, and the run still delivers. A retry after `phrasing` makes no second call. The call carries `client_ref` equal to the stored `model_request_id`, and that value is not empty | `test_brief_phrase.py`, `test_brief_phrase_r8.py` |
| 5 | The chat's first turn and the My Day card show the stored lines, and a diff against `plan.brief_parts` of the same cards is empty. On the My Day card, each claim href opens as an ordinary in-app link (`inAppLink`). The chat pills stay text, because a link chip needs a tool event, and a stored row has none. `GET /brief/today` answers 404 for a run of another member. With `BRIEF_IN_APP` off, both surfaces answer as before, byte for byte. BR-2c-1 holds the chat and the route | `test_chat_brief_turn_r8.py`, `test_brief_today_r8.py`, `test_brief_today.py` |
| 6 | A run that is not ready at `slot_at + 3 min` delivers the template brief. `brief.slo_missed` logs once when a fake week falls under 99%. The health route answers the lag percentiles | `test_brief_monitor.py` |
| 7a | **BUILT (BR-2b-1).** On a fake clock, a member in `Asia/Kolkata` gets one close-out run at 18:30 local, and none on a day off. Two ticks give one row and one ledger entry. A task closed today gives one `done` claim with `source_id` `tasks:<id>`. An open card gives one `moved` claim with the text of `plan.line`. A day with neither gives `empty`. With `BRIEF_COMPOSE` on and 4 claims or more, the close-out makes no model call. With `BRIEF_CLOSEOUT` off, no close-out is planned. A payload with no `kind` runs as a `brief`. A Handled read that is not ok retries. `GET /brief/today?kind=closeout` serves the run to its member only | `test_brief_closeout.py`, `test_brief_closeout_r8.py` |

| 7b | BR-7b: the 18:30 run also lists the acts of the day (`acted`), each with a claim. Each act from `audit_event` names its policy. Rows 15 and 16 hold the tests of the acts. Stays open | `test_brief_acted_r8.py` |
| 8 | A new job kind with no `effect` fails the fence. So does a `send` kind with no policy, and an import of a send seam into `gateway/brief/` | `test_job_effects_fence.py` |
| 9 | Between the moments, a card that no rule matches never moves to the top. A matching card does, inside the budget of `proactive_reach.md` §6.1. A rule that the member muted matches nothing | `test_reach_exceptions.py` |
| 10 | The ledger shows one brief and one close-out for each pilot member on 5 working days. Each brief has one email claim and one calendar claim at least | The ledger, read by the owner |
| 11 | Reach sends the stored lead with a link, and `reach_outbox` holds the row | `test_reach_loop.py` |
| 12 | An interrupt writes a `reach_signal_log` row with the `rule_id`. A rating of `useful` or `not_useful` stores `feedback` and `feedback_at` on that row only. A second rating replaces the first. A rating by member A never changes a row of member B or of another tenant. A rating mutes no rule | `test_reach_exceptions_feedback_r8.py` (a real database, two tenants) |
| 13 | With the `notify_me` source of Q8 on, a `block_soon` card carries `notify_me` and rule 3 fires. With it off, rule 3 matches nothing | `test_reach_exceptions.py` (waits on Q8) |
| 14 | The Settings screen lists the four rules in words. A switch mutes or unmutes one rule, and the route of slice 2a stores it | `test_reach_rule_mutes_route.py`, and the D145 preview |
| 15 | BR-7b-1 done when: a fake day with 3 sent rows and 2 allow-listed audit acts gives 5 acted claims, each with source, policy and href. An act of org B never appears for org A. A non-act audit row never appears. R8 uses the `promoted` fixture | `test_brief_acted_r8.py` (a real database, two tenants) |
| 16 | BR-7b-2 done when: a fake day with 3 sent rows and 2 allow-listed audit acts gives a close-out with 5 acted claims, each with source, policy and href. An act of org B never appears for org A. A non-act audit row never appears. A day with no act gives `empty` for the section | `test_brief_run_r8.py` |

All fences are under `tests/unit/`.

## 11. Verification commands

```bash
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_brief_store_r8.py tests/unit/test_brief_run.py \
  tests/unit/test_brief_run_r8.py tests/unit/test_brief_claim_fence.py \
  tests/unit/test_job_effects_fence.py tests/unit/test_brief_monitor.py \
  tests/unit/test_reach_exceptions.py tests/unit/test_brief_phrase.py \
  tests/unit/test_brief_phrase_r8.py tests/unit/test_brief_closeout.py \
  tests/unit/test_brief_closeout_r8.py -q
# BR-7b (not built): test_brief_acted_r8.py with the promoted fixture of
# tests/unit/test_h3_rls_promotion_rehearsal.py
uv run pytest tests/unit/test_reach_plan.py tests/unit/test_chat_brief_turn.py \
  tests/unit/test_chat_brief_turn_r8.py tests/unit/test_job_queue.py \
  tests/unit/test_tenant_coverage.py -q
uv run python scripts/affected_tests.py --run
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/linkChips.test.ts
```

A slice that is not built yet has no test file. Each slice creates the
files that its acceptance names.
