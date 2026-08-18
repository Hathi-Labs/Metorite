# Subscription Console — the customer-facing billing surface (WS-30)

**Status:** ◐ **SC-5's billing view BUILT 2026-08-14** on
`claude/multi-tenancy-ai-metering-nr8zbj`, unmerged and **not reachable from the
nav** — the Control Plane it reads from is undeployed (WS-31: "where it runs is
an open owner decision"), so the page fails closed with *"Billing is not
configured for this deployment"*. It stays URL-only until the Control Plane has
somewhere to run; promoting it into the sidebar would hand every customer admin
a menu item that always errors, in our internal environment-variable
vocabulary. That is CLAUDE.md §4's ship-dark, not an oversight — a previous
commit added the nav entry reading it as one, and it was reverted.
**Everything else here is still SPEC — nothing built.** ·
**Date:** 2026-08-09 · status header corrected 2026-08-14 (R4: it still read
"nothing built" after the console shipped) · verified against code
2026-08-09 (repo-wide grep: zero hits for `module_catalog`, `org_module_entitlement`,
`user_module_seat`, `ModuleGate`, `entitlement_mask` — MT-2's substrate does not
exist yet, so nothing here is dispatchable before MT-2's tables land) ·
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

**Non-goals:** the payment *provider* integration itself (Razorpay SDK wiring,
MT-4) — SC-4 specifies the customer-facing flow over it · the Operator Console,
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

Razorpay-only (D19.5) behind the `payment_provider` seam. The mechanics of the
ledger are `customer_console.md` §3.4; this is the part a customer touches.

**SC-4a · Fixed packs, self-serve (D37.1).** A short ladder of pre-priced packs
rather than a free-text amount. Three reasons, in order of importance:
**(1)** every pack is priced **under ₹15,000**, so a repeat purchase clears the
RBI e-mandate AFA threshold and never demands an OTP from a customer who is
already out of credits mid-workflow (`saas_operations_doctrine.md` §3.2);
**(2)** a ladder makes price-per-credit legible, where an arbitrary amount makes
the customer do arithmetic to know if they are getting a good deal; **(3)** it is
the smallest thing that can be built correctly. Custom amounts are a later
question, and only if a customer asks.
**Done when:** buying a pack moves the balance by exactly the pack's credits and
lands **one** `credit_ledger` row referencing the provider payment id; a
duplicate webhook for the same payment credits **once** (the ledger is
append-only, so idempotency is on the reference, not on a mutable balance); a
failed payment credits nothing and says so.

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

`uv run pytest tests/unit/test_billing_console*.py` (to be created per slice) ·
`cd workbench/control_plane && npx tsc --noEmit && npx vitest run` · the two-org
fixture from MT-1i reused for every SC-1 read test.

## 6. Sequencing

MT-2 tables → SC-1a → (SC-1b after MT-3's ledger) → SC-2 → SC-3 → (SC-4 with
MT-4). SC-3's *flow* can be built against MT-2 alone — it is the piece that lets
you sell before billing automation exists.

## Gate labels

Building every surface: **AGENT-SAFE**. Fulfilling a change request, editing any
live org's entitlements, granting the console to a real customer admin:
**OWNER-GATE** (work_plan.md §6).
