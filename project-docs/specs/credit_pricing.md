# Credit pricing — the economics of one credit

**Status: ACTIVE. Verified against code on 2026-09-04.** Owner directive,
2026-09-04, from the credit-pricing design document. This specification owns
the **economics** of a credit. It owns what a credit is worth, what margin we
take, and how we hold and settle one charge.

⚠️ **It does NOT own the catalog.** `customer_console.md` §6A.11 to §6A.13 own
the vendor feed, the per-unit vendor costs, `tier_rate_card` and
`credit_price`. `ai_metering_and_analytics.md` owns the tier vocabulary and
every surface that reports AI use. Read this file for **how much**, and read
those two for **what** and **where shown**.

**Board row:** WS-31 (Customer Console). **Decisions:** D67 is the parent.
This file proposes D71 to D74, and §7 names them.

---

## 1. Terms

The design document uses coined terms. This section defines each one, because
a reader who must look one up cannot take the decision the term supports.

| Term | Definition | Value |
|---|---|---|
| **PEG** | The provider spend, in USD, that one credit stands for | `0.0001`, so 10000 credits equal one dollar of raw cost |
| **M** | The margin multiplier. We multiply raw cost by M to get the charge | Per tier. §4.3 holds the table |
| **Margin** | The share of the customer payment that we keep | `1 - 1/M`. At M of 1.6 the margin is 37.5 percent |
| **Raw cost** | What the vendor bills us for one request, in USD | Measured, never estimated |
| **Partition** | Cached tokens plus fresh tokens must equal the prompt total | §3 makes this an assert |
| **Hold** | Credits reserved before a call runs | §5 |
| **Settle** | The act that turns a hold into a real charge | §5 |
| **Lot** | One buy of credits, with its own price and expiry | §6 |

---

## 2. Scope and non-goals

### In scope

1. The partition assert, and the refusal it raises (§3).
2. Two vendor rates per model, peak and off-peak, and one lookup that takes
   token count and timestamp (§4).
3. The per-million-token scale on the customer card (§4.2).
4. Per-tier margin, and the floor that alarms (§4.3).
5. Hold and settle, and the sweeper that releases a stranded hold (§5).
6. Credit lots, their consumption order and their expiry (§6).
7. `MIN_CHARGE`, the floor under one billable operation (§5.4).

### Non-goals

1. **We do not build a second catalog.** The design document proposes
   `provider`, `model`, `rate_card`, `tier`, `tier_price` and `tier_binding`.
   Every one of them duplicates a live table. CLAUDE.md §4 calls a second
   implementation of a seam a defect. §8 holds the map from each proposed
   table to the table that already holds it.
2. **We do not set the numbers.** PEG, M and every margin floor are H-42, and
   H-42 is an owner gate.
3. **We do not flip the spend gate.** H-42 names the order. Price the card
   first. Flip `CUSTOMER_CONSOLE_SPEND_GATE` second.
4. **We do not price video or music.** No Router verb serves them (D67.3).

---

## 3. The partition assert — AGENT-SAFE

### 3.1 What is wrong today

`TokenUsage.fresh_prompt_tokens` subtracts cached tokens from the prompt
total. It clamps the result at zero. So a bad partition becomes a silent
undercharge, and nothing writes a log line.

Measured on 2026-09-04, against `tier-balanced` rates of 34 and 3.4 and 204
credits per 1000 tokens:

| Input | Fresh tokens | Credits |
|---|---|---|
| `prompt=8000 cached=6000` | 2000 | 251.60 |
| `prompt=2000 cached=6000` | 0 | 183.60 |
| `prompt=100 cached=99999` | 0 | 340.00 |

Row 2 is the same real call as row 1, reported in the sibling convention. It
undercharges by 27 percent. Row 3 is impossible and the code accepts it.

`usage_from_response` reads Anthropic's `cache_read_input_tokens` and OpenAI's
nested `cached_tokens`. It treats both as a subset of the prompt total.
Anthropic reports them as siblings. So the normaliser is right for one vendor
convention and wrong for the other, and no code sees the difference.

### 3.2 Done when

1. `TokenUsage` raises `UsagePartitionError` when `cached_tokens` is greater
   than `prompt_tokens`. The message names both counts.
2. The Router catches that error, meters the call at zero, and writes
   `metering_fault = 'usage_partition'`. It never fails the completion.
3. ⚠️ **The fault column is NOT `refusal_reason`, and the difference is
   load-bearing.** Migration 020 gives a slug to a **customer wall**, where the
   call did not serve. Five reads in `store.py` then exclude such a row from
   every call count. A partition failure serves the customer a completion and
   fails only our meter. Reusing `refusal_reason` would hide a served call from
   five counts and report a wall that never happened.
4. Migration adds `usage_event.metering_fault`, nullable, with a closed
   vocabulary. It also adds `usage_event.cache_convention`, which records
   whether we read the count as `subset` or as `sibling`. Take the next free
   number at build time (R1).
5. A structlog line `router.usage_partition_failed` carries the organization
   and the request id. The alarm then joins to the row it belongs to (H-85).
6. Every count read keeps ignoring `metering_fault`. A faulted call served, so
   it counts as a call. Only its credits are zero.

### 3.2a What we do with a call we cannot meter (added 2026-09-05)

The owner asked how you price something when you do not know how much of it
somebody consumed. **You do not. You absorb it.**

1. The call **serves**. The customer holds their completion.
2. We bill **zero** and pay the vendor. We never estimate. Estimating is the
   defect §3.1 measured, pointed the other way. A bill nobody can explain is
   worse than a call we admit we could not meter.
3. We record the fault, so the choice is **countable**. Absorbing cost is only
   defensible while it is rare, and until this column nothing measured whether
   it was.
4. Later, we stop refusing. `cache_convention` records which vendor field
   answered, so the convention is knowable. An agent may normalise correctly
   **only after** the fleet data shows the signal is reliable. Acting on an
   unmeasured inference is how you overcharge.

⚠️ **There are TWO faults, and the second is worse.** Migration 025 adds it.
`usage_from_response` never raises. So a body we do not recognise returns three
zeros and the partition assert passes. The row then lands looking like a served
call that happened to be free. The partition case at least writes a log line.

| Fault | Meaning |
|---|---|
| `usage_partition` | The counts contradict each other |
| `usage_unreadable` | The provider reported nothing we recognise |

### 3.2b The surface (added 2026-09-05)

**Owner directive, 2026-09-05: make it very visible that this is not built and
the customer used the tokens anyway.**

1. `/usage` carries a **danger banner**, above the silent-customer one. It
   says the calls served and that we paid the vendor. It says we charged
   nothing. It says billing them correctly **is not built yet**.
2. A stat tile reads **"Served, unbilled"**. It renders at zero too. A tile
   that appears only on failure teaches nobody where to look.
3. The table gains a **"Given away"** column, beside "Refused". It is not
   called "Unbilled". That reads as an invoice still to send, which is
   recoverable. This is not.
4. 🔴 **The banner reads the SERVER's uncapped total, never the page's sum.**
   `usage_by_org` orders by credits and caps the page. A leak bills zero by
   definition. So the leaking organization sorts **last** and falls off the
   page. The worse the leak, the more certainly it hides. Measured 2026-09-05:
   the page summed to 0 while the fleet held 2. `store.unbilled_fleet_total`
   is the uncapped read, and its fence asserts it takes no `limit`.

### 3.3 The fence (R7)

`tests/unit/test_customer_console_credits.py` gains three cases. One asserts
the raise. One asserts the Router still returns the completion. One asserts
the refusal row. The suite must fail if any case is removed.

### 3.4 Files

- `apps/services/customer_console/customer_console/credits.py` — `TokenUsage`
- `apps/services/customer_console/customer_console/router.py` —
  `usage_from_response`
- `apps/services/customer_console/customer_console/main.py` —
  `_record_completion`
- `infra/customer_console/` — the new migration

---

## 4. Cost, window and scale — AGENT-SAFE

### 4.1 Two rates per model, one lookup

**Owner directive, 2026-09-04.** We calculate credit cost from the **peak**
rate always. We store the off-peak rate as well, and we report both.

The design document contradicts itself here, and the contradiction is
material. §10.7 says bill at the peak rate. §3.5 and §13.1 derive the Fast
price from the off-peak cost of 0.00101 dollars. At 30 credits per turn:

| Cost basis | Margin |
|---|---|
| Off-peak | 66.3 percent |
| Peak | **32.7 percent** |
| The document's own floor for `tier-fast` | 45 percent |

So peak traffic breaks the floor of the tier that carries the volume. The
owner directive resolves this. We price from peak, and the Fast card doubles
from 5500 to 11000 credits per million tokens.

📌 **The live feed already carries the peak rate.** Measured 2026-09-04:
`vendor_price_feed` holds 0.44 and 1.32 for `deepseek-v4-flash`, which are the
document's peak numbers. So peak-only billing is what happens today by
accident. This section makes it deliberate and adds the off-peak number beside
it.

**Window and context tier are ONE change** (owner directive). Both change the
same lookup, and two passes over one code path is waste.

#### Done when

1. `model_profile` grows `vendor_*_offpeak_usd` for the three token columns,
   nullable (R6). The existing columns hold the peak rate and keep their names.
2. `model_profile` grows `context_tier_threshold` and
   `vendor_*_long_context_usd`, both nullable.
3. The vendor cost lookup takes `token_count` and `started_at` as arguments.
   It returns the peak rate for the credit calculation always.
4. The lookup resolves `context_tier` from the **response**, never from the
   pre-flight estimate. A large request must not under-bill by half.
5. `usage_event` grows `window_at_call` and `context_tier`, so a later reader
   can reconstruct the choice.
6. A request that crosses a window boundary resolves the window from its
   **start time in UTC**.
7. The off-peak rate reaches `analytics` only. It never changes a charge.

#### The fence (R7)

`tests/unit/test_customer_console_pricing_window.py` is new. It asserts the
peak rate drives the charge, the off-peak rate reaches the report, and a
boundary-crossing request bills peak. R8 binds it, so it runs against a real
database.

`workbench/operator_console/src/lib/window.test.ts` fences the surface half.

#### The surface (added 2026-09-05)

⚠️ **A price nobody can enter is a price nobody sets.** The first draft of this
section named only the columns. An operator then had four new numbers per
model and no box to type them in, so the whole slice would have shipped inert.

1. The **Models** page carries the off-peak rates, the window, the long-context
   threshold and its rates. `ModelDetails.tsx` holds the form.
2. The three existing boxes keep their labels and hold the **peak** rate.
3. The window shape is a **pure function** in `lib/window.ts`, never a rule
   inside the component. `priceboard.ts` and `pricing.ts` set that idiom.
4. The form refuses one bound alone, exactly as
   `model_profile_offpeak_range_complete` does. The refusal only moves where
   the operator reads it.
5. The form says out loud when a window **crosses midnight**. Without that,
   "16:30 to 00:30" reads as either eight hours or none.
6. ⚠️ **The feed prefill leaves every new box empty, and that is deliberate.**
   `vendor_price_feed` has no window and no context tier, so there is nothing
   upstream to copy. Filling them from the peak number would write a fact
   nobody measured.

### 4.2 Per million tokens

**Owner directive, 2026-09-04.** The customer card moves to credits per
million tokens. Everybody quotes per million now, and the vendor feed already
does.

`tier_rate_card` names its columns `input_credits_per_1k`,
`output_credits_per_1k` and `cached_input_credits_per_1k`.

#### Done when, and R6 binds every step

1. Release one adds `*_per_1m` columns, nullable. It backfills each one from
   the `_per_1k` column multiplied by 1000.
2. Release one writes both column sets on every insert. It reads the `_per_1m`
   set.
3. Release two drops the `_per_1k` columns.
4. `rate_call` divides by 1000000, never by 1000.
5. The console shows per million, and `priceboard.ts` converts once.

⚠️ **We cannot roll back** (CLAUDE.md §3.4). A single-release rename would
meet old code with a new schema and bill nothing.

#### The WIRE is an expand surface too (added 2026-09-05)

🔴 **The Console and the operator console deploy apart.** So the JSON between
them needs the same treatment as the schema, and release one did not name it.

1. The wire carries **both** scales. A console that has not shipped release one
   still reads `_per_1k` and still draws a correct price.
2. The reader prefers `_per_1m` and falls back to `_per_1k` times 1000. New
   code can legitimately meet an old wire, and a blank price is the failure.
3. `POST /catalog/tier-rates` accepts **either** scale. The field the caller
   sent is the authority. The writer derives the other one from it, so the
   two columns cannot drift apart.
4. ⚠️ The **validator reads the derived numbers**. A caller on the new scale
   leaves the old fields at zero. `all_rates_zero` would then refuse a real
   price as "you priced nothing".

#### The surface (added 2026-09-05)

1. The price list and the tier board read `describeTierRate`, which says
   **per 1M**. The retired model card keeps its own describer, because the two
   cards sit on different scales during release one.
2. The rupee line under each price says per 1M as well. A rupee figure at a
   different scale from the credit figure beside it reads as a contradiction.
3. 🔴 **`inrLabel` now groups digits.** At the old scale it drew `₹4`. At this
   one it draws `₹2,04,000`, and an operator should not count digits to
   compare two prices. The grouping is `en-IN`, because the product is priced
   in rupees for an Indian market.

#### 🔴 Restating a money string must never touch a float

`Number(v) * 1000` is a money bug. Measured 2026-09-05 over the 20000 rates
between 0.0001 and 2.0000: **4773 of them** come back with a float artefact.
`0.0041` becomes `4.1000000000000005`, and that string then reaches a price
column or a comparison and stops matching itself.

`lib/scale.ts` moves the decimal point instead, which is exact at every scale.
`scale.test.ts` asserts the property over the whole range, never five examples.

### 4.3 Per-tier margin — the numbers are OWNER-GATE

The console holds one margin knob today, and `PriceFromCost.tsx` defaults it
to 70 percent. The design document holds eleven values.

**An agent builds the table and the floor check. The owner sets every
number** (H-42).

#### Done when

1. A new `tier_margin` table holds `tier`, `margin_multiplier`,
   `margin_floor` and `effective_from`. It is INSERT-only, as every rate table
   here is.
2. It ships **empty**. An absent row means the tier has no suggestion, exactly
   as `test_the_rate_card_ships_unpriced` refuses a priced ladder.
3. The console reads the multiplier per tier and stops defaulting to 70.
4. A margin monitor reports realised margin per tier over seven days, and it
   compares against that tier's floor.
5. ⚠️ **The monitor groups by TIER and never by organization.** The question
   is whether a PRODUCT is priced right. One customer's mix says nothing about
   that. An organization-shaped read would also inherit H-76's cap, which
   drops the quiet rows. A tier nobody used much is exactly the one whose
   price nobody has checked. *(This clause said "read `usage_by_org`
   with a funded-customer sort" until 2026-09-05. That was wrong: it named a
   per-organization read for a per-tier question.)*

#### What slice 7 BUILT (added 2026-09-05)

Migration 028 adds `tier_margin`, and it **ships empty**. `analytics.
realised_margin` computes the fraction, `analytics.margin_alarms` finds the
tiers below their floor, and `store.margin_by_tier` is the seven-day read. The
Pricing page carries a **Margin monitor** panel.

🔴 **`realised_margin` is the number `margin_ratio` deliberately refused to
compute.** That refusal still stands for its own return, because credits and
dollars are different units. Migration 017 changed the premise. The operator
now SAVES a rupee price for a credit and a planning rate for dollars. So the
conversion is a fact somebody asserted and not one this module invented.
Without a saved price the answer is NULL, and NULL is neutral.

⚠️ **The board no longer opens at 70 percent.** That was a commercial number
nobody chose, shown to an operator as an answer. The box now starts from what
the owner has actually set, and stays empty when they have set nothing.

⚠️ **Two silences, two sentences.** "No tier has a floor" and "no credit
price is saved" are different states with different fixes. The panel says
which it is rather than drawing a healthy-looking empty table.

---

## 5. Hold and settle — AGENT-SAFE

### 5.1 Why

Nothing reserves credits today. The Router meters after the call returns. So
two calls can each pass a balance check, and the organization goes negative.

### 5.2 Three cost shapes

| Shape | Example | Rule |
|---|---|---|
| Deterministic | transcribe. Duration is a property of the file | No hold. Charge exactly. |
| Deterministic input | vision. Image tokens follow the dimensions | Hold the output side only |
| Variable | chat | Hold the worst case on both sides |

Audio is easier than text here, not harder. Do not push it through the hold
path.

### 5.3 Done when

1. `credit_ledger.reason` accepts `hold`, `settle` and `release`.
2. A hold row carries `hold_ref`, and a settle row points at its hold.
3. The hold takes `SELECT ... FOR UPDATE` on the balance, inside one
   transaction. Two concurrent calls must not both pass.
4. `idempotency` keys read `{request_id}:hold` and `{request_id}:settle`. The
   existing unique index on `(organization_id, reason, ref)` carries them.
5. A settle writes in the **same transaction** as the `usage_event` row, as
   `_record_completion` already does.
6. A sweeper releases any hold older than the request timeout plus five
   minutes. It logs each release loudly, because a stranded hold means a
   request path fails in silence.
7. The worst case always rates at the **uncached** rate. Never assume a hit.
8. `max_tokens` gains a per-tier cap, so the hold stays near the real charge.

### 5.3a What slice 4 BUILT, and what it did not (added 2026-09-05)

**Built.** Migration 026 adds `credit_ledger.hold_ref` and a partial index
for open holds. `estimate_hold` is pure and sizes the reservation.

`place_hold`, `release_hold` and `sweep_orphan_holds` live in `store.py`. The
chat door reserves before it calls a vendor. `record_usage` releases in the
SAME transaction as the charge.

🔴 **The lock is the whole slice.** Balance is `SUM(delta)` and not a column,
so there is nothing to contend on. `place_hold` takes `SELECT ... FOR UPDATE`
on the **organization** row. Measured 2026-09-05: two threads each reserved
600 of a 1000 balance. One held and one was refused, and the balance stayed at
400. Without the lock both pass and the organization lands at -200.

⚠️ **The reserve is armed by `CUSTOMER_CONSOLE_SPEND_GATE`, and that gate
ships OFF.** A reserve is a spend refusal by another name. Arming it while the
gate is off would refuse customers the gate deliberately does not. H-42 names
the order: price the card, then flip the gate.

⚠️ **`place_hold` hangs off the CHAT door alone.** The other three doors know
their quantity before the call. A duration, a picture count and a character
count are all measured. So those doors reserve nothing and take the exact
charge at settle time.

**NOT built, and named so nobody assumes otherwise.**

1. §5.3 clause 8, the per-tier `max_tokens` cap. An uncapped request reserves
   `MAX_OUTPUT_FOR_HOLD`, which is generous rather than accurate. §7.3 of
   the design document holds the cost. A hold far larger than the real charge
   refuses calls a customer could afford.
2. The sweeper is a FUNCTION and nothing calls it on a schedule. A crash still
   strands credits until somebody runs it.
3. The prompt-token estimate is a character count divided by four. It sizes a
   reservation and never a charge.

### 5.4 `MIN_CHARGE`

1. `floor_charge` applies the floor. `MIN_CHARGE_ENV` carries the figure.
2. 🔴 **It SHIPS AT ZERO, so the floor is inert until the owner sets it.**
   How much an operation must cover is a commercial number, and §2 puts every
   commercial number behind H-42.

   ⚠️ **The first build shipped it at 5 and it changed real bills.** Thirteen
   suites went red because their fixtures price in fractions of a credit. Each
   failure was the floor working correctly on a number nobody had agreed to.
   The mechanism is an agent's to build. The figure is the owner's to choose.
3. ⚠️ **`embed` is exempt.** One embedding costs a fraction of a credit. A
   5-credit floor would charge 50000 credits to index 10000 documents, against
   perhaps 200 credits of real value.
4. ⚠️ **The exemption is keyed on the TASK and never on the tier.** D61 makes
   tiers free text and tasks an allowlist. A second embedding tier would
   silently lose the exemption if the check read a tier slug.
5. A zero stays zero. An absorbed task (D19.2) and an unpriced card both rate
   to nothing on purpose. Lifting either to the floor invents a charge.

### 5.5 The fence (R7)

`tests/unit/test_customer_console_hold_settle.py` is new. It asserts the
concurrent hold refuses, the settle is idempotent, the sweeper releases, and
`tier-embed` skips the floor. R8 binds it.

---

## 6. Credit lots — AGENT-SAFE

### 6.1 Why

Balance is one sum today. So nothing knows what a credit cost, when it
expires, or whether it was bought or granted. A refund cannot be computed and
deferred revenue cannot be reported.

### 6.2 Done when

1. `credit_lot` holds `organization_id`, `source`, `credits`, `credits_used`,
   `price_paid_inr` and `expires_at`. R5 binds it, so it is tenant-scoped.
2. `source` is one of `purchase`, `trial`, `promo` and `refund`.
3. Consumption draws from the lot that **expires soonest**. Free credits burn
   before paid credits at the same expiry.
4. `credit_ledger` grows `lot_id`, nullable (R6).
5. The balance stays `SUM(delta)`. The lot table explains a balance and never
   replaces it.

### 6.2a What slice 6 BUILT (added 2026-09-05)

Migration 027 adds `credit_lot` and `credit_ledger.lot_id`. `store.open_lots`
holds the order. `store.draw_from_lots` allocates a charge across lots and
`store.add_credit_lot` records one.

⚠️ **`NULLS LAST` on the expiry is load-bearing.** Postgres orders NULLs FIRST,
so a bare `ORDER BY expires_at` burns the lot that never expires and lets a
dated lot reach its expiry unused. That is the same loss the ordering exists to
prevent, by another route.

⚠️ **`add_credit_lot` writes NO ledger row.** The caller writes the balance.
A lot that wrote its own row could disagree with the ledger, and the balance a
customer reads is the ledger.

**WIRED, 2026-09-05.** The gap above is closed.

🔴 **The bookkeeping lives in `add_credit`, the ONE ledger writer, and not at
the three call sites that grant credits.** A fourth site would otherwise arrive
without lots and nobody would notice until a refund could not be computed.

1. Credits arriving open a lot. `_LOT_SOURCE_FOR_REASON` maps the ledger
   reason to a lot source.
2. ⚠️ **`release` opens NO lot.** It returns a reservation the customer
   already owned, and a lot for it would double-count.
3. ⚠️ **`hold` draws NO lot.** A reservation is not a spend. Drawing one down
   would consume value the customer may never use. Only the settle spends.
4. The buy path passes the real price, converted from paise once, at the
   boundary. Zero stays zero and never becomes NULL.
5. The customer page carries a **Credit lots** panel, in burn order.
6. ⚠️ The panel tells a MISSING `credit_lots` key apart from an empty list. An
   older Console sends no key, and an empty table over a missing feature would
   say something untrue.

**Still not built.** Nothing expires a lot. D74 has not been taken, so every
lot ships with a NULL expiry. Only a lot an operator dates by hand exercises
the ordering's expiry rule.

### 6.3 The fence (R7)

`tests/unit/test_customer_console_credit_lots.py` asserts the consumption
order and the expiry. R8 binds it.

---

## 7. Decisions this file proposes

The owner takes each one. An agent must not build against its own answer
(CLAUDE.md §5).

| Id | Question | Recommendation |
|---|---|---|
| **D71** | Do we bill from the peak rate always? | **Yes.** Owner directed it on 2026-09-04. §4.1 holds the margin evidence. |
| **D72** | Does the customer card move to per million tokens? | **Yes.** Owner directed it on 2026-09-04. §4.2 holds the two-release path. |
| **D73** | Is margin per tier, not global? | **Yes.** A flat multiplier either overprices Fast or underprices Powerful. |
| **D74** | Do credits expire? | **Owner call.** Expiry is an accounting choice before it is a product one. §9 names the risk. |

---

## 8. The map — proposed table to live table

Read this before you create any table. Every row on the left already exists on
the right.

| Design document | Lives today as | Note |
|---|---|---|
| `provider` | `provider_credential` | Keys live in the secrets store, never in a row |
| `model` | `model_profile` + `model_capability` | D60 keys capability on `(model, task)` |
| `rate_card` | `model_profile` vendor columns + `vendor_price_feed` | The feed stages. Staff approval promotes. |
| `tier` | `tier_catalog` | The same eleven slugs already ship |
| `tier_price` | `tier_rate_card` | This **is** D67 |
| `tier_binding` | `tier_binding` | ⚠️ Same name, different shape. Do not replace it. |
| `credit_ledger` | `credit_ledger` | Append-only already |
| `credit_lot` | — | §6 builds it. The one genuinely new table. |

---

## 9. Risks

1. 🔴 **`tier-embed` and `MIN_CHARGE`.** §5.4 holds the rule. Ship the floor
   without the exemption and an index operation overcharges by 250 times.
2. 🔴 **The scale migration.** §4.2 is two releases. One release bills 1000
   times wrong in whichever direction the code and schema disagree.
3. ⚠️ **Deferred revenue.** An unredeemed credit is a liability, not revenue.
   Expiry is therefore an accounting decision. The owner takes it to an
   accountant, and D74 records the answer.
4. ⚠️ **Retries inside one chain step.** The Console records the step that
   answered (§8 of `customer_console.md`). A litellm retry inside that step is
   invisible, so `duplicate_provider_spend_pct` stays unmeasured.
5. ⚠️ **The feed is a cache of vendor claims.** It is never billing truth.
   Staff approval into `model_profile` stays the only path to a billed rate.

---

## 10. Verification commands

```bash
# The database R8 needs. Without it 843 tests SKIP and the run reads green.
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"

# The suites this specification owns
uv run pytest tests/unit/test_customer_console_credits.py \
              tests/unit/test_customer_console_pricing_window.py \
              tests/unit/test_customer_console_hold_settle.py \
              tests/unit/test_customer_console_credit_lots.py -q

# The pricing surface
cd workbench/operator_console && npx tsc --noEmit && npx vitest run
```

⚠️ `scripts/dev_db.sh` starts the tenant database and does **not** apply the
tenant ladder. **H-96** holds that defect. Apply
`infra/postgres/[0-9]*_*.sql` in numeric order until H-96 closes.

---

## 11. Slices, in build order

| # | Slice | Gate | Blocked by |
|---|---|---|---|
| 1 | The partition assert (§3) | **AGENT-SAFE** | Nothing |
| 2 | Window and context tier (§4.1) | **AGENT-SAFE** | Nothing |
| 3 | Per-million scale, release one (§4.2) | **AGENT-SAFE** | Nothing |
| 4 | Hold and settle (§5) | **AGENT-SAFE** | Nothing |
| 5 | `MIN_CHARGE` and the embed exemption (§5.4) | **AGENT-SAFE** | Slice 4 |
| 6 | Credit lots (§6) | **AGENT-SAFE** | Nothing |
| 7 | `tier_margin` table and the monitor (§4.3) | **AGENT-SAFE** | Nothing |
| 8 | Set PEG, M and every floor | 🔴 **OWNER-GATE** | H-42 |
| 9 | Flip `CUSTOMER_CONSOLE_SPEND_GATE` | 🔴 **OWNER-GATE** | Slice 8 |
| 10 | Per-million scale, release two (§4.2) | **AGENT-SAFE** | Slice 3 ships |

**Slice 1 goes first**, because it undercharges today and it is small.

⚠️ **Slice 9 follows slice 8, and the order is not a preference.** H-42 says
so. Flip the gate against an unpriced card and every zero-balance organization
loses AI. A funded organization never reaches a refusal, because nothing bills.
Provisioning leaves every organization at zero.
