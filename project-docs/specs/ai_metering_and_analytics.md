# AI metering and analytics

**Status: ACTIVE.** Owner directive, 2026-08-29. This specification owns the AI
tier vocabulary that a customer sees, and every surface that reports AI use.

**Slice state, re-measured 2026-08-31.** Slices 1, 2, 4, 5, 6, 10, 11, 12 and
13 are **BUILT**. Slice 6 shipped as `b3ce3a9c` (#163) and slice 12 as
`537147b2` (#168). §8's table carries a Gate column, so a dispatcher reads
AGENT-SAFE or OWNER-GATE per row.

**Three of them shipped on 2026-08-31.** Slice 5 shipped as
`020_usage_refusal.sql` plus the Router and store changes §8.1 records. Slice
11 shipped as the stream walk §8.6 records. Slice 4 shipped as the image rule
§8.5 records, and a follow-up the same day added §3.2 step 3b and §8.5 clauses
7 and 8.

**One slice is SPEC ONLY, and it holds a contract.** Slice 3 is §8.4, and it is
AGENT-SAFE. Its clause 6 is the only unbuilt clause left in this file. The
contract names its done-when clauses, its fences (R7) and its verification
command. Slices 4, 5 and 11 held three more contracts, and §8.5, §8.1 and §8.6
are now their build records.

**The other three, so that 13 rows add up.** *(Added 2026-08-30 — the two
paragraphs above accounted for 10 slices and left 3 unnamed.)* Slice 7 is SPEC
ONLY and holds no contract yet. Slice 8 is OWNER-GATE behind **H-73**. Slice 9
reads as BUILT, because `011_tier_fallback_chain.sql` and the
`test_customer_console_fallback_chain.py` fences both exist. It owes one audit
before anybody marks the row.

⚠️ **Slice 3's TABLE already shipped.** `tier_catalog` landed in
`015_tier_pricing.sql` under D67, and `016_tier_task.sql` added its `task`
column. Slice 3 is now one column plus the read seam. §3.1 carries the
rewritten build note.

⚠️ **This file said the opposite in four places until 2026-08-30.** F7, F9,
§3.6 and §5 each claimed an unbuilt surface that had shipped. Each one now
carries the date of its rewrite. Re-verify every anchor at dispatch.

⚠️ **A diff review then corrected four more places, also on 2026-08-30.** §8.1
clause 3 wrote a refusal row inside the transaction that raises. §8.4 clause 6
named one label source of the two that exist. §3.6 and §8.6 both cited
`relay_stream` for the three failover constants. Each correction carries its
own date.

**Owning board row:** WS-31. **Related:** `customer_console.md` §6A owns the
model catalog. `launch_surface.md` §4 owns the price. This file owns what a
person **sees** and what the numbers **mean**.

---

## 1. The target experience

The owner stated this on 2026-08-29. It is recorded here word for word in
meaning, and every section below builds one part of it.

### 1.1 A customer in chat

1. A customer sees three tiers. They are **Fast**, **Balanced** and **Powerful**.
2. An uploaded image goes to a separate **image tier**. The Router calls that
   tier separately. If the chat model already understands images, the Router
   uses the chat model and calls nothing else.
3. Each app selects a tier for itself. Most apps take a default.
4. Each tier costs the customer a different amount.

### 1.2 A specialised model

Speech to text, text to speech and other specialised models sit behind a
proxy. An app such as the Note Taker calls the proxy. The customer does not see
the model. The call still consumes AI credits.

### 1.3 A customer administrator

The administrator sees AI use across the whole company, and can slice it:

- Which member used how much.
- Which app consumed how much.
- How use changed over time.
- A budget for each member.
- A way to buy more AI credits.

All five surfaces belong to the customer app.

### 1.4 An operator (us)

- Assign AI credits to an organization.
- See how each organization uses AI.
- See the analytics in §6.

All three surfaces belong to the Operator Console.

---

## 2. What is true today — measured, not remembered

An agent must read this table before it proposes a change. The values come from
the live Console database on 2026-08-29.

| # | Finding | Evidence |
|---|---|---|
| **F1** | **`usage_event` already carries every slice §1.3 asks for.** Columns: `organization_id`, `user_email`, `agent`, `module_slug`, `model`, `tier`, `task`, `quantity`, `unit`, `billed_credits`, `provider_cost_usd`, `created_at` | `information_schema` |
| **F2** | **The task catalog already separates the modes.** `chat` (tokens), `vision` (tokens), `image` (images), `transcribe` (minutes), `speak` (characters), `embed` (tokens) | `task_catalog` |
| **F3** | **Four tiers hold a binding.** `tier-fast`, `tier-balanced`, `tier-powerful`, `tier-stt` | `tier_binding` |
| **F4** | 🔴 **Only three capabilities exist**, and all three are for `chat` or `transcribe`. Nothing declares `vision`, `image`, `speak` or `embed`. Those four tasks cannot be served | `model_capability` |
| **F5** | 🔴 **A rate card names the wrong task.** `groq/whisper-large-v3-turbo` declares `transcribe`, and its only rate card row is `chat`. Whisper bills per minute, and a chat card prices per 1000 tokens | `model_rate_card` |
| **F6** | 🔴 **Every rate card is `unpriced`.** No AI call bills anything | `model_rate_card` |
| **F7** | **The operator can grant credits AND read use.** *(Rewritten 2026-08-30 — slice 6 shipped, and this row said the opposite.)* `usage_by_org` (`store.py:721`) feeds `GET /admin/usage/orgs` (`main.py:5083`), beside `POST /credits/grant` and `GET /credits/balance` | `store.py`, `main.py` |
| **F8** | **The customer can read use by app and by member.** `GET /my/usage/activity` and `GET /my/usage/members` shipped in CP-7 | `main.py` |
| **F9** | **The operator time series exists. The customer one does not.** *(Rewritten 2026-08-30.)* `GET /admin/usage/daily` (`main.py:5148`) serves the operator, for the platform or for one organization. C3 has a store function and no route | `main.py` |
| **F10** | **No per-member budget table exists.** H-73 records why: the member identity arrives in a header the member controls | `information_schema`, HANDOFF |
| **F11** | **`usage_event` holds 0 rows.** Every surface below ships to an empty table | live query |
| **F12** | 🔴 **F1's column list is INCOMPLETE, and an agent must not read it as the table.** Five later migrations added columns F1 never names: `run_id` (`003`), `client_ref` (`005`), `served_rank` and `byok_served` (`013`), and `refusal_reason` (`020`). `001` itself also holds `id`, `request_id` and the three token counters. `010` added the `task`, `quantity` and `unit` that F1 does name. **Read `information_schema`, never this row** | `infra/customer_console/001`, `003`, `005`, `010`, `013`, `020` |

---

## 3. The tier vocabulary

### 3.1 A tier has a slug and a label

**Decision D-AI-1.** The wire name and the display name are two things.

`tier_binding.tier` holds the slug. A slug is permanent, because a past invoice
names it. The customer never sees a slug.

| Slug | Label the customer sees |
|---|---|
| `tier-fast` | Fast |
| `tier-balanced` | **Balanced** |
| `tier-powerful` | Powerful |
| `tier-vision` | (not shown — see §3.2) |
| `tier-stt` | (not shown — see §3.3) |

⚠️ **The label is `Balanced`, and the slug is `tier-balanced`.** The owner
considered "Medium" and kept "Balanced" on 2026-08-29. The two now agree, and
that is convenient rather than required — D-AI-1 exists so a future label
change costs nothing. A slug rename would break every binding row and every
past usage row that names the old slug.

**Build note, rewritten 2026-08-30.** This paragraph asked for a table that
already exists. `tier_catalog` **shipped** in `015_tier_pricing.sql:29-58`
under D67, and `016_tier_task.sql:23-24` added its `task` column. The shipped
columns are `slug`, `label`, `blurb`, `sort_order` and `task`.

⚠️ **The customer-facing text is `blurb`, not `description`.** `015:33` is the
shipped name. This section said `description` until 2026-08-30. Use the
shipped name, because W3 allows one term for one thing.

**Slice 3 adds ONE column.**

| Column | Type | What it holds |
|---|---|---|
| `customer_visible` | `BOOLEAN NOT NULL DEFAULT TRUE` | TRUE lets a customer picker show the tier. FALSE hides it |

⚠️ **The default is TRUE, and §3.3 turns six rows FALSE.** A tier added later
shows up until an operator hides it. R6 binds the migration. Add one column
with a default. Rename nothing. Drop nothing.

⚠️ **Migration `021` today, and an agent re-takes the number (R1).** The
highest number on disk on 2026-08-30 is `018_credit_ref_unique.sql`. H-78
claims `019` (`customer_console.md` §6A.11a). Slice 5 claims `020` (§8.1). So
`021` is the number today. List `infra/customer_console/` before you name the
file, and list it again at merge.

§8.4 holds the rest of the contract. The customer picker reads the table. The
operator edits it.

### 3.2 An image follows the chat model when it can

**Decision D-AI-2.** The caller declares `task: vision`. The Router never
reads the payload.

*(The opening line read "The Router decides, and the customer does not" until
2026-08-30. It fought three live decisions. G-3 and D61 put the declaration on
the caller, and `CompletionRequest.task` (`main.py:834-852`) carries it today.
The Router still picks WHICH MODEL answers, and the four steps below are that
choice.)*

🔴 **Step 0.5. A TIER THAT BINDS THE DECLARED TASK SERVES IT DIRECTLY. The
image rule runs only for a tier that does not.** This rule names the declared
TASK and not one slug. So a second vision tier an operator adds tomorrow
serves itself, with no Router change.

*(Added 2026-08-31. The build read the chat binding first, and a verifier
measured the result. A caller who named `tier-vision` with `task: vision` got
the answer `no binding for tier 'tier-vision' on task 'vision'`. That tier
binds `vision` and binds no chat model, so the sentence is FALSE. The same
call returned 200 before the slice.)*

**D16 marker:** step 0.5 is an agent-proposed repair the owner may overrule.

⚠️ **Step 0, and it did not move.** A chosen tier that binds NEITHER the
declared task NOR a chat model meets the `TierUnknown` wall this route already
had. The detail still says *"name a tier, not a model"*, and the refusal row
names the tier the CALLER asked for. D-AI-2 changes nothing here, because
there is no chat model on which to read a flag.

**On a call that declares `task: vision` for a tier that binds no `vision`
model of its own, the Router does this:**

1. Read the model bound to the chosen tier for the `chat` task.
2. If `model_profile.reads_images` is TRUE for that model, send the image to
   it. Bill the (chosen tier, `chat`) pair.
3. If the flag is FALSE, resolve `tier-vision` for the `vision` task, and send
   the image there. Bill the (`tier-vision`, `vision`) pair.
4. If nothing binds `tier-vision`, refuse with HTTP 400 and name the reason.

🔴 **Step 3b. A BLIND STEP NEVER ENTERS THE LIFT CHAIN. For a declared vision
task, keep only the chain steps that set `reads_images`.** When no step
remains, the Router falls to `tier-vision`, exactly as a chain of one FALSE
step does. Steps 1 to 3 above read the RANK-1 step alone until 2026-08-31, and
step 3b narrows all three.

*(Added 2026-08-31. Rank 1 read images, rank 1 failed, and the walk moved to a
blind rank 2. That model then answered about a picture it never saw, with a
confident 200. A wrong answer is worse than a refusal, which is why step 4
refuses and does not serve.)*

**D16 marker:** step 3b is an agent-proposed repair the owner may overrule.

⚠️ **ONE source for the flag, and it is `model_profile.reads_images`
(`012_model_profile.sql`).** `model_capability` holds no `vision` row for
any model today (F4), so a capability read answers nothing. Two sources for
one fact is how the two start to disagree.

⚠️ **The feed PREFILLS the flag. It does not populate it** *(corrected
2026-08-31)*. `feed.sync` writes `vendor_price_feed` alone, so an operator
types nothing (§6A.11) but must still SAVE each model. `POST /catalog/profiles`
is the one writer of `model_profile`, and §8.5 names the two acts.

⚠️ **Step 2 is the money.** A second call to a vision model costs a second
call. A chat model that already reads images costs one.

⚠️ **Both bills read the (tier, task) pair, never a model rate card.** Step 2
bills (chosen tier, `chat`). Step 3 bills (`tier-vision`, `vision`). Both go
through `resolve_tier_rate` (`router.py:367`). D67 moved the customer price onto
the tier, and the model-keyed write endpoint answers 410
(`015_tier_pricing.sql:16-19`). This section said "rate card" and meant the
retired one until 2026-08-30.

⚠️ **Step 3 is a capability LIFT, and §6A.9 rule 1 does not forbid it.** That
rule says a degradation stays WITHIN a task, so `(image, powerful)` must never
fall to `(chat, fast)`. Step 3 is the opposite move. It adds a `vision` call
beside the chat call, and the tier the customer picked does not drop. Every
chat turn in the same conversation stays on the chosen tier.

⚠️ **Step 4 refuses with 400, and never with 200.** The detail body is one
line, and it names both halves of the wall:

```text
no vision model is bound; the chat model for tier <slug> does not read images
```

An operator reads it and knows which half to fix. The wording follows the shape
the unbound-tier refusal beside it already uses (`_resolve_serving_chain`,
`main.py:4983`). A silent drop of the image makes the model answer about text
it cannot see, and the answer looks correct.

### 3.3 A specialised tier stays invisible

**Decision D-AI-3.** A tier the CALLER never names stays out of the customer
picker. An app names the task. The Router resolves the tier.

*(This decision named three tiers until 2026-08-30. `015_tier_pricing.sql:46-58`
seeded ELEVEN, so three was no longer the whole answer. The table below names
every one of the eleven. Each value is an **agent-proposed answer the owner
may overrule**, which is the D16/D17 convention CP-2b and CP-2c used.)*

| Slug | `customer_visible` | Why |
|---|---|---|
| `tier-fast` | **TRUE** | §1.1. The customer picks one of three chat bands |
| `tier-balanced` | **TRUE** | §1.1 |
| `tier-powerful` | **TRUE** | §1.1 |
| `tier-code` | **TRUE** | A fourth chat band. A customer picks it the same way |
| `tier-image` | **TRUE** | The customer asks for a picture, so the customer picks the quality |
| `tier-vision` | **FALSE** | §3.2 step 3 resolves it. No caller names it |
| `tier-stt` | **FALSE** | §1.2. The app names `transcribe` |
| `tier-tts` | **FALSE** | §1.2. The app names `speak` |
| `tier-embed` | **FALSE** | §1.2, and D19.2 absorbs the price. Nobody picks a search index |
| `tier-video` | **FALSE** | Nothing binds it, and no Router verb serves it (§6A.11a) |
| `tier-music` | **FALSE** | Nothing binds it, and litellm carries no `music` mode |

**One test separates the two columns.** TRUE means a person chooses this tier
on purpose. FALSE means the Router or the app chooses it, so a picker entry
would offer a choice nobody can act on.

⚠️ **`tier-video` and `tier-music` turn TRUE the day something binds them.**
They are FALSE because they are empty, and not because they are internal.
`015` put them on the slate to show what we intend to sell.

The usage still records every tier, so the administrator in §1.3 sees the cost
of a hidden one.

### 3.4 An app selects a tier

**Decision D-AI-4.** Each app declares a default tier for each task it uses.
An administrator may change it. A member may not.

This reuses `model_config`, key `agent_aliases`, which already exists.

### 3.5 A tier holds an ordered chain

**Decision D-AI-5.** A tier points at an ordered list of models, not at one
model. The Router tries rank 1. If that step fails, it tries rank 2.

**Owner directive, 2026-08-29.** A provider goes down. We hold a live key for
three other vendors. Today every customer on that tier gets an error, because
`tier_binding` has nowhere to put a second choice.

Three rules make a chain mean something.

1. **The Console writes a chain whole.** Every step shares one `effective_from`.
   To remove a step, write the chain you want. §6A.5 stays insert-only, so an
   old invoice still reads against what it charged.
2. **The Console checks every step, not the first.** An unchecked backup is
   worse than no backup. The Router reaches it only after the first choice
   failed, so the error arrives during an outage.
3. **A chain on one provider is not a backup.** The provider goes down, not
   the model. The Operator Console warns, and it does not refuse — the operator
   decides.

⚠️ **The table stores an order. It does not retry.** The Router does not yet
walk the chain. Slice 10 adds that.

---

### 3.6 Failover, and what it refuses to do

**Decision D-AI-6.** The Router walks the chain. Three rules bound it.

1. **A bad request does not fail over.** A 400, 404, 413 or 422 fails the same
   way on every step. Each try costs money, so the walk stops.
2. **A bad key of ours strikes off the whole vendor.** A 401 or 403 means our
   credential is wrong. Every model from that vendor presents the same key.
3. **The customer pays for the step that ANSWERED.** A request that falls over
   from an expensive model to a cheap one costs the cheap one.

✅ **A stream fails over BEFORE its first frame, and never after it** *(slice
11, built 2026-08-31)*. After the first frame reaches the client, the request
is half answered. A retry would join two different completions into one
response, which is worse than the error. §8.6 holds the contract.

**The boundary, stated once.** The route walks the chain, opens each step and
pulls its FIRST CHUNK (`open_stream_chain`, `router.py:1025`). Only then does it
hand Starlette a body generator, and only then does the 200 status line go out.
Every failure up to that chunk may fail over. Every failure after it may not.

**The stream path reuses one policy and adds none of its own.**
`TERMINAL_STATUSES` (`router.py:890`) and `CREDENTIAL_STATUSES`
(`router.py:894`) are read in `walk_chain` (`router.py:928`), through
`is_retryable`, and in no other function. Both `call_chain` (`router.py:978`)
and `open_stream_chain` (`router.py:1025`) walk through it. A second failover
policy beside the first is the CLAUDE.md §5 defect, not a feature.

⚠️ **`MAX_CHAIN_ATTEMPTS` (`router.py:882`) is the ONE exception, and it is a
trap.** `walk_chain` does not read it. Each ROUTE caps its own list before it
hands the list over, with `attempts[:router_mod.MAX_CHAIN_ATTEMPTS]`
(`main.py:5224` chat, `main.py:5647` transcribe).

So a third caller of `walk_chain` that forgets that slice inherits NO ceiling.
An unbounded chain is an unbounded bill and an unbounded wait. *(This
paragraph said `walk_chain` reads the constant until 2026-08-31. It does
not.)*

*(The anchors above read `router.py:559-608` until 2026-08-30, and
`router.py:617-663` until 2026-08-31. The first range is `relay_stream`, which
is a different function.)*

✅ **`_note_failover` says WHY, and the row says WHICH** *(the stale comment in
the code, repaired by slice 11)*. That callback read *"`usage_event` has no
column for the step that served"*. Slice 12 built `served_rank` in
`013_pricing_truth.sql:26-40`, so the sentence was false. The callback
(`main.py:5021`) now states the split: the row carries the step, the log line
carries the reason. The route declares it ABOVE the stream branch and both
branches use it, so the chat route holds one copy and not two.

⚠️ **The chain tries at most `MAX_CHAIN_ATTEMPTS` steps.** An unbounded chain
is an unbounded bill and an unbounded wait.

✅ **`usage_event` records the step that ANSWERED.** *(Rewritten 2026-08-30.
This paragraph said the column did not exist. Slice 12 built it — see §8.3.)*

`served_rank` (migration `013`) holds the position of the step that served.
Rank 1 is the first choice. A rank above 1 is a failover. NULL predates the
column, or comes from a caller that does not say. The Operator Console reads
`served_rank > 1` over 14 days (`main.py:1789-1804`).

⚠️ **The record holds no `from` and no `reason`, and that is deliberate.** The
step we fell FROM is a join against a re-bindable history, so a chain edited
tomorrow rewrites what yesterday's row appears to say. The reason lives in the
`router.failover` log line, where an outage is read.

---

### 3.7 What a model IS

**Decision D-AI-7.** `model_profile` records the window, the output cap, what
the vendor charges us, and two capability flags. It is keyed on the model.

**Why it exists.** `/models` is the page an operator picks a model on. It could
not say how big the window is, whether the model reads an image, or what we
pay — no column held any of it. Every card showed a dash.

**Four rules.**

1. **Keyed on the model, not on the model and the task.** A window is a
   property of the model. The pair key gives a model with two tasks two copies,
   free to disagree.
2. **This table is UPDATED in place.** A binding and a rate card stay
   insert-only. A past invoice must read against the decision that made it. A
   window is a fact about the world.
3. **NULL means nobody told us.** The database refuses a window of zero. Zero
   reads as a broken model, and as a free one in the price column.
4. **Nothing is seeded.** A table of vendor windows and prices is a mirror of
   eleven vendors' documentation. It starts to lie the first time one of them
   ships a model.

⚠️ **`vendor_*_per_1m_usd` is what the VENDOR charges US.** `model_rate_card`
is what we charge a customer. These two are the most confusable pair after
`provider_credential` and `llm_api_key`. Read one as the other and the margin
inverts. So the column name carries the payer and the unit.

⚠️ **`reads_images` and `thinks_first` are not tasks.** No tier binds them.
They are properties of a chat model, and D-AI-2 turns on the first one.

⚠️ **Migration `019` added the three per-unit vendor costs to this table, and
the seam fills them on declare since 2026-08-31.** The columns are
`vendor_per_minute_usd`, `vendor_per_character_usd` and
`vendor_per_image_usd`, each in the task's natural unit. H-78 clause 1 built
them, and clauses 5 to 7 built the seam.

The feed read converts the per-second price once. Then
`POST /catalog/profiles` writes the result through without arithmetic. A
column still holds NULL until an operator saves a profile. That keeps the rule
that only a staff write changes what billing costs.

`customer_console.md` §6A.11a owns the columns, the parse rules and the ×60
conversion. *(This read "nothing fills them yet" until 2026-08-31.)*

⚠️ **`editor`, and no elevation window.** This is the only catalog write that
demands neither. It changes nothing about what runs or what we charge. A
description edit behind an elevation window teaches an operator to reach for
the break-glass token for routine work.

---

## 4. Customer surfaces — documented now, built later

**Owner directive, 2026-08-29: record these and build them when the time is
right.** The operator side comes first. This section holds enough detail that a
later agent does not have to re-derive any of it.

Every surface below lives in `workbench/control_plane`. That app has had **no
UI change** for AI metering. The two reads in C1 and C2 shipped in CP-7 and no
page calls them.

### 4.1 The table

| # | Surface | Reads | State | Blocked by |
|---|---|---|---|---|
| C1 | Spend by app | `GET /my/usage/activity` | API built, **no UI** | — |
| C2 | Spend by member | `GET /my/usage/members` | API built, **no UI** | — |
| C3 | Spend over time | `usage_daily(org_id=...)` | Store built, no route | — |
| C4 | Buy AI credits | `POST /credits/grant` | Not built | Razorpay (**H-14**) |
| C5 | A budget for each member | — | Not built | 🔴 **H-73** |
| C6 | The tier picker | `tier_catalog` | Not built | §3.1 |

### 4.2 What each one must do

**C1 and C2 are the cheapest work in this file.** The reads exist, they are
tenant-safe, and they take a `member` argument. One page consumes both.

- Show the whole company by default.
- Let an administrator pick one member, and pass that member to C1.
- Name the unattributed row. `store.UNATTRIBUTED_ACTIVITY` holds the word.
- 🔴 Say that every credit column reads zero while the rate card is unpriced
  (**H-42**). A column of zeros with no explanation reads as "we use no AI".

**C3 uses `usage_daily` with an `org_id`.** That function already exists and is
tenant-safe with the argument. Do not write a second query.

- The series fills every gap. Do not add a second gap fill in the client.
- 30 days by default. `USAGE_MAX_DAYS` bounds the request.

**C4 sells credits.** It needs the Razorpay account first.

- Reuse `credit_ledger` and the `LEDGER_REASON_PURCHASE` vocabulary.
- Show the balance before and after.

**C5 is blocked, and the block is not a schedule problem.**

🔴 The Console reads the member from the `X-CC-Member` header, and the gateway
forwards that header from the request. **A member who omits it escapes the
budget.** A budget the budgeted person can turn off is not a budget. H-73 owns
this. Do not build C5 first and secure it later.

**C6 shows three labels.** `tier_catalog.customer_visible` decides which.

- Read the label, never the slug. §3.1 says why.
- An app default comes from `model_config`, key `agent_aliases`.

### 4.3 What the customer must never see

**D66 binds every surface above.** A customer sees a tier. A customer never
sees a model id, a provider name, or a rate card.

⚠️ `usage_event` carries `model` and `tier` on the same row. A spend table that
selects `model` because the column is there breaks D66 in one line. The two
reads in C1 and C2 already exclude it, and a new read must do the same.

---

---

## 5. Operator surfaces

*(Re-measured 2026-08-30. O2, O3 and O5 shipped in slice 6, `b3ce3a9c`, #163.)*

| # | Surface | State | Blocked by |
|---|---|---|---|
| O1 | Grant credits to an organization | **Built** (`POST /credits/grant`) | — |
| O2 | **Use by organization** | **Built** — `GET /admin/usage/orgs` (`main.py:5083`) over `usage_by_org` (`store.py:721`) | — |
| O3 | **Use over time** | **Built** — `GET /admin/usage/daily` (`main.py:5148`) over `usage_daily` (`store.py:826`) | — |
| O4 | Drill into one organization by member, app and tier | ◐ **Half built.** `GET /admin/usage/daily` takes `org_slug`, so the per-organization series exists. **No operator read returns one organization by member, by app or by tier** — only the customer routes in F8 do that | — |
| O5 | The analytics in §6 | **Built** — `analytics.py` carries A1, A2, A3 and A6, and `GET /admin/usage/orgs` sends them. A4 and A7 stay unbuilt | — |

---

## 6. Analytics we propose

These are not in the owner's list. Each one answers a question the data can
already answer, and each one names the action it triggers.

| # | Metric | The question it answers | The action it triggers |
|---|---|---|---|
| **A1** | **Margin per organization** — `billed_credits` against `provider_cost_usd` | Do we make money on this customer? | Re-price, or change the tier binding |
| **A2** | **Credit runway** — the balance divided by the 7-day burn rate | When does this customer run out? | Call them before they stop |
| **A3** | **A silent customer** — credits granted, and no use for 14 days | Is this customer leaving? | Ask why |
| **A4** | **Model mix** — the share of calls for each model | Which vendor contract matters? | Negotiate, or move a tier |
| **A5** | **A refused call** — a 402 for no credit, a 400 for an unknown tier, a 403 for the run ceiling. *(This cell named only two of the three until 2026-08-30. `_spend_refusal` raises the 402 and the 403 in one function.)* | Is a customer hitting a wall? | Support, before they write in |
| **A6** | **A cost spike** — a day above five times the trailing mean | Did something run away? | Investigate the app and the member |
| **A7** | **Tier efficiency** (customer side) — the share of calls on Powerful | Does this company overpay? | Move an app to Medium |

⚠️ **A1 is the most important number on the operator side, and nobody asked for
it.** We record what we pay (`provider_cost_usd`) and what we charge
(`billed_credits`) on the same row. A customer can be busy and unprofitable,
and no current surface shows it.

✅ **A5 got its column on 2026-08-31.** `usage_event.refusal_reason` names the
wall a customer hit, and NULL means the call served. §8.1 is the build record.

### 6.1 Two decisions the build already took

**Both are agent-proposed answers the owner may overrule**, which is the
D16/D17 convention CP-2b and CP-2c used. Slice 6 shipped them, and this
section records them so a later agent does not re-take them differently.

**Decision D-AI-8. An unmeasured provider cost reads as UNKNOWN margin, never
as zero.** `provider_cost_usd` is nullable, and NULL means nobody measured what
the traffic cost us. `margin_ratio` (`analytics.py:54`) returns `None` on a
cost of zero or less. `GET /admin/usage/orgs` sends `marginRatio: null`
(`main.py:4911`), and `marginTone` paints null neutral.

Two rules follow. `usage_by_org` judges margin over the **costed** calls only,
so all-calls credits over some-calls cost never inflates the ratio. `costedShare`
(`main.py:4915`) travels beside the ratio and carries the coverage, so an
operator reads how much of the traffic the number speaks for.

⚠️ **Infinity rendered as "excellent margin" is the failure this refuses.** A
confident wrong number in an analytics surface costs more than a blank one.

**Decision D-AI-9. Margin is a RATIO, never money.** `billed_credits` and
`provider_cost_usd` hold different units, and no credit has a rupee price yet
(H-42). Subtracting one from the other invents an exchange rate nobody chose.
The rationale is in `analytics.py:10-19`.

So the surface reports credits billed per dollar of provider cost. That number
is unitless. It compares between organizations and across time. It turns into
money on the day the owner prices a credit, and not before.

---

## 7. What blocks the chain

An agent must not report these as done. They are owner acts.

| Gate | Handoff | Effect while open |
|---|---|---|
| Install a provider credential | — | No AI call succeeds |
| Price the rate cards | **H-42** | Every call bills 0 |
| Turn on `ROUTER_SERVING_ENABLED` | **H-69** | The Router serves nothing |
| Close the member identity hole | **H-73** | C5 cannot ship |
| Create the Razorpay account | **H-14** | C4 cannot ship |

🔴 **F11 follows from these.** `usage_event` holds 0 rows, so every surface in
§4 and §5 ships to an empty table. **Each surface must therefore say what is
absent and what it costs.** An empty chart reads as a quiet week.

---

## 8. Slices, in order

Each slice ships alone and is verifiable alone.

**The Gate column, added 2026-08-30.** AGENT-SAFE means an agent builds the
whole slice and opens a pull request. OWNER-GATE names an act only the owner
takes. A flag flip and a price are owner acts, and the build in front of one
is still agent-safe. §7 holds the gates themselves.

| # | Slice | Delivers | Gate |
|---|---|---|---|
| **1** | Operator reads: `usage_by_org`, `usage_daily` in `store.py` ✅ | O2, O3 | AGENT-SAFE |
| **2** | Operator Console `/usage` page ✅ | O2, O3 | AGENT-SAFE |
| **3** | The `customer_visible` column and the label seam — the TABLE shipped in `015`. **§8.4 holds the contract** | §3.1, C6 | AGENT-SAFE |
| **4** | Router image rule ✅ `resolve_vision_chain` — **§8.5** | §3.2 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **5** | Record a refusal in `usage_event` ✅ `020_usage_refusal.sql` — **§8.1** | A5 | AGENT-SAFE |
| **6** | Margin and runway ✅ `b3ce3a9c` (#163) — **§8.2** | A1, A2 | AGENT-SAFE · a priced margin waits on H-42 |
| **7** | Customer time series | C3 | AGENT-SAFE |
| **8** | Per-member budget | C5 | 🔴 **OWNER-GATE** — blocked on H-73. Do not build it first and secure it later |
| **9** | `tier_binding.rank`, and the Console reads and writes a chain | §3.5 | AGENT-SAFE |
| **10** | The Router walks the chain when a step fails ✅ | §3.5, §3.6 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **11** | A stream fails over before its first frame ✅ — **§8.6** | §3.6 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **12** | `usage_event` records the step that served ✅ `537147b2` (#168) — **§8.3** | §3.6 | AGENT-SAFE |
| **13** | `model_profile` — what a model IS ✅ | §3.7 | AGENT-SAFE |

⚠️ **Row 12 delivered §3.6 and NOT A5.** The cell named A5 until 2026-08-30,
and that was wrong: `served_rank` records a call that answered, and a refusal
answers nothing. A5 belongs to slice 5 alone.

### 8.1 Record a refusal in `usage_event` (slice 5, A5) — BUILT, 2026-08-31

**This shipped on 2026-08-31.** The migration is `020_usage_refusal.sql`. The
writer is `main._record_refusal`, and the five counting reads in `store.py`
now exclude a refusal. Every clause below carries its fence, and the section
is the build record rather than a proposal.

⚠️ **The anchors below are the ones the build left.** The route moved when the
400 stopped raising from inside the serving transaction, so the line numbers
this section carried on 2026-08-30 no longer hold. Re-verify every anchor
before you cite one.

**The problem this closed.** `usage_event` recorded a call that happened, and
a refusal wrote no row. So A5 could not answer "is a customer hitting a wall",
and six refusal shapes in the Router route left no trace in the meter.

**The answer, in one line.** One nullable column names why we refused. NULL
means the call served. Every read that counts calls excludes the refusals.

#### The column — migration `020_usage_refusal.sql`

⚠️ **The build took the number from the directory, and R1 re-checks it at
merge.** The
highest number on disk on 2026-08-31 was `018_credit_ref_unique.sql`, because
`customer_console.md` §6A.11a's `019` for H-78 sits on another branch. If a
merge collides, renumber this file. No suite names it by number:
`_customer_console_ladder.ladder()` reads the directory.

R6 binds the migration. It adds one nullable column. It renames nothing and it
drops nothing.

| Column | Type | What it holds |
|---|---|---|
| `refusal_reason` | `TEXT` NULL | The slug we refused on. NULL means the call served |

The column carries a CHECK: `refusal_reason IS NULL OR refusal_reason IN` the
three slugs below. A closed vocabulary is the point. An open TEXT column grows
a fourth spelling of the same wall within a month.

#### The vocabulary — three slugs, closed

| Slug | Status | Where the code raises it |
|---|---|---|
| `insufficient_credits` | 402 | `credits.py:401`, inside `decide_spend`. Copied word for word |
| `run_ceiling_exceeded` | 403 | `main.py:1020`, inside `_spend_refusal`. Copied word for word |
| `tier_unknown` | 400 | `resolve_chain` raises `TierUnknown` and `main.py:4702` catches it. This section mints the slug |

⚠️ **The 2026-08-30 draft of this table said `main.py:4571` RAISES
`TierUnknown`, and that was one line out in the wrong direction.** The raise is
in `router.resolve_chain`. The route only catches it. Corrected 2026-08-31.

`main._REFUSAL_REASONS` names the same three slugs in the writer. The writer
drops a fourth spelling and logs it, so a typo never becomes an IntegrityError
on the hottest path in the system.

⚠️ **Two of the three already exist in the body the customer reads.** Copy
them. Do not mint a second spelling for a wall that has a name. W3 binds this.

#### Which refusal writes a row, and which does not

**A refusal writes a row when the CUSTOMER caused it.** Those three are the
walls A5 watches for.

| Shape | Anchor | Writes a row |
|---|---|---|
| 400 unknown tier | `main.py:4702` | **Yes** |
| 402 no credit | `credits.py:401` | **Yes** |
| 403 run ceiling | `main.py:1020` | **Yes** |
| 503 credential unavailable | `main.py:4533`, in `_chain_credentials` | No |
| 503 no vendor configured | `main.py:4754` | No |
| 502 vendor failure | `main.py:4882` | No |
| 401 bad key | `auth.py:454` | **Cannot** |

**Named non-goal: the two 503s and the 502 are OUR failures, not a customer
wall.** They belong to the log line and to a later operations surface. One
table that mixes a customer wall with a broken vendor answers neither
question. Building that surface is a separate slice, and this one does not
start it.

🔴 **A 401 CANNOT write a row, and the reason is structural.** `auth.py:454`
refuses before the code knows the organization. `usage_event.organization_id`
is `NOT NULL` (`001_customer_console.sql:256`). A row needs a tenant, and at
401 there is none. **Do not invent a system organization to make it fit.**

**No 413 exists.** The route clamps `max_tokens` to `_MAX_OUTPUT_TOKENS`
(`main.py:4771-4773`) instead of refusing an oversized request.

#### What a refusal row holds

| Field | Value | Why |
|---|---|---|
| `request_id` | a fresh `rtr-<uuid4>` | `001_customer_console.sql:271` makes it NOT NULL UNIQUE. Minted exactly as the served path mints it at `main.py:4470` |
| `refusal_reason` | one of the three slugs | The wall |
| `billed_credits` | `0` | We served nothing, so we charge nothing |
| `quantity` | `0` | The call consumed nothing |
| `unit` | the task's unit, from `task_catalog` | The row stays readable beside a served row |
| `tier` | the tier the caller ASKED for | A5 must say which tier the customer wanted |
| `run_id` | `caller.run_id` | A `run_ceiling_exceeded` row without its run is not actionable. `main.py:1007` reads the same field to decide the refusal |
| `client_ref` | `req.client_ref` | *(Added 2026-08-31.)* The customer's own correlation id, exactly as a served row carries it. Support matches "my request failed" against this and nothing else |
| `model` | `NULL` | No model answered |
| `provider_cost_usd` | `NULL` | No vendor billed us |

⚠️ **`tier` holds the REQUESTED tier, never a resolved one.** At
`tier_unknown` there is nothing to resolve, and the tier on the request is the
fact A5 reports.

🔴 **`tier`, `task` and `client_ref` are CLIPPED at 200 characters
(`_REFUSAL_LABEL_MAX`, added 2026-08-31).** All three are caller-supplied, and
nothing upstream bounds any of them. A refused request costs the sender
nothing. So an unclipped label lets one sender grow the table by megabytes,
for the price of a rejected request. These cells are observability — *which tier
did they ask for* — and never an authority anything reads back. A clipped
value answers that question, and a whole one answers nothing more.

#### 🔴 The five counting reads MUST exclude a refusal

**This is the defect this slice could have shipped, and it is silent.** A
refusal row lands in `usage_event`, and every read that counts rows starts
counting a refusal as a call. The call counts inflate. The credit sums stay
correct, because a refusal bills 0. So the two columns disagree and nothing
says why.

| # | Read | Anchor | The count | What the build did |
|---|---|---|---|---|
| 1 | `usage_by_activity` | `store.py:595` | `COUNT(*) AS calls` | `AND refusal_reason IS NULL` in the WHERE clause |
| 2 | `usage_by_member` | `store.py:656` | `COUNT(*) AS calls` | `AND refusal_reason IS NULL` in the WHERE clause |
| 3 | `usage_by_org` | `store.py:773` | `COUNT(u.id) AS calls` | `FILTER (WHERE u.refusal_reason IS NULL)` |
| 4 | `usage_by_org` | `store.py:788` | `COUNT(DISTINCT u.user_email) AS members` | `FILTER (WHERE u.refusal_reason IS NULL)` |
| 5 | `usage_daily` | `store.py:863` | `COUNT(u.id) AS calls` | `FILTER (WHERE u.refusal_reason IS NULL)` |

⚠️ **`usage_by_org.costed_calls` needed no edit.** It already filters on
`provider_cost_usd IS NOT NULL`, and a refusal records no provider cost.

⚠️ **Reads 3, 4 and 5 take a `FILTER` clause, never a WHERE clause and never
an ON clause.** All three sit on a LEFT JOIN that exists on purpose.

A WHERE clause on the right table turns the LEFT JOIN into an inner join. The
zero-usage organization `usage_by_org` exists to show then disappears. An ON
clause keeps the join, and it still hides the refusal from
`MAX(u.created_at)`. That makes a customer at a wall read as SILENT to A3.

**Three reads stay unchanged, and an agent must not touch them.** The build
left all three byte-identical.

- `run_spend` (`store.py:370`) sums `billed_credits`. A refusal adds 0, so the
  circuit breaker keeps its meaning with no edit.
- The failover read (`main.py:1779`) filters `served_rank > 1`. A refusal
  carries no served rank, so it never reaches that read.
- 🔴 `last_seen_by_org` (`store.py:698`) counts nothing. It reads
  `MAX(u.created_at)` for each organization, and a refusal MUST move that
  timestamp. A customer at a wall is a customer who is trying. Filtering the
  refusal out here makes that customer read as SILENT to A3, which is the
  exact defect H-76 closed. An agent sweeping the file for a refusal filter
  must skip this read on purpose.

#### 🔴 The operator SEES the wall — added 2026-08-31

**A diff review found the signal stopped half way, and this closes it.** The
first build wrote `refusal_reason` and filtered five reads. Nothing then read
the column. So a walled customer LOST `silent` — their `last_seen` moved —
and gained nothing in its place. Hitting a wall made a customer harder to find
than saying nothing did, which is the opposite of A5.

| Piece | Where | What it does |
|---|---|---|
| `refusals` | `store.py:790` | `COUNT(u.id) FILTER (WHERE u.refusal_reason IS NOT NULL)`, over the same window as `calls` |
| `refusals` | `main.py`, `OrgUsageRow` | On the wire from `GET /admin/usage/orgs`. A plain int, because it is a count and not money |
| `isWalled` | `usage.ts` | Refusals above zero AND `calls` at zero — an organization that got NOTHING through |
| the `walled` chip | `usage.ts::orgFlags` | Renders immediately above `silent`, in `danger` tone |
| the Refused column | `UsageBoard.tsx` | Beside Calls, so "0 calls, 41 refused" reads as one sentence |

⚠️ **`walled` and `silent` are ONE signal handed between two flags.** A
refusal moves `last_seen`, so the two can never fire on one row. The narrow
test is deliberate. Refusals beside real traffic are a customer who meets a
limit now and then. Only "nothing got through" is a support call nobody has
made yet.

**Fences.** `test_customer_console_sql.py` proves the count and proves
`is_silent` stays false for a funded, walled organization.
`test_customer_console_pricing_truth.py` drives a real 400 and reads the count
off the board. `usage.test.ts` holds the chip, the tone and the handoff.

#### Done when — one clause per artefact. All seven are met

1. **The migration.** ✅ `020_usage_refusal.sql` adds `refusal_reason TEXT`
   NULL, plus `usage_event_refusal_reason_known`, the CHECK on the three
   slugs. Every column stays nullable (R6).
2. **The number.** ✅ The build listed `infra/customer_console/` and found
   `018` at the top. The merge re-checks it (R1).
3. **The route writes three, and only three.** ✅ The 400, the 402 and the 403
   each write one `usage_event` row. The two 503s and the 502 write none. The
   401 cannot.

   🔴 **The refusal write opens its OWN short transaction.** The 400 used to
   raise from INSIDE the serving transaction. A refusal row written on that
   connection rolls back with the raise. So the route now CARRIES the refusal
   out of the block. It holds the 400 in `unknown_tier`, closes the
   transaction, and only then calls `_record_refusal` and raises.

   `_record_refusal` (`main.py:4558`) opens its own `get_engine().begin()`.
   `_spend_refusal` (`main.py:972`) answers the same hazard the other way —
   it RETURNS the refusal instead of raising it — and its docstring states the
   rule.

   ⚠️ The two 503s and the 502 still raise from inside their transactions, on
   purpose. They write nothing, so nothing can roll back.

   🔴 **THE METER IS BEST EFFORT AND NEVER CHANGES THE ANSWER.** A failure
   inside `_record_refusal` is caught and logged as
   `router.refusal_metering_failed`, and the customer still receives the 400,
   the 402 or the 403. An unmetered refusal is a reporting gap. A refusal the
   customer never receives is an outage, and the outage is worse.
   `_record_completion` follows the same rule for the served path.

   Two smaller branches write nothing, and both are deliberate. The writer
   drops a slug outside `_REFUSAL_REASONS` and logs
   `router.refusal_slug_unknown`, so a typo never becomes an IntegrityError on
   the hottest path in the system. A refusal whose `detail` is a plain string
   carries no slug. We do not mint one from the status code, because that is
   the second spelling W3 forbids. Both shipped gate refusals build a dict, so
   nothing in the tree takes that branch today.
4. **The row shape.** ✅ A refusal row carries `billed_credits` 0, `quantity`
   0, the task's unit, the requested tier, `model` NULL and
   `provider_cost_usd` NULL. It mints its own `request_id`, because
   `001_customer_console.sql:271` is NOT NULL UNIQUE. It carries
   `caller.run_id`, because a ceiling refusal without its run is not
   actionable.
5. **The five reads exclude it.** ✅ Reads 1 and 2 take a WHERE clause. Reads
   3, 4 and 5 take a `FILTER` clause, so each LEFT JOIN survives.
6. **The three unchanged reads carry no diff.** ✅ `run_spend`, the failover
   read and `last_seen_by_org` are byte-identical.
7. **The tests.** ✅ One refusal row and one served row return `calls` 1 from
   every one of the five reads.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A refusal never counts as a call | `test_customer_console_sql.py::TestARefusalIsNotACall` — one refusal and one served call return calls = 1 from all five reads |
| The CHECK holds the vocabulary closed | `test_customer_console_sql.py` — a fourth slug raises `IntegrityError`, and the three shipped slugs pass |
| Our own failure writes no usage row | `test_customer_console_router.py` — `test_a_503_credential_failure_writes_NOTHING`, plus `TestFailureShapes::test_a_failed_provider_call_writes_no_usage_row` for the 502 |
| A refusal draws no credit | `test_customer_console_sql.py` — `run_spend` reads the same before and after a refusal row. `test_customer_console_router.py` — `credit_ledger` stays empty |
| 🔴 A refusal SURVIVES the raise | `test_customer_console_router.py::TestARefusalReachesTheMeter` — the test DRIVES the HTTP route. A request for an unknown tier returns 400, and `usage_event` then holds exactly one row with `refusal_reason` of `tier_unknown`. A hand-inserted row does not satisfy this fence |
| 🔴 The meter never changes the answer | `test_customer_console_router.py::TestTheThreeBranchesTheRefusalWriterCanTake` — three tests, one for each branch that writes nothing. A meter failure still answers 400. A fourth slug logs `router.refusal_slug_unknown` and never reaches the CHECK. A plain-string detail writes no row |
| A refusal keeps a customer visible | `test_customer_console_sql.py` — `last_seen_by_org` moves to the refusal's `created_at` |
| The failover read never sees one | `test_customer_console_pricing_truth.py::test_a_REFUSAL_is_NOT_reported_as_a_failover` |

⚠️ **Two fences moved suite on 2026-08-31, and the reason is that they had to.**
The 2026-08-30 draft put "a 503 and a 502 leave the table empty" in
`test_customer_console_sql.py`. Neither status exists in SQL, because only the
HTTP route produces one. A SQL test could assert it only by hand-inserting the
rows it must prove absent. Both fences now live in the router suite, which
drives the route.

**Verification.** The suites are database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_sql.py \
  tests/unit/test_customer_console_router.py \
  tests/unit/test_router_failover.py \
  tests/unit/test_customer_console_key_auth.py \
  tests/unit/test_migration_prefixes.py -q
```

The `FILTER` change touches `usage_by_org` and `usage_daily`, so the analytics
regression runs beside it:

```bash
uv run pytest tests/unit/test_operator_analytics.py \
  tests/unit/test_customer_console_pricing_truth.py -q
```

### 8.2 Margin and runway (slice 6) — BUILT

Merged as `b3ce3a9c` in **#163**. This section records what shipped, because
§2 and §5 both said it had not.

| Piece | Where | What it does |
|---|---|---|
| `margin_ratio` | `analytics.py:45` | Credits billed per dollar of provider cost. `None` on a cost of zero |
| `runway_days` | `analytics.py:59` | Whole days of credit left at the recent burn rate. `None` on no burn |
| `BURN_WINDOW_DAYS` | `analytics.py:42` | Seven days, so a Monday-to-Friday customer keeps a rate |
| `usage_by_org` | `store.py:721` | Calls, credits, members, cost and last seen, for each organization |
| `credit_balance_by_org` | `store.py:883` | The balance for each organization, summed from the ledger |
| `GET /admin/usage/orgs` | `main.py:5083` | The board. Money leaves as strings (`main.py:5131`) |
| `GET /admin/usage/daily` | `main.py:5148` | The series, for the platform or for one organization |
| `usage.ts`, `UsageBoard.tsx` | operator console | The page and its pure display logic |
| `test_operator_analytics.py` | tests | 24 tests. `usage.test.ts` fences the frontend |

**Done when** — every clause is met:

1. `GET /admin/usage/orgs` returns `marginRatio` and `runwayDays` for each
   organization.
2. A zero provider cost returns a null margin, never a good one (D-AI-8).
3. A zero burn returns a null runway, never a large number.
4. `test_operator_analytics.py` passes.

**Verification.** `uv run pytest tests/unit/test_operator_analytics.py -q`.
Frontend: `npx vitest run src/lib/usage.test.ts` in
`workbench/operator_console`.

### 8.3 `usage_event` records the step that served (slice 12) — BUILT

Merged as `537147b2` in **#168**. §3.6 said this column did not exist until
2026-08-30.

| Piece | Where | What it does |
|---|---|---|
| `served_rank`, `byok_served` | `013_pricing_truth.sql:26-40` | The two columns, plus the `served_rank >= 1` CHECK |
| `record_usage` | `store.py:487`, `store.py:522` | Persists both. NULL rank from a caller that does not say |
| The Router hand-off | `main.py:4635` | Passes the rank the Router walked, from `router.py:85` and `router.py:166` (`tier_binding.rank`) |
| The failover read | `main.py:1789-1804` | 14 days of `served_rank > 1`, one row per day, tier, task and model |
| `TierBoard` | operator console | Shows the failovers that happened |
| `test_customer_console_pricing_truth.py` | tests | `:201`, `:257`, `:298`, `:353` |

**Done when** — every clause is met:

1. A failover writes `served_rank` 2.
2. A first-choice answer writes `served_rank` 1.
3. A caller that says nothing writes NULL.
4. The pricing view returns the failovers, filtered on a rank above 1.

**Verification.** `uv run pytest tests/unit/test_customer_console_pricing_truth.py -q`.
The suite is database-gated (R8), so run `bash scripts/dev_db.sh` first.

### 8.4 The label seam (slice 3, C6) — BACKEND BUILDING · CLAUSE 6 SPECCED, 2026-08-31

**Where the slice stands, 2026-08-31.** An agent builds the backend half on a
sibling branch. That half is clauses 1 to 5 and clause 7. Clause 6 is the
Control Plane half, and a re-audit held it out on four gaps. The seven edits
dated 2026-08-31 below close those gaps, so clause 6 is now specced.

**The table is built. The Console half of the seam is not.** Every default
here is an **agent-proposed answer the owner may overrule**, which is the
D16/D17 convention CP-2b and CP-2c used. Where a name or a number below
disagrees with the tree, the tree wins. Re-verify every anchor at dispatch.

**Gate: AGENT-SAFE.** No owner act stands in front of this slice.

**The problem.** `tier_catalog` shipped in `015` with `label` and `blurb`, and
the customer app reads neither. `AgentChat.tsx:48-50` hard-codes three labels
in a fallback list, and they read `Tier 1 (fast / cheap)`,
`Tier 2 (balanced)` and `Tier 3 (powerful)`. D-AI-1 says the label is a
display name the operator owns. A hard-coded label is a second source, and it
already disagrees with `015:47-49`.

**The answer, in one line.** One column decides which tiers a customer may
see. One customer-authenticated read serves the visible rows with their words.

#### What this slice builds

| Piece | Where | What it does |
|---|---|---|
| `customer_visible` | migration `021` | `BOOLEAN NOT NULL DEFAULT TRUE`. §3.1 holds the shape |
| The six FALSE rows | migration `021` | §3.3 names all eleven tiers and their value |
| `GET /my/tiers` | Console `main.py` | The customer read. Beside `GET /my/usage/activity` (`main.py:5041`) |
| The catalog read | `main.py:1742-1748` | The operator read of the registry. It gains the column |
| `Tier` | `contract.ts:133-148` | The operator console type. It gains the field |
| The chat picker | `AgentChat.tsx:46-53` | `MODELS_FALLBACK`, the loading placeholder. Its three labels go |
| The live model list | `route.ts:79-81` and `route.ts:294` | The SECOND copy of the three labels. Its labels go, its ids stay |
| `CONSOLE_SLUG_BY_WIRE_ID` | `route.ts`, beside `LITELLM_MODELS` | The join table. It maps a wire id to a Console slug |
| `GET /api/tiers` | `src/app/api/tiers/route.ts` | The Control Plane proxy of the Console read |

*(The last two rows are edits of 2026-08-31.)*

#### The join table between the two id sets

*(Edits 1 to 7 are of 2026-08-31. Below them, a bare `route.ts` means the live
model list at `workbench/control_plane/src/app/api/models/all/route.ts`. Every
other route carries its full path.)*

The two id sets do not match, and clause 6 moves neither one. So one file
holds the map between them.

| Wire id (`route.ts`) | Console slug (`tier_catalog`) |
|---|---|
| `tier1-local-qwen3` | `tier-fast` |
| `tier2-sonnet` | `tier-balanced` |
| `tier3-opus` | `tier-powerful` |

**Its home is `workbench/control_plane/src/app/api/models/all/route.ts`,
beside `LITELLM_MODELS` (`route.ts:77-81`).** The map is a wire fact of that
file. That file already declares the three wire ids, so the map belongs with
the ids it names. `CONSOLE_SLUG_BY_WIRE_ID` is an agent-proposed name.

**The map carries ids only. It holds no label string.** A label enters the
file at request time, from the proxy route. A label inside the map would be a
third copy of the words D-AI-1 gives the operator.

#### The proxy route, and its client

**The route is `src/app/api/tiers/route.ts`.** It reads `GET /my/tiers` on the
Console and serves the rows to the browser. Shape it on
`src/app/api/billing/summary/route.ts:57-80`: one `fetch` with no store, a
status branch that relays no upstream body, and a `catch` that degrades.

**It writes no second Console client.** The route imports `consoleConfig` and
`consoleHeaders` from `src/app/api/billing/_console.ts`. CLAUDE.md §4 gives one
seam per concern, and `_console.ts` is the Console seam. A second client is a
defect, not a feature.

#### Which tiers the picker shows

**Rule.** `route.ts` serves a tier only when the join table names it. This is a
rule of the file, and it is not a task for later.

§3.3 marks five tiers `customer_visible` TRUE. The join table names three of
them. So `tier-code` and `tier-image` stay out of the chat picker. Each
one enters the picker on the day it gains a wire id, and the join table gains
that row in the same change. Nothing else in this slice moves for them.

#### What the picker shows when the Console does not answer

**Rule.** `route.ts` always serves the three tier aliases. When
`consoleConfig()` returns null, or when the Console does not answer, the route
serves them with the labels `Tier 1`, `Tier 2` and `Tier 3`.

These three short labels are deliberate. They are not the three strings fence 4
bans, so fence 4 holds through a Console outage. A picker that loses its words
is worse than a picker that shows a plain number.

`route.ts` already degrades in four places — `route.ts:154`, `route.ts:190`,
`route.ts:222` and `route.ts:349`. Each one wraps a `fetch` in `try`, sets a
timeout with `AbortSignal.timeout`, and keeps a default on failure. Follow that
pattern. Add no fifth pattern.

#### Done when — one clause per artefact

1. **The migration.** `021` adds `customer_visible BOOLEAN NOT NULL DEFAULT
   TRUE`, and sets FALSE on the six tiers §3.3 names. R6 binds it. Rename
   nothing. Drop nothing.
2. **The number.** An agent lists `infra/customer_console/` at build time. The
   merge re-checks it (R1).
3. **`GET /my/tiers` returns only `customer_visible` rows.** A row carries the
   `label` and the `blurb`, and never the slug alone.
4. **The read is customer-authenticated and tenant-safe.** It takes the
   organization from the API key, the same way `GET /my/usage/activity` does.
   It never takes a tenant from request input (R11).
5. **The read carries no model.** D66 binds it. No model id, no provider name
   and no rate card leaves this route.
6. **BOTH label sources read the route, and no wire id moves.** Two files
   hard-code the label of each tier today, and the slice must find both.
   `AgentChat.tsx:48-50` holds the loading placeholder `MODELS_FALLBACK`
   (`AgentChat.tsx:46-53`). The LIVE labels come from `route.ts:79-81`, and
   `route.ts:294` serves them as *"Tier routing aliases — always present"*.
   D-AI-1 owns the label, and `tier_catalog` holds the words.

   🔴 **This slice replaces the LABEL strings only.** The route serves the ids
   `tier1-local-qwen3`, `tier2-sonnet` and `tier3-opus`. The Console slugs are
   `tier-fast`, `tier-balanced` and `tier-powerful`. The two sets do not match,
   so a rename here changes what a picker saves. A saved raw model id then
   breaks on the `ROUTER_SERVING_ENABLED` flip, and **H-72** owns that hazard.
   Change no id on either side.

   **The two files do not do the same work.** *(Split on 2026-08-31. The one
   clause hid that only one file needs the join table.)*

   **6a. `AgentChat.tsx` needs NO join.** Its three ids at `AgentChat.tsx:48-50`
   read `tier-fast`, `tier-balanced` and `tier-powerful`. Those are already the
   Console slugs. The file keeps all three ids, and it replaces its three label
   strings with the words the proxy route serves. It does not read the join
   table.

   **6b. `route.ts` needs the join table.** Its three ids are wire ids, and the
   Console knows none of them. So the file reads `CONSOLE_SLUG_BY_WIRE_ID`,
   takes the slug, and puts the Console label on the wire id it keeps serving.
7. **The operator catalog read carries the column**, so an operator sees which
   tiers a customer can pick.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A hidden tier never reaches a customer | `test_customer_console_tier_pricing.py` — a `customer_visible` FALSE row is absent from `GET /my/tiers` |
| A tier read carries the label, never the slug alone | `test_customer_console_tier_pricing.py` — every row holds `label` and `blurb` |
| A customer tier read crosses no tenant | `test_customer_console_tier_pricing.py` — organization A reads no row of organization B |
| NEITHER file holds a hard-coded tier label, and the degrade says the short words | `npx vitest run` in `workbench/control_plane` — one test reads the source of `AgentChat.tsx` AND of `route.ts`, and fails while either one holds `Tier 1 (fast / cheap)`, `Tier 2 (balanced)` or `Tier 3 (powerful)`. **The same test calls `route.ts` with an unreachable Console, and reads back the labels `Tier 1`, `Tier 2` and `Tier 3`** *(second half added 2026-08-31, so the degrade and the ban cannot drift apart)* |
| A picker tier holds a wire id | `npx vitest run` in `workbench/control_plane` — `route.ts` serves no tier that `CONSOLE_SLUG_BY_WIRE_ID` does not name *(added 2026-08-31)* |
| The three wire ids do not move | `npx vitest run` in `workbench/control_plane` — `route.ts` still serves `tier1-local-qwen3`, `tier2-sonnet` and `tier3-opus` |

**Verification.** The suite is database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_tier_pricing.py -q
```

Frontend: `npx vitest run` in `workbench/control_plane`.

### 8.5 The Router image rule (slice 4, §3.2) — BUILT, 2026-08-31 · clauses 7 and 8 BUILT, 2026-08-31 · one availability gap DISCLOSED and OPEN (H-81)

**This shipped on 2026-08-31.** The resolver is `router.resolve_vision_chain`,
the route reads it through `main._resolve_serving_chain`, and the meter now
records the task the CUSTOMER declared. The section is the build record rather
than a proposal. Every anchor below was re-measured on the branch that built
it.

✅ **A follow-up closed the mixed-chain gap on the same day.** The resolver now
filters the lift chain to the steps that set `reads_images`. §3.2 step 3b holds
the rule under a D16 marker, and clauses 7 and 8 below hold the done-when.
Every anchor in this section and in §8.6 was re-measured then.

⚠️ **That follow-up also opened ONE availability gap.** Clause 8 states it in
full. A blind rank 1 in front of a seeing rank 2 the service holds no key for
now answers 503. It answered a correct 200 before. **H-81** holds the decision,
and the shape is latent until an operator arms the lift.

**Gate: AGENT-SAFE.** The serving flip stays the owner's act (H-69), and the
build in front of it is agent work.

⚠️ **What a customer sees today, stated exactly.** Three clauses hold it.

*(Corrected 2026-08-31. The first draft said the slice "changes no behaviour a
customer can see until an operator acts". A verifier showed that the change
lands AT the operator's first act.)*

1. **A tier that binds the declared task is UNCHANGED, now and after any
   operator act.** §3.2 step 0.5 serves it directly, so a caller who names a
   bound `tier-vision` gets the 200 they always got.
2. **A tier that binds no `vision` model answers the 400 of clause 3 today.**
   Nothing populates `model_profile.reads_images` (§3.7 rule 4 seeds no row at
   all), and nothing binds `tier-vision` (F3). So the flag reads FALSE, the
   call falls, and the fall finds nothing. That 400 is what the route answered
   before this slice as well, with a different sentence.
3. **The LIFT is the part that waits on an operator, and it takes TWO acts.**
   *(Corrected 2026-08-31. This named the vendor feed alone, and the feed is
   only half of it.)* `feed.sync` writes `vendor_price_feed` and writes
   nothing to `model_profile`. The ONE writer of `model_profile.reads_images`
   is `POST /catalog/profiles` (`main.py:2433`), which the console reaches per
   MODEL through the declare click (`declareBodies`,
   `workbench/operator_console/src/lib/feed.ts:95-110`).

   So arming the lift takes a feed sync AND a per-model profile save. Arming
   the FALL is a third act, separate from both: bind `tier-vision`. None of
   the three is ours (H-69).

**The problem this closed.** `POST /v1/chat/completions` resolved one chain for
the task the caller declared. A `vision` task therefore reached `tier-vision`
or it reached a 400. Nothing read `model_profile.reads_images`
(`012_model_profile.sql:60`), so a chat model that already reads images was
never used, and every image call cost a second call.

**The answer, in one line.** Serve the tier's own `vision` binding when it has
one. Otherwise read the flag, use the chat model when it is TRUE, and fall to
the `tier-vision` chain when it is FALSE.

**No migration.** `reads_images` shipped in `012`. `tier_rate_card` shipped in
`015`. This slice adds a read, and it adds no column.

#### Done when — one clause per artefact

0. **A tier that binds the declared task serves it.** §3.2 step 0.5. A
   `task: vision` call on a tier that binds `vision` reaches that binding.
   The image rule does not run. Two fences hold it. One drives a bound
   `tier-vision`, and one drives a second vision tier whose slug the Router
   has never heard of.
1. **One model on a TRUE flag.** Take a tier whose chat model sets
   `reads_images`. A `task: vision` call on it calls exactly one model. That
   model is the tier's own chat binding. **The test writes its own
   `model_profile` row.** Nothing seeds that table, so a fence that leaned on
   the ladder would measure an empty one.
2. **The `tier-vision` chain when NO step of the chat chain reads an image.**
   *(Reworded 2026-08-31. It read "on a FALSE flag", and clause 7 made that
   sentence conditional.)* Take a tier whose chat chain clears the flag on
   every step. The same call resolves `tier-vision` for the `vision` task. It
   then walks that chain (`resolve_chain`, `router.py:152-193`).
   **The test writes its own `tier_binding` rows** for both halves, and it
   removes the `tier-vision` one afterwards. F3 measured that tier as unbound,
   and clause 3's fence needs it to stay unbound.

   ⚠️ **Name the condition, because a rank-1 FALSE flag alone no longer
   decides.** One seeing step anywhere in the chain holds the lift, and the
   call does not fall. That step holds the lift even when the service holds no
   credential for it, which is the shape clause 8 discloses.
3. **400 and no completion on an unbound `tier-vision`.** The route returns
   HTTP 400 with the detail §3.2 step 4 names. It calls no provider, and it
   writes no completion.

   🔴 **The refusal row says `tier_unknown`, and the HTTP detail says the
   vision sentence.** These are two different things. `_REFUSAL_REASONS`
   (`main.py:4827-4831`) and `020_usage_refusal.sql`'s CHECK both close the
   vocabulary at three slugs. A fourth slug needs a migration, and it is a
   second spelling of one wall. The row is true as it stands, because
   nothing binds `tier-vision`. It names `tier-vision` as the tier and
   `vision` as the task, because the missing binding is the thing an operator
   has to go and make. Its unit is `tokens`, which `_task_unit` reads from
   `task_catalog` (`010_tasks_units_capabilities.sql:46`).
4. **The bills follow the pair, and the ROW follows the customer.** Step 1
   above bills (chosen tier, `chat`). Step 2 bills (`tier-vision`, `vision`).
   Both read `tier_rate_card` through `resolve_tier_rate` (`router.py:367`).

   🔴 **`usage_event.task` reads `vision` on BOTH served paths.** The customer
   asked for vision, so analytics must answer with vision — otherwise the lift
   files a customer's image work under `chat` and §1.3 undercounts it.
   `_record_completion` takes a `declared_task` argument for exactly this
   split, and `served_rank` still records the step that answered.
5. **The Router still reads no payload.** The caller declares the task
   (`CompletionRequest.task`, `main.py:834-852`). Nothing added by this slice
   looks inside `messages`. `resolve_vision_chain` takes a connection and a
   tier slug, and it takes nothing else.
6. **The tier does not drop.** §6A.9 rule 1 forbids a degradation across
   tasks, and step 2 is a lift rather than a degradation. §3.2 records the
   reconciliation.
7. **A blind step never enters a lift chain** *(§3.2 step 3b, D16)*. Take a
   tier whose rank-1 chat model sets `reads_images` and whose rank-2 model does
   not. A `task: vision` call resolves a chain of ONE step. Rank 1 then fails
   with a retryable status, and the route calls no second model. The walk ends
   there, and the caller gets the 502 an exhausted chain has always given.

   📌 **The filter reads the whole chain, and not the head of it.** A rank-2
   step that SETS the flag stays in the lift chain. So a tier whose rank 1 is
   blind lifts on rank 2, and it does not fall. A chain where no step sets the
   flag falls to `tier-vision`.

   🔴 **The fall is a RESOLUTION act, and never a failover act.**
   `resolve_vision_chain` picks the chain before the walk starts. So a step
   that fails at RUNTIME falls to the next seeing step and to nothing else.
   Splicing the `tier-vision` chain onto the tail would bill two pairs out of
   one walk, and §3.2 records no decision on that.
8. **An unkeyed rank 1 does not promote a blind rank 2.** Same chain, and the
   service holds no credential for the rank-1 vendor. The credential filter
   (`main.py:5221-5224`) then empties the chain, and the route answers the 503
   it already gives an unconfigured vendor. It reaches no provider, and it
   never reaches the blind model.

   ⚠️ **This shape needed no failover at all, which was the wider half.** The
   route drops every step it holds no key for before it tries anything. So one
   missing credential gave the wrong answer, with nothing having failed.

   🔴 **The 503 does NOT fall to `tier-vision`, and that is deliberate.** A
   credential-aware fall is a THIRD resolution rule, and it would make the
   resolver read `provider_credential`. §3.2 records no decision on it, so an
   agent may not mint one (CLAUDE.md §5).

   ⚠️ **DISCLOSED: one shape LOSES a CORRECT 200** *(a verifier drove both
   sides on 2026-08-31)*. The paragraph above does not cover it. The shape is a
   chat chain of a BLIND rank 1 and a SEEING rank 2 that the service holds no
   key for. `tier-vision` is bound and healthy.

   **Old outcome:** the rank-1 read found FALSE, fell to `tier-vision`, and
   answered 200 from a model that saw the image. **New outcome:** the filter
   keeps the seeing rank 2, the credential filter empties the chain, and the
   route answers 503. `tier-vision` is never reached.

   🔴 **The loss is AVAILABILITY, and never correctness.** No blind model
   answers in EITHER version. So this shape trades a right answer for a
   refusal, where the rest of clause 8 trades a WRONG answer for one. Both are
   worth naming, and they are not the same trade.

   📌 **LATENT today, and three operator acts away from live.** Nothing binds
   `tier-vision` (F3) and nothing writes `model_profile.reads_images` (§3.7
   rule 4), so no live box can build this shape. **`HANDOFF.md` H-81** carries
   it, holds the two candidate closes, and gives the owner the deadline
   *decide before H-69 arms the lift*.

🔴 **A KNOWN WRONG ANSWER RODE ON THE LIFT, AND CLAUSES 7 AND 8 CLOSE IT** *(a
verifier drove it on 2026-08-31)*. The flag was read on the RANK-1 step of the
chat chain, because §3.2 step 1 said *the model bound to the chosen tier*.
Nothing then checked the steps behind it.

**The outcome, stated as it happened.** Rank 1 read images. Rank 1 failed, so
the chain failed over. Rank 2 was blind. The blind model answered about text it
could not see. The customer got a confident 200, and the meter filed the turn
as `vision`.

**This is the exact harm §3.2 cites when it refuses to answer 200 at the image
wall.** A silent drop of the image makes the answer look correct.

**The fix, and it is a SECOND resolution rule.** `resolve_vision_chain` filters
the lift chain to the steps that set `reads_images`. An empty result falls to
`tier-vision`, exactly as a chain of one FALSE step does. §3.2 step 3b records
the rule under a D16 marker, so the owner may overrule it.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A tier that binds the declared task serves it directly | `test_customer_console_router.py` — a `task: vision` call on a bound `tier-vision` is a 200, and a second vision tier with an unheard-of slug serves itself too |
| A chat model that reads images serves the image itself | `test_customer_console_router.py` — `TestTheRouterImageRule`, a TRUE flag calls exactly one model |
| A chat chain where NO step reads an image falls to `tier-vision` | `test_customer_console_router.py` — a FALSE flag, and an ABSENT profile row, both call the `tier-vision` chain, and a chain of two FALSE steps does the same. A chain that keeps ONE seeing step does not fall, and clause 8 holds what that costs when the service holds no key for the step |
| An image refusal names the reason | `test_customer_console_router.py` — a missing `tier-vision` returns 400, calls no provider, and writes no completion |
| An image refusal reaches the meter as `tier_unknown` | `test_customer_console_router.py` — one row, `tier-vision` / `vision` / `tokens`, and `_REFUSAL_REASONS` still holds three |
| The bill follows the pair that SERVED | `test_customer_console_router.py` — the lift bills the chosen tier's `chat` card, and the fall bills nothing from it |
| The row says what the CUSTOMER asked for | `test_customer_console_router.py` — the lift writes `task = vision` on a call served by the `chat` binding |
| The Router never reads the payload | `test_customer_console_router.py` — an image in `messages` with `task: chat` stays on the chat binding, with `tier-vision` bound and available |
| A stream takes the same two paths | `test_customer_console_router.py` — a streamed `task: vision` call lifts, falls, and walls exactly as a buffered one does |
| A blind step never enters a lift chain | `test_customer_console_router.py` — a blind rank 2 leaves the chain, a SEEING rank 2 stays in it, and a rank-1 failure calls no second model |
| An unkeyed rank 1 never promotes a blind rank 2 | `test_customer_console_router.py` — the route answers 503, and the blind model is never called |

**Verification.** The suite is database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_router.py -q
```

### 8.6 A stream fails over before its first frame (slice 11, §3.6) — BUILT, 2026-08-31

**Gate: AGENT-SAFE.** The serving flip stays the owner's act (H-69).

**The problem this closed.** A streamed request took step 1 of the chain and
stopped there. `_streamed_completion` caught the open failure, logged
`router.stream_open_failed`, and sent the `[DONE]` sentinel. So a provider that
was down cost the customer their request, and the chain the operator configured
did nothing.

**The answer, in one line.** Pull the first chunk before the 200 status line
goes out, and walk the chain until then.

**No migration.** `served_rank` shipped in `013`.

#### The mechanism — where the walk lives, and why it cannot live elsewhere

1. **The route walks.** `open_stream_chain` (`router.py:1025`) opens each step
   and pulls ONE chunk from it. The route calls this at `main.py:5332`, in
   place of the old `resolved = attempts[0]`.
2. **The generator replays.** `_streamed_completion` (`main.py:5084`) yields
   the chunk the route already holds, then relays the rest of the same open
   stream. `relay_stream` sees one unbroken source, so byte-identity, the
   single usage row and the `[DONE]` sentinel all keep working unchanged.

⚠️ **The walk CANNOT move into the body generator.** Starlette sends the
`http.response.start` message before it pulls the first item. So the 200
status line has gone out by the time a body iterator runs. The code has said
so since CP-4b. A generator has no failover left to express.

⚠️ **The route runs the walk on the SERVING loop, through
`anyio.from_thread.run`** (`_open_stream_chain`, `main.py:5039`). The route is
`def`, so FastAPI runs it in an anyio worker thread. `asyncio.run` would build
a private loop, and closing that loop calls `shutdown_asyncgens()` — which
throws `GeneratorExit` into the stream just opened. Measured 2026-08-31 on a
three-frame source: the client received frame one and nothing else.

⚠️ **An OUTPUT frame never crosses from one try to the next.** Each retry opens
a fresh provider stream. Only the first chunk of the step that SUCCEEDED
reaches the client, and it reaches it exactly once.

⚠️ **A stream that opens and yields NOTHING is an ANSWER.** Zero chunks give
an empty `head`, and the walk stops there. The provider completed with no
content, so it served the request. Paying a second vendor to repeat it would
bill twice for one empty answer. `relay_stream` never sets `started`, so the
meter writes no row either. Fence:
`test_an_EMPTY_stream_is_an_answer_and_not_a_failure`.

⚠️ **The Router CLOSES every provider stream, at both ends of the walk.**
`router.aclose_quietly` (`router.py:1000`) is the one close. The walk closes a
LOSER before it moves on, because the open already succeeded and the socket is
ours. The route closes the WINNER in `_streamed_completion`'s `finally`,
because Starlette 1.1.0 never calls `aclose` on a body iterator. Both fences
are mutation-proved.

#### The threadpool hazard — recorded, NOT fixed (H-80)

🔴 **The stream open now holds one of 40 shared threadpool tokens for up to
`3 × 120` seconds.** The route is `def`, so FastAPI runs it through
`anyio.to_thread.run_sync` on the DEFAULT `CapacityLimiter`, which holds 40
tokens. Before this slice the route returned as soon as it built the response.
It now blocks until the walk finds a chunk.

🔴 **litellm borrows from the SAME limiter.** `asyncify` (`asyncify.py:57`)
calls `anyio.to_thread.run_sync` with `limiter=None`, so it takes the default
one. Seven call sites do this. Two are on serving paths: Vertex AI's token
fetch (`vertex_llm_base.py:718`) and SageMaker's request preparation
(`sagemaker/completion/handler.py:428` and `:499`).

⚠️ **So 40 concurrent stream walks plus one asyncify-bound model is a
deadlock that does not recover.** All 40 tokens sit in stream walks. Each walk
waits on a provider call that needs a 41st token to make progress. Nothing
releases.

**Latent today, and one row from being live.** Every bound model reaches
httpx, which never borrows a thread. A vendor swap is one `tier_binding` row,
and that row is an operator act with no code review. **H-80** holds the fix
shape: a dedicated `CapacityLimiter` for stream walks, or a bounded semaphore
in front of them. This slice does not build it.

#### The boundary — §3.6 states it, and this section builds it

Every failure before the first chunk may fail over. Every failure after it may
not. The stream path reads `TERMINAL_STATUSES` (`router.py:890`) and
`CREDENTIAL_STATUSES` (`router.py:894`) through `walk_chain`
(`router.py:928`), the one function `call_chain` (`router.py:978`) also walks
through. It adds no second policy.

⚠️ **`MAX_CHAIN_ATTEMPTS` (`router.py:882`) does NOT come with it.** The cap
lives in the route's list slice, `attempts[:router_mod.MAX_CHAIN_ATTEMPTS]`
(`main.py:5224`), which the stream branch reuses because it walks the SAME
`attempts` the buffered branch built. §3.6 records the trap for a future third
caller.

*(The anchors above read `router.py:559-608` until 2026-08-30, and
`router.py:617-663` until 2026-08-31. The first range is `relay_stream`.)*

#### Done when — five clauses

1. **A retryable failure before any chunk fails over.** A 529 on step 1 serves
   step 2, and the client sees one clean stream.
2. **A terminal failure stops the walk.** A 400 on step 1 calls no step 2.
3. **The usage row records the step that answered.** It carries that step's
   `served_rank`, the same way `_record_completion` (`main.py:4635`) does for
   a non-streamed call. The route passes the answering step as `resolved`.
4. **A chain that fails at every step writes NO usage row.** This preserves
   `test_customer_console_router.py:750`
   (`test_a_stream_that_never_starts_writes_no_usage_row`), the phantom-row
   fence.
5. **An exhausted chain still answers 200 with `data: [DONE]`.**
   `_stream_closed` (`main.py:5064`) sends the sentinel and nothing else. That
   keeps the body assertion at `test_customer_console_router.py:764` true.

⚠️ **Clause 5's 200 is a CHOICE now, and it was a constraint before.** While
the open lived inside the body generator, the status line had gone out and a
502 could not be expressed. This slice moved the walk into the route, so that
502 became reachable. It stays a 200, because what a streaming caller is
answered WITH is a response-shape decision and not a failover one. **No
decision records the alternative.** A later slice may prefer a 502 for an
exhausted streamed chain, and it would edit the fence at
`test_customer_console_router.py:764`.

⚠️ **`_REFUSAL_REASONS` (`main.py:4827`) stays CLOSED.** An exhausted chain
writes no refusal row and mints no new reason slug. The three reasons in §8.1
are customer walls — no credits, the run ceiling, an unknown tier. An upstream
outage is our supplier failing, and not the customer meeting a limit. A refusal
row would put a vendor's bad night into the customer's own record of the walls
they hit.

#### The repairs this slice carried

1. **The `_note_failover` comment.** It read that `usage_event` has *"no
   column for the step that served"*. Slice 12 built `served_rank`
   (`013_pricing_truth.sql:26-40`), so the sentence was false. It now states
   the split: the row carries the step, the log line carries the reason.
2. **The stream-branch comment.** It read *"A STREAM DOES NOT FAIL OVER"* and
   named the change as a separate slice. This was that slice, so the comment
   now states the boundary.
3. **One `_note_failover` for the chat route.** The route declares it above
   the stream branch and both branches use it. The transcribe route keeps its
   own, because it names its own task.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A stream fails over before its first frame | `test_customer_console_router.py` — `test_a_529_before_any_frame_serves_the_backup_as_ONE_clean_stream`, on exact bytes |
| The walk pulls a chunk, and does not only open | `test_router_failover.py` — `test_a_stream_that_OPENS_and_then_dies_still_fails_over` |
| A stream never fails over after its first frame | `test_customer_console_router.py` — `test_a_failure_AFTER_the_first_frame_does_NOT_fail_over` calls no second model |
| A bad request stops a streamed walk | `test_customer_console_router.py` — a 400 on step 1 calls no step 2 |
| The row records the step that answered | `test_customer_console_router.py` — a streamed rank-2 walk writes `served_rank` = 2 |
| A stream that never starts writes no usage row | `test_customer_console_router.py:750` — the phantom-row fence, unchanged |
| The head is replayed once, and once only | `test_router_failover.py` — `test_the_head_leaves_the_source_at_the_SECOND_chunk` |
| An empty stream is an answer, not a failure | `test_router_failover.py` — `test_an_EMPTY_stream_is_an_answer_and_not_a_failure` |
| The walk closes the LOSER's stream | `test_router_failover.py::TestTheLoserStreamIsCLOSED` — a step that opens then 529s has `aclose` called, and the winner does not |
| The route closes the WINNER's stream | `test_customer_console_router.py::TestTheWinningStreamIsCLOSED` — read to the end, abandoned, or died mid-relay, each closes once |

**Two observations this slice records.**

1. **A client can disconnect while the walk is still running.** The route is
   `def`, so nothing cancels it when the client goes away. The walk can open a
   provider stream that nobody reads. Three things bound that, and they bound
   different halves. `MAX_CHAIN_ATTEMPTS` and the 120-second timeout bound the
   WALK — how long it runs and how many steps it tries. Neither of them
   touches an abandoned WINNER. `_streamed_completion`'s `finally` bounds
   that one, and it closes the source on every exit. `relay_stream` never
   starts, so no row is written.

   ⚠️ **One window stays open.** The client can vanish between the route
   returning and Starlette's `http.response.start`. The body generator then
   never starts, so its `finally` never runs and nothing closes the winner.
   Starlette 1.1.0 calls `aclose` on a body iterator nowhere, so no code in
   this repository reaches that case.
2. **A keepalive comment is never the first chunk.** litellm parses the
   provider's SSE and yields objects. An SSE comment line stays inside that
   parser. A provider that keeps a slow stream alive that way still makes
   `open_stream_chain` wait for a real chunk.

**Verification.** `test_customer_console_router.py` is database-gated (R8), so
start the database first. `test_router_failover.py` needs no database and runs
anywhere.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_router_failover.py \
  tests/unit/test_customer_console_router.py -q
```

---

## 9. Fences

**R7 binds every slice.** Each rule below names the test that fails when an
agent breaks it.

| Rule | Fence |
|---|---|
| An operator read crosses tenants. A customer read never does | `test_customer_console_sql.py` — a customer route must reject a cross-org read |
| A daily series fills every gap | `test_customer_console_sql.py` — a window with one event returns the full day count |
| Money leaves the API as a string | `test_customer_console_key_auth.py` — no float in a spend body |
| A tier slug never changes | `test_index_completeness.py` sibling — the slug set is append-only |
| An image refusal names the reason | `test_customer_console_router.py` — a missing `tier-vision` returns 400, never a text-only answer |
| A tier that binds the declared task serves it | `test_customer_console_router.py` — a `task: vision` call on a bound `tier-vision` is a 200, and the image rule does not run |
| A chat model that reads images costs ONE call | `test_customer_console_router.py` — a `task: vision` call on a TRUE flag calls exactly one model |
| The meter says what the CUSTOMER asked for | `test_customer_console_router.py` — a lifted vision call writes `task = vision` and bills the `chat` pair |
| Every step of a chain shares one date | `test_customer_console_fallback_chain.py` — two dates resolve as two chains, and the first choice disappears |
| A shorter chain leaves no orphan step | `test_customer_console_fallback_chain.py` — a three-step chain replaced by two resolves to two |
| The Console saves the chain whole | `catalog.test.ts` — the page posts `models`, never `model` |
| A bad request is not retried | `test_router_failover.py` — a 400 stops the walk and never calls the backup |
| A bad key strikes off its vendor | `test_router_failover.py` — a 401 skips every model from that vendor |
| The customer pays for what ANSWERED | `test_router_failover.py` — the walk returns the step that replied |
| An unknown measurement is never zero | `test_customer_console_model_profile.py` — the database refuses a window of 0 |
| A capability flag is never assumed | `read.test.ts` — a profile that says false yields no kind |
| A per-unit vendor cost parses in the vendor's unit and declares in the task's unit | `test_customer_console_vendor_feed.py::test_the_feed_read_serves_transcription_PER_MINUTE` — a per-second `0.0001` in the feed reads as `0.006` per minute on the wire, Decimal-exact. Its partner `test_only_the_feed_read_multiplies_a_PRICE_by_sixty` names every legal ×60 site. Built 2026-08-31 with `customer_console.md` §6A.11a clauses 5 to 7 |
| The profile write adds no arithmetic of its own | `test_customer_console_model_profile.py::test_the_per_unit_prices_read_back_BYTE_IDENTICAL` — a posted per-minute price reads back unchanged |
| A refusal never counts as a call | `test_customer_console_sql.py` — one refusal and one served call return calls = 1 |
| An unmeasured cost never reads as a good margin | `test_operator_analytics.py::TestMarginRatio` |
| An operator usage read is Operator-gated | `test_operator_roles.py` |
| A failover records the step that answered | `test_customer_console_pricing_truth.py` — a rank-2 walk writes `served_rank` = 2 |
| A stream fails over before its first frame | `test_customer_console_router.py` — a 529 on step 1 serves step 2 as one clean stream, on exact bytes |
| A streamed walk pulls a chunk, and does not only open | `test_router_failover.py` — a step that opens and then dies still falls over |
| A stream never fails over after its first frame | `test_customer_console_router.py` — a mid-stream failure calls no second model |
| A hidden tier never reaches a customer | `test_customer_console_tier_pricing.py` — a `customer_visible` FALSE row is absent from `GET /my/tiers` |

⚠️ **R8 binds every read here.** Verify the SQL against a real database. The
two reads in slice 1 were verified against the live Console database on
2026-08-29, and the gap fill was the defect that check found. Slice 9 ran
against a throwaway Postgres on 2026-08-30, and `now()` being stable inside one
transaction was the defect that check found.
