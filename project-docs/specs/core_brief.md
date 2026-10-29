# Core brief — the 07:30 brief, the 18:30 close-out, and silence between

**Status: ACTIVE, BR-2a-1 BUILT 2026-10-29 (dark).** Verified against code
on 2026-10-28, at `origin/main` `bd0efea9c`.

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
| `model_request_id` | TEXT NULL | Joins the `usage_event` row of the phrase call |
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
5. It sets `ready` with the lead, then **delivers** (§5) and sets
   `delivered`.
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
  for `moved`, and BR-7b's executed acts for `acted`.
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
  of `_numbers` (`assistant_profile.py:612`). BR-2a-2 makes it one public
  helper and copies nothing. A failure keeps the template lead, and the run
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

---

## 5. Delivery (BR-2c)

In the app first. Every surface reads the stored run, so all show the same
words.

1. **My Day.** The MD-7 `brief` card reads today's run. In the evening, the
   `day_end` card reads the close-out. MD-7 owns the card. This spec owns its
   data.
2. **The chat's first turn.** `POST /chat/sessions/{id}/brief` reads the
   stored run when one exists. It keeps its composition as the fallback, and
   its rules of once a day and phone only (D-CF-2, D-CF-3).
3. **A read route.** `GET /brief/today?kind=brief|closeout` answers the run
   and its claims, for the member of the session only.
4. **The bell.** One entry in v1 (§9 Q2, owner 2026-10-29). The entry says
   that the brief is ready, and it opens the brief.
5. **Outside the app.** Web push (PN-1) is in v1, and it ships last (§9 Q2
   and Q7). It sends through Reach, behind `BRIEF_PUSH` and its owner gate.
   WhatsApp stays later (D115).

"Delivered" means the run is `ready` and the read route serves it. The
member does not need to open the app.

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

---

## 7. Delivery monitoring (BR-2d)

**The target.** 99% of runs are `delivered` within 5 minutes of `slot_at`,
over 7 days, for each org.

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
reads `audit_event` rows with an agent actor and `reach_outbox` rows in state
`sent`. Each line names the policy that allowed it. At dispatch, the builder
lists each act path that writes no `audit_event` row. Each one is a finding.

---

## 9. The owner's decisions

The owner decided all seven questions on 2026-10-29 (epic #479, the comment
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
| 3 | BR-4b: the claim fence | 2 | none | AGENT-SAFE |
| 4 | BR-2a-2: the phrase step | 3 | `BRIEF_COMPOSE` | AGENT-SAFE to build. **OWNER-GATE** to flip in production (credits, §3a rule 3) |
| 5 | BR-2c: in-app delivery and the read route | 2 | `BRIEF_IN_APP`, `NEXT_PUBLIC_BRIEF_CARD` | AGENT-SAFE. The production flip: `enforcement-flip` |
| 6 | BR-2d: the monitor | 2, AUTO-2 | none | AGENT-SAFE |
| 7 | BR-2b with BR-7b: the close-out | 2 | `BRIEF_CLOSEOUT` | AGENT-SAFE. The production flip: `enforcement-flip` |
| 8 | BR-7a: the effect registry and its fence | BR-1a | none | AGENT-SAFE |
| 9 | BR-5: the exception rules | 2 | `BRIEF_EXCEPTIONS` | AGENT-SAFE to build. The production flip: `enforcement-flip` (Q4 is decided) |
| 10 | BR-3a: one week on pilot data | 5 | none | **OWNER-GATE**: the pilot orgs and their inboxes are the owner's |
| 11 | Web push of the brief, through Reach | 6 | `BRIEF_PUSH` | **OWNER-GATE**: it reaches a person through a third party |

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
| 2 | On a fake clock, a member in `Asia/Kolkata` gets one run at 07:30 local, and none on a day off. Two planner ticks and a restart in a run give one row and one ledger entry. A timeout of the handler gives the slot back, and the retry gathers once. A planner outage under 2 hours loses no brief, and an older slot leaves a `slot_missed` row. Each line equals `plan.line` of its card, with its `signal_id` and `href`. The run calls no model | `test_brief_run.py`, `test_brief_run_r8.py` |
| 3 | The check refuses a sentence that cites no claim, and a sentence with a number that no cited claim holds. A `delivered` run with a line that has no claim row fails the R8 test | `test_brief_claims_fence.py` |
| 4 | With 4 claims, one call goes out with `source="brief"`. A refused answer, a model error or a balance refusal keeps the template lead, and the run still delivers. A retry after `phrasing` makes no second call. On staging, `model_request_id` joins a `usage_event` row | `test_brief_run.py` |
| 5 | The chat's first turn and the My Day card show the stored lines, and a diff against `plan.brief_parts` of the same cards is empty. A chip opens the record of each claim. `GET /brief/today` answers 404 for a run of another member. With `BRIEF_IN_APP` off, both surfaces answer as before, byte for byte | `test_chat_brief_turn_r8.py`, `linkChips.test.ts` |
| 6 | A run that is not ready at `slot_at + 3 min` delivers the template brief. `brief.slo_missed` logs once when a fake week falls under 99%. The health route answers the lag percentiles | `test_brief_monitor.py` |
| 7 | The 18:30 run lists the Handled items, the moved items and the acts of the day, each with a claim. A day with no item gives `empty`. Each act from `audit_event` names its policy | `test_brief_run_r8.py` |
| 8 | A new job kind with no `effect` fails the fence. So does a `send` kind with no policy, and an import of a send seam into `gateway/brief/` | `test_policy_fence.py` |
| 9 | Between the moments, a card that no rule matches never moves to the top. A matching card does, inside the budget of `proactive_reach.md` §6.1. A rule that the member muted matches nothing | `test_brief_exceptions.py` |
| 10 | The ledger shows one brief and one close-out for each pilot member on 5 working days. Each brief has one email claim and one calendar claim at least | The ledger, read by the owner |
| 11 | Reach sends the stored lead with a link, and `reach_outbox` holds the row | `test_reach_loop.py` |

All fences are under `tests/unit/`.

## 11. Verification commands

```bash
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_brief_store_r8.py tests/unit/test_brief_run.py \
  tests/unit/test_brief_run_r8.py tests/unit/test_brief_claims_fence.py \
  tests/unit/test_policy_fence.py tests/unit/test_brief_monitor.py \
  tests/unit/test_brief_exceptions.py -q
uv run pytest tests/unit/test_reach_plan.py tests/unit/test_chat_brief_turn.py \
  tests/unit/test_chat_brief_turn_r8.py tests/unit/test_job_queue.py \
  tests/unit/test_tenant_coverage.py -q
uv run python scripts/affected_tests.py --run
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/lib/linkChips.test.ts
```

The new test files do not exist yet. Each slice creates the files that its
acceptance names.
