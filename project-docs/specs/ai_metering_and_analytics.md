# AI metering and analytics

**Status: ACTIVE.** Owner directive, 2026-08-29. This specification owns the AI
tier vocabulary that a customer sees, and every surface that reports AI use.

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
| **F7** | **The operator can grant credits and cannot read use.** `POST /credits/grant` and `GET /credits/balance` exist. No operator read of `usage_event` exists | `main.py` |
| **F8** | **The customer can read use by app and by member.** `GET /my/usage/activity` and `GET /my/usage/members` shipped in CP-7 | `main.py` |
| **F9** | **No time series exists** on either side | `main.py` |
| **F10** | **No per-member budget table exists.** H-73 records why: the member identity arrives in a header the member controls | `information_schema`, HANDOFF |
| **F11** | **`usage_event` holds 0 rows.** Every surface below ships to an empty table | live query |

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

**Build:** a `tier_catalog` table. Columns: `slug`, `label`, `description`,
`sort_order`, `customer_visible`. The customer picker reads it. The operator
edits it.

### 3.2 An image follows the chat model when it can

**Decision D-AI-2.** The Router decides, and the customer does not.

**When a customer sends an image, the Router does this:**

1. Read the model bound to the chosen tier for `chat`.
2. If that model also declares the `vision` capability, send the image to it.
   Bill the `chat` rate card.
3. If that model does not declare `vision`, resolve `tier-vision` for the
   `vision` task, and send the image there. Bill the `vision` rate card.
4. If nothing binds `tier-vision`, refuse the request and name the reason.

⚠️ **Step 2 is the money.** A second call to a vision model costs a second
call. A chat model that already reads images costs one.

⚠️ **Step 4 must refuse, not degrade.** A silent drop of the image makes the
model answer about text it cannot see, and the answer looks correct.

### 3.3 A specialised tier stays invisible

**Decision D-AI-3.** `tier-stt`, `tier-tts` and `tier-embed` never appear in a
customer picker. An app names the task. The Router resolves the tier.

`tier_catalog.customer_visible` is `false` for these three. The usage still
records the tier, so the administrator in §1.3 still sees the cost.

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

⚠️ **A stream does not fail over.** After the first frame reaches the client,
the request is half answered. A retry would join two different completions into
one response, which is worse than the error. Failover *before* the first frame
is legal, and it is a separate slice.

⚠️ **The chain tries at most `MAX_CHAIN_ATTEMPTS` steps.** An unbounded chain
is an unbounded bill and an unbounded wait.

⚠️ **`usage_event` has no column for the step that served.** A failover writes
a `router.failover` log line, and nothing else. So the Operator Console cannot
show a failover history yet, and that page stays sample-only.

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

| # | Surface | State | Blocked by |
|---|---|---|---|
| O1 | Grant credits to an organization | **Built** (`POST /credits/grant`) | — |
| O2 | **Use by organization** | To build | — |
| O3 | **Use over time** | To build | — |
| O4 | Drill into one organization by member, app and tier | To build | — |
| O5 | The analytics in §6 | To build | — |

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
| **A5** | **A refused call** — a 402 for no credit, a 400 for a bad tier | Is a customer hitting a wall? | Support, before they write in |
| **A6** | **A cost spike** — a day above five times the trailing mean | Did something run away? | Investigate the app and the member |
| **A7** | **Tier efficiency** (customer side) — the share of calls on Powerful | Does this company overpay? | Move an app to Medium |

⚠️ **A1 is the most important number on the operator side, and nobody asked for
it.** We record what we pay (`provider_cost_usd`) and what we charge
(`billed_credits`) on the same row. A customer can be busy and unprofitable,
and no current surface shows it.

⚠️ **A5 needs a new column.** `usage_event` records a call that happened. A
refusal writes no row. See §8, slice 5.

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

| # | Slice | Delivers |
|---|---|---|
| **1** | Operator reads: `usage_by_org`, `usage_daily` in `store.py` | O2, O3 |
| **2** | Operator Console `/usage` page | O2, O3, O4 |
| **3** | `tier_catalog` table and the label seam | §3.1, C6 |
| **4** | Router image rule | §3.2 |
| **5** | Record a refusal in `usage_event` | A5 |
| **6** | Margin and runway | A1, A2 |
| **7** | Customer time series | C3 |
| **8** | Per-member budget | C5, **after H-73** |
| **9** | `tier_binding.rank`, and the Console reads and writes a chain | §3.5 |
| **10** | The Router walks the chain when a step fails ✅ | §3.5, §3.6 |
| **11** | A stream fails over before its first frame | §3.6 |
| **12** | `usage_event` records the step that served | §3.6, A5 |
| **13** | `model_profile` — what a model IS ✅ | §3.7 |

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
| An image refusal names the reason | Router test — a missing `tier-vision` returns 4xx, never a text-only answer |
| Every step of a chain shares one date | `test_customer_console_fallback_chain.py` — two dates resolve as two chains, and the first choice disappears |
| A shorter chain leaves no orphan step | `test_customer_console_fallback_chain.py` — a three-step chain replaced by two resolves to two |
| The Console saves the chain whole | `catalog.test.ts` — the page posts `models`, never `model` |
| A bad request is not retried | `test_router_failover.py` — a 400 stops the walk and never calls the backup |
| A bad key strikes off its vendor | `test_router_failover.py` — a 401 skips every model from that vendor |
| The customer pays for what ANSWERED | `test_router_failover.py` — the walk returns the step that replied |
| An unknown measurement is never zero | `test_customer_console_model_profile.py` — the database refuses a window of 0 |
| A capability flag is never assumed | `read.test.ts` — a profile that says false yields no kind |

⚠️ **R8 binds every read here.** Verify the SQL against a real database. The
two reads in slice 1 were verified against the live Console database on
2026-08-29, and the gap fill was the defect that check found. Slice 9 ran
against a throwaway Postgres on 2026-08-30, and `now()` being stable inside one
transaction was the defect that check found.
