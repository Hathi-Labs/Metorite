# Subscription Console — the customer-facing billing surface (WS-30)

**Status:** ◐ **The billing view is MERGED and live-but-inert; the checkout's
SERVER SIDE is BUILT and its SURFACE is not.** *(Header rewritten 2026-08-18 —
R4. What stood here was wrong on three counts, each corrected below rather than
quietly deleted: it called the billing view "unmerged" on a branch, it said
"everything else … nothing built", and it said dispatch waits on MT-2's
tables.)*

**SC-4g (i)–(v) — server side — ✅ BUILT 2026-08-18** with CP-9's substrate
half, in WS-31's tree because that is where the tables live: `discount_code` +
`discount_redemption` (`infra/customer_console/007_payments.sql`),
`cc_disc_<prefix>_<secret>` through the shared `keys.py` seam (`ENV_DISCOUNT`,
a fourth env **value**, not a fourth implementation), the pre-GST basis-point
discount with clamping, `POST /discounts` (Operator) and
`POST /billing/orders/{id}/redeem` (the customer's own key, gated on
`can_pay`), the ₹0 path through the **one** `payments.fulfil`, and
`credits.LEDGER_REASONS`. **Done-when 1–8 met; clause 9 — the test-mode capture
rehearsal — is NOT met and is not claimed**, because creating a Razorpay
account is owner-side even in test mode. Fences:
`tests/unit/test_customer_console_payments.py` (**110** tests, real Postgres 16,
0 skipped) — **independently verified, FAILED, and repaired 2026-08-19**; the
finding that lands *here* is **F2**: done-when 1's *"harmless orphan"* residual
was **false in one direction**. Detaching or replacing the provider order does
not retract it at Razorpay, and paying the stale link is a capture the Console
cannot attribute to any order — **money received with nothing granted**.
Restated in done-when 1, owned by `customer_console.md` **CP-8** (the
NULL-`order_id` receipts), alerted at `ERROR` in the webhook, and answered on
the surface by new **SC-4a done-when 5a**: never expose a replaced order's
payment link. The other four findings (F1/F4/F5/F7) are fence and doc repairs
inside WS-31's tree. **SC-4a's checkout UI and its two write proxies remain held back**
behind B7's capability decision, which reaches the tenant plane's vocabulary
and must not ride in on a payments PR.

**What exists, verified against code 2026-08-18:**
- **SC-1b/SC-5's billing view MERGED as `f1fcca4f`** *("feat(WS-30): the billing
  console — first UI for the platform")*:
  `workbench/control_plane/src/app/settings/billing/page.tsx` plus its read-only
  proxy `src/app/api/billing/summary/route.ts`, which calls the Customer
  Console's `GET /me/billing` with this deployment's own `cc_live_` key. It
  **fails closed** — 503 *"Billing is not configured for this deployment"* when
  `CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_ORG_KEY` are unset
  (`route.ts:34-53`) — and stays URL-only, out of the nav, because the Customer
  Console is on no box (`customer_console_infrastructure.md`: where it runs is an
  open owner decision). That is CLAUDE.md §4's ship-dark, not an oversight.
- **The substrate it reads is BUILT** — not pending. WS-31 shipped CP-1, CP-2,
  CP-2a, CP-2b, CP-3, CP-4 and CP-6's mechanism against a real Postgres:
  `plan_catalog` (seeded with the priced D23/D24 ladder), `org_subscription`,
  `seat_grant`, `seat_assignment`, `credit_ledger`, `usage_event`,
  `model_rate_card`, `usage_rollup` all exist in
  `infra/customer_console/001_customer_console.sql`. **The old "wait for MT-2's
  tables" sentence is obsolete**: D32 moved this console from *reader of
  CC-local tables* to *client of the Customer Console*, and its server side is
  there. What MT-2 still owns is **enforcement** inside Metorite (`intersect()`,
  `ModuleGate`, the 402-vs-403 split) — which gates the *entitlement* surfaces,
  not this console's reads.
- **The checkout is the hole, and it is now specced.** `GET /me/billing` returns
  `"purchaseEnabled": False` (`customer_console/main.py:1266`) and the page
  renders a contact prompt instead of a button on that branch
  (`page.tsx:202`). That flag is the landing site for SC-4a.

**What this 2026-08-18 remediation minted**, after the checkout was audited
**NO-GO** for dispatch (the payment seam had no ticket body anywhere; this
console had no customer-authenticated write path; SC-4a and SC-4g were both
under-specified):
- **WS-31 `customer_console.md` CP-9 — the `payment_provider` seam (Razorpay)**,
  the ticket the corpus cited in three places and never wrote. It carries the
  auth answer (no fifth scheme; the org key gains two writes that cannot move
  value), the order state machine, the signature-verified idempotent webhook and
  the **one fulfilment function** both the paid and ₹0 paths write through.
- **SC-4a re-scoped** to the seeded, priced Center-package ladder; its
  credit-pack clauses **deferred**, dated, behind an owner pricing act.
- **SC-4g completed** — code storage, the two tables, percent-or-fixed against
  the pre-GST base, the named ledger vocabulary, and an honest account of which
  half of its own acceptance an agent cannot reach.
All are **agent-proposed defaults the owner may overrule** (the D16/D17
convention). ·

**Then RE-AUDITED for dispatch the same day → GO-NARROWED, nine blocking doc
corrections (B1–B9) + six nits, all answered 2026-08-18** (still docs-only; the
CP-9 half of the answers is in `customer_console.md` §6). What changed **here**:
**B2** — SC-4g done-when 4 demanded five distinct refusal reasons *and*
unknown ≡ wrong-org in one sentence; it now **partitions** into three distinct
reasons for a code this org may see and **one** indistinguishable shape for
{unknown, wrong-org} · **B3** — done-when 6's three-way `credit_ledger`
distinguishability was **vacuous at launch** (zero ledger rows are written on
the subscription path) and now rides `discount_redemption` + `seat_grant.reason`,
with the ledger clause dated to when packs land · **B7** — SC-4a's two new write
proxies ride a BFF pattern whose admin gate is **documented but does not exist**;
the gate is now named (a `billing:purchase` capability over §3's floor, resolved
server-side), and the **existing read proxy's gap is recorded as a board finding,
not fixed here** · plus SC-4g (i)'s code format corrected to `cc_disc_…`, which
is what the seam it names will actually parse. ·
**Date:** 2026-08-09 · corrected 2026-08-14 · **rewritten 2026-08-18** ·
**Owner:** WS-30 (this spec) · **Decisions:** **D23 + D24** (work_plan.md §3,
2026-08-10 — Center packages are the governing pricing shape, carrying D19's
credit/seat rules; D24 closed every customer-framing question: ₹600 headline
stays, all-Centers seat ₹1,800, Complete ₹3,000, role presets in SC-2). None may
be re-litigated here; purchase-flow copy is buildable. Standing page rules: a
typical-month credit anchor, and no internal vocabulary (atoms/slices/modules)
customer-facing.

> ⚠️ **Substrate moved — D32.2 (2026-08-12).** The tables this console reads
> (`org_module_entitlement`, `user_module_seat`, `credit_ledger`, `usage_rollup`,
> `invoice`) now live in the **central Control Plane service**, not in each
> Metorite deployment: **`specs/customer_console.md` (WS-31)**. This
> console becomes a **client** of that service rather than a reader of CC-local
> tables. **D35 (2026-08-12) confirms the split and fixes the other half:** this
> console stays **inside Metorite** at `/settings/billing`, while the
> Operator Console becomes a **separate deployable app** — so §1's "the two share
> tables and must never share routes" is enforced by the deployment boundary
> rather than by a guard inside one application. **Nothing about its scope, its surfaces or its access rules changes** —
> SC-1/SC-2/SC-3 read exactly as written, and D19.3's hard cap, D23's Center
> framing and D24's customer framing are all carried unchanged. Update the data
> source, not the design. The seat vocabulary it renders (purchased / assigned /
> available) is defined once in WS-31 §3.3 — do not recompute it here.

**What this is.** The console a **customer's org admin** uses to manage their
Metorite subscription: see what **Centers and add-ons** they own (modules
are internal atoms, never the customer frame — D23), assign seats, watch AI
credit burn, and request changes. It is the customer-side complement of the
**Operator Console** (`saas_multitenancy.md` §4.1a, `/operator`, MT-4) — the two
share tables and must never share routes: the Operator Console is staff-only and
cross-org; this console is one org, admin-gated, tenant-scoped.

**Launch posture (D19.4): manage-only for SEATS.** View + assign within purchased
caps + request changes fulfilled manually (Phase-2 "invoice by hand"). **⚠️ AI
CREDITS are the exception, and D37 (2026-08-13) makes SC-4 a real spec rather
than a placeholder.** Seats can wait on a human because they change monthly;
credits cannot, because an organization runs dry mid-workflow at 2am and a
top-up that waits for an invoice is an outage. SC-4 is still sequenced last, but
it is now specified — including the parts that were missing entirely: the
purchase itself, alert *delivery* (as opposed to alert rendering), the runway
figure, adjustments, and the usage statement.

---

## 1. Scope and non-goals

**In scope:** the `/settings/billing` surface in the workbench (the URL already
promised by `NotEntitled.upgrade_url`, `saas_multitenancy_implementation.md` §4.2);
its gateway endpoints; seat assign/unassign writes under the D19.3 rules; a
change-request flow that lands in the operator's inbox.

**Non-goals:** the payment *provider* integration itself — now WS-31
`customer_console.md` **CP-9**, minted 2026-08-18; SC-4 specifies the
customer-facing flow **over** it. *(This line read "(Razorpay SDK wiring, MT-4)" and that phrasing put SC-4g's
own acceptance outside the spec that carries it — SC-4g clause 2 mandates a
test-mode capture, which is provider work. The boundary is restated so the
straddle is gone: **the seam, the order, the webhook and fulfilment are CP-9;
the surfaces, the pack/package choice, the code entry and the copy are here.**
CP-9 also names the part of clause 2 no agent can execute — creating the
Razorpay account is owner-side even in test mode.)* · the Operator Console,
which is a **separate deployable** (D35) · the entitlement tables and enforcement
seam (MT-2) · the rate card itself (WS-31 CP-6) · **dunning** (the processor's
collection retries) · any surface a non-admin member sees, who get the
`ModuleGate` upsell fallbacks rather than this console — with the single
exception of SC-4d's "your organization is out of credits" message, which carries
no figures.

> ⚠️ **"Invoicing and tax" used to appear in this list as "the processor's job".
> That was wrong and is corrected by D38.** Razorpay collects money and issues a
> payment receipt; under GST the **tax invoice is the supplier's obligation**,
> and the supplier is us. Issuing it is **SC-5**, firmly in scope.

## 2. The surfaces (each is an acceptance unit)

### SC-1 — Read views *(after MT-2 tables + MT-3 ledger exist)*
- **SC-1a Centers & add-ons panel** *(re-shaped by D23, 2026-08-10)*. **Center
  packages are the primary purchase framing** (per-user counts on each Center,
  ₹600 app-bearing / ₹300 slices-only), with the org-wide add-ons (Builder,
  Workflows) and the Complete bundle beside them; `module_catalog` rows are the
  internal atoms and never the customer-facing frame. Each shows the org's
  entitlement state (`active | trial(expiry) | locked`), price, and seats
  purchased vs assigned.
  Locked modules render as upsell cards (the §2.4 rule 1 lever), never hidden.
  When a user's stacked a-la-carte seats cost more than the covering tier, the
  panel surfaces the swap as a savings prompt (§2.4a rule 2). **Done when:** a
  two-org fixture shows org A its own entitlements and never org B's; a locked
  module renders its card with a request-CTA; the savings prompt is pinned by a
  test case where a-la-carte sum > tier price; the panel is driven entirely by
  `/auth/me`'s `modules` + one `GET /billing/summary` call.
- **SC-1b Credit monitor.** Balance (credits + ₹), burn this cycle, per-module
  burn chart from `usage_rollup`, the 80% alert state, BYOK orgs see consumption
  with "not billed — your key" labelling (§3.4). **Done when:** the displayed
  balance equals `SUM(credit_ledger.delta)` for the org in the fixture; a
  `usage_event` written for module X moves only X's bar.
- **SC-1c Invoice list.** Read-only mirror of the `invoice` table (§4.1b). **Done
  when:** rows render from the mirror with no provider round-trip on the request
  path. *(Expanded into **SC-5** by D38 — a list of rows is not a billing
  surface, and the documents in it are ours to issue, not the processor's.)*

### SC-2 — Seat writes *(the D19.3 rules, verbatim)*
`POST /billing/seats` assign/unassign — the primary surface is the **users ×
Centers grid** (D23): assigning a Center package is ONE act creating the billing
seat + `org_group` membership + module entitlements + D12 slice grants
(`source='center'`), and unassignment reverses all four. Add-ons are a per-user
column (`source='alacarte'`); the **all-Centers seat (₹1,800, D24.3)** and the
Complete bundle (₹3,000) expand as `source='plan'`. **Role presets are launch
scope (D24.5):** named presets ("Sales rep", "Field staff", "Founder") generate
a member's row in the grid, adjustable after — the first-purchase flow is
"assign roles", never "fill a matrix". **Done when (presets):** applying a
preset writes exactly its packages/add-ons in the one-assignment act;
re-applying is idempotent; adjusting after never re-applies the preset.
**Hard cap:** assignment beyond
`seats_purchased` returns a 409 with a buy-more payload — never auto-upgrades.
Core seats are **not managed here**: membership is the Core seat (D19.3), so the
member admin surface is the only place Core count changes. **Done when:** the
cap 409 is pinned by a test; unassignment frees the seat immediately; every write
lands an audit row; the pushed processor quantity (once MT-4 exists) equals
`COUNT(user_module_seat)` — until then the count is the invoice input the operator
reads.

### SC-3 — Change requests *(the manual-fulfilment bridge)*
`POST /billing/requests` (add module / change seat count / cancel). Creates a
durable request row + notifies the operator; the customer sees request status.
Fulfilment is the operator editing entitlements — **🔴 OWNER-GATE to execute**
during the silo phase, exactly like every live entitlement change. **Done when:**
a request round-trips to visible status; nothing in the request path mutates
entitlements directly.

### SC-4 — Buying credits, and running out *(specced 2026-08-13, D37)*

Razorpay-only (D19.5) behind the `payment_provider` seam — **which is WS-31
`customer_console.md` CP-9** (minted 2026-08-18; the corpus previously cited
CP-8, which is the Operator Console). The mechanics of the ledger are
`customer_console.md` §3.4; this is the part a customer touches.

> ⚠️ **Read CP-9 before building any SC-4 slice.** It carries the parts that are
> *not* customer-facing and that these clauses silently assume: the
> `payment_order` state machine, the mandatory signature check, webhook
> idempotency on `provider_event_id`, the **one** fulfilment function, integer
> paise, and the auth answer — **the organization key gains exactly two writes
> (`POST /billing/orders`, `POST /billing/orders/{id}/redeem`), neither of which
> can move value, and no fifth auth scheme is minted.** Value moves only on a
> signature-verified webhook or an operator-issued code. That answer is recorded
> in CP-9 §9.3 and cross-cited here so a WS-30 agent cannot build the surface
> against a different assumption.

**SC-4a · The self-serve checkout — SCOPED 2026-08-18 to the seeded, priced
Center-package ladder; credit packs DEFERRED.**

> ⚠️ **Re-scoped 2026-08-18, dated rather than silently narrowed.** This clause
> was written (D37.1) as *"a short ladder of pre-priced packs"* for **AI credit
> packs**. Measured against the code the same day: **no pack ladder exists
> anywhere.** `plan_catalog.kind` is `CHECK (kind IN
> ('core','center','addon','bundle'))` (`001_customer_console.sql:144`) and
> `002_seed_catalog.sql` seeds no pack row. D19.2 fixes the ₹10 credit **unit**
> and no pack price; pricing a ladder is the owner's commercial act
> (`customer_console.md` §8 gate 4, §9 item 4).
>
> **So the launch checkout sells what is actually priced and seeded — the
> Center-package subscription ladder (D23/D24)**, which is also what customer
> zero buys (D36): `core` ₹600 · app-bearing Centers ₹600 · slices-only Centers
> ₹300 · `builder` ₹500 · `workflows` ₹300 · `all_centers` ₹1,800 · `complete`
> ₹3,000. Building a pack checkout against an unpriced table would mean either
> inventing prices (D19.2's gate) or shipping a surface with an empty ladder.
>
> **The pack clauses below are DEFERRED, not deleted** — they are correct and
> they return the moment the owner prices a ladder. Everything CP-9 builds is
> pack-agnostic: `payment_order_line.plan_slug` references `plan_catalog`, so a
> pack is a catalog row plus a `kind` value, never a second code path.

**Deferred — the pack rationale, unchanged and still right when packs land
(D37.1).** A short ladder of pre-priced packs rather than a free-text amount.
Three reasons, in order of importance: **(1)** every pack is priced **under
₹15,000**, so a repeat purchase clears the RBI e-mandate AFA threshold and never
demands an OTP from a customer who is already out of credits mid-workflow
(`saas_operations_doctrine.md` §3.2); **(2)** a ladder makes price-per-credit
legible, where an arbitrary amount makes the customer do arithmetic to know if
they are getting a good deal; **(3)** it is the smallest thing that can be built
correctly. Custom amounts are a later question, and only if a customer asks.
**Deferred done-when:** buying a pack moves the balance by exactly the pack's
credits and lands **one** `credit_ledger` row referencing the provider payment
id; a duplicate webhook for the same payment credits **once**; a failed payment
credits nothing and says so.

**Launch done-when (the subscription checkout):**
1. The purchase surface renders **only `plan_catalog` rows that are `active` and
   priced** — never a hard-coded ladder in TypeScript. A price change is a
   database row (D23/D24's own rule), so a surface that transcribes prices is a
   second source of truth and a defect.
2. Choosing packages and quantities and confirming creates **one**
   `payment_order` through `POST /billing/orders` under the deployment's
   organization key, and **changes nothing else** — no seat, no subscription, no
   ledger row until capture (CP-9 §9.3, fenced there).
3. On capture, the org's entitlements reflect the order: `org_subscription` is
   active for the period and `seat_grant` totals equal the ordered quantities —
   read back through the **existing** `GET /billing/summary`, so no surface
   recomputes seats (SC-2's rule, `customer_console.md` §3.3).
   ⚠️ **A green `GET /billing/summary` is a CONSOLE fact, not a live product
   entitlement** *(added 2026-08-18)*. It says the Console recorded the
   purchase; it says nothing about whether any Metorite surface will *honour*
   it — enforcement inside the product is **MT-2**'s `intersect()` / `ModuleGate`
   / the 402-vs-403 split, and **SC-2 over MT-2 owns that**, not this clause.
   Two further facts about the route, both measured: it is the **Operator**,
   cross-org read (`customer_console/main.py:875`), so this acceptance runs
   operator-side in the suite; the customer-reachable read is `GET /me/billing`
   (`:1213`) and it returns **no seats at all** today — growing it a seats block
   is SC-1a's work, named here so nobody reads clause 3 as a promise the
   customer's own page can keep.
4. **A duplicate webhook grants once** — CP-9 §9.5's **two** guards, not one:
   the `provider_event_id` key is transport dedup, and the **terminal-state
   rule** is the money guard, because Razorpay sends *different* event ids
   (`payment.captured`, `order.paid`) for one capture *(corrected 2026-08-18,
   B8)*.
5. **A failed or abandoned payment grants nothing and says so** on the page,
   naming what to do next — read through **`GET /billing/orders/{id}`**, minted
   as CP-9 §9.3a *(2026-08-18, B6: as first specced nothing could read an order
   back, so this clause had no data source and would have been "met" by a page
   that guesses)*.
5a. **The surface must never expose a replaced order's payment link.** *(Added
   2026-08-18, CP-9 finding F2.)* A redemption **replaces** the provider order
   (partial) or **detaches** it (₹0), and neither retracts order #1 at Razorpay:
   paying the stale link is a capture the Console cannot attribute to any order
   — money received with nothing granted. So the checkout re-reads the order
   after every redemption and renders only the **current** `provider_order_id`;
   a link captured in component state before the redeem call is the bug this
   clause exists to name.
6. `purchaseEnabled` (`customer_console/main.py:1266`) is what flips the page
   from the contact prompt to the checkout (`page.tsx:202`) — **the flip is the
   owner's**, and its preconditions are the flip set below.
7. `npx tsc --noEmit && npx vitest run` green; the surface uses
   `src/components/ui/` primitives and the theme tokens (no colour literals,
   no `lucide-react` import) per `workbench/control_plane/AGENTS.md`.
8. **The two new write proxies are gated SERVER-SIDE**, per the block
   immediately below *(added 2026-08-18, B7)*.

> ### The gate on the two new write proxies — B7, answered 2026-08-18
>
> **The problem, measured.** This checkout adds two **write** proxies
> (`POST /api/billing/orders`, `POST /api/billing/orders/[id]/redeem`) to a BFF
> pattern whose admin gate is **documented but does not exist**. The shipped
> read proxy asserts it in its own header — *"Admin-gating is enforced upstream
> by the Control Plane's key scope and here by the session check — a member
> without admin never reaches billing figures"* (`api/billing/summary/route.ts:21-22`)
> — and the handler checks only `currentIdentity()`, i.e. **signed in**, not
> admin. There are **zero** capability checks anywhere under
> `src/app/api/billing/` or `src/app/settings/billing/` except the page's
> client-side `access?.is_admin` (`page.tsx:102`), and that check does **not**
> stop the fetch: the `useEffect` is registered at `:93` *before* the early
> return at `:102`, so a non-admin's browser calls the proxy and receives the
> billing payload while being shown "Billing is admin-only."
>
> **Why this bites harder for the writes than for the read.** The workbench's
> BFF rule is *"nothing here grants anything, and every real request is
> authorized again at the gateway"* (`api/auth/me/route.ts:8-10`). The billing
> proxies **do not go to the gateway** — they call the Customer Console
> directly with the deployment's `cc_live_` key — so there is no second
> authorization anywhere. For these routes **the BFF is the only gate there
> will ever be.**
>
> **The default (agent-proposed, D16/D17 — the owner may take the fallback):**
> the two write proxies require a **new, dedicated `billing:purchase`
> capability**, on top of §3's `admin:members:read` floor, resolved
> **server-side**.
> 1. **Stricter than the floor, deliberately.** `admin:members:read` is *"may
>    see the member list"*; these routes **spend the company's money**. The
>    person who administers members and the person who buys are not the same
>    person in most companies of any size, and a capability is how that is
>    expressible without a second role system.
> 2. **It must not be born unheld.** A capability nobody holds makes checkout
>    dead on arrival, and granting it per-member against a live org is an owner
>    gate (`work_plan.md` §6, WS-24 (d)). So the same change **seeds it onto the
>    `owner` and `admin` roles**, exactly as
>    `infra/postgres/133_workflows_publish_permission.sql` did for
>    `workflows:publish` — same shape, same idempotent `DO $$` block, **migration
>    number taken by listing `infra/postgres/` at build time and re-checked at
>    merge (R1)**. It joins `acb_auth.permissions.CAPABILITIES`
>    (`permissions.py:126-154`) so a typo is catchable rather than silently inert.
> 3. **Resolved server-side, never from the browser.** The route resolves the
>    caller's access through the **existing** seam — the gateway's `/auth/me`
>    via `headersActingAs(email)`, the same hop `api/auth/me/route.ts` already
>    makes — and refuses **403** before any Customer Console call. Never from a
>    header, a body or a client-supplied claim (`user_management_contract.md`
>    R3/R11).
> 4. **Fences** (`src/app/api/billing/*.test.ts`): a signed-out caller is
>    **401**; a signed-in member **without** the capability is **403**; the
>    refusal happens **before** the upstream fetch (assert the fetch mock was
>    never called — a 403 issued *after* the money route was hit is a different
>    and worse bug); a holder passes. Parametrised over both write proxies, so
>    a third one added later is covered without anyone remembering.
>
> **The fallback the owner may prefer:** the §3 floor (`admin:members:read`)
> alone, no new capability, no tenant-plane migration. Cheaper by one migration
> and one vocabulary entry; it makes every member-admin a purchaser. Recorded so
> the choice is visible rather than defaulted into.
>
> **🔎 Board finding, recorded and NOT fixed here — the EXISTING read proxy.**
> `GET /api/billing/summary` is reachable by **any signed-in member** and
> returns the org's credit balance, burn and BYOK status; its own header says
> otherwise. That is a **live gap in merged code (`f1fcca4f`)**, not a gap in
> this ticket, and fixing it is a code change to a shipped route — out of scope
> for a docs round and out of scope for a checkout slice that must stay
> reviewable. It belongs on the board as its own small ticket: add the same
> server-side check to the read proxy and correct the header comment, which is
> currently a **false statement about a security control** (the most expensive
> kind of stale comment).

> **🔴 The flip set — what must be true before checkout is LIVE** *(recorded
> 2026-08-18; D35.5's "revenue order is enforcement, then checkout" binds the
> FLIP, not the dark build, so this ticket is dispatchable now):*
> **(1)** a **priced rate card** — owner act, D19.2 / `customer_console.md` §8
> gate 4 · **(2)** `CUSTOMER_CONSOLE_SPEND_GATE`'s two open money invariants
> closed (BYOK zero-rating; the pre-flight-cost invariant) and the gate flipped
> — owner act, `work_plan.md` §6 · **(3)** `purchaseEnabled` → true — owner act
> · **(4)** live Razorpay credentials — owner act, `work_plan.md` §6(b) /
> `customer_console.md` §8 gate 3. An agent builds all four dark and flips none.

**SC-4b · Auto-top-up (off by default at launch).** §3.3 makes it the default
for paid plans; **D37.1 defers that**, because an auto-charge above the AFA cap
fails precisely when the customer is dry, and a top-up that silently fails is
worse than one that never existed. Ships **opt-in**, with the pack and the
trigger threshold both chosen by the admin, and both bounded by the cap.
**Done when:** a failed auto-top-up notifies (SC-4d) and degrades to the §3.3
soft-block rather than a hard stop; the feature is off unless explicitly enabled.

**SC-4c · The runway figure.** Balance answers "how much"; the number that
prompts a purchase is **"about N days left at your current rate"**, computed from
`usage_rollup` over a trailing window. Shown beside the balance, not buried in a
chart. **Done when:** an org with no usage shows no runway rather than a
division-by-zero or a misleading "∞"; the window is named in the UI so a spike
does not read as a permanent trend.

**SC-4d · Who is told, and how (D37.2).** An alert nobody receives is a colour,
not an alert — the 80% state was previously specced only as something the
console *renders*, which requires the admin to already be looking.
- **Email the billing admins** at 80%, at zero, and on a failed auto-top-up. The
  only channel that reaches someone not currently signed in, which is exactly
  the case that matters.
- **In-app banner** for holders of the billing capability, persistent while the
  condition holds.
- **The affected member sees why their own call was refused**, and who to ask.
  A member staring at a generic failure files a bug; a member told "your
  organization is out of AI credits — contact <admin>" does not.
  ⚠️ This deliberately exposes *that* the org is out of credits to non-admins —
  never the balance, never the spend, never the invoice.
**Done when:** each threshold notifies **once per cycle**, not per call (a
per-call alert on the 402 path is a mail storm); crossing back above 80% re-arms
it; the member-facing message carries no figures.
*(Operator-side alerting was considered and deliberately not taken: CP-8's
console surfaces burn across all customers already, so this would be a push
channel for something already visible.)*

**SC-4e · Adjustments and goodwill credits.** The case that will actually happen:
an agent loops, burns credits on nothing, and the customer asks for them back.
A negative-or-positive `credit_ledger` row with `reason='adjustment'` and a
mandatory human-readable note, **never** an edit of history and never
indistinguishable from a purchase — a refunded credit and a bought credit must
be tellable apart on the ledger a year later. 🔴 **OWNER-GATE to execute against
a live org.** **Done when:** an adjustment is visibly distinct from a purchase in
the ledger view; it lands an audit row naming the operator; the balance is still
`SUM(delta)` with no compensating update anywhere.

**SC-4f · The usage statement.** A monthly per-member, per-app breakdown the
customer can export — needed for their own accounting, and in India it feeds
their input-credit claim. Read from `usage_rollup`, not raw events, so it stays
correct after the 90-day raw retention window. **Done when:** the statement
totals equal the period's ledger consumption; an org with BYOK sees consumption
labelled "not billed — your key" (§3.4).

**SC-4g · Discount codes, and the ₹0 checkout *(owner directive 2026-08-18,
D42)*.** Customer zero (Fracktal, D36) must complete the ENTIRE purchase flow —
package selection, checkout, invoice, entitlement/credit grant — while paying
nothing, and the mechanism is a real product feature, not a test backdoor: an
operator-issued **discount code** applied at checkout. Mechanics that follow
from the rails rather than from preference: **(1)** Razorpay cannot capture ₹0,
so a 100%-off checkout completes **without a provider order** while writing
exactly the records a paid one writes — the same subscription/`credit_ledger`
rows, referenced to the coupon **redemption id** where a paid purchase carries
the payment id; **(2)** the capture leg itself is rehearsed with Razorpay
**test-mode keys**, never with the coupon — the coupon path deliberately never
reaches the provider, so the two together cover the whole flow and neither
alone does; **(3)** the tax invoice shows the discount as a line (gross ·
discount · ₹0 net) rather than suppressing the document — the paper trail is
the point of the exercise (SC-5b's mandatory fields still apply); **(4)** codes
are operator-issued in the Customer Console and scoped — org, expiry, max
redemptions, percent-or-fixed — with an audit row on issue and on redemption,
and redemption idempotency sits beside SC-4a's webhook idempotency (append-only
ledger, idempotent on the reference). 🔴 **Issuing a code against a live org is
the same owner gate as SC-4e's adjustments.**

> ### SC-4g completed — 2026-08-18
>
> The clause above states the *policy* and was silent on five things a build
> must decide. All five are answered here as **agent-proposed defaults the owner
> may overrule** (D16/D17), each derived from the code rather than from
> preference. A NO-GO dispatch audit named exactly these gaps.

**(i) How a code is stored: the split-key pattern, reused verbatim.** A discount
code is a **bearer secret that grants value**, so it is stored the way this
service already stores bearer secrets — `customer_console/keys.py`: a **prefix
in the clear, indexed** and a **SHA-256 hash of the secret**, never the secret
itself. Format **`cc_disc_<prefix>_<secret>`**, minted through the same
`mint_key`/`split_key`/`verify_secret` seam (`keys.py` already parameterises the
env segment for `live`/`depl` — `disc` is a third **value**, not a third
implementation; add it as a named constant beside `ENV_DEPLOYMENT`,
`keys.py:42`, for the reason stated there).
> ⚠️ **Format corrected 2026-08-18** during the repair round's anchor
> re-verification. It read `disc_<prefix>_<secret>`, which **the named seam
> rejects**: `split_key` returns `None` unless the first segment is exactly
> `"cc"` (`keys.py:136-138`), and `_canonical_prefix` composes `cc_{env}_{prefix}`
> (`:59-60`). The old format could only have been built by editing the shared
> key seam — i.e. by doing the opposite of what this clause's own sentence
> ("a third value, not a third implementation") asks for.
Consequences that make this the right call rather than a stylistic one: a
database disclosure hands over no working codes; lookup is one indexed read, not
a scan-and-compare; and `split_key`'s left-split discipline (the secret's
alphabet contains `_`) is inherited rather than re-derived wrongly.
*Trade-off, stated:* the operator sees the full code **once**, at issue, exactly
as with a `cc_live_` key. A code that must be re-read later is re-issued, not
recovered. That is deliberate — a recoverable discount code is a shared password
in a database somebody exports.

**(ii) The two tables.** On the **Customer Console** ladder,
`infra/customer_console/`, **the number taken by listing that directory at build
time and re-checked at merge (R1)** — the same migration territory as CP-9's
order tables, and the two may share one migration. *(On 2026-08-18 the ladder
was 001–006 and the next free number was 007; that is a measurement, not an
instruction.)*

```sql
discount_code(id UUID PK,
  prefix TEXT UNIQUE NOT NULL, code_hash TEXT NOT NULL,   -- keys.py, (i)
  label TEXT NOT NULL,                                    -- what it is FOR, on the audit row
  organization_id UUID REFERENCES organization(id),       -- NULL = open (any org)
  kind TEXT NOT NULL CHECK (kind IN ('percent','fixed')),
  percent_bp INT CHECK (percent_bp BETWEEN 1 AND 10000),  -- basis points; 10000 = 100%
  amount_paise BIGINT CHECK (amount_paise > 0),           -- for kind='fixed'
  CHECK ((kind = 'percent') = (percent_bp IS NOT NULL)),
  CHECK ((kind = 'fixed')   = (amount_paise IS NOT NULL)),
  max_redemptions INT NOT NULL DEFAULT 1 CHECK (max_redemptions > 0),
  expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
  created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());

discount_redemption(id UUID PK,
  discount_code_id UUID NOT NULL REFERENCES discount_code(id),
  organization_id UUID NOT NULL REFERENCES organization(id),
  order_id UUID NOT NULL REFERENCES payment_order(id),
  gross_paise BIGINT NOT NULL, discount_paise BIGINT NOT NULL,
  net_paise BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (discount_code_id, order_id));
```

**Single- vs multi-use is `max_redemptions`, defaulting to 1** — the safe
default, because the dangerous mistake is a code intended for one customer that
turns out to be reusable, never the reverse. **Percent is basis points**
(`percent_bp`), not a float: 33⅓% has no float representation and a discount
that rounds differently on two reads is a dispute. **The count that enforces the
cap is `COUNT(discount_redemption)`, never a mutable `times_used` column** —
the same append-only argument `credit_ledger` makes, and the same reason.
**Concurrency:** the redemption path takes the advisory-lock idiom already
proven for seats (`store.lock_seat_capacity`, `pg_advisory_xact_lock`) before
counting, plus `UNIQUE (discount_code_id, order_id)` so one order can never
redeem one code twice. A check-then-insert without the lock is the seat race CP-2b
had to fix; do not re-introduce it here.

**(iii) Percent-or-fixed against WHICH base: the PRE-GST taxable base.**
`discount_paise` applies to `gross_paise`; **GST is computed on the discounted
base** (`taxable_paise = gross − discount`, then `gst_paise` on
`taxable_paise`). That is standard GST invoice practice — a discount recorded on
the invoice reduces the taxable value — and it is the only reading under which
**100% off yields taxable 0 → GST 0 → total 0**, which is what D42 requires. The
alternative (discount after tax) would leave GST payable on a zero-rupee sale,
i.e. a bill for customer zero. The columns live on `payment_order` (CP-9 §9.2)
so gross · discount · net · GST are recorded per order whether or not a document
is ever rendered.
**A fixed-amount code larger than the gross is clamped to the gross**, never
negative — a negative order total is a refund path and this is not one.

**(iv) The ₹0 path, and the equivalence fence.** A 100% redemption completes
with **`provider='none'` on `payment_order`, no `provider_order_id`, and zero
provider calls**, and then calls **the same `payments.fulfil()`** the capture
path calls (CP-9 §9.6), with `reference = redemption:<uuid>` where a paid order
passes `order:<uuid>`.

> ⚠️ **The fence as first written was UNSATISFIABLE and is repaired in CP-9
> §9.6 — read it there, it is the authority** *(2026-08-18, B4)*. "The only
> difference is the reference" is false against the schema: `seat_grant.id`,
> `seat_assignment.id` and `credit_ledger.id` are `gen_random_uuid()` defaults
> and every timestamp defaults to `now()`, so **two runs of the same path
> already differ**. §9.6 now names (a) the **carrier** — `seat_grant.reason`,
> holding `<reason>:<ref>`, i.e. `purchase:order:<uuid>` vs
> `discount_redemption:redemption:<uuid>` — (b) the **excluded classes**
> (surrogate ids; `created_at`/`updated_at`/`effective_from`/`assigned_at`), and
> (c) the one difference that is **expected and asserted rather than excluded**:
> `org_subscription`'s `provider` / `provider_customer_id` /
> `provider_subscription_id` are set on the paid path and **NULL** on the ₹0
> one. ⚠️ **Not `'none'` there** — that value belongs to `payment_order.provider`;
> `org_subscription.provider` is `CHECK (provider IN ('razorpay','manual'))`
> (`001_customer_console.sql:163`).

**Fence — this is SC-4g's central one:**
`test_the_free_path_and_the_paid_path_write_identical_records` runs both paths
over an identical basket and compares the written `org_subscription`,
`seat_grant`, `seat_assignment` and `credit_ledger` rows **field by field**,
minus the excluded classes, asserting **exactly two** differences (the reason
prefix and the provider columns). A third difference
means one of the two paths is not the product — which is precisely the failure
D42 exists to prevent. **A partial code (e.g. 50%) routes the REMAINDER through
CP-9's provider path**: one order, `discount_paise` recorded, `total_paise > 0`,
provider `razorpay`, fulfilment on capture. There is no second flow.

**(v) The ledger vocabulary, named — so the three-way clause is a real fence.**
`credit_ledger.reason` is bare `TEXT` today (`001_customer_console.sql:283-284`)
and `POST /credits/grant` accepts any string, so *"distinguishable a year
later"* was unenforceable. The vocabulary, defined once in
`customer_console/credits.py` as `LEDGER_REASONS` and imported by every writer:

| `reason` | `ref` | Written by |
|---|---|---|
| `usage` | `<request_id>` | the Router's meter (**shipped**, `store.py:379`) |
| `purchase` | `order:<uuid>` | CP-9's fulfilment, paid path |
| `discount_redemption` | `redemption:<uuid>` | CP-9's fulfilment, ₹0 / partial path |
| `adjustment` | operator-supplied note ref | SC-4e goodwill |
| `grant` | operator-supplied | `POST /credits/grant`, non-commercial grants |

**Fence:** a structural test that every `store.add_credit(...)` call site passes
a member of `LEDGER_REASONS` — **real in this slice**, since it reads call sites
rather than rows. ⚠️ **The data test that the three commercial reasons are
pairwise distinguishable by `(reason, ref)` is scoped to WHEN PACKS LAND**
*(2026-08-18, B3)*: CP-9 §9.6 writes **zero** `credit_ledger` rows on the
subscription path at launch, so that test would pass over an empty table — the
disarmed-gate shape CP-3 already cost us once. Launch distinguishability rides
`discount_redemption` + `seat_grant.reason` (done-when 6). The vocabulary
**is** defined now, because the two tables must say the same word for the same
event on the day packs arrive. ⚠️ **Deliberately NOT a `CHECK` constraint in this slice, and the reason is
R6:** `/credits/grant` accepts free-form reasons today, so an expand-phase
migration must not reject rows the running code can still write. Narrowing
`CreditGrantRequest.reason` to the enum comes first; the `CHECK` is a later
contract-phase migration. Recorded here so the omission reads as a decision
rather than as forgetting.

**(vi) The invoice: what this slice owes, and what it honestly does not.**
Clause 1 above says the tax invoice shows gross · discount · ₹0 net.
**The amounts are recorded by this slice** (on `payment_order` and
`discount_redemption`, per (iii)). **The invoice DOCUMENT is not**, and cannot
be: **no `invoice` table exists anywhere in the tree** (verified repo-wide
2026-08-18), and SC-5c's gapless-per-financial-year serial constrains the data
model — a serial allocated at *issue* and never at *attempt*, immutable once
issued, corrected only by credit note. Inventing that model inside a checkout
slice is how a year of invoices turns out non-compliant at once (SC-5's own
warning).
**Therefore: SC-5b + SC-5c are named HARD PREREQUISITES for the DOCUMENT and are
explicitly NOT prerequisites for the CHECKOUT.** Customer zero can complete the
whole purchase — package selection → code → ₹0 checkout → active entitlements —
with every amount recorded, and the rendered invoice follows when SC-5b/5c land
against the same recorded numbers.
> 🔴 **Launch-sequencing note for the OWNER, stated plainly rather than buried:**
> **the first purchase can complete before the first tax invoice can be issued.**
> Under GST the invoice is our obligation as supplier of record (D38), so
> SC-5b/5c must land before we take money from a customer who is not us. For a
> ₹0 first purchase by customer zero (D36) nothing is collected, which is what
> makes running the rails ahead of the document safe **in that case and only
> that case**.

**Done when:** *(clauses 1–8 **✅ MET 2026-08-18** by CP-9's substrate half —
`tests/unit/test_customer_console_payments.py`, 110 tests against a real
Postgres 16, 0 skipped. Clause 9 is the owner-gated rehearsal and is **NOT**
met, as it says.)*
1. ✅ A **100% code** takes an org from package selection to **active entitlements**
   with **zero provider calls** and a `discount_redemption` row carrying
   gross · discount · net; the order is `captured` with `provider='none'` and
   `total_paise = 0`. — `test_a_hundred_percent_code_completes_with_zero_provider_calls`
   asserts the provider-call **count** does not move across the redemption,
   that `provider_order_id` is NULL afterwards, and that the seat grant and
   subscription actually landed.

   🔴 **Named residual — and it is NOT harmless. Corrected 2026-08-18
   (verification finding F2).** This read *"a Razorpay order is left unpaid to
   expire there … the harmless orphan direction"*. The order created *before*
   the code was presented **did** call the provider, and detaching it here does
   not retract it **at Razorpay**: order #1 keeps a live payment link. **The
   orphan has a PAID direction.** A customer who pays it produces a
   signature-verified capture whose `order_id` matches no row in the Console —
   measured against the built code: **200** `{recorded: true, fulfilled:
   false}`, a `payment_event` row with `order_id` NULL, **nothing granted**, and
   the 200 stops Razorpay retrying. **A capture with no matching order is money
   received with nothing granted.**

   Unreachable in the substrate slice (nothing writes `attempted`, there is no
   checkout UI, no Razorpay account exists), and reachable the moment **SC-4a**
   renders a payment link — which is why the answer is split three ways:
   `customer_console.md` **CP-8's reconciliation owns the NULL-`order_id`
   receipts**; the webhook's unknown-order arm alerts at **ERROR** with the
   amount and both provider identifiers
   (`test_a_capture_with_no_matching_order_is_kept_and_alerted_at_error`); and
   **SC-4a must never expose a replaced order's payment link** (stated in its
   done-when). The full argument is in CP-9's build box, decision 2.
2. ✅ `test_the_free_path_and_the_paid_path_write_identical_records` passes and
   **goes red** under a deliberate mutation of either path (per (iv)). —
   Red-first evidence: granting `quantity + 1` on the free path fails with a
   third difference, `('seat_grant', 'quantity_purchased')`.
3. ✅ A **partial code** routes the remainder through CP-9's provider path — one
   order, discount recorded, fulfilment on capture, not a second flow. — And
   the provider order is **replaced** for the discounted amount: one created
   for the pre-discount total would collect it, overcharging the customer and
   failing our own amount check.
4. ✅ **The refusals PARTITION — three distinct reasons and one collapsed shape.**
   *(Rewritten 2026-08-18, B2. As written this clause demanded five distinct
   reasons **and** unknown ≡ wrong-org in the same sentence, which is a
   contradiction: an implementer satisfies one half and quietly drops the
   other.)* The partition is on **what this org is entitled to see**:
   - **{`expired`, `revoked`, `exhausted`} — three DISTINCT reasons**, given
     only for a code this organization is entitled to see at all: one bound to
     it (`discount_code.organization_id = <caller org>`) or an **open** code
     (`organization_id IS NULL`). The caller has already proven possession of
     the secret, so naming *why* it failed tells them nothing they could not
     learn by asking us, and an admin told "this code expired on the 3rd" does
     not file a support ticket.
   - **{`unknown`, `wrong-org`} — ONE indistinguishable refusal shape**: same
     status, same body bytes, same latency class, naming nothing. These two are
     collapsed precisely because telling them apart is the **non-oracle rule**
     (CP-3's lesson): a distinguishable "wrong org" answer confirms that a code
     exists and belongs to somebody, which is a membership test over other
     tenants' data run from a customer's own key.
   **Fence:** `test_the_five_refusals_partition_three_and_two` — asserts the
   three named reasons are distinct strings **and** that the unknown and
   wrong-org responses are byte-identical. Both halves in one test, so the
   contradiction cannot reappear by satisfying one of them.
   *Built 2026-08-18:* the collapsed shape is `404 {"detail": "no such
   discount code"}` and it covers **four** cases, not two — malformed, unknown
   prefix, wrong secret, wrong org — because a caller must not be able to tell
   those apart either. Red-first evidence: naming the wrong-org case fails the
   fence. ⚠️ **A sixth refusal exists and is deliberately outside the
   partition:** presenting a *second, different* code against an order that
   already carries one is `409 {"reason": "already_discounted"}`. Stacking is a
   commercial decision nobody has taken, so it is refused rather than invented
   — and it is a statement about the **order**, not about the code, which is
   why it is not one of the five.
5. ✅ **Redemption is idempotent**: re-submitting the same code against the same
   order redeems **once** (`UNIQUE (discount_code_id, order_id)`), and a
   concurrent double-redeem of a `max_redemptions = 1` code yields one success
   and one refusal — proven under the advisory lock with a real two-connection
   race, not a mock (R8, and the CP-2b race precedent). — Raced **ten times**,
   two threads on a barrier, against two different orders: `sorted(status) ==
   [200, 409]` and exactly one `discount_redemption` row each time.
6. ✅ **A discounted purchase is distinguishable from a paid purchase and from an
   SC-4e adjustment — on `discount_redemption` + `seat_grant.reason`.**
   *(Re-pointed 2026-08-18, B3. This clause named the `credit_ledger`
   three-way and was **vacuous at launch**: CP-9 §9.6 writes **zero**
   `credit_ledger` rows on the subscription path, because §9.1 sells no credit
   packs. A test over an empty table passes for the wrong reason, which is
   worse than no test.)* What carries it at launch:
   - a discounted purchase has a `discount_redemption` row referencing the
     order; a paid purchase has none; an adjustment has no order at all;
   - `seat_grant.reason` carries `<reason>:<ref>` from the ONE vocabulary —
     `purchase:order:<uuid>` vs `discount_redemption:redemption:<uuid>`
     (CP-9 §9.6, B4's carrier).
   **Fence:** `test_a_discounted_grant_is_tellable_from_a_paid_one_a_year_later`
   — the three cases seeded, then classified **from the stored rows alone**,
   with no access to how they were created.
   ⚠️ **The `credit_ledger` half of (v) is scoped to WHEN PACKS LAND** (dated
   2026-08-18): the vocabulary is defined now and the **structural** call-site
   fence is real now; the *data* test that the three commercial reasons are
   pairwise distinguishable becomes meaningful with the first pack row and is
   part of that ticket, not this one.
7. ✅ **Issue and redemption each land a `control_audit` row** naming the operator
   and the code's **prefix** — never its secret, asserted over the log/audit
   record rather than by reading the code (CP-3's fence discipline). — The two
   rows carry **different actors**: `operator` on issue, `organization` on
   redemption. `_audit`'s hard-coded `actor='operator'` would have
   misattributed the first act a customer's own credential ever performed on
   this service, which is the one class of write an audit trail most needs to
   tell apart.
8. ✅ **R8** — the constraints, the uniqueness, the count-based cap and the
   fulfilment transaction run against a real Postgres 16 via
   `tests/unit/_customer_console_ladder.py`; the suite joins §7's command block
   and `pr-check.yml`'s skip-guard in the same PR. — **110 tests, 0 skipped**;
   the Console skip-guard hand-list went **6 → 7**, and the suite reads both
   the workflow and both owning specs so its own name cannot be dropped
   silently.
9. 🔴 ⚠️ **NOT MET, and not claimed — Clause 2 of the policy above (the
   test-mode capture rehearsal) is NOT met by an agent** — creating the
   Razorpay account is owner-side even in test mode (`customer_console.md` §8
   gate 3, CP-9 §9.7). The agent-reachable half is the whole thing against
   CP-9's **fake provider with a real HMAC signer**; the rehearsal is scripted
   and handed over. **A PR claiming this clause met is claiming something it
   did not do.** *(2026-08-18: the substrate half was built and this clause is
   the one thing in SC-4g it did not touch. What the owner needs, once an
   account exists: set the three `CUSTOMER_CONSOLE_RAZORPAY_*` variables, POST
   a real order, pay it in test mode, and confirm the webhook fulfils exactly
   once — the same assertions
   `test_two_different_event_ids_for_one_order_fulfil_exactly_once` makes
   against the fake.)*

🔴 **OWNER-GATE:** issuing a code against a **live** organization (`work_plan.md`
§6(g), the SC-4e adjustment gate class). Authoring codes and running both paths
against fixtures is **AGENT-SAFE**.

### SC-5 — Billing management: the documents, and who issues them *(D38, 2026-08-13)*

> ⚠️ **This section corrects a wrong assumption carried since §1.** The non-goals
> called invoicing and tax *"the processor's job (§4.3)"*. **That does not survive
> Indian GST.** Razorpay **collects money** and issues a *payment receipt*; a **tax
> invoice** is the obligation of the **supplier of record**, which is us. It must
> carry OUR GSTIN, our invoice serial, the SAC code, the place of supply and the
> CGST+SGST vs IGST split — none of which a payment processor is in a position to
> assert on our behalf. Treating this as outsourced is how a year of invoices
> turns out to be non-compliant at once.

**SC-5a · One billing home, two revenue streams.** The admin sees a single
chronological list at `/settings/billing`; underneath, **subscriptions and credit
purchases are different documents on purpose**, because they are different taxable
events. A seat subscription is a recurring supply billed monthly; a credit pack is
a discrete prepaid sale whose tax point is its own. Forcing them onto one document
means either delaying the credit invoice to the cycle boundary or back-dating the
subscription — both wrong. **Done when:** the list interleaves both types in date
order with type, period, amount and status; each row opens its own document; an
org with no subscription still sees its credit invoices, and vice versa.

**SC-5b · The tax invoice itself.** Generated by us, from `invoice` plus the org's
billing profile. Mandatory fields: our legal name + GSTIN + address · the
customer's legal name + **GSTIN** + address · a **unique serial** (SC-5c) · date ·
**SAC code** for software services · description, taxable value, rate ·
**place of supply** · the **CGST+SGST (intra-state) or IGST (inter-state)** split
derived from the customer's `billing_state` against ours · total in words and
figures. **Done when:** an intra-state customer's invoice splits CGST/SGST and an
inter-state one shows IGST, driven by the stored state and never by a guess; an
org without a GSTIN gets a valid B2C invoice rather than a blank field; the
taxable value plus tax equals the amount actually collected, to the paisa.

**SC-5c · Serial numbers, immutability, and credit notes.** Three rules that are
law rather than preference, and all three constrain the data model:
1. **Gapless and sequential per financial year** (April–March). Not
   `gen_random_uuid()`, not a timestamp, and **not** the processor's id. A
   reserved-then-abandoned number is a gap, so the serial is allocated at
   *issue*, never at *attempt*.
2. **An issued invoice is never edited.** Not the amount, not the GSTIN, not a
   typo in the address.
3. **Corrections are credit notes**, referencing the original. That is the only
   lawful way to reverse or amend, and it is also the honest one — SC-4e's
   goodwill adjustment on the ledger and a credit note on the invoice are two
   halves of the same act.
**Done when:** issuing N invoices across a year-boundary produces two unbroken
series; a failed payment consumes no serial; an attempt to mutate an issued
invoice fails at the storage layer, not merely in the UI; a credit note is
reachable from the invoice it corrects.

**SC-5d · Download and history.** Per-invoice **PDF** (the artefact their
accountant wants), plus a **period export** — all documents for a chosen range as
a zip, and a CSV summary for reconciliation. **All periods, forever**; §4.1b's
mirror exists so this needs no processor round-trip and keeps working if we ever
change processor. **Done when:** an invoice downloads identically a year after
issue; the CSV totals equal the sum of the PDFs; nothing in the path calls the
processor.

**SC-5e · The billing profile — the admin's own controls.** Editable by the
billing admin: legal entity name · **GSTIN** · billing address and **state**
(this decides the tax split, so it is not cosmetic) · billing contact email
(where SC-4d's alerts and invoices go, distinct from the owner's login) · an
optional **PO / cost-centre reference** printed on invoices, which mid-size
customers' finance teams require. **Done when:** changing the state changes the
tax treatment of the NEXT invoice and never a past one; changing the billing
email redirects delivery without changing who can sign in; every change lands an
audit row, because a GSTIN edit is a tax-relevant act.

**SC-5f · Payment methods and mandates.** View the mandate backing auto-top-up
(SC-4b) and subscription renewal, its cap, and its next debit; replace or cancel
it. ⚠️ Card and UPI credentials never touch us — this surface renders what the
processor reports and links out to its own flows. **Done when:** cancelling a
mandate disables auto-top-up in the same act rather than leaving it armed and
failing; no card data is stored or logged.

**SC-5g · E-invoicing (IRN) readiness.** Mandatory once aggregate turnover crosses
**₹5 crore** (`saas_operations_doctrine.md` §3.1). Not built now — but SC-5b's
field set is deliberately the IRN field set, so the change is registering with
the IRP and adding the QR, not re-modelling invoices. **Done when:** the invoice
model carries every IRN-required field even while unused.

> ### GST on prepaid credits — working assumption: **taxed at purchase** (owner, 2026-08-13)
>
> **Assumption adopted:** credits are a **single-purpose voucher**, so GST at 18%
> applies **when the pack is bought**, not when the credits are consumed.
>
> **Why that reading fits.** A voucher is single-purpose when the supply it will
> be redeemed against is identifiable at issue. Ours is: one supplier (us), one
> service (AI usage), one SAC, one rate. Nothing about how the customer later
> spends the credits changes the tax treatment — which is precisely the condition
> that makes the tax point the issue date rather than the redemption date.
>
> **Two consequences that must be built in, not discovered:**
> 1. **Consumption is NOT a taxable event.** SC-4f's monthly usage statement is
>    therefore an **informational statement, not a tax invoice** — it must not
>    carry an invoice serial, must not show a tax line, and must say so on its
>    face. Invoicing usage as well would tax the same supply twice.
> 2. **Unused and expiring credits need no adjustment.** Tax was discharged at
>    purchase, and D32.6 already makes credits non-refundable in cash — so
>    expiry is not a credit-note event. That consistency is a point in favour of
>    the reading, not a coincidence.
>
> ⚠️ **Still confirm with a CA before the first tax invoice goes out** — but the
> build is **no longer blocked**: SC-4a may now issue a tax invoice under this
> assumption. The design is deliberately robust if the answer turns out to be
> redemption-based: the tax point is recorded **per document** (`invoice.tax_point`
> = `purchase | redemption`) rather than assumed in code, so a change is a policy
> flip plus credit notes over the affected period — not a re-model of invoicing.
>
> **Do not treat "probably" as settled** in customer-facing copy. Invoices say
> what was charged; they do not explain the reasoning.

## 3. Access

The console requires the customer-admin capability (`admin:members:read` floor —
same floor as `/admin`, `routes/admin/_common.py`), inside the `core` module, on
the org resolved by the session — never from request input
(`user_management_contract.md` R11/R3). All queries run through the tenant-bound
seam (R5); the console must be impossible to render cross-org by construction.

## 4. Open design items (engineering, not owner)

1. **`usage_event.module_slug` attribution rule** — a chat agent that reads Email
   and writes CRM burns credits under which module? Owned by **MT-3** (the metering
   hook decides at write time); SC-1b consumes whatever rule MT-3 records. The rule
   must be written into `saas_multitenancy.md` §3.2 when MT-3's contract is drafted.
2. **Seat↔identity join** — `user_module_seat.user_id` (control plane UUID) vs the
   email-keyed tenant plane; the console needs the hop for the assignment picker.
   Owned by MT-1a's identity model (`user_identity`/`org_membership`, migration 159).

## 5. Verification

> ⚠️ **Rewritten 2026-08-18.** The block that stood here named
> `tests/unit/test_billing_console*.py` — **no file with that prefix has ever
> existed**. The real prefix for this system's suites is
> `test_customer_console_*.py`, and the two database variables the R8 suites
> gate on were unnamed, so a reader following this section would have run
> nothing and seen green.

```bash
# Server side — this console is a CLIENT of the Customer Console (D32.2), so its
# server-side acceptance runs in WS-31's suites.
# ⚠️ These SKIP THEMSELVES without a real Postgres (R8) — and a skipped R8 test
# proves nothing (CP-3: CI silently skipped every DB-gated fence while green):
#   export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
uv run pytest tests/unit/test_customer_console_seats.py \
              tests/unit/test_customer_console_credits.py \
              tests/unit/test_customer_console_sql.py \
              tests/unit/test_customer_console_api.py \
              tests/unit/test_customer_console_lifecycle.py
# SC-4g's own suite (and SC-4a's, when its surface lands), ✅ created with
# CP-9's build slice 2026-08-18 and added to `customer_console.md` §7 AND to
# pr-check.yml's hand-maintained skip-guard list in the same PR — that list
# discovers nothing, so the suite additionally reads both and fails if its own
# name is ever dropped:
uv run pytest tests/unit/test_customer_console_payments.py

# ⚠️ SECOND, DIFFERENT database for any tenant-side suite —
# `TENANT_LADDER_DATABASE_URL`, deliberately NOT `DATABASE_URL`, which would arm
# two tenant-coverage tests that fail by construction (WS-29 MT-1b/MT-1c's
# gates, not this console's). See `customer_console.md` §7.

# The surfaces themselves.
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

The two-org fixture from MT-1i is reused for every SC-1 read test.

## 6. Sequencing

**Corrected 2026-08-18.** The old line read *"MT-2 tables → SC-1a → …
(SC-4 with MT-4)"* and is obsolete on both ends: D32 made this console a
**client** of the Customer Console, whose subscription/seat/ledger tables are
**built**, and MT-4's payment work is now **CP-9**.

Live order: **SC-1b/SC-5's billing view (merged, `f1fcca4f`)** → **SC-4a's
checkout over CP-9** (+ **SC-4g** in the same slice — D42.1: *"do not build a
purchase slice that treats codes as a later add-on"*) → SC-2 seat writes →
SC-1a's Centers panel (needs MT-2's entitlement surface) → SC-3 → SC-5b/5c's
invoice documents → SC-4b/4c/4d/4f.

SC-3's *flow* can be built against the existing tables alone — it is the piece
that lets you sell before billing automation exists. **SC-5b/5c are hard
prerequisites for the invoice DOCUMENT, not for the checkout** (SC-4g (vi)); the
owner-facing consequence is recorded there.

⚠️ **The checkout dispatches in TWO slices, not one** *(2026-08-18, the
GO-NARROWED re-audit).* The **substrate half** — CP-9's tables, seam, webhook,
fulfilment, order reads, and **SC-4g (i)–(v) server-side** — is dispatchable
now, entirely against a real Postgres and a fake provider. The **surface half**
— SC-4a's checkout UI and its two write proxies — is **held back** until B7's
gate is chosen, because that decision reaches the tenant plane's capability
vocabulary and must not ride in on a payments PR. The full split, with the
other two held-back items, is written once in `customer_console.md` §6's
sequencing note; do not restate it here, follow it.

## Gate labels

Building every surface: **AGENT-SAFE**. Fulfilling a change request, editing any
live org's entitlements, granting the console to a real customer admin:
**OWNER-GATE** (work_plan.md §6).
