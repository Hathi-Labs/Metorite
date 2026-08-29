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

1. A customer sees three tiers. They are **Fast**, **Medium** and **Powerful**.
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
| `tier-balanced` | **Medium** |
| `tier-powerful` | Powerful |
| `tier-vision` | (not shown — see §3.2) |
| `tier-stt` | (not shown — see §3.3) |

⚠️ **Do not rename `tier-balanced`.** The owner says "Medium". The slug stays
`tier-balanced`, and the label becomes `Medium`. A rename breaks every binding
row and every past usage row that names the old slug.

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

---

## 4. Customer surfaces

| # | Surface | State | Blocked by |
|---|---|---|---|
| C1 | Spend by app | **Built** (CP-7) | — |
| C2 | Spend by member | **Built** (CP-7) | — |
| C3 | **Spend over time** | To build | — |
| C4 | **Buy AI credits** | To build | Razorpay (H-14) |
| C5 | **A budget for each member** | To build | 🔴 **H-73** |
| C6 | Tier picker showing three labels | To build | §3.1 `tier_catalog` |

🔴 **C5 cannot ship until H-73 closes.** The Console reads the member from the
`X-CC-Member` header, and the gateway forwards that header from the request. A
member who omits it escapes their own budget. A budget the person being
budgeted can turn off is not a budget.

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

⚠️ **R8 binds every read here.** Verify the SQL against a real database. The
two reads in slice 1 were verified against the live Console database on
2026-08-29, and the gap fill was the defect that check found.
