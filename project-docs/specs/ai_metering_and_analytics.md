# AI metering and analytics

**Status: ACTIVE.** Owner directive, 2026-08-29. This specification owns the AI
tier vocabulary that a customer sees, and every surface that reports AI use.

**Slice state, re-measured 2026-08-30.** Slices 1, 2, 6, 10, 12 and 13 are
**BUILT**. Slice 6 shipped as `b3ce3a9c` (#163) and slice 12 as `537147b2`
(#168). §8's table carries a Gate column, so a dispatcher reads AGENT-SAFE or
OWNER-GATE per row.

**Four slices are SPEC ONLY, and each one now holds a contract.** Slice 5 is
§8.1, slice 3 is §8.4, slice 4 is §8.5 and slice 11 is §8.6. All four are
AGENT-SAFE. Each contract names its done-when clauses, its fences (R7) and its
verification command.

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
| **F7** | **The operator can grant credits AND read use.** *(Rewritten 2026-08-30 — slice 6 shipped, and this row said the opposite.)* `usage_by_org` (`store.py:700`) feeds `GET /admin/usage/orgs` (`main.py:4952`), beside `POST /credits/grant` and `GET /credits/balance` | `store.py`, `main.py` |
| **F8** | **The customer can read use by app and by member.** `GET /my/usage/activity` and `GET /my/usage/members` shipped in CP-7 | `main.py` |
| **F9** | **The operator time series exists. The customer one does not.** *(Rewritten 2026-08-30.)* `GET /admin/usage/daily` (`main.py:5017`) serves the operator, for the platform or for one organization. C3 has a store function and no route | `main.py` |
| **F10** | **No per-member budget table exists.** H-73 records why: the member identity arrives in a header the member controls | `information_schema`, HANDOFF |
| **F11** | **`usage_event` holds 0 rows.** Every surface below ships to an empty table | live query |
| **F12** | 🔴 **F1's column list is INCOMPLETE, and an agent must not read it as the table.** Four later migrations added columns F1 never names: `run_id` (`003`), `client_ref` (`005`), `served_rank` and `byok_served` (`013`). `001` itself also holds `id`, `request_id` and the three token counters. `010` added the `task`, `quantity` and `unit` that F1 does name. **Read `information_schema`, never this row** | `infra/customer_console/001`, `003`, `005`, `010`, `013` |

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
the caller, and `CompletionRequest.task` (`main.py:834-843`) carries it today.
The Router still picks WHICH MODEL answers, and the four steps below are that
choice.)*

**On a call that declares `task: vision`, the Router does this:**

1. Read the model bound to the chosen tier for the `chat` task.
2. If `model_profile.reads_images` is TRUE for that model, send the image to
   it. Bill the (chosen tier, `chat`) pair.
3. If the flag is FALSE, resolve `tier-vision` for the `vision` task, and send
   the image there. Bill the (`tier-vision`, `vision`) pair.
4. If nothing binds `tier-vision`, refuse with HTTP 400 and name the reason.

⚠️ **ONE source for the flag, and it is `model_profile.reads_images`
(`012_model_profile.sql`).** The vendor feed ships the flag populated, so an
operator types nothing (§6A.11). `model_capability` holds no `vision` row for
any model today (F4), so a capability read answers nothing. Two sources for
one fact is how the two start to disagree.

⚠️ **Step 2 is the money.** A second call to a vision model costs a second
call. A chat model that already reads images costs one.

⚠️ **Both bills read the (tier, task) pair, never a model rate card.** Step 2
bills (chosen tier, `chat`). Step 3 bills (`tier-vision`, `vision`). Both go
through `resolve_tier_rate` in `router.py`. D67 moved the customer price onto
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

An operator reads it and knows which half to fix. The wording follows the
shape `main.py:4571` already uses for an unbound tier. A
silent drop of the image makes the model answer about text it cannot see, and
the answer looks correct.

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

⚠️ **A stream does not fail over TODAY, and slice 11 moves the line.** After
the first frame reaches the client, the request is half answered. A retry
would join two different completions into one response, which is worse than
the error. Failover *before* the first frame is legal, and §8.6 holds its
contract.

**The boundary, stated once (agent default, 2026-08-30).** The Router opens
the provider stream inside `_streamed_completion` (`main.py:4494-4536`) and
awaits the FIRST FRAME before Starlette sends the 200 status line. Every
failure up to that point may fail over. Every failure after it may not.

**The stream path reuses `call_chain`'s policy and adds none of its own.**
`MAX_CHAIN_ATTEMPTS` (`router.py:617`), `TERMINAL_STATUSES` (`router.py:625`)
and `CREDENTIAL_STATUSES` (`router.py:629`) bind the walk before the first
frame exactly as they bind `call_chain` (`router.py:663`). A second failover
policy beside the first is the CLAUDE.md §5 defect, not a feature.

*(The three anchors above read `router.py:559-608` until 2026-08-30. That
range is `relay_stream`, which is a different function.)*

⚠️ **`main.py:4715` carries a stale comment, and slice 11 repairs it.** The
line reads that `usage_event` has *"no column for the step that served"*.
Slice 12 built `served_rank` in `013_pricing_truth.sql:26-40`, so the comment
is now false. This is a finding for slice 11 to fix in the code it already
touches. It is not a separate ticket.

⚠️ **The chain tries at most `MAX_CHAIN_ATTEMPTS` steps.** An unbounded chain
is an unbounded bill and an unbounded wait.

✅ **`usage_event` records the step that ANSWERED.** *(Rewritten 2026-08-30.
This paragraph said the column did not exist. Slice 12 built it — see §8.3.)*

`served_rank` (migration `013`) holds the position of the step that served.
Rank 1 is the first choice. A rank above 1 is a failover. NULL predates the
column, or comes from a caller that does not say. The Operator Console reads
`served_rank > 1` over 14 days (`main.py:1775-1786`).

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

⚠️ **Three per-unit vendor costs join this table under H-78, and nobody has
built them yet.** `model_profile` gains `vendor_per_minute_usd`,
`vendor_per_character_usd` and `vendor_per_image_usd`, each in the task's
natural unit. `customer_console.md` §6A.11a owns the columns, the parse rules
and the ×60 conversion. This section does not repeat them.

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
| O2 | **Use by organization** | **Built** — `GET /admin/usage/orgs` (`main.py:4952`) over `usage_by_org` (`store.py:700`) | — |
| O3 | **Use over time** | **Built** — `GET /admin/usage/daily` (`main.py:5017`) over `usage_daily` (`store.py:790`) | — |
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

⚠️ **A5 needs a new column.** `usage_event` records a call that happened. A
refusal writes no row. §8.1 holds the contract for slice 5.

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
| **4** | Router image rule — **§8.5 holds the contract** | §3.2 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **5** | Record a refusal in `usage_event` — **§8.1 holds the contract** | A5 | AGENT-SAFE |
| **6** | Margin and runway ✅ `b3ce3a9c` (#163) — **§8.2** | A1, A2 | AGENT-SAFE · a priced margin waits on H-42 |
| **7** | Customer time series | C3 | AGENT-SAFE |
| **8** | Per-member budget | C5 | 🔴 **OWNER-GATE** — blocked on H-73. Do not build it first and secure it later |
| **9** | `tier_binding.rank`, and the Console reads and writes a chain | §3.5 | AGENT-SAFE |
| **10** | The Router walks the chain when a step fails ✅ | §3.5, §3.6 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **11** | A stream fails over before its first frame — **§8.6 holds the contract** | §3.6 | AGENT-SAFE · the serving flip is the owner's (H-69) |
| **12** | `usage_event` records the step that served ✅ `537147b2` (#168) — **§8.3** | §3.6 | AGENT-SAFE |
| **13** | `model_profile` — what a model IS ✅ | §3.7 | AGENT-SAFE |

⚠️ **Row 12 delivered §3.6 and NOT A5.** The cell named A5 until 2026-08-30,
and that was wrong: `served_rank` records a call that answered, and a refusal
answers nothing. A5 belongs to slice 5 alone.

### 8.1 Record a refusal in `usage_event` (slice 5, A5) — SPEC ONLY, 2026-08-30

**Nothing below is built.** Every default here is an **agent-proposed answer
the owner may overrule**, which is the D16/D17 convention CP-2b and CP-2c used.
Where a name or a number below disagrees with the tree, the tree wins.
Re-verify every anchor at dispatch.

**The problem.** `usage_event` records a call that happened. A refusal writes
no row. So A5 cannot answer "is a customer hitting a wall", and six refusal
shapes in the Router route leave no trace in the meter.

**The answer, in one line.** One nullable column names why we refused. NULL
means the call served. Every read that counts calls excludes the refusals.

#### The column — migration `020`

⚠️ **Take the migration number at build time, and again at merge (R1).** The
highest number on disk on 2026-08-30 is `018_credit_ref_unique.sql`.
`customer_console.md` §6A.11a claims `019` for H-78. So `020` is the number
today. List `infra/customer_console/` before you name the file.

R6 binds the migration. Add one nullable column. Rename nothing. Drop nothing.

| Column | Type | What it holds |
|---|---|---|
| `refusal_reason` | `TEXT` NULL | The slug we refused on. NULL means the call served |

The column carries a CHECK: `refusal_reason IS NULL OR refusal_reason IN` the
three slugs below. A closed vocabulary is the point. An open TEXT column grows
a fourth spelling of the same wall within a month.

#### The vocabulary — three slugs, closed

| Slug | Status | Where the code raises it |
|---|---|---|
| `insufficient_credits` | 402 | `credits.py:397-401`. Copied word for word |
| `run_ceiling_exceeded` | 403 | `main.py:1017-1020`, inside `_spend_refusal` (`main.py:972-1026`). Copied word for word |
| `tier_unknown` | 400 | `main.py:4571` raises `TierUnknown`. This section mints the slug |

⚠️ **Two of the three already exist in the body the customer reads.** Copy
them. Do not mint a second spelling for a wall that has a name. W3 binds this.

#### Which refusal writes a row, and which does not

**A refusal writes a row when the CUSTOMER caused it.** Those three are the
walls A5 watches for.

| Shape | Anchor | Writes a row |
|---|---|---|
| 400 unknown tier | `main.py:4571` | **Yes** |
| 402 no credit | `credits.py:397-401` | **Yes** |
| 403 run ceiling | `main.py:1017-1020` | **Yes** |
| 503 credential unavailable | `main.py:4591` | No |
| 503 no vendor configured | `main.py:4617` | No |
| 502 vendor failure | `main.py:4739` | No |
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
(`main.py:4639-4643`) instead of refusing an oversized request.

#### What a refusal row holds

| Field | Value | Why |
|---|---|---|
| `request_id` | a fresh `rtr-<uuid4>` | `001_customer_console.sql:271` makes it NOT NULL UNIQUE. Mint it exactly as the served path does at `main.py:4470` |
| `refusal_reason` | one of the three slugs | The wall |
| `billed_credits` | `0` | We served nothing, so we charge nothing |
| `quantity` | `0` | The call consumed nothing |
| `unit` | the task's unit, from `task_catalog` | The row stays readable beside a served row |
| `tier` | the tier the caller ASKED for | A5 must say which tier the customer wanted |
| `run_id` | `caller.run_id` | A `run_ceiling_exceeded` row without its run is not actionable. `main.py:1007` reads the same field to decide the refusal |
| `model` | `NULL` | No model answered |
| `provider_cost_usd` | `NULL` | No vendor billed us |

⚠️ **`tier` holds the REQUESTED tier, never a resolved one.** At
`tier_unknown` there is nothing to resolve, and the tier on the request is the
fact A5 reports.

#### 🔴 The five counting reads MUST exclude a refusal

**This is the defect this slice can ship, and it is silent.** A refusal row
lands in `usage_event`, and every read that counts rows starts counting a
refusal as a call. The call counts inflate. The credit sums stay correct,
because a refusal bills 0. So the two columns disagree and nothing says why.

| # | Read | Anchor | The count | The shape of the fix |
|---|---|---|---|---|
| 1 | `usage_by_activity` | `store.py:583` | `COUNT(*) AS calls` | `AND refusal_reason IS NULL` in the WHERE clause |
| 2 | `usage_by_member` | `store.py:641` | `COUNT(*) AS calls` | `AND refusal_reason IS NULL` in the WHERE clause |
| 3 | `usage_by_org` | `store.py:700` | `COUNT(u.id) AS calls` | `FILTER (WHERE u.refusal_reason IS NULL)` |
| 4 | `usage_by_org` | `store.py:700` | `COUNT(DISTINCT u.user_email) AS members` | `FILTER (WHERE u.refusal_reason IS NULL)` |
| 5 | `usage_daily` | `store.py:790` | `COUNT(u.id) AS calls` | `FILTER (WHERE u.refusal_reason IS NULL)` |

⚠️ **Reads 3, 4 and 5 take a `FILTER` clause, never a WHERE clause and never
an ON clause.** All three sit on a LEFT JOIN that exists on purpose.

A WHERE clause on the right table turns the LEFT JOIN into an inner join. The
zero-usage organization `usage_by_org` exists to show then disappears. An ON
clause keeps the join, and it still hides the refusal from
`MAX(u.created_at)`. That makes a customer at a wall read as SILENT to A3.

**Three reads stay unchanged, and an agent must not touch them.**

- `run_spend` (`store.py:389`) sums `billed_credits`. A refusal adds 0, so the
  circuit breaker keeps its meaning with no edit.
- The failover read (`main.py:1781`) filters `served_rank > 1`. A refusal
  carries no served rank, so it never reaches that read.
- 🔴 `last_seen_by_org` (`store.py:677-697`) counts nothing. It reads
  `MAX(u.created_at)` for each organization, and a refusal MUST move that
  timestamp. A customer at a wall is a customer who is trying. Filtering the
  refusal out here makes that customer read as SILENT to A3, which is the
  exact defect H-76 closed. An agent sweeping the file for a refusal filter
  must skip this read on purpose.

#### Done when — one clause per artefact

1. **The migration.** `020` adds `refusal_reason TEXT` NULL, plus the CHECK on
   the three slugs. Every column stays nullable (R6).
2. **The number.** An agent lists `infra/customer_console/` at build time. The
   merge re-checks it (R1).
3. **The route writes three, and only three.** The 400, the 402 and the 403
   each write one `usage_event` row. The two 503s and the 502 write none. The
   401 cannot.

   🔴 **The refusal write opens its OWN short transaction.** The 400 raises
   from INSIDE the serving transaction. `main.py:4563` opens
   `get_engine().begin()`, and `main.py:4574` raises inside that block. A
   refusal row written on that connection rolls back with the raise, so the
   meter records nothing.

   Two shapes work. Open a short transaction for the write, after the serving
   transaction closes. Or write the row before the raise leaves the handler.
   `_spend_refusal` (`main.py:972-1026`) documents the same hazard, and its
   docstring (`main.py:975-978`) states the rule. It RETURNS the refusal
   instead of raising it, so the caller leaves the transaction cleanly.
4. **The row shape.** A refusal row carries `billed_credits` 0, `quantity` 0,
   the task's unit, the requested tier, and `model` NULL. It mints its own
   `request_id`, because `001_customer_console.sql:271` is NOT NULL UNIQUE. It
   carries `caller.run_id`, because a ceiling refusal without its run is not
   actionable.
5. **The five reads exclude it.** Reads 1 and 2 take a WHERE clause. Reads 3,
   4 and 5 take a `FILTER` clause, so each LEFT JOIN survives.
6. **The three unchanged reads carry no diff.** `run_spend`, the failover read
   and `last_seen_by_org` all stay as they are.
7. **The tests.** One refusal row and one served row return `calls` 1 from
   every one of the five reads.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A refusal never counts as a call | `test_customer_console_sql.py` — one refusal and one served call return calls = 1 |
| Our own failure writes no usage row | `test_customer_console_sql.py` — a 503 and a 502 leave the table empty |
| A refusal draws no credit | `test_customer_console_sql.py` — `run_spend` reads the same before and after a refusal row |
| 🔴 A refusal SURVIVES the raise | `test_customer_console_router.py` — the test DRIVES the HTTP route. A request for an unknown tier returns 400, and `usage_event` then holds exactly one row with `refusal_reason` of `tier_unknown`. A hand-inserted row does not satisfy this fence |
| A refusal keeps a customer visible | `test_customer_console_sql.py` — `last_seen_by_org` moves to the refusal's `created_at` |

**Verification.** The suites are database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_sql.py \
  tests/unit/test_customer_console_router.py \
  tests/unit/test_router_failover.py \
  tests/unit/test_customer_console_key_auth.py -q
```

### 8.2 Margin and runway (slice 6) — BUILT

Merged as `b3ce3a9c` in **#163**. This section records what shipped, because
§2 and §5 both said it had not.

| Piece | Where | What it does |
|---|---|---|
| `margin_ratio` | `analytics.py:45` | Credits billed per dollar of provider cost. `None` on a cost of zero |
| `runway_days` | `analytics.py:59` | Whole days of credit left at the recent burn rate. `None` on no burn |
| `BURN_WINDOW_DAYS` | `analytics.py:42` | Seven days, so a Monday-to-Friday customer keeps a rate |
| `usage_by_org` | `store.py:700` | Calls, credits, members, cost and last seen, for each organization |
| `credit_balance_by_org` | `store.py:841` | The balance for each organization, summed from the ledger |
| `GET /admin/usage/orgs` | `main.py:4952` | The board. Money leaves as strings (`main.py:5001`) |
| `GET /admin/usage/daily` | `main.py:5017` | The series, for the platform or for one organization |
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
| `record_usage` | `store.py:481`, `store.py:515` | Persists both. NULL rank from a caller that does not say |
| The Router hand-off | `main.py:4487` | Passes the rank the Router walked, from `router.py:85` and `router.py:166` (`tier_binding.rank`) |
| The failover read | `main.py:1775-1786` | 14 days of `served_rank > 1`, one row per day, tier, task and model |
| `TierBoard` | operator console | Shows the failovers that happened |
| `test_customer_console_pricing_truth.py` | tests | `:201`, `:257`, `:298`, `:353` |

**Done when** — every clause is met:

1. A failover writes `served_rank` 2.
2. A first-choice answer writes `served_rank` 1.
3. A caller that says nothing writes NULL.
4. The pricing view returns the failovers, filtered on a rank above 1.

**Verification.** `uv run pytest tests/unit/test_customer_console_pricing_truth.py -q`.
The suite is database-gated (R8), so run `bash scripts/dev_db.sh` first.

### 8.4 The label seam (slice 3, C6) — SPEC ONLY, 2026-08-30

**The table is built. The seam is not.** Every default here is an
**agent-proposed answer the owner may overrule**, which is the D16/D17
convention CP-2b and CP-2c used. Where a name or a number below disagrees with
the tree, the tree wins. Re-verify every anchor at dispatch.

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
7. **The operator catalog read carries the column**, so an operator sees which
   tiers a customer can pick.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A hidden tier never reaches a customer | `test_customer_console_tier_pricing.py` — a `customer_visible` FALSE row is absent from `GET /my/tiers` |
| A tier read carries the label, never the slug alone | `test_customer_console_tier_pricing.py` — every row holds `label` and `blurb` |
| A customer tier read crosses no tenant | `test_customer_console_tier_pricing.py` — organization A reads no row of organization B |
| NEITHER file holds a hard-coded tier label | `npx vitest run` in `workbench/control_plane` — one test reads the source of `AgentChat.tsx` AND of `route.ts`, and fails while either one holds `Tier 1 (fast / cheap)`, `Tier 2 (balanced)` or `Tier 3 (powerful)` |
| The three wire ids do not move | `npx vitest run` in `workbench/control_plane` — `route.ts` still serves `tier1-local-qwen3`, `tier2-sonnet` and `tier3-opus` |

**Verification.** The suite is database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_tier_pricing.py -q
```

Frontend: `npx vitest run` in `workbench/control_plane`.

### 8.5 The Router image rule (slice 4, §3.2) — SPEC ONLY, 2026-08-30

**Nothing below is built.** Every default here is an **agent-proposed answer
the owner may overrule**, which is the D16/D17 convention CP-2b and CP-2c
used. Where a name or a number below disagrees with the tree, the tree wins.
Re-verify every anchor at dispatch.

**Gate: AGENT-SAFE.** The serving flip stays the owner's act (H-69), and the
build in front of it is agent work.

**The problem.** `POST /v1/chat/completions` resolves one chain for the task
the caller declared (`main.py:4570`). A `vision` task therefore reaches
`tier-vision` or it reaches a 400. Nothing reads `model_profile.reads_images`
(`012_model_profile.sql:60`), so a chat model that already reads images is
never used, and every image call costs a second call.

**The answer, in one line.** Read the flag first. Use the chat model when the
flag is TRUE. Fall to the `tier-vision` chain when it is FALSE.

**No migration.** `reads_images` shipped in `012`. `tier_rate_card` shipped in
`015`. This slice adds a read, and it adds no column.

#### Done when — one clause per artefact

1. **One model on a TRUE flag.** Take a tier whose chat model sets
   `reads_images`. A `task: vision` call on it calls exactly one model. That
   model is the tier's own chat binding.
2. **The `tier-vision` chain on a FALSE flag.** Take a tier whose chat model
   clears the flag. The same call resolves `tier-vision` for the `vision`
   task. It then walks that chain (`router.py:127-168`).
3. **400 and no completion on an unbound `tier-vision`.** The route returns
   HTTP 400 with the detail §3.2 step 4 names. It calls no provider, and it
   writes no completion.
4. **The bills follow the pair.** Step 1 above bills (chosen tier, `chat`).
   Step 2 bills (`tier-vision`, `vision`). Both read `tier_rate_card` through
   `resolve_tier_rate` (`router.py:224`).
5. **The Router still reads no payload.** The caller declares the task
   (`main.py:834-843`). Nothing added by this slice looks inside `messages`.
6. **The tier does not drop.** §6A.9 rule 1 forbids a degradation across
   tasks, and step 2 is a lift rather than a degradation. §3.2 records the
   reconciliation.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A chat model that reads images serves the image itself | `test_customer_console_router.py` — a TRUE flag calls one model |
| A chat model that reads no image falls to `tier-vision` | `test_customer_console_router.py` — a FALSE flag calls the `tier-vision` chain |
| An image refusal names the reason | `test_customer_console_router.py` — a missing `tier-vision` returns 400, never a text-only answer |
| The Router never reads the payload | `test_customer_console_router.py` — an image in `messages` with `task: chat` stays on the chat binding |

**Verification.** The suite is database-gated (R8), so start the database
first.

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_router.py -q
```

### 8.6 A stream fails over before its first frame (slice 11, §3.6) — SPEC ONLY, 2026-08-30

**Nothing below is built.** `main.py:4664-4700` says so in its own comment.
Every default here is an **agent-proposed answer the owner may overrule**,
which is the D16/D17 convention CP-2b and CP-2c used. Where a name or a number
below disagrees with the tree, the tree wins. Re-verify every anchor at
dispatch.

**Gate: AGENT-SAFE.** The serving flip stays the owner's act (H-69).

**The problem.** A streamed request takes step 1 of the chain and stops there.
`_streamed_completion` (`main.py:4494-4536`) catches the open failure, logs
`router.stream_open_failed`, and sends the `[DONE]` sentinel. So a provider
that is down costs the customer their request, and the chain the operator
configured does nothing.

**The answer, in one line.** Await the first frame before the 200 status line
goes out, and walk the chain until then.

**No migration.** `served_rank` shipped in `013`.

#### The boundary — §3.6 states it, and this section builds it

Every failure before the first frame may fail over. Every failure after it may
not. The stream path reuses `MAX_CHAIN_ATTEMPTS` (`router.py:617`),
`TERMINAL_STATUSES` (`router.py:625`) and `CREDENTIAL_STATUSES`
(`router.py:629`), exactly as `call_chain` (`router.py:663`) uses them. It adds
no second policy.

*(The three anchors above read `router.py:559-608` until 2026-08-30. That
range is `relay_stream`.)*

#### Done when — four clauses

1. **A retryable failure before any frame fails over.** A 529 on step 1
   serves step 2, and the client sees one clean stream.
2. **A terminal failure stops the walk.** A 400 on step 1 calls no step 2.
3. **The usage row records the step that answered.** It carries that step's
   `served_rank`, the same way `_record_completion` (`main.py:4487`) does for
   a non-streamed call.
4. **A chain that fails at every step writes NO usage row.** This preserves
   `test_customer_console_router.py:715`, which is the phantom-row fence.

#### Two repairs this slice carries

1. **`main.py:4715`'s comment is stale.** It reads that `usage_event` has *"no
   column for the step that served"*. Slice 12 built `served_rank`
   (`013_pricing_truth.sql:26-40`). Correct the comment in the code this slice
   already touches.
2. **`main.py:4671-4677`'s comment describes the old rule.** It states that a
   stream does not fail over and that the change is a separate slice. This is
   that slice, so the comment states the boundary instead.

#### Fences (R7)

| Rule | Fence |
|---|---|
| A stream fails over before its first frame | `test_router_failover.py` — a 529 on step 1 serves step 2 as one clean stream |
| A stream never fails over after its first frame | `test_customer_console_router.py` — a mid-stream failure calls no second model |
| A bad request stops a streamed walk | `test_router_failover.py` — a 400 on step 1 calls no step 2 |
| A stream that never starts writes no usage row | `test_customer_console_router.py:715` — the phantom-row fence, unchanged |

**Verification.** Both suites are database-gated (R8), so start the database
first.

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
| Every step of a chain shares one date | `test_customer_console_fallback_chain.py` — two dates resolve as two chains, and the first choice disappears |
| A shorter chain leaves no orphan step | `test_customer_console_fallback_chain.py` — a three-step chain replaced by two resolves to two |
| The Console saves the chain whole | `catalog.test.ts` — the page posts `models`, never `model` |
| A bad request is not retried | `test_router_failover.py` — a 400 stops the walk and never calls the backup |
| A bad key strikes off its vendor | `test_router_failover.py` — a 401 skips every model from that vendor |
| The customer pays for what ANSWERED | `test_router_failover.py` — the walk returns the step that replied |
| An unknown measurement is never zero | `test_customer_console_model_profile.py` — the database refuses a window of 0 |
| A capability flag is never assumed | `read.test.ts` — a profile that says false yields no kind |
| A per-unit vendor cost parses in the vendor's unit and declares in the task's unit | `test_customer_console_vendor_feed.py` — a per-second feed price declares as ×60 per-minute on the profile, Decimal-exact. ⚠️ **Nobody has built this fence yet. It lands with `customer_console.md` §6A.11a clauses 5 to 7**, which own the ×60 conversion |
| A refusal never counts as a call | `test_customer_console_sql.py` — one refusal and one served call return calls = 1 |
| An unmeasured cost never reads as a good margin | `test_operator_analytics.py::TestMarginRatio` |
| An operator usage read is Operator-gated | `test_operator_roles.py` |
| A failover records the step that answered | `test_customer_console_pricing_truth.py` — a rank-2 walk writes `served_rank` = 2 |
| A stream fails over before its first frame | `test_router_failover.py` — a 529 on step 1 serves step 2 as one clean stream |
| A stream never fails over after its first frame | `test_customer_console_router.py` — a mid-stream failure calls no second model |
| A hidden tier never reaches a customer | `test_customer_console_tier_pricing.py` — a `customer_visible` FALSE row is absent from `GET /my/tiers` |

⚠️ **R8 binds every read here.** Verify the SQL against a real database. The
two reads in slice 1 were verified against the live Console database on
2026-08-29, and the gap fill was the defect that check found. Slice 9 ran
against a throwaway Postgres on 2026-08-30, and `now()` being stable inside one
transaction was the defect that check found.
