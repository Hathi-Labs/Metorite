# Subscription Console — the customer-facing billing surface (WS-30)

**Status:** ◐ **The billing view is MERGED and live-but-inert; the checkout's
SERVER SIDE is BUILT and its SURFACE is now BUILT TOO — SC-4a's narrowed LAUNCH
SLICE (the ₹0 / discount path) shipped 2026-08-19 on `ws-30-sc4a-surface`, dark
behind `purchaseEnabled`. Launch done-whens 1, 2, 5a, 6, 7, 8 and 9 are met;
3, 4 and clause 5's attempt half stay DEFERRED to SC-4h, which is still unbuilt
because there is no browser→provider hand-off anywhere in the tree. F-I's
prerequisite CLOSED the same day — WS-31 shipped `GET /billing/catalog` (§6 item
(f)), so done-when 1 has a data source and the page renders the ladder from it
rather than from TypeScript. The slice was then INDEPENDENTLY VERIFIED and
ADVERSARIALLY REVIEWED, FAILED on one blocker plus 2×P1 + 3×P2, and REPAIRED on
the same branch the same day — see the repair-round box below. No done-when mark
moved: the repairs close a cross-tenant purchase path, a misleading 401 and
three smaller defects, none of which un-met anything already claimed.**
*(Header rewritten 2026-08-18 — R4 — extended
2026-08-19 with the NO-GO answer, again the same day when the launch slice
landed, and again for the repair round. What stood here before 08-18 was wrong on three counts, each corrected
below rather than quietly deleted: it called the billing view "unmerged" on a
branch, it said "everything else … nothing built", and it said dispatch waits on
MT-2's tables.)*

**SC-1a re-shaped 2026-08-20 — R4** *(WS-31 `ws-31-seats-mvp`)*: the panel is
**split** into a 🟢 dispatchable **seats block** (purchased / assigned / available
/ oversubscribed, rendered from the new customer seats read `GET /me/seats` —
`customer_console.md` §6 item (g) / done-when 19, `can_pay` door) and an ⏸
**entitlement half** (Centers/add-ons `active | trial | locked`) that stays
**DEFERRED behind MT-2 (unbuilt)**. The old done-when — *"driven entirely by
`/auth/me`'s `modules` + one `GET /billing/summary`"* — was NO-GO: `/auth/me`
modules is MT-2 and `GET /billing/summary` is the Operator / cross-org read. See
SC-1a below.

**SC-4a's LAUNCH SLICE — ✅ BUILT 2026-08-19 (WS-30).** What landed, in the
architectural order rather than the diff order:
- **`billing:purchase`** joins `acb_auth.permissions.CAPABILITIES`
  (`permissions.py:126-163`) and is seeded onto the `default` organization's
  `admin` (and `agent_service` for table-consistency) by
  **`infra/postgres/178_billing_purchase_permission.sql`** — number taken by
  listing `infra/postgres/` at build time and re-checked across every branch
  (R1; the ladder topped at 177 on this date). `manager` is deliberately not
  seeded. Fence: **`tests/unit/test_billing_purchase_capability.py`** (10 tests,
  0 skipped, real Postgres 16 through `_tenant_ladder.py`), which finds the
  migration **by content** rather than by number so a merge renumber does not
  read as a broken seed. Red-first: deleting the `admin` insert fails
  `test_admin_holds_it_after_the_ladder_replays` and
  `test_a_re_replay_grants_nothing_twice`; copying 133's admin+manager set
  fails `test_manager_and_member_do_not` on `'manager' holds 'billing:purchase'`.
- **Four BFF routes** under `workbench/control_plane/src/app/api/billing/`,
  sharing `_console.ts`: `POST orders`, `POST orders/[id]/redeem` (B7's two
  writes), `GET orders/[id]` (5a's re-read, gated the same way — it exposes an
  order's state and a code's prefix, which is not the summary's data) and
  `GET catalog` (session-gated, the price list is the same for every customer).
  No new environment variables: the same `CUSTOMER_CONSOLE_URL` /
  `CUSTOMER_CONSOLE_ORG_KEY` and the same fail-closed 503.
- **The checkout UI** — `src/app/settings/billing/Checkout.tsx` plus the pure
  `lib/checkout.ts` — mounted **only** inside the `data.purchaseEnabled` arm
  (`page.tsx:211`, *re-anchored from `:202` by this change*).
- **Fences:** `src/app/api/billing/checkout.test.ts` (the B7 gate, running the
  real handlers) and `src/app/settings/billing/lib/checkout.test.ts` (done-when
  1's no-transcribed-price scan, done-when 6's arm scan, 5a's arithmetic and
  done-when 9's refusal partition). 106 vitest files / 2305 tests green.
- **NOT built, and not claimed:** any provider hand-off, any payment link, any
  capture. The read proxy's board finding below is untouched by design.

**⚠️ REPAIR ROUND — 2026-08-19, on the same branch.** Independent verification
FAILED the slice on one blocker and adversarial review returned two P1s and
three P2s. All six are fixed; **no done-when mark changes** — nothing above
regressed, and every clause the repairs touch is now held by a fence that was
shown red first.
- **V1 (blocking, verification) — the tenant fence was red from Git Bash on the
  primary dev box**, i.e. from the exact `export TENANT_LADDER_DATABASE_URL=…`
  form this suite's own docstring and §5's command block document.
  `test_the_seed_is_on_the_replayable_ladder` compared a path built with
  `Path(__file__).resolve()` (Windows canonicalises the drive to `C:`) against
  paths built with `os.path.abspath` (`_tenant_ladder.py:65`, which inherits the
  launching shell's case — `c:` from bash). Green from PowerShell and in CI,
  red from bash: *`assert 'C:\…\178_billing_purchase_permission.sql' in {'c:\…'}`*.
  Both sides now go through **`os.path.normcase`**, a no-op on POSIX, so the
  assertion stays an exact **whole-path** comparison — the "a file the ladder
  does not pick up is a seed that never runs" property is unchanged, and it is
  still a path and not a name. Proven from **both shells**: 10 passed / 0
  skipped each.
- **P1 · The BFF gated the CALLER and bound the DEPLOYMENT's key, and never
  compared the two organizations.** `org_placement` is N organizations to one
  deployment; the capability answers *"may this person buy"* while the key
  decides *"whose account is charged"*. The day B7 clause 2's org-provisioning
  ticket parameterizes capability seeding, org B's admin passes
  `requirePurchaser` and buys **into org A** — seats granted in a tenant the
  buyer has no standing in. `requirePurchaser` now keeps the caller's
  `organization.slug` from the gateway `/auth/me` payload (the CALLER's org,
  `admin/me.py:112-133`) and learns the key's from the Console's read-only
  whoami `GET /me` (`main.py:1211-1234`, which returns `slug`), compares them,
  and refuses **403 before the money route** on a mismatch. The copy names the
  caller's own organization and **never the key's** — which tenant owns this
  deployment's billing account is a cross-tenant fact the caller has no need
  for. An unresolvable whoami (unreachable, 401 on a rotated key, no slug) is
  the existing **unavailable 503**, never a pass and never an admission; a
  failed resolution is **not cached**, so one blip is not a dead deployment.
  The successful one is cached module-level — **per worker**, and a key rotated
  to another organization needs a restart. Red-first: *`expected 200 to be 403`*
  on all three gated routes.
- **P1 · An upstream 401 was relayed as a 401**, so a signed-in purchaser was
  told *"Sign in to continue"* when the fault was **this deployment's** key
  being rotated or revoked (`customer_console/auth.py:188-220` — 401 is always
  about the API key). `relayConsole` maps upstream 401 → **502** with
  *"Billing is temporarily unavailable."*, which is the conclusion
  `summary/route.ts:73` reached first; it is mirrored rather than extracted
  because that route is merged code carrying the board finding below, and
  `checkout.test.ts` asserts the two policies still agree so the mirror cannot
  go stale in silence. Red-first: *`expected 401 to be 502`*. The BFF's **own**
  401 still means sign in, and a case pins that too.
- **P2 · The gated list now has a completeness sweep.** `checkout.test.ts` walks
  `src/app/api/billing/**` for `route.ts`, subtracts the two exclusions **by
  name with their reasons**, and asserts the remainder IS the gated set — so the
  fifth billing route cannot arrive unnoticed, which is the property an explicit
  list otherwise gives up (recorded above as given up; now bought back in the
  only form a list can have it). Red-first: a transient `probe/route.ts` failed
  with *`- "probe/route.ts"`*.
- **P2 · `Checkout.tsx`'s `initialFocus` ternary could never fire** — `Modal`
  reads that prop when the dialog mounts, and at that moment the step is always
  `pick`, so the code field did not exist. Replaced by an effect on `step`,
  and the dead prop dropped.
- **P2 · Stale anchor.** `purchaseEnabled` is `main.py:**1438**`, not `:1408` —
  in this file twice, in `page.tsx`'s flip-point comment, and in F-G's own
  re-anchor line, which claimed to have fixed exactly this while being 30 lines
  short. F-G's other two are re-measured in the same edit.
- **Advisory, recorded not fixed (two).** *(1)* `lib/checkout.ts:259`'s 404
  discriminator is `detail.includes("order")` — coupled to the Console's copy
  **across a repository boundary with no fence**: it reads `_NO_SUCH_ORDER =
  "no such order"` and `_NO_SUCH_CODE = "no such discount code"`
  (`customer_console/main.py:1470,1476`). Reword either and a missing ORDER
  silently renders the CODE sentence. The honest fix is a machine-readable
  discriminator in the Console's 404 bodies — a CP-9 change, not a surface one.
  *(2)* `OrderPanel` multiplies `unit_price_paise × quantity` for the per-line
  display, so the header's *"it never computes a total"* was an overstatement;
  it now reads **the browser never NAMES a price to the server, and the display
  arithmetic is integer-exact** — paise are integers, it is never summed into a
  basket and never sent.

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
`tests/unit/test_customer_console_payments.py` (**115** tests, real Postgres 16,
0 skipped) — **independently verified, FAILED, and repaired 2026-08-19**; the
finding that lands *here* is **F2**: done-when 1's *"harmless orphan"* residual
was **false in one direction**. Detaching or replacing the provider order does
not retract it at Razorpay, and paying the stale link is a capture the Console
cannot attribute to any order — **money received with nothing granted**.
Restated in done-when 1, owned by `customer_console.md` **CP-8** (the
NULL-`order_id` receipts), alerted at `ERROR` in the webhook, and answered on
the surface by new **SC-4a done-when 5a**: never expose a replaced order's
payment link. The other four findings (F1/F4/F5/F7) are fence and doc repairs
inside WS-31's tree.

⚠️ **Then ADVERSARIALLY REVIEWED and repaired again — 2026-08-19, one P0 and
two P2s. What lands *here* is the P0, because it changes what SC-4a may
assume.** **A failed payment ATTEMPT is not a failed ORDER.** One Razorpay order
accepts many attempts until one captures, and the shipped webhook drove the
order to `failed` — terminal — on the first `payment.failed`, so the customer's
retry inside the same Checkout captured against a dead order: **₹1,416 taken,
zero grants, a false info line and a 200 that stopped the provider retrying**.
Two consequences SC-4a must build on: **(1)** an order that shows a failed
attempt is still **open** and still payable — the surface renders the attempt
from `payment_event`, never from a closed order (this is what SC-4a done-when 5's
*"a failed payment says so"* now reads), and **nothing writes `failed`** at all,
so the only terminal states a customer's page sees are `captured` and
`abandoned`; **(2)** a capture landing on an already-`abandoned` order is the
**second** money-received-nothing-granted shape, alerted at `ERROR`
(`payments.capture_after_terminal`) and owned by **CP-8** beside the
NULL-`order_id` receipts — so **SC-4a done-when 5a's rule extends**: never
expose a payment link for an order this Console has abandoned, not only for one
it replaced. The two P2s are WS-31-internal (the redeem-attempt log ran below
its verifier; a non-ASCII signature header 500'd instead of 400'ing).
**SC-4a's checkout UI and its two write proxies remain held back**
behind B7's capability decision, which reaches the tenant plane's vocabulary
and must not ride in on a payments PR.

⚠️ **SC-4a was then AUDITED FOR DISPATCH and returned NO-GO — 2026-08-19, seven
blockers. All seven are answered in SC-4a's remediation box, docs-only, plus one
non-blocking residual and one finding the audit did not reach.** The two that
change what may be built: **(F-A)** there is **no browser→provider hand-off
anywhere in the tree** — `create_order` posts to the Orders API and returns no
payment link, the seam is `create_order` + `verify_webhook` and nothing else,
and browser Checkout's two required values (`key_id`, `order_id`) are exposed by
no route, `OrderView` being pinned to an exact 14-name field set. **SC-4a's
LAUNCH slice is therefore narrowed in writing to the ₹0 / discount path plus
order create + read** (which is what customer zero actually does — D36/D42);
done-whens 3 and 4 are **DEFERRED-until-hand-off** to a new named ticket,
**SC-4h**, jointly owned with WS-31/CP-9. **Deferral does not un-rehearse the
capture leg** — SC-4g clause 2's test-mode rehearsal stands, owner-side,
unchanged. **(F-I)** the audit did not reach it and re-measurement found it:
**no route exposes the priced `plan_catalog` to a customer credential**, so
launch done-when 1 has no data source and is blocked on one small WS-31 read.
B7's gate is now **chosen and correct** — `billing:purchase`, seeded like its
siblings onto `default`'s `admin`, with the honest statement that in every other
org it is **born unheld** because a new org gets **no roles at all** (a recorded
pre-existing defect class, `saas_multitenancy_implementation.md` §7.1 step 3 /
§8 trap 5, owned by the org-provisioning ticket, not by this one).

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
  `"purchaseEnabled": False` (`customer_console/main.py:1438` — *re-anchored
  2026-08-19 from `:1266`, F-G; corrected the same day from `:1408`, which was
  30 lines short and claimed to be re-anchored while it was not*) and the page
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
**SC-4a NO-GO remediation 2026-08-19** ·
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

### SC-1 — Read views *(after MT-2 tables + MT-3 ledger exist — **except SC-1a's seats block, dispatchable now over `GET /me/seats`; see below**)*
- **SC-1a Centers & add-ons panel** *(re-shaped by D23, 2026-08-10; **split
  2026-08-20 into a launch SEATS BLOCK and an MT-2-gated ENTITLEMENT half** — R4,
  after a dispatch audit returned NO-GO on the old done-when: it named
  `/auth/me`'s `modules` (MT-2, unbuilt) and `GET /billing/summary` (Operator /
  cross-org — the wrong door for a customer))*. **Center packages are the primary
  purchase framing** (per-user counts on each Center, ₹600 app-bearing / ₹300
  slices-only), with the org-wide add-ons (Builder, Workflows) and the Complete
  bundle beside them; `module_catalog` rows are the internal atoms and never the
  customer-facing frame.

  🟢 **LAUNCH SLICE — the SEATS BLOCK, dispatchable now.** On the existing
  `/settings/billing` page, render **per plan** the four seat numbers —
  **purchased · assigned · available · oversubscribed** — from the customer
  seats read `GET /me/seats` (`customer_console.md` §6 item (g), `can_pay` door),
  formatting the counts and **never** recomputing them (the seat vocabulary is
  WS-31 §3.3's; the read returns it, the page renders it verbatim). An
  oversubscribed plan (`assigned > purchased`) surfaces that state rather than
  hiding behind a clamped `available == 0`. **Done when:** a two-org fixture
  shows org A its own seat rows and **never** org B's; the four rendered numbers
  equal the read's payload for a known grant/assignment fixture (e.g. purchased 3
  / assigned 1 / available 2, plus an oversubscribed case); the block is driven
  **entirely by `GET /me/seats`** and holds no seat arithmetic of its own.
  It is a UI surface on the existing billing page, so
  `workbench/control_plane/DESIGN_SYSTEM.md` and the eight rules in its
  neighbouring `AGENTS.md` bind — no app-local palette, headless primitives
  from `src/components/ui/`, and the real gate is the theme-switch check (Fluent →
  Material → Graphite) on this surface **and** its neighbour.

  ⏸ **DEFERRED behind MT-2 (unbuilt) — the ENTITLEMENT half.** The per-Center /
  add-on **entitlement state** (`active | trial(expiry) | locked`), locked
  modules as upsell cards (the §2.4 rule 1 lever, never hidden), the price beside
  each, and the a-la-carte-vs-tier **savings prompt** (§2.4a rule 2) all read the
  org's **module entitlements** — which live in **MT-2's tables** and its
  `/auth/me` `modules` payload (`intersect()` / `ModuleGate`,
  `saas_multitenancy.md`), and **MT-2 is not built**. This half returns with MT-2,
  unchanged. **Done when (MT-2):** a two-org fixture shows org A its own
  entitlements and never org B's; a locked module renders its card with a
  request-CTA; the savings prompt is pinned by a test case where a-la-carte sum >
  tier price. It is **not** driven by `GET /billing/summary` (Operator /
  cross-org) and not by `/auth/me` modules until MT-2 ships that payload.
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
Center-package ladder (credit packs DEFERRED); LAUNCH SLICE NARROWED 2026-08-19
to the ₹0 / discount path plus order create + read.** The browser→provider
hand-off does not exist anywhere in the tree and is deferred by name to
**SC-4h**; see the remediation box below the pack rationale.

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

> ### The 2026-08-19 dispatch audit returned **NO-GO**. This is the answer.
>
> Seven blockers, one non-blocking residual, and **one the audit did not reach**
> — found while re-measuring its anchors, and it is the one that decides whether
> the narrowed slice is buildable at all (F-I). Docs-only round; **every anchor
> below was re-measured against the tree on 2026-08-19**, and every default is
> **agent-proposed, the owner may overrule** (the D16/D17 convention).

**F-A · There is no browser→provider hand-off anywhere in the tree, so the paid
path cannot be built. The LAUNCH slice is narrowed to the ₹0 path.**

Measured, not inferred:
- `RazorpayProvider.create_order` POSTs to `{API_BASE}/orders`
  (`payments.py:449-473`) — the **Orders** API. An Order is not a payment link:
  the response is parsed into `ProviderOrder` = `provider_order_id` +
  `amount_paise` + `raw` (`payments.py:333-338`). There is **no `short_url`**,
  because `/v1/orders` does not return one; that field belongs to the Payment
  **Links** API, which nothing here calls.
- The seam is **exactly two methods** — `create_order` + `verify_webhook`
  (`PaymentProvider`, `payments.py:414-425`). Neither hands the customer
  anything to pay with.
- Razorpay's browser **Checkout** needs two values in the page: the publishable
  `key_id` and the `order_id`. This Console exposes **neither**. `key_id` is
  read from env into `RazorpayProvider.__init__` (`payments.py:442-447`) and
  appears in **no** response anywhere in `main.py`; and `OrderView`
  (`main.py:268-295`) deliberately strips every provider identifier — pinned by
  `test_the_order_read_carries_no_provider_identifiers`
  (`tests/unit/test_customer_console_payments.py:1560-1576`) as an **equality**
  over the exact **14** field names, not a "no provider_ prefix" rule. Putting
  `provider_order_id` on the wire is therefore not a field addition; it is a
  **fence amendment**.

So an agent handed "build the checkout surface" would have to invent the
hand-off — a credential-exposure decision, a wire-format change and a fence
amendment — inside a UI PR, on a service it does not own. That is precisely the
straddle §6's two-slice split exists to prevent, one seam further along.

**The decision — the audit's option 1b, taken because it matches the launch goal
rather than because it is smaller:** customer zero pays **₹0** (D36 + D42), so
the flow that must work at launch never reaches a provider. **SC-4a's LAUNCH
slice is the ₹0 / discount path plus order create + read.** The customer journey
it must complete end to end:

> **pick package → order created (`POST /billing/orders`) → enter code
> (`POST /billing/orders/{id}/redeem`) → 100% redemption fulfils → done**,
> with the result read back through `GET /billing/orders/{id}`.

Every hop in that line exists in built, R8-tested code today. Nothing in it
needs a `key_id`, an `order_id` on the wire, or a fence amendment.

**Done-whens 3 and 4 are DEFERRED-until-hand-off** — dated, not deleted. Both
are statements about **capture**, and capture is unreachable from a surface that
never hands off. Done-when **5**'s *attempt* half goes with them (a page that
cannot start a payment cannot render a failed one); its **terminal-state** half
survives inside the rewritten **5a**, which is where `abandoned` now lives.

**SC-4h · The provider hand-off — a named future ticket, jointly owned with
WS-31 / CP-9.** One paragraph deliberately, not a full mint: two of its three
parts are decisions about the **Console's** responses, and minting them from
WS-30 would recreate the boundary straddle §1 already had to repair. It carries:
**(a)** the **`key_id` exposure decision** — Razorpay's `key_id` is publishable
by design, but *whether a customer-key response may carry it* is still a
decision about that service's response surface, and CP-3's non-oracle
discipline is the reason it gets argued rather than assumed; **(b)**
**`order_id` on the wire** — which provider identifier the browser receives and
under which auth; **(c)** the **`OrderView` fence amendment** — the 14-name
equality will go red, so amending it (with the argument, in the ticket) is the
first act of the slice rather than a diff nobody reads. It also carries SC-4a's
deferred done-whens 3, 4 and 5, and CP-9 §9.5's capture leg against a real
account. **Dispatch it only after `work_plan.md` §6(b) is answered** — the
Razorpay account is owner-side even in test mode.

⚠️ **Deferring the paid path does NOT un-rehearse the capture leg.** SC-4g
clause 2 stands exactly as written: the capture is rehearsed with Razorpay
**test-mode keys**, by the **owner**, and it remains the one clause of SC-4g an
agent may not claim (SC-4g done-when 9, still 🔴). The coupon path deliberately
never reaches the provider, so the two together cover the whole flow and
**neither alone does**. Narrowing the surface changes which half a WS-30 agent
builds; it changes nothing about whether the rehearsal is owed.

**F-I · ✅ CLOSED 2026-08-19 — WS-31 built the read on branch
`ws-31-catalog-read` (merged `4c2c521f`): `GET /billing/catalog`
(`main.py::billing_catalog`) under `PayingCaller`, over `store.active_plans`,
shaped by `CatalogPlanView`'s five fields with `payments.paise` doing the one
conversion. The launch slice consumes it through the thin
`src/app/api/billing/catalog/route.ts` and renders the rows verbatim, so
done-when 1 is met without a figure in TypeScript.** The finding as it stood,
kept because it is what made the prerequisite legible:

**Launch done-when 1 had NO DATA SOURCE.** *(Found 2026-08-19 while
re-measuring F-A's anchors. It was recorded rather than fixed, because the fix
was a route on WS-31's service.)*
Done-when 1 requires the surface to render *"only `plan_catalog` rows that are
`active` and priced — never a hard-coded ladder in TypeScript"*. Measured
repo-wide: **no route exposes the priced catalog to a customer credential.**
`plan_catalog` is read in exactly four places
(`store.py:281`, `store.py:606`, `store.py:845`, `main.py:1024`); of those the
only one on a response path is `GET /billing/summary` (`main.py:1017`), which is
**`Operator`**-scoped, cross-org, and returns plan **slugs the org already holds
seats on** — never the ladder and never a price. `GET /me/billing`
(`main.py:1355`) returns credits, an empty `invoices` list and
`purchaseEnabled`. `GET /me` (`main.py:1181`) returns identity and balance.
There is no `module_catalog` anywhere in the tree (repo-wide: zero hits).
**So an implementer meets done-when 1 only by transcribing the D23/D24 prices
into TypeScript — which is the exact defect done-when 1 was written to forbid.**
**Named as a HARD PREREQUISITE of the launch slice, owned by WS-31 / CP-9**, and
it is small: one **customer-key**, own-org-irrelevant read returning the
`active` rows of `plan_catalog` as `slug · kind · price_inr · sort_order`, no
per-org pricing, no entitlement state (that is SC-1a over MT-2). *Agent-proposed
default; the owner or WS-31 may shape the payload differently.* Two properties
it must have, both inherited rather than re-derived: `active` belongs in the
`WHERE` clause, not in the caller (`store.py:596-603`'s argument — *"a caller
that had to remember the filter is a caller that eventually sells R&D Center"*),
and prices stay integer paise on the wire per CP-9's rule, the browser
formatting and never arithmeticking. **Until that read exists, done-when 1 is
not met and must not be claimed.**

**F-B · Done-when 5a, rewritten against fields that exist.** As written it
demanded behaviour over "replaced, detached or abandoned" — three states the
surface has no way to observe. What the shipped read actually carries
(`OrderView`, `main.py:282-295`) is `status`, `expires_at`, `terminal_at` and
`discount`. The rewrite is below, in the done-when list, and it is testable
against those four.

**F-C · B7's seed clause, fixed against the actual pattern** — see the B7 block
below, which now names the role set, the org scope, and the defect class the
org scope belongs to.

**F-D · The fence shape collides with a known-open route** — see the B7 block.

**F-E · Done-when 6 gains its fence** — see done-when 6 below.

**F-F · `billing:purchase` is registered in the contract as a deliberate,
argued deviation.** `user_management_contract.md` §3 says *"Do not mint a new
permission slug to fix a hole"* — a rule this ticket breaks on purpose, so it is
recorded **in §3 itself** rather than only here. The argument, in one line: no
existing capability governs **spending the company's money**, and reusing the §3
floor `admin:members:read` would hand purchase authority to every member-reader.
The rule's own stated failure mode ("a new slug is nobody's grant until an admin
creates it") is answered by the seed, which is why B7's clause 2 exists and why
F-C had to make it correct.

**F-G · Anchors re-measured 2026-08-19** (the corpus already carries ~12 wrong
citations; these were four more). `purchaseEnabled` **1266 → 1438**;
`GET /me/billing` **1213 → 1386** (`my_billing`); `GET /billing/summary`
**875 → 1048** (`billing_summary`, `Operator`). Handler names now travel beside
the numbers, per the §6(c) convention.
⚠️ **Re-measured AGAIN in the repair round the same day, because the first
re-anchor was itself stale**: it was taken against a `main.py` that had since
grown ~30 lines, so `:1408` / `:1355` / `:1017` were all short while the prose
claimed to have fixed exactly that. The three numbers above are the measured
ones (`def` lines, `grep -n`). `work_plan.md` §6(c)'s four —
`grant_credits` **1135**, `assign_seat` **1080**, `release_seat` **1120**,
`set_lifecycle` **668** — are stale in the same direction and are **left for
that file's owner**: this branch does not touch `work_plan.md`.

**F-H · Non-blocking residual: the write routes discard WHO bought.** The read
proxy sends `X-CC-Member: identity.email`
(`src/app/api/billing/summary/route.ts:63`), and **nothing on the Console reads
it on the write path** — `create_order` audits with `actor="organization"`
(`main.py:1592-1595`) and `redeem_discount_code` does the same. So the audit
trail records *which organization* bought, never *which person*, which is the
one distinction an audit trail most needs on a money write (SC-4g done-when 7
made exactly this point about the operator-vs-organization actor split, one
column short of this one). **Not a blocker for the launch slice** — the two
write proxies still resolve the caller server-side and refuse without the
capability, so authorization is unaffected; only *attribution* is lost.
**Attributing the buyer is a WS-31 column ask** (a `purchased_by` on
`payment_order`, or an actor on the audit row) **for a later ticket**, not a
WS-30 surface change: the surface already holds the identity and already sends
it.

> **What the 2026-08-19 audit VERIFIED — recorded so the implementer does not
> re-derive it, and so nobody re-opens a settled question out of caution:**
>
> - **The BFF fence precedent is `src/lib/export.test.ts`, and it is named here
>   deliberately.** It **imports the real route handlers** through the same `@/`
>   specifier the app uses (`load: () => import("@/app/api/crm/[...path]/route")`,
>   `export.test.ts:100-115`), mocks `@/auth` rather than importing it
>   (`:38-41`), stubs `fetch` with `vi.stubGlobal` (`:148-151`) and builds
>   requests from `NextRequest` out of `next/server` (`:152-155`). ⚠️ **Do not
>   fall back to source-regex out of fear of the `signin.test.ts` import
>   warning.** That warning is real but *specific*: `import("@/auth")` dies
>   inside `next-auth/lib/env.js` in this node-env vitest
>   (`signin.test.ts:36-45`). Mocking `@/auth` — which `export.test.ts` does —
>   sidesteps it entirely, and B7's clause 4 fence (*assert the upstream fetch
>   was never called*) is **only expressible by running the handler**. A
>   source-regex cannot prove a 403 happened *before* the money route was hit.
> - **The capability chain is complete end to end** — nothing new has to be
>   built to carry a slug from the database to the browser:
>   `acb_auth.permissions.CAPABILITIES` (`permissions.py:126-163` — *re-anchored
>   2026-08-19 by the build, which added the slug*) → the seed row
>   in `org_role_permission` → `admin/me.py:164-166`, which emits
>   `"capabilities": [c for c in CAPABILITIES if "*" not in c and access.has(c)]`
>   (so an owner holding `*` resolves to yes without holding the literal string)
>   → `Access.capabilities` + `hasCapability()`
>   (`src/lib/access.ts:43`, `:172-174`). A slug added to `CAPABILITIES` and
>   seeded is visible at every hop with **no** other code change.
> - **No new environment variables.** The two write proxies read the same
>   `CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_ORG_KEY` the shipped read proxy
>   reads (`summary/route.ts:34-36`), and inherit its fail-closed 503 (`:44-55`).
>   Adding a variable here would be a third configuration surface for one
>   credential.
> - **The tenant ladder topped at `177_console_resolve_projection.sql`** as of
>   2026-08-19, and the seed took **178** at build time after re-checking every
>   local and `origin/` branch (highest anywhere: 177). That was a
>   **measurement, not an instruction**: the next one is taken by listing
>   `infra/postgres/` again and re-checked at merge (R1).

**Launch done-when (the subscription checkout):**
1. ✅ **MET 2026-08-19.** The purchase surface renders **only `plan_catalog` rows
   that are `active` and priced** — never a hard-coded ladder in TypeScript. A
   price change is a database row (D23/D24's own rule), so a surface that
   transcribes prices is a second source of truth and a defect.
   *Built:* `PackagePicker` maps whatever `GET /api/billing/catalog` returned,
   in the order the server sent it (`active` is in the Console's `WHERE`
   clause, never in the caller), and formats `price_paise` through the one
   `formatPaise`. **Fence:** `lib/checkout.test.ts`'s comment-stripped source
   scan over `page.tsx`, `Checkout.tsx` and `lib/checkout.ts` for **every**
   D23/D24 figure in **both denominations** (300/500/600/1800/3000 and their
   paise), plus "no `₹`-and-a-digit in the markup" and "no plan slug in the
   client". Red-first evidence: replacing one row's `formatPaise(...)` with the
   literal `"600"` fails with *`600 appears as a literal — done-when 1 forbids
   transcribing a price`*.
   *(This clause was 🔴 blocked on F-I until the same day; see F-I above for
   what closed it.)*
2. ✅ **MET 2026-08-19.** Choosing packages and quantities and confirming creates
   **one** `payment_order` through `POST /billing/orders` under the deployment's
   organization key, and **changes nothing else** — no seat, no subscription, no
   ledger row until capture (CP-9 §9.3, fenced there).
   *Built:* the picker's quantity steppers feed `basketLines()`, and the proxy
   **rebuilds** the body as `{lines:[{plan_slug, quantity}]}` rather than
   forwarding it — so no price the browser holds can reach the wire even if a
   future page field carries one. Fence:
   `checkout.test.ts::never forwards a price the browser named`. The
   "changes nothing else" half is the Console's own, fenced there.
3. ⏸ **DEFERRED-until-hand-off → SC-4h** *(2026-08-19, F-A)*. This clause and
   clause 4 are statements about **capture**, and a surface that cannot hand off
   to the provider cannot reach one. They are correct and they return with
   SC-4h, unchanged. *(The ₹0 path's own entitlement read-back is clause 9,
   which is the launch-slice version of this criterion and is **not** deferred.)*
   On capture, the org's entitlements reflect the order: `org_subscription` is
   active for the period and `seat_grant` totals equal the ordered quantities —
   read back through the **existing** `GET /billing/summary`, so no surface
   recomputes seats (SC-2's rule, `customer_console.md` §3.3).
   ⚠️ **A green `GET /billing/summary` is a CONSOLE fact, not a live product
   entitlement** *(added 2026-08-18)*. It says the Console recorded the
   purchase; it says nothing about whether any Metorite surface will *honour*
   it — enforcement inside the product is **MT-2**'s `intersect()` / `ModuleGate`
   / the 402-vs-403 split, and **SC-2 over MT-2 owns that**, not this clause.
   Two further facts about the route, both measured: it is the **Operator**,
   cross-org read (`customer_console/main.py:1283`, `billing_summary` —
   *re-anchored 2026-08-20 on `ws-31-seats-mvp` from `:1017`, earlier `:875`*), so
   this acceptance runs operator-side in the suite; the customer-reachable billing
   read is `GET /me/billing` (`:1621`, `my_billing` — *re-anchored 2026-08-20 from
   `:1355`, earlier `:1213`*) and it returns **no seats at all**. The customer's
   own seat grid is instead served by the dedicated `GET /me/seats` read
   (`customer_console.md` §6 item (g) / done-when 19, `can_pay` door), which
   SC-1a's launch seats block renders — named here so nobody reads clause 3 as a
   promise the customer's own page can keep by itself.
4. ⏸ **DEFERRED-until-hand-off → SC-4h** *(2026-08-19, F-A)* — see clause 3.
   **A duplicate webhook grants once** — CP-9 §9.5's **two** guards, not one:
   the `provider_event_id` key is transport dedup, and the **terminal-state
   rule** is the money guard, because Razorpay sends *different* event ids
   (`payment.captured`, `order.paid`) for one capture *(corrected 2026-08-18,
   B8)*.
5. ⏸ **The ATTEMPT half is DEFERRED-until-hand-off → SC-4h** *(2026-08-19,
   F-A)*: a surface that cannot start a payment cannot render a failed one, so
   the `payment_event` attempt copy below has no way to occur at launch. **The
   TERMINAL-STATE half is NOT deferred** — it moved into the rewritten **5a**,
   which is where `abandoned` (the TTL, written by the clock and reachable
   without any provider) is now handled.
   **A failed or abandoned payment grants nothing and says so** on the page,
   naming what to do next — read through **`GET /billing/orders/{id}`**, minted
   as CP-9 §9.3a *(2026-08-18, B6: as first specced nothing could read an order
   back, so this clause had no data source and would have been "met" by a page
   that guesses)*.
   ⚠️ **A failed ATTEMPT is not a failed ORDER, and the page must not say it
   is** *(corrected 2026-08-19, CP-9's review P0)*. One provider order takes
   many attempts until one captures, so **`order.status` never becomes
   `failed`** — nothing writes it. The attempt is read from CP-9's
   `payment_event` receipt; the order beside it is still **open and still
   payable**, and the correct copy is *"that attempt didn't go through — try
   again"*, never *"this order failed, start a new one"*. The only terminal
   states this page renders are `captured` and `abandoned` (the TTL).
5a. ✅ **MET 2026-08-19** (the halves reachable without a hand-off; the
   before-hand-off half travels to SC-4h with the hand-off itself). **The
   surface never re-uses an order after redemption or after
   `expires_at`; it re-reads `GET /billing/orders/{id}` before every hand-off;
   and it treats `captured` and `abandoned` as terminal.** *(Added 2026-08-18 as
   CP-9 finding F2 · extended 2026-08-19 with CP-9's review P0(b) · **rewritten
   2026-08-19, F-B, against fields that exist**.)*

   *Built:* `Checkout.applyCode()` re-reads **before** the redeem, refuses on a
   terminal order without calling redeem at all, and re-reads **again
   afterwards whatever the response was** — a refusal is about the row on
   screen, and a stale row after a 409 reads as success. `orderState()` in
   `lib/checkout.ts` is the one place the rule lives. **Fence:**
   `lib/checkout.test.ts`'s five cases — `captured`/`abandoned` terminal and
   `created`/`attempted` not; a past `expires_at` terminal while `status` still
   reads open; an **unparseable** expiry deliberately NOT terminal (the TTL is
   the server's and a date format must not take a working checkout down); a
   naive timestamp read as UTC rather than local, because a machine an hour
   behind would otherwise call a live order dead; and `discounted` /
   `completed` derived from the re-read rather than from the basket.

   > ⚠️ **Why the rewrite.** As it stood, this clause was expressed in terms of
   > orders the Console had *"replaced, detached or abandoned"* — and **two of
   > those three are invisible to the surface**. `OrderView`
   > (`main.py:282-295`) carries `id · status · provider · gross_paise ·
   > discount_paise · taxable_paise · gst_paise · total_paise · gst_split ·
   > expires_at · created_at · terminal_at · lines · discount`, and no more:
   > *replaced* and *detached* are both facts about `provider_order_id`, which
   > the read deliberately withholds (F-A). A criterion a surface cannot
   > observe is a criterion that gets "met" by a page that guesses — the same
   > defect B6 minted `GET /billing/orders/{id}` to fix, one clause along.

   The rule, restated over the four fields the read actually gives:
   - **Re-read before every act.** After **every** successful redeem, and
     **before every hand-off to the provider**, the surface re-reads
     `GET /billing/orders/{id}` and acts on that response — never on an order
     object captured in component state before the redeem call, and never on one
     rendered before the page was left open.
   - **`captured` and `abandoned` are terminal, and nothing else is.** Nothing
     writes `failed` at order level (CP-9's review P0, and
     `test_no_code_path_drives_an_order_to_failed` pins it), so a page that
     branches on a third terminal state is branching on a state that cannot
     occur. A terminal order is never re-used: no redeem, no hand-off, no
     retry — start a new order.
   - **`expires_at` in the past is treated as terminal even while `status`
     still reads open.** `abandoned` is written by the clock on the next touch
     (`abandon_if_expired`), so a page held open past `expires_at` will be
     looking at a `status` the server is about to change. Trusting the stale
     `status` is exactly the stale-link bug this clause exists to name.
   - **A `discount` on the read means the amount moved**, and any figure or
     link derived from the pre-redemption total is stale by definition.

   The reason all of this matters, unchanged: a redemption **replaces** the
   provider order (partial) or **detaches** it (₹0), and the TTL **abandons** an
   order the customer left open — **none of the three retracts the link at
   Razorpay**. Paying a stale link is a capture the Console either cannot
   attribute to any order or attributes to a **terminal** one: money received
   with nothing granted, alerted at `ERROR` in both shapes and owned by
   `customer_console.md` **CP-8**. At launch the ₹0 path hands off to nothing, so
   the re-read-before-hand-off half is **carried forward to SC-4h**; the re-read
   and terminal-state halves are **live now** and testable against the shipped
   read.
6. ✅ **MET 2026-08-19.** `purchaseEnabled` (`customer_console/main.py:1438` —
   *re-anchored 2026-08-19 from `:1266`, F-G; the re-anchor itself was wrong by
   30 lines and is corrected here the same day*) is what flips the page from the
   contact prompt to the checkout (`page.tsx:211` — *re-anchored from `:202` by
   the build*) — **the flip is the owner's**, and its
   preconditions are the flip set below.
   **Fence** *(added 2026-08-19, F-E — R7: a rule with no test is advisory, and
   "it ships dark" is the kind of claim that quietly stops being true)*: a
   **source-level assertion** over
   `src/app/settings/billing/page.tsx`, in the `signin.test.ts` idiom —
   `readFileSync(new URL(…), "utf-8")` plus
   `toContain` / `not.toContain` (`signin.test.ts:28`, `:71-81`). It asserts the
   **entire purchase flow renders only inside the `data.purchaseEnabled`
   conditional**: the checkout's entry control, the package
   picker and the code entry appear in the true arm and **nowhere else in the
   file**, and the false arm stays the contact prompt.
   *Built as:* the whole flow is `<Checkout />`, so the assertion is
   **positional** — the mount appears exactly once in `page.tsx` (comments
   stripped, since the file documents the flip point in prose) and its index
   lies strictly between `data.purchaseEnabled ? (` and the contact prompt that
   opens the false arm. A second half checks that none of the flow's own
   markers (`/api/billing/orders`, `/api/billing/catalog`, `redeem`,
   `PackagePicker`, `CodeEntry`) appear anywhere in the page. Red-first
   evidence: hoisting `<Checkout />` above the conditional fails with
   *`expected 5880 to be greater than 5903`*.
   ⚠️ **Source-regex is the right tool for THIS one and the wrong tool for
   B7's** — the distinction is worth stating because the two sit in the same
   PR. Here the subject is **where a thing may appear in a file**, which is
   exactly what a source assertion decides; B7's subject is **whether a refusal
   happens before a network call**, which only running the handler can decide
   (see the verified-facts box above, and `src/lib/export.test.ts`).
7. ✅ **MET 2026-08-19.** `npx tsc --noEmit && npx vitest run` green — **106
   files / 2305 tests, 0 failures**, `tsc` silent. The surface uses
   `src/components/ui/` primitives (`Button`, `Input`, `Badge`, and `Modal` —
   D-PM-15's one substrate wrapper) and the theme tokens (no colour literals,
   no `lucide-react` import) per `workbench/control_plane/AGENTS.md`; the plan
   `kind` chip is a **category**, so its hue comes from
   `src/lib/categorical.ts`'s ramp rather than a tone or a palette class
   (AGENTS.md rule 7). `src/lib/theme/conformance.test.ts` passes with no new
   baseline entry.
   ⚠️ **What that green does NOT cover, stated rather than implied:** there is
   no layout or structural test in this tree and vitest here is node-env, so
   nothing renders the dialog. DESIGN_SYSTEM.md §8's theme-switch pass —
   Fluent → Material → Graphite, on this surface *and* its neighbour — is
   review-only and is **owed** at review.
8. ✅ **MET 2026-08-19.** **The two new write proxies are gated SERVER-SIDE**,
   per the block immediately below *(added 2026-08-18, B7)* — and so is the
   order **read**, for the reason argued in `orders/[id]/route.ts`'s header:
   it exposes an order's state, amounts and a code's prefix, which is not the
   summary's data, and a gate on the write of an object with a door beside it
   on the read is not a gate. `src/app/api/billing/checkout.test.ts` runs all
   three real handlers over a stubbed `fetch`, parametrised as an **explicit
   list** with `summary/route.ts` excluded **by name** (the board finding below)
   and `catalog/route.ts` excluded **by name** (session-gated on purpose).
   Red-first evidence: moving the gate below a Console call keeps the 403 but
   fails with *`expected [ Array(1) ] to deeply equal []`* naming
   `https://console.invalid/billing/catalog` — which is precisely the "403
   issued after the money route was hit" bug the clause exists to catch.
   ⚠️ **The gate has a SECOND half since the repair round** *(2026-08-19)*: the
   capability says who may buy, the key says whose account is charged, and
   `requirePurchaser` now compares the caller's organization against the key's
   before either write is reached. The clause-4 fences below are joined by six
   org-binding cases per gated route (mismatch, unresolvable caller org,
   whoami non-200, whoami unreachable, no failed-resolution caching, and the
   matching-slug pass), each asserting the **money route was never fetched**.
   ⚠️ **The literal wording of clause 4 — "assert the fetch mock was never
   called" — is met in spirit and not in letter, deliberately.** The gate
   itself resolves the caller through the gateway's `/auth/me`, so `fetch` **is**
   called once on the refusal path; asserting zero calls would fence the wrong
   thing. What the fence asserts is **zero calls to the Customer Console**,
   which is the money route and the actual subject.
9. ✅ **MET 2026-08-19** *(surface half; the server half was SC-4g's and is
   fenced there)*. **The ₹0 journey completes on the surface, end to end** *(new 2026-08-19,
   F-A — this is the launch slice's central criterion and the reason the
   narrowing is a narrowing rather than a retreat)*: **pick package → order
   created → enter code → 100% redemption fulfils → done.** Concretely, with a
   100%-off code issued against the fixture org:
   - the surface calls `POST /billing/orders` **once** and holds the returned
     order id;
   - it calls `POST /billing/orders/{id}/redeem` with the code, and **re-reads**
     `GET /billing/orders/{id}` afterwards (5a);
   - the re-read shows `status = "captured"`, `provider = "none"`,
     `total_paise = 0` and a `discount` block carrying the code's **prefix** —
     never its secret (`OrderDiscountView`, `main.py:262-265`);
   - the page renders a completed state from that response and **stops** — it
     does not poll, does not offer a retry, and does not construct a payment
     link, because on this path there is nothing to pay;
   - each of SC-4g done-when 4's refusal shapes renders distinguishable copy for
     the three named reasons and **one** identical message for the collapsed
     `404 {"detail": "no such discount code"}`, plus the two order-level 409s
     (`already_discounted`, `order_not_open`). A surface that leaks the
     wrong-org case apart from the unknown case defeats the partition
     server-side code was built to hold.

   *Built:* `Checkout` walks exactly that path — `createOrder()` posts once and
   holds the id; `applyCode()` re-reads, redeems, re-reads; `Completed` renders
   from the final response and **stops** (no polling interval, no retry
   control, no link construction — there is nothing to pay). The refusal copy
   is `redeemRefusal()` in `lib/checkout.ts`, and the collapsed shape's
   sentence is a **single constant with one call site**, so the two cases
   cannot drift apart by editing one of them. **Fences:**
   `lib/checkout.test.ts` asserts the three named reasons produce three
   distinct strings, that unknown and wrong-org produce **byte-identical**
   `Refusal` objects and that neither equals any of the three, and that the two
   order-level 409s are told apart from the code ones (with `order_not_open`
   carrying the order's own status into the sentence);
   `api/billing/checkout.test.ts` proves the proxy **relays** those bodies
   verbatim, since a partition the BFF summarised would be no partition at all.
   ⚠️ **A partial code has no completion on this surface**, and the page says so
   in plain words rather than offering a control that cannot work — the
   remainder needs SC-4h.

> **The narrowed LAUNCH done-when list, stated once so it cannot be inferred
> wrongly** *(2026-08-19)*. In scope for this slice: **1**, **2**, **5a**
> (rewritten), **6** (fenced), **7**, **8**, **9** — **all seven ✅ MET
> 2026-08-19**, F-I's prerequisite having closed the same day. Deferred to
> **SC-4h** with the hand-off: **3**, **4**, and
> clause **5**'s attempt half. Nothing else in SC-4a is in this slice; SC-4b–4f
> keep their own sequencing in §6.

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
> 2. **It must not be born unheld — and the honest version of that sentence is
>    narrower than the original.** *(Rewritten 2026-08-19, F-C. As written this
>    clause said "seeds it onto the `owner` and `admin` roles", which
>    **misdescribes the pattern it cites**: 133 seeds nothing onto `owner` — it
>    carries the comment `-- owner already holds '*'; nothing to add`
>    (`133_workflows_publish_permission.sql:40`) — and it says nothing about
>    which organizations the seed reaches, which is the half that actually
>    decides whether checkout works for a customer.)*
>
>    **The role set (agent-proposed default): `admin` only.** `owner` already
>    holds `*` (`130_org_access_control.sql:188-190`) and needs no row; adding
>    one would be a second statement of the same grant. `manager` is
>    deliberately **excluded**, which is where this seed diverges from 133's
>    admin+manager: 133 gated *publishing an automation*, an operational act a
>    manager owns; this gates **spending the company's money**, and the whole
>    argument for minting the slug (clause 1) is that money authority is
>    narrower than the §3 `admin:members:read` floor. Seeding `manager` would
>    re-widen it to every holder of that floor and make clause 1 decorative.
>    `agent_service` gets the row for table-consistency exactly as 133 does
>    (`:62-70`) — it resolves to `*` in `acb_auth.access.SERVICE_ACCESS`
>    regardless, and the row is for anyone reading the table directly.
>    ⚠️ **No agent may grant this capability per-member against a live org** —
>    that is `work_plan.md` §6, WS-24 (d), unchanged.
>
>    **The org scope, stated honestly rather than optimistically.** The seed
>    migration seeds the **`default`** organization, like **every** permission
>    seed in this tree: `130_org_access_control.sql:180`,
>    `131_integration_memory_permissions.sql:36` and
>    `133_workflows_publish_permission.sql:34` all open with
>    `SELECT id INTO org_id FROM organization WHERE slug = 'default'`. Writing a
>    fourth one that loops every org would be inventing a second seeding
>    doctrine inside a checkout ticket — the thing root `CLAUDE.md` §5 forbids
>    — and it would not help, because the roles it would attach to **do not
>    exist in other orgs either**.
>
>    **So say the consequence plainly: in any organization other than
>    `default`, `billing:purchase` is born UNHELD.** Not because of this
>    ticket — because a newly created organization gets **no roles at all**
>    today. That is a **recorded, pre-existing defect class**, not a new one:
>    `saas_multitenancy_implementation.md` §7.1 step 3 (*"that seeding must
>    become a parameterised function, or org #2 has no roles and no owner"*) and
>    §8 trap 5 (*"Role seeding still keyed `slug='default'` → Org #2 gets no
>    roles and no owner"*), the same finding the second-org work carries. **It is
>    fixed by the org-provisioning ticket that parameterises role seeding — not
>    here.** A checkout slice that invented per-org role provisioning on the way
>    past would be building WS-29's work in the wrong layer, unreviewed.
>    ⚠️ **The launch consequence, so nobody is surprised by it:** customer zero's
>    org (Fracktal, D36) will need its roles provisioned by that ticket — or the
>    capability granted by the owner as a gated act — before its admin can reach
>    checkout. The **flip set** below is where that belongs operationally; it is
>    named here because a capability that is unheld in the only org that matters
>    is a checkout that is dead on arrival, which is the exact failure this
>    clause was written to prevent.
>
>    Shape otherwise unchanged: same idempotent `DO $$` block as 133, guarded on
>    a NULL `org_id` with a `RAISE NOTICE` (`:35-38`), `ON CONFLICT DO NOTHING`
>    on every insert, **migration number taken by listing `infra/postgres/` at
>    build time and re-checked at merge (R1)**. The slug joins
>    `acb_auth.permissions.CAPABILITIES` so a typo is catchable rather than
>    silently inert, and it is registered in `user_management_contract.md` §3 as
>    an argued deviation from that section's own rule (F-F).
>    ✅ **BUILT 2026-08-19 as `infra/postgres/178_billing_purchase_permission.sql`**
>    — 178 taken by listing the directory and re-checked against every local and
>    `origin/` branch (highest anywhere was 177).
>    **Fence for the seed (R7/R8):** ✅ **BUILT** —
>    `tests/unit/test_billing_purchase_capability.py`, run against the **tenant**
>    ladder via `tests/unit/_tenant_ladder.py` and `TENANT_LADDER_DATABASE_URL`
>    (§5's command block carries it) — it asserts the slug is in `CAPABILITIES`,
>    that after the ladder replays the `default` org's `admin` role holds it and
>    `manager` and `member` do **not**, and that a **re-replay grants nothing
>    twice**. **10 tests, 0 skipped**, against a real Postgres 16. It joined
>    `pr-check.yml`'s hand-maintained skip-guard list in the same change and
>    asserts its own membership there and in §5, because that list discovers
>    nothing. ⚠️ It finds the migration **by content, not by number**: R1 means
>    the file can be renumbered at merge, and a suite hard-coding `178` would
>    report a renumber as a broken seed. ⚠️ **Never `DATABASE_URL`** for this:
>    that name arms
>    `test_tenant_coverage.py`'s two DB-gated tests, which fail by construction
>    against a freshly-replayed ladder and are WS-29 MT-1b/MT-1c's gates, not
>    this console's.
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
>    ✅ **BUILT 2026-08-19 as `src/app/api/billing/checkout.test.ts`**, over the
>    two writes **and** the order read. Two refinements the build had to make,
>    both recorded rather than silently taken: **(a)** *"the fetch mock was
>    never called"* is asserted as *"the **Customer Console** was never
>    fetched"*, because the gate's own `/auth/me` hop is a fetch — the money
>    route is the subject, not the mock; **(b)** the capability check runs
>    **before** the 503 configuration check, so a member without it cannot learn
>    whether this deployment is wired to a Console. A fourth case was added for
>    the same reason the third exists: an **unresolvable** capability (gateway
>    down, non-JSON, no `capabilities` array) is a **403**, never a pass — the
>    read path's habit of degrading to `NO_ACCESS` and rendering is right for
>    deciding what to draw and wrong for deciding whether to spend.
>
>    **The pattern to copy is `src/lib/export.test.ts`, by name** *(2026-08-19,
>    F-D)*. It imports the real handlers (`import("@/app/api/…/route")`,
>    `:100-115`), mocks `@/auth` (`:38-41`), stubs `fetch` via `vi.stubGlobal`
>    (`:148-151`) and builds `NextRequest` from `next/server` (`:152-155`) — the
>    only shape in this tree that can assert *"the refusal happened before the
>    fetch"*, because that assertion requires **running the handler**. Do not
>    fall back to a source regex here out of caution about `signin.test.ts`'s
>    recorded import warning: that warning is about importing `@/auth`, which
>    this pattern **mocks**.
>
>    ⚠️ **The parametrisation is an EXPLICIT LIST, and a directory
>    sweep over `src/app/api/billing/**` is FORBIDDEN here** *(2026-08-19,
>    F-D — the fence shapes in this tree collide, and the collision has to be
>    named or the implementer will discover it as a red test and "fix" it the
>    wrong way).* *(As built it carries **three** entries rather than two — the
>    two writes plus the order read, which takes the same gate for the reason
>    argued in `orders/[id]/route.ts`'s header — and **two** exclusions by
>    name.)* The house sweep idiom — `routeFiles(API_DIR).filter(...)`,
>    `signin.test.ts:60-69`, `:171-174` — walks every `route.ts` under a
>    directory. Pointed at `src/app/api/billing/**` it would immediately go
>    **RED on `summary/route.ts`**, which is the **known-open board finding**
>    recorded at the foot of this block: that route is reachable by any
>    signed-in member and is deliberately **not** fixed in this slice.
>    An implementer meeting a red sweep has two moves and **both are wrong**:
>    fix the read proxy (scope creep into merged code, in a checkout PR), or
>    **narrow the sweep** to make it pass — which is the CP-6 failure mode B1
>    already names, a fence quietly re-shaped around the thing it caught.
>    So: **an explicit list of the gated proxies, with the read proxy
>    excluded BY NAME and the board finding cited on the exclusion line.** A
>    named exclusion is visible in a diff and dies when the finding is fixed; a
>    narrowed sweep is invisible and outlives it. *(This is the same discipline
>    CP-9 §9.3(4) applied to `METERING_EXEMPTION` — exempt by name in a separate
>    constant with its own fence, never narrow the list the ticket counts.)*
>    *As built, the exclusion is not only a comment:* `checkout.test.ts`'s
>    closing block **runs** `summary/route.ts` and asserts it answers **200 to a
>    signed-in member holding no capabilities**. That is not a claim the gap is
>    correct — it is the exclusion made executable, so the day the finding is
>    fixed the case goes red and the entry has to move into the gated list.
>    `catalog/route.ts` is excluded by name for a different and non-defective
>    reason (it returns the price list, identical for every customer), and is
>    asserted to keep its session gate: 401 signed-out, with **no** fetch at all.
>    ⚠️ Consequence, stated so it is a decision rather than an oversight: **the
>    "a third proxy is covered without anyone remembering" property does not
>    hold** under an explicit list. It is bought back the day the read proxy is
>    fixed and the sweep can be armed — which is the small ticket the board
>    finding below asks for.
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
`tests/unit/test_customer_console_payments.py`, 115 tests against a real
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
   and `pr-check.yml`'s skip-guard in the same PR. — **115 tests, 0 skipped**;
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

⚠️ **The floor is the READ floor. The two SC-4a write proxies sit above it**
*(recorded here 2026-08-19 so §3 does not read as the whole access story)*: they
require **`billing:purchase`**, resolved server-side, per SC-4a's B7 block —
because `admin:members:read` means *"may see the member list"* and these routes
spend the company's money. The slug is registered as an argued deviation in
`user_management_contract.md` §3.

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
#
# SC-4a's `billing:purchase` SEED migration is TENANT-side, so its fence runs
# here and nowhere else (named 2026-08-19, F-C; ✅ CREATED 2026-08-19 by the
# launch slice, like SC-4g's suite was — 10 tests, 0 skipped).
# The suite builds the tenant
# schema from infra/postgres/ through tests/unit/_tenant_ladder.py and gates on
# TENANT_LADDER_DATABASE_URL *as pytest was launched with it* (the snapshot line
# beside tests/conftest.py:16). It needs the pgvector image — infra/postgres/
# 01_schema.sql requires uuid-ossp AND vector:
#   export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1/acb_tenant
uv run pytest tests/unit/test_billing_purchase_capability.py
# ✅ It JOINED pr-check.yml's hand-maintained skip-guard list in the PR that
# created it — that list discovers nothing, and a tenant-side R8 suite that
# skips in CI reports green while proving nothing (CP-3's disarmed gate). The
# suite reads both the workflow and this file and fails if its own name is
# dropped from either.
# ⚠️ Do NOT export TENANT_LADDER_DATABASE_URL as DATABASE_URL to "make things
# run": those two tenant-coverage tests need 04_policies.sql promoted and a
# non-superuser role, i.e. WS-29 MT-1b/MT-1c.

# The capability chain the seed feeds, no database needed:
uv run pytest tests/unit/test_permission_policy.py tests/unit/test_org_access_control.py

# The surfaces themselves — SC-4a's BFF gate fence
# (src/app/api/billing/checkout.test.ts, the src/lib/export.test.ts pattern:
# real handlers, @/auth mocked, fetch stubbed) and done-when 1/5a/6/9's fences
# (src/app/settings/billing/lib/checkout.test.ts) run here.
# ✅ Measured 2026-08-19 on the launch slice: 106 files / 2305 tests, 0 failures.
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

⚠️ **The surface half itself split again on 2026-08-19** *(the NO-GO dispatch
audit; the answers are in SC-4a's remediation box)*. B7's gate is now **chosen**
(a seeded `billing:purchase`, F-C), so the surface is no longer held on that —
but **the paid path is, and by a harder thing than a decision: there is no
browser→provider hand-off in the code** (F-A). Live order inside SC-4a:
**(1)** the **₹0 / discount launch slice** — order create + read + redeem, done-whens
1, 2, 5a, 6, 7, 8, 9 — which needed **one prerequisite from WS-31/CP-9: a
customer-key read of the priced `plan_catalog`** (F-I); then **(2) SC-4h, the
provider hand-off**, jointly owned
with WS-31/CP-9, carrying done-whens 3, 4 and 5's attempt half and gated behind
`work_plan.md` §6(b)'s Razorpay account.

✅ **Step (1) is DONE — 2026-08-19, WS-30, branch `ws-30-sc4a-surface`**, the
prerequisite having merged the same morning (`4c2c521f`). What is live is
**dark**: `purchaseEnabled` is still `False` on the Console, so no customer
sees the checkout until the owner flips it, and the flip set below is unchanged.
The next thing in this ticket's own order is **SC-4h**, and it is **still
owner-gated** — the Razorpay account is `work_plan.md` §6(b), even in test mode.

## Gate labels

Building every surface: **AGENT-SAFE**. Fulfilling a change request, editing any
live org's entitlements, granting the console to a real customer admin:
**OWNER-GATE** (work_plan.md §6).
