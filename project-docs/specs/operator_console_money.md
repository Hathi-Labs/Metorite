# Operator Console — money clarity and the UI rebuild (WS-50)

**Status:** ACTIVE. Minted 2026-10-09 by **D94**. Verified against the code on
2026-10-09 at `main` `5405a0b7f`.

- Slice 0 (the backend): merged in PR #779.
- Slices 1 and 2: merged in PR #782.
- Slice 3a (a price on a manual grant): BUILT in PR #786.
- Slice 3: BUILT.
- Slice 4: merged in PR #788.
- Slices 5 and 6: spec only. Open gaps: HANDOFF H-283 and H-284.

**Owner request, 2026-10-09.** The owner wants to see, for each customer, what
their AI costs us and what we charge them. The owner also wants no ambiguity
anywhere in the console.

## 1. The problem, measured on 2026-10-09

1. **No page shows revenue, cost and profit together.** The customer page shows
   seat revenue (MRR, in rupees) and AI cost (in dollars) in separate panels. No
   page adds credit revenue to seat revenue. No page shows AI cost in rupees. No
   page shows profit, for one customer or for the business.
2. **Six labels name the vendor cost:** "Our cost", "Cost to us", "Provider
   cost", "Vendor cost", "Cost" and "costs us".
3. **"Margin" names four different numbers.** `/usage` and the customer page
   show a ratio of credits to dollars ("2.5× cost"). The breakdown shows a
   realised percent. `/pricing` shows a planned percent and a 7-day percent.
4. **The realised margin values a FREE credit as revenue.** It multiplies every
   credit spent by the current credit price, and a trial or promotion credit
   earned nothing.
5. **Runway hides its own window.** It divides the balance by the average
   daily spend of the last 7 days. The panel around it says "last 30 days".
6. **Every explanation lives in a `title` attribute.** It is invisible until a
   person suspects it exists, and it does not work on a touch screen. The
   customer page and `/usage` have almost none.
7. **Internal codes reach the screen:** H-numbers, D-numbers, migration
   numbers, env var names, "Router", "litellm", "BYOK", raw JSON.
8. **Two acts are both called "Activate".** "Activate subscription" (billing)
   and "Activate account" (lifecycle) differ, and only a hint says how.

## 2. Scope and non-goals

**In scope:** the Operator Console (`workbench/operator_console`), and one
additive Console read (§3).

**Non-goals:**

- No change to what a customer is billed. This work reads money. It never
  moves money.
- No change to the customer-facing app (`workbench/control_plane`).
- No new pricing decision. The credit price and the seat price stay where the
  owner sets them (`/pricing`, `plan_catalog`).
- The "not attributed" email calls (97 percent of email calls on 2026-10-09)
  are an attribution defect in the email pipeline. It is out of scope here.

## 3. The money model (D94)

One vocabulary, on every page. `src/lib/money.ts` is its only home.

| Word | Meaning | Unit |
|---|---|---|
| **We charged** | Seat revenue for the period, plus the value of PAID credits the customer spent | ₹ |
| **Seats** | Seats bought × the plan price, for an active subscription | ₹ a month |
| **Paid credits** | Credits spent from a `purchase` lot, at the price that lot was sold at | ₹ |
| **AI cost** | What the AI vendors charged us for this customer's calls | ₹, with $ in the tooltip |
| **Given away** | The AI cost of calls paid with FREE credits (trial, promotion, grant, refund) | ₹ |
| **Profit** | We charged − AI cost | ₹ |
| **Margin** | Profit ÷ We charged | percent |
| **Credits left** | The credit balance | credits |
| **Days left** | Credits left ÷ average credits a day over the last 7 days | days |

**D94 rules:**

1. **A free credit is not revenue.** Its AI cost is "Given away".
2. **Rupees everywhere.** A dollar figure converts at the planning rate saved
   on `/pricing`. The tooltip shows the dollar figure and the rate.
3. **"We charged" is the value of what the customer used in the period**, not
   cash received. Cash received (credits bought) is on the customer's
   "Credits and billing" tab.
4. **One PR per slice**, in order, each merged and deployed before the next.

**Slice 0 — the backend (BUILT).** Migration `036_credit_draw.sql` adds
`credit_draw`, one row per lot that a usage charge drew from.
`store.add_credit` writes the rows in the same transaction as the ledger row.
A charge that no lot covers writes one more row with a NULL `lot_id`.
`GET /admin/usage/orgs` gains, per row: `paidCredits`, `paidValueInr`,
`unpricedPaidCredits`, `freeCredits`, `unbackedCredits`, `lifePaidUsed`,
`lifePaidValueInr`, `lifeUnpricedPaidUsed`, `lifeFreeUsed` and `creditsLast7Days`. The view gains
`drawsSince`, and the saved credit price as `inrPerCredit` and `usdToInr`.

**An estimate is labelled, never hidden.** Draws begin when migration 036
applies. For the part of a window before `drawsSince`, the console splits the
untraced credits by the customer's lifetime lot mix and labels the figure
"estimated". A `purchase` lot with no recorded price is valued at the current
credit price and labelled the same way.

## 4. The slices

Every slice is **AGENT-SAFE**. None flips a flag, moves money or writes a live
organization.

| Slice | What | Done when |
|---|---|---|
| 0 | `credit_draw` and the usage read fields (§3) | `test_customer_console_credit_draw.py` passes against a real Postgres |
| 1 | `lib/money.ts`: the D94 model, one function per word, with the estimate flags | `money.test.ts` covers paid, free, unbacked, unpriced, the estimate, and a missing credit price |
| 2 | `Explain`: an ⓘ tooltip that opens on hover, focus and tap, and a glossary in `lib/glossary.ts`. Each tooltip shows the definition, the formula and the row's own numbers | Every money figure on the customer page and on Money has an `Explain`. `glossary.test.ts` fails on an entry with no formula |
| 3 | Customer page: a money strip, a lifecycle bar, tabs, one "Start paid plan" act, one seats panel, the rupee value on a credit grant | The strip shows We charged, AI cost, Profit, Margin, Credits left and Days left for the last 30 days |
| 3a | `POST /credits/grant` takes an optional `price_paid_inr`, legal only on `manual` or `purchase` with positive credits. The lot records what the customer paid | `test_customer_console_manual_credits.py` passes against a real Postgres |
| 4 | Customer list columns from §3, and a Money page that replaces AI usage, with the vendor bill moved onto it | The ratio "× cost" appears on no page |
| 5 | Navigation groups, plain words, no internal codes, Activity in sentences, a Setup page for the go-live checklist | `words`-style test refuses `H-[0-9]`, `D[0-9]`, `migration [0-9]` and env names in rendered strings |
| 7 | A chosen date range. `/admin/usage/orgs`, `/admin/usage/daily`, `/admin/usage/breakdown` and `/providers/spend` take `from` and `to`: inclusive calendar days in India. With no `from`, every read answers the last N days as before. The Money page and the customer page get a range picker | `test_customer_console_usage_range.py` passes against a real Postgres: the India-midnight edges, the daily span, the default unchanged, and a 422 for an unanswerable range |
| 6 | A visual pass: dark, light and phone width, with the fixture rig in `e2e/visual/` | Screenshots of every page reviewed before each PR |

## 5. Files

- Backend: `infra/customer_console/036_credit_draw.sql`,
  `apps/services/customer_console/customer_console/store.py`
  (`record_draws`, `draws_by_org`), `.../main.py` (`OrgUsageRow`,
  `OrgUsageView`, `admin_usage_by_org`, `_ORG_PURGE_KEEPS_TABLES`).
- Console: `workbench/operator_console/src/lib/usage.ts`, `src/lib/money.ts`
  (new), `src/lib/glossary.ts` (new), `src/app/Explain.tsx` (new),
  `src/app/customers/[slug]/*`, `src/app/page.tsx`, `src/app/CustomerTable.tsx`,
  `src/app/usage/*`, `src/app/Header.tsx`.

## 6. Verification

```bash
eval "$(bash scripts/dev_db.sh --export)"
uv run pytest tests/unit/test_customer_console_credit_draw.py \
  tests/unit/test_customer_console_credit_lots.py \
  tests/unit/test_org_purge_console.py -q
cd workbench/operator_console && npx tsc --noEmit && npx vitest run && npm run build
```

## 7. Fences (R7)

- `tests/unit/test_customer_console_credit_draw.py`: the draw rows, the
  overdraft row, the window split and the sold-at price.
- `tests/unit/test_org_purge_console.py`: `credit_draw` is in the purge keeps.
- `workbench/operator_console/src/lib/money.test.ts` (slice 1).
- `workbench/operator_console/src/lib/glossary.test.ts` (slice 2).
