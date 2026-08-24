# Customer Console — the subscription, seat and AI-metering engine (WS-31)

*(Authored 2026-08-12 as the "Platform Control Plane"; renamed **Customer
Console** by **D41**, 2026-08-18 — file was `platform_control_plane.md`. The
path/env/package mapping is in D41.1.)*

**Status:** ◐ **CP-0 · CP-1 · CP-2 · CP-2a · CP-2b · CP-3 · CP-4 BUILT · CP-2d
SLICES 1+2 of 2 BUILT (slice 1 2026-08-22 — passwordless email OTP via Resend,
the Auth.js provider + `EMAIL_OTP_ENABLED` flag, DARK; **slice 2 2026-08-23 — the
Auth.js DB adapter (tenant-plane migration 186 `auth_email_otp_token`,
RLS-EXEMPT), the gateway-side token routes with the server-side rate limit, the
`session: { strategy: "jwt" }` pin, the `/signin/code` entry page, and
`EMAIL_OTP_ADAPTER_READY = true` — OTP is now ARMED BY THE FLAG, still DARK
because the flag and the key are unset everywhere**; the RESEND_API_KEY secret +
the live flag flip are OWNER-GATE; **slice 2 REVIEWED and REPAIRED 2026-08-23
(round 1) on one P0, two P1s, one P2 and four accuracy findings — the adapter is
now STATELESS-TRANSPARENT rather than "minimal" (the five-method shape would
have taken Google and Microsoft sign-in down on the flag flip,
`callback/index.js:56-62`), the OTP identifier is canonical at both browser write
sites and echoed as requested, the send budget is enforced by an atomic slot
claim rather than an in-flight exclusion, and the consume statement takes exactly
one row**; **slice 2 RE-REVIEWED and REPAIRED 2026-08-23 (round 2) on three
findings — (P1) the `/signin/code` fallback arm was UNUSABLE, because `known`
derived from the live input and `"a"` canonicalises to `"a"`, so the first
keystroke unmounted the field being typed into; the decision is now the pure
`codeEntryState` helper keyed on the mount-time stash ALONE. (P2) the burst's one
mailed code could be CLOBBERED by the send loser's token leg — `Promise.all`
cancels nothing, so refusing the mail did not refuse the write — and the claim
now carries the winner's hash while `record_token` is first-writer-wins, so the
row's hash at mail time is the slot winner's under every interleaving. (P2) the
`updateUser` trap's fence is now STRUCTURAL (an identity comparison against the
exported constant-null implementation) rather than a three-address value
check**) · CP-2c
SLICES 1+2+3+4 of 4 BUILT (slice 1 2026-08-19 — the deployment-key provision arm;
slice 2 2026-08-20 — the gateway `POST /signup/provision` route + the
Console-provision client; slice 3 2026-08-20 — the `signIn` callback
self-serve-signup "limbo" branch (zero-org-only, keyed on the gateway's additive
`signup_eligible` signal), BUILD+TEST only; slice 4 2026-08-20 — the `/signup`
form UI + its `/api/signup` Next hop, BUILD+TEST only; **AMENDED 2026-08-24 by
WS-29 MT-1f slice 1 — done-when 4a, the `ReservedSlug` refusal (owner ruling B7,
a live-defect fix: `api`/`app`/`www` were registrable through the public form),
and done-when 8a, `/signup` with no session redirects to `/signin`; **repair round
1 the same day** — the reserved set extended additively to 21 labels, 8a re-worded
to name `currentIdentity()` and its defence-in-depth motivation**) · CP-2e BUILT 2026-08-20
(the signup Console-mirror reconciler — build + R8 fence; RUN/deploy/key/flag
owner-gated) · CP-6
mechanism BUILT (refusals ship OFF) · CP-8 SLICES 1+2 BUILT 2026-08-22 (the
Operator Console — slice 1 the live customer-management surface, slice 2
provision-a-new-customer create-only; a SEPARATE Next.js app
`workbench/operator_console/`, DARK; deploy + operator-token env + staff Entra
owner-gated) · CP-9 SUBSTRATE HALF BUILT** —
**CP-9's substrate half BUILT 2026-08-18**, the same day it was minted and
twice audited: migration `007_payments.sql`, `customer_console/payments.py`
(the seam, the order state machine, integer paise, the one `fulfil`),
`lifecycle.can_pay`, `credits.LEDGER_REASONS`, six routes, and **WS-30 SC-4g
(i)–(v) server-side** — then **INDEPENDENTLY VERIFIED, FAILED on two blocking
findings, REPAIRED, then ADVERSARIALLY REVIEWED and REPAIRED AGAIN on a P0 and
two P2s (2026-08-19)**: **115** tests in
`tests/unit/test_customer_console_payments.py` against a real Postgres 16,
**0 skipped**, every load-bearing fence shown red first.

**§6 item (j) — the MANUAL / bank-transfer activation
(`POST /billing/subscriptions/activate`) BUILT 2026-08-21 (`cc-manual-activate`)**:
the Operator-only offline twin of `payments.fulfil` — `org_subscription`
`provider='manual'` + the paid seat grant + an optional `manual`-reason credit,
one transaction over the existing store seams (no new seam), refusing **409** on
an org that already holds an active subscription (the double-grant guard). The
check-then-grant is serialised by an org-keyed advisory lock
(`store.lock_org_activation`, the seat/discount-cap idiom), so the one-grant
guarantee holds under CONCURRENCY, not merely for a sequential repeat — a real
two-thread race is the fence (`test_two_concurrent_activations_grant_exactly_once`,
red-first without the lock). Suite **145/145** against a real Postgres 16, 0
skipped; the authz 401 mutation-proved.
Ships **DARK** — the Console deploys nowhere, and both the operator token and
issuing it stay **OWNER-GATE** (§8). `credits.LEDGER_REASONS` grew one member
(`manual`); no migration.

**CP-8 SLICE 1 BUILT 2026-08-22 (`cp-8-operator-console`) — the Operator
Console's live customer-management surface.** Two halves, both DARK. **Backend:**
the §4.1a cross-org read `GET /orgs` (`store.cross_org_summary` + `OrgListView`),
the one read that spans organizations — Operator-only (a `cc_live_` customer key
is 401), returning per company its plan/seat grid (folded through the ONE
`seat_counts` vocabulary, byte-identical to `GET /billing/summary`), credit
balance, lifecycle + subscription status, trial expiry, and MRR (paise, the one
`payments.paise` denomination, **zero unless the subscription is active** — an
agent-proposed default, D16/D17). No new seam, no migration.
`tests/unit/test_customer_console_operator_list.py` — R8 against a real Postgres
16, **0 skipped**, joined to §7's command block and `pr-check.yml`'s skip-guard
(Console hand-list 7 → 8) in this same change. **App:** a SEPARATE Next.js app
`workbench/operator_console/` (D35 — its own package.json/build, NOT a route in
the customer workbench; theming-exempt per D35.4), with a customers list + a
customer-detail surface wiring the management ACTIONS (activate subscription,
grant seats, add AI credits, suspend/resume) through **server-side BFF routes**
that hold `CUSTOMER_CONSOLE_OPERATOR_TOKEN` + `CUSTOMER_CONSOLE_URL` — the token
NEVER reaches the browser. Staff auth is gated behind an **interim server-side
staff secret** (`OPERATOR_CONSOLE_STAFF_SECRET`, clearly marked); the staff
Entra directory (D35.3) is an OWNER dependency, not built. **DEFERRED to later
CP-8 slices:** the payment-reconciliation queue (NULL-`order_id` +
capture-after-terminal `payment_event` rows), the nightly seat/subscription
drift job, invoice rendering. **OWNER-GATE go-live:** deploy the app, wire a
hostname/Caddy route, set the operator token in its env, stand up the staff
Entra app.

**CP-2c (the self-serve signup flow — the form over CP-2a's API) MINTED
2026-08-19 (D46, owner directive); CP-2d (passwordless email OTP via Resend)
MINTED + FIRST SLICE BUILT 2026-08-22 (owner directive) — the provider + flag
(`EMAIL_OTP_ENABLED`) ship DARK; the DB-adapter + code-entry-page prerequisites
are named-deferred and the two runtime flips are OWNER-GATE.** CP-2c's hard
dependency —
MT-1j slice 4 (`saas_multitenancy.md` §11), the Console↔tenant seam it
orchestrates — is **✅ MET on the operator arm as of 2026-08-19**: the
`org_placement` write and the `provision_local_organization` seam both ship.
◐ **CP-2c SLICE 1 BUILT 2026-08-19 — the deployment-key provision arm**
(done-when 6 + 8's Console half): a key holding `{resolve, provision}`
provisions an organization onto itself, one holding only `{resolve}` is refused
403 and the refusal is logged, and the D46.6-item-1 amendment landed with it
(`deployment_label` → `str | None`, both arms' rules in the handler, operator
missing-label **422→400**, slice 4's no-inference fence amended red-first).
Zero migrations, zero new engine sites; 409 passed / 0 skipped; six mutations
killed. ◐ **CP-2c SLICE 2 BUILT 2026-08-20 — the gateway `POST /signup/provision`
route + the Console-provision client** (done-when 2–5): a NEW
`access.membership_of(email)` tenant read (beside slice 7's `org_owner_of`); the
route mounted in `gateway/main.py` mirroring `routes/signin.py`'s posture
(BFF-internal bearer, session-email-only, IDENTITY-ONLY — never binds a tenant);
the two-plane orchestration **tenant plane FIRST** (step 0 read-only pre-flight
`membership_of` → `AlreadyMember` / `org_owner_of` → `SlugTaken`; step 1 the
hard one-email-one-org guard, its two typed raises the TOCTOU backstop mapped to
the same two codes; step 2 the Console deployment-key arm, which cannot
permanently refuse a fresh tenant-born slug so only transient failures →
`ConsoleUnavailable`); the flag `SELF_SERVE_SIGNUP_ENABLED` minted as a GATEWAY
settings field (exact-`"true"`, default OFF → `SignupDisabled`); slug shape
validated at the route (`MissingSlug`/`InvalidSlug` 400s, a DNS-label-safe
`fullmatch` — agent-proposed default, D16/D17); GST validated at the route
(`MissingState`/`InvalidGstin` 400s) and threaded to the Console org row; the
Console-provision client added to `acb_auth.console_resolve` (the ONE Console
httpx client) and `test_console_dependency_boundary.py`'s allow-list grown from
ONE importer to EXACTLY TWO (`signin.py` + `signup.py`). Zero migrations, zero
new engine sites; **39 route tests / 97 in the verify set, 0 skipped on both
ladders**; every auth/tenancy clause mutation-proved (drop the flag →
`SignupDisabled` red; trust a body email → the R11 400 red; map the wrong
exception → the code red; skip `org_owner_of` + uncaught step-1 raise → the
`SlugTaken` path red; drop the slug shape gate → a blank slug returns the false
`ConsoleUnavailable`/a spaced slug 200-admits, red). ◐ **CP-2c SLICE 3 BUILT
2026-08-20 — the `signIn` callback self-serve-signup "limbo" branch, NARROWED to
zero-org-only after a P1 security review** (done-when 7 / §6(j) row vii): under
`SELF_SERVE_SIGNUP_ENABLED === "true"` (Next-side env) the callback admits an
org-less session ONLY for the genuinely org-less person, keyed on the gateway's
new load-bearing `signup_eligible` signal (True for the zero-org
`console-empty` outcome ALONE), so the ordinary redirect lands them where
`/signup` is reachable; caches nothing (the callback writes no cache, the
gateway's zero-org `invalidate` still fires); flag unset/any-other-value → row
iv verbatim, byte-identical to today. ⚠️ **The funnel does NOT key on the
`AccessDenied` code** — a SUSPENDED/non-paying org (`console-refused`,
`cache-dead`) and a seat-capped one (`console-error`) carry that code too and
must KEEP being refused, since registry suspension is enforced only at the
resolve door; keying on the code would readmit a non-paying customer. **The
repair DOES touch the gateway, additively** — `console_resolve.py`
(`ResolveDecision.signup_eligible`, set True on the zero-org return alone),
`gateway/routes/signin.py` (passes it through) and
`test_deployment_resolve_cache.py` (R8 fence that the three suspended/seat-cap
outcomes are `signup_eligible=False` against the LIVE DB) all changed;
`console_resolve.py` still reads NO signup flag and the admit decision stays the
callback's. Build+test only; the live `SELF_SERVE_SIGNUP_ENABLED` flip stays §8
gate 8. Zero migrations, zero new engine sites; source-regex pins in
`signin.test.ts` (positive trigger + negative no-`AccessDenied`-key pin) plus the
R8 suspended→False assertions. ◐ **CP-2c SLICE 4 BUILT 2026-08-20 — the
`/signup` form UI** (done-when 1 + done-when 4's client half; BUILD+TEST only,
dark). A NEW Next route `workbench/control_plane/src/app/signup/` mirroring
`signin/`: a server `page.tsx` (`export const dynamic = "force-dynamic"`,
providers derived from the one `configuredProviders` seam) that REDIRECTS to
`/signin` when `SELF_SERVE_SIGNUP_ENABLED` is not exactly `"true"` (redirect, NOT
a 404 — audit B4a) and otherwise renders a dark client form (org display name ·
slug · registered state required · GSTIN optional), rendering the outcome codes
through the ONE `errorCopy` seam (`signInErrorMessage` — no duplicated copy).
Because the gateway's `POST /signup/provision` is BFF-internal-bearer and
session-email-only, the form POSTs to a NEW `/api/signup` Next hop
(`src/app/api/signup/route.ts`) that attaches the acting identity through the one
door (`lib/gateway.ts` — `requireIdentity` + `gatewayHeaders`, mints no bearer of
its own) and forwards ONLY `{slug, display_name, registered_state, gstin}` —
never `email`/`org`/`deployment_label` (R11; the owner is the session email,
resolved server-side, and the gateway 400s those as `InvalidBody`). The slug and
GSTIN regexes are mirrored client-side as advisory UX (`signup.py` stays the
fence); the state list is an agent-proposed default (D16/D17). Design-system
primitives only (`Input`/`Select`/`Button`/`Icon`, semantic tokens —
conformance-clean, theme-switch-ready). Zero migrations, zero new engine sites;
source-regex fence `src/app/signup/signup.test.ts` (both flag positions + the
three `errorCopy` codes + the hop's forbidden-key drop, shown red-first) and the
behavioural `tests/unit/test_signup_provision_route.py` unchanged and green. **The
full self-serve signup flow now exists in code, dark.** **Issuing or widening a
REAL deployment key stays §8 gate 7; flipping `SELF_SERVE_SIGNUP_ENABLED` live is
§8 gate 8; the Console is still deployed nowhere.** ◐ **CP-2e — the out-of-band
Console-mirror reconciler that closes CP-2c done-when 5's named un-metered-org
gap — is BUILT 2026-08-20 (this file's CP-2e ticket; build + R8 fence).** The
owner-decided mechanism is a SWEEP plus the new `organization.console_mirrored_at`
marker (with `gstin`/`billing_state` persisted tenant-side so the sweep can
re-drive faithfully — they lived nowhere on the tenant plane before): the
`reconcile` function (`acb_auth.console_resolve`), the tenant migration
(`181_signup_console_mirror.sql`), the signup step-1 GST-profile write + step-2
marker write (`acb_common.provisioning` seam), the `scripts/reconcile_console_mirror.py`
CLI, and the R8 fence (`tests/unit/test_signup_reconciler.py`, both ladders, 0
skips) are BUILT, dark. RUNNING the sweep against a live Console is owner-gated
(§8 gates 2 + 8): `is_wired()` is False until then, so the CLI is a logged no-op.
The code is built where the acts are the agent's; the acts that go live are the
owner's.

⚠️ **The 2026-08-19 review round, because its P0 was a money-losing defect that
all 110 tests were green over.** **P0 — a failed payment ATTEMPT permanently
bricked the ORDER.** One Razorpay order accepts many attempts until one
captures, but `payment.failed` transitioned the order to `failed`, which is
terminal — so the retry the customer made inside the same Checkout arrived, the
capture succeeded, and `fulfil` refused: **₹1,416 taken, zero grants, a false
`payments.already_fulfilled` info line, and a 200 that stopped Razorpay
retrying**. Repaired in both halves: **(a)** a failed attempt is recorded and
logged and transitions nothing (9.5; `failed` keeps its place on 9.2's graph
with **no writer**, for a surface-half customer cancel), and **(b)** the single
`except TransitionRefused` arm is split on the order's status — `captured` keeps
the benign info line, **anything else is an ERROR** carrying the amount and both
provider identifiers, which is the same class as
`payments.webhook_unknown_order` and is now owned by **CP-8** alongside the
NULL-`order_id` receipts. **P2-1** — the redeem-attempt log sat *below*
`_verified_code`, so the "measured attempt rate" 9.3(6) defers the rate-limit
decision to was **zero under probing**; it now runs first, prefix-only.
**P2-2** — a non-ASCII byte in `x-razorpay-signature` reached
`hmac.compare_digest(str, str)` and raised `TypeError`, i.e. an unhandled
**500** on the one route with no bearer token; the comparison is over bytes.
Five new fences, each shown red against `5acad0c1` first, including a structural
`test_no_code_path_drives_an_order_to_failed` (mutation-proved). Three further
findings are recorded rather than fixed (§6 CP-9).

**The five findings of the 2026-08-19 verification round and
where each is answered** (every behavioural probe had passed; these are fence,
doc and severity defects): **F1** — clause 2's parametric transition test
asserted nothing for the ten non-terminal source pairs, so three off-graph edges
were fenced nowhere; it now compares all 25 against a transcribed graph and the
clause carries its mark (done-when 2) · **F2** *(blocking)* — the replaced
provider order was recorded as *"the harmless orphan direction"* and **the
orphan has a PAID direction**: a capture with no matching order is money
received with nothing granted, restated in both specs, given an owner in **CP-8**
and an `ERROR`-level alert in the code (§6 CP-9 decision 2) · **F4** — the
declared `METERING_EXEMPTION` deviation is now an owner-ratification item (§9
item 6) rather than an agent's standing amendment to §9.3(4) · **F5** — the
transitive walk's **depth** was unfenced (narrowing it to one hop left all 105
green); two fences now feed it graphs whose answer is known and non-empty ·
**F7** — the allow-listed pair licenses the whole subtree under
`payments.fulfil`, so that subtree's writer set is contents-pinned (§9.3(4)).
Its **surface half
remains held back** (SC-4a's UI and its two write proxies, the operator
code-issue surface, the invoice document, the four-item flip set), and **no
Razorpay account exists or may be created by an agent** — the whole seam runs
against a fake provider carrying the real HMAC signer, so **SC-4g clause 2's
test-mode capture rehearsal is NOT met and is not claimed**. Three build-time
decisions no clause named — including **a declared deviation from 9.3(4)'s
"exactly one edge"**, forced by CP-6's pre-existing metering draw on the
org-key Router route — are recorded in the CP-9 ticket rather than in a commit
message. · **CP-2b BUILT 2026-08-18 (both
halves)**, the deployment half landing the same day the Console half did, after
two audit rounds (B1–B7 + two drift items, then B-a…B-g + one mis-anchor) whose
answers are in §6(d)–(k) — **271** Customer Console tests against a real
Postgres 16 per R8 (219 + the **52** of `test_customer_console_resolve.py`),
plus **50** in `test_deployment_resolve_cache.py` against a real **tenant**
Postgres, **4** database-free structural fences in
`test_console_dependency_boundary.py` and **7** in
`test_signin_resolve_route.py` · CP-3 was **rejected by independent
verification once** and rebuilt (see its ticket) · CP-5 · CP-7 · CP-8 spec only ·
**CP-4b (streaming pass-through) MINTED 2026-08-18, spec only** — it carries the
half of CP-4's done-when that was never met (`stream: true` returns 501) and
CP-4's ✅ is amended accordingly ·
**CP-9 (the `payment_provider` seam — Razorpay) MINTED 2026-08-18, spec only.**
It is the ticket **three documents already cited and nobody had written**: §2's
non-goal, `work_plan.md` §6(b) and §3 D35.5/D42.1 all pointed at **CP-8** for
payments, and CP-8 is the **Operator Console and reconciliation**. The seam
existed in neither code nor ticket — a repo-wide search on 2026-08-18 returned
one hit, `org_subscription.provider CHECK (…'razorpay'…)`
(`001_customer_console.sql:163`). All four citations are corrected in the same
change. CP-9 answers the question that blocked WS-30's checkout dispatch —
**a customer-authenticated write path into a service whose customer key is
read-only by design** — with **no fifth auth scheme**: the org key gains exactly
two writes that cannot move value (§6 CP-9 §9.3), value moves only on a
signature-verified webhook or an operator-issued code, and the CP-3 fence class
widens to *"no org-key route writes an entitlement or ledger row"*. One
code-derived correction rides with it: `can_use_ai` is the **wrong** lifecycle
gate for paying (`auth.py:217` shuts the door on exactly the `suspended`
customer who needs to pay), so `lifecycle.OrgCapabilities` gains `can_pay` —
in the **one** state machine, never a second frozenset ·
**CP-9 was then RE-AUDITED for dispatch and returned GO-NARROWED with nine
blocking doc corrections (B1–B9) + six nits — all answered 2026-08-18 in this
same file and in `subscription_console.md`, still spec-only, no code touched.**
The four that changed the design rather than the wording: **B1** — redeem
**does** grant when a verified code brings the total to 0, and 9.3(4)'s fence is
transitive with **one** carve-out allow-listed by name (as minted, the fence was
red by construction and an implementer would have narrowed it — the CP-6 failure
mode) · **B4** — the ₹0-vs-paid equivalence fence was **unsatisfiable** ("only
the reference differs" is false against `gen_random_uuid()` ids and `now()`
defaults); it now names the carrier (`seat_grant.reason`), the excluded field
*classes*, and the one difference that is **expected and asserted** ·
**B6** — nothing could read an order back, so SC-4a's *"a failed payment says
so"* was unbuildable; `GET /billing/orders/{id}` and `GET /billing/orders` are
minted in 9.3a · **B8** — duplicate-safety was attributed to
`provider_event_id`, but Razorpay sends **different** event ids for one capture;
the money guard is the terminal-state rule, and the event-id key is transport
dedup. The narrowed dispatchable slice (substrate half) and the held-back
surface half are written into §6's sequencing note ·
**§6's held-back list gained TWO ENTRIES on 2026-08-19**, from WS-30 SC-4a's own
NO-GO dispatch audit and recorded here because both are **this service's** to
answer: **(e)** the browser→provider **hand-off** (`SC-4h`, jointly owned) —
`create_order` returns no payment link, the seam is two methods, and browser
Checkout's `key_id` + `order_id` are exposed by no route, `OrderView` being
pinned to an exact 14-name set; and **(f)** a small **customer-key read of the
priced `plan_catalog`**, without which SC-4a's launch done-when 1 has no data
source. Both were named so the next WS-31 slice would not have to rediscover
them — and **(f) is now ✅ BUILT, 2026-08-19**: `GET /billing/catalog` on the
`can_pay` door, active rows only, integer paise through the one
`payments.paise`, two red-first fences, **no migration**, carried by **done-when
18**. The suite is **117** against a real Postgres 16, **0 skipped**; the
ten-suite Console block **395**. **(e) stays held back** — it needs a
capability decision and the owner-gated Razorpay account · **(g) is now ✅ BUILT,
2026-08-20 (WS-31 `ws-31-seats-mvp`)** — a customer-key **seats read**
(`GET /me/seats`, `can_pay` door), item (g) / done-when 19, so the post-signup
MVP's seat grid (`subscription_console.md` SC-1a) has a customer-reachable
source: the ONE shared `_seat_grid(conn, org_id)` helper (extracted from
`billing_summary`'s plan loop; both routes now call it) with the org id from
`caller.organization_id`, the four counts folded from `store.seat_rows` through
`seats.seat_counts` (no second SQL, no recompute), a `SeatsView`/`SeatPlanView`
pair mirroring the catalog pair, two red-first R8 fences, **no migration**. The
suite is now **119** against a real Postgres 16, **0 skipped**. The seat WRITES
stay owner-gated (§8 gate 4) · **(h) is now ◐ BUILT 2026-08-21 (WS-31
`ws-31-seat-assign`)** — the write-side twin of (g): a
**customer-authenticated seat WRITE** on a new deployment-key `seat_admin`
capability door (console-authoritative — org from placement∩membership, actor
`org_membership.role`/`status` read on the resolved pair, no oversubscription via
self-serve), reusing the `deployment_or_operator` seam and the operator
`assign_seat` composition (no fork, no second seat SQL), **no migration**. Endpoint
+ door + **seven** red-first R8 fences (`TestTheSeatAdminWrite`) green against a
real Postgres 16, 0 skipped; ships dark (no live key carries `seat_admin`). The
live write against a real customer org and adding `seat_admin` to a real key stay
owner-gated (§8 gate 4 / gate 8); the "manage seats" SURFACE is WS-30's. See §6
item (h) and §9 residual 7 (the surface transport) · **(i) is now ✅ BUILT
2026-08-21 (WS-31 `ws-31-members-read`)** — the customer
**member-list read** the manage-seats surface (SC-2b) needs to pick WHOM to seat:
`GET /me/members`, the exact sibling of item (g)'s seats read (org-key `can_pay`
door), returning `email · role · status` per member from the ONE new
membership-list read `store.org_members` (the `org_membership ⋈ user_identity`
join idiom), org-scoped by `caller.organization_id`, two red-first R8 fences on
item (f)/(g)'s suite, **no migration**. Member EMAILS are PII → rides item (g)'s
"any signed-in member" hardening ticket; per-member seat summary DEFERRED;
admin-gating the roster recorded as an owner-overrulable alternative. Carried by
done-when 20; a separate agent built it and an independent audit validated the
clause first. See §6 item (i) ·
⚠️ **CP-2b's deployment half then FAILED independent verification on one
blocking finding, F1, and was REPAIRED the same day (2026-08-18).** The
ship-dark guarantee was false in the half-configured case — the `signIn`
callback gated on `CUSTOMER_CONSOLE_URL` alone while `is_wired()` requires that
*and* the deployment key, and `CUSTOMER_CONSOLE_URL` is already set on any
Console-connected box for the billing surface, so the hop would have armed
itself at merge and refused sign-ins during ordinary deploy windows. The repair
is a **dedicated Next-side flag, `CUSTOMER_CONSOLE_RESOLVE_ENABLED`, default
unset = OFF**, which the callback now gates on alone (§6(f)/§6(g); flipping it
on a live deployment joins §8 gate 7). Every other acceptance probe passed. ·
⚠️ **The repaired half was then REVIEWED ADVERSARIALLY and came back
REQUEST-CHANGES on five defects — all five REPAIRED 2026-08-18, each shown red
first.** They are recorded here because four of them are in the *failure*
semantics, which is the part of this ticket nobody exercises until an incident:
- **F5 (P0) — a half-provisioned gateway SILENTLY ADMITTED.**
  `resolve_for_signin` fails open when `is_wired()` is false (correct for the
  module: that is what ships dark means) and `POST /signin/resolve` passed the
  answer through. The two switches sit in **different containers with different
  env files**, so "Next flag on, gateway env empty" is the ordinary topology —
  every sign-in admitted, no seat allocated, nothing asked, and **no log line at
  all**. The route now checks `is_wired()` itself and refuses with
  `ConsoleUnavailable`, logging `signin.resolve_unwired` at error. Fence:
  `tests/unit/test_signin_resolve_route.py` (**new** — nothing tested the route
  at all, which is how this survived).
  ✅ **This also closes finding F4**, raised by the same verification round: the
  header sentence below (*"gateway env half-filled → refuses"*) was **false**
  whenever the gateway was reachable, because the box answered `admit: true`.
  It is true now, and it is true by construction rather than by intention —
  re-verified 2026-08-18 against the route, not against this paragraph.
- **P1-1 — a Console 5xx or 401 showed the WRONG refusal.** Everything that was
  not 200/403 mapped to `AccessDenied`, so an nginx 502 told cache-fresh users
  *"your account isn't authorized"* and a rotated `cc_depl_` key told every user
  of every tenant the same — while the identical outage over a closed port
  degraded gracefully. 5xx/401/408/429 now take the unreachable path (§6(j) row
  v narrowed accordingly).
- **P1-2 — a 403 wrote a PERSON fact onto an ORG row and locked out every
  member, permanently.** The dead-state short-circuit outranked every freshness
  bound at any age forever, and the only thing that could clear it was a 200 it
  prevented from being requested; recovery was a manual `UPDATE` on the tenant
  database. It is now bounded by `MAX_STALENESS` — self-healing within 24h, and
  still fail-closed while the Console is unreachable.
- **P1-3 — an org MOVE left a live admission behind.** The unprovisioned-slug
  branch returned before `_forget_others`, so a person moved to an org placed
  here but not yet bootstrapped was admitted back into the org they LEFT for up
  to 24h on the next outage.
- **P2 — `_age_seconds`'s `max(0.0, …)` floor was documented fail-closed and was
  fail-open**: a record stamped in the future read as maximally fresh. Age is
  honest now, with a **measured** 60s skew tolerance (the tenant container's
  `now()` runs 0.40 s ahead of the app process, so a hard zero floor turns the
  read-through cache off entirely).
Two review notes were taken in the same round: the refusal code is
`encodeURIComponent`-ed into `/signin?error=`, and the R8 fixture disposes the
engine it strands on setup. ·
✅ **CP-2b is BUILT on both sides of the wire (2026-08-18), and the split is
still the thing to understand.** The **Customer Console side**: the fourth auth
scheme (`cc_depl_…`, capability set exactly `{resolve}` *as CP-2b shipped it —
`provision` exists beside it since CP-2c slice 1, 2026-08-19, per-capability
enforced, issuance still gate 7*, `auth.py`), migration
`006_deployment_key.sql`, `POST /registry/resolve` answering **two schemes with
two shapes** chosen by the credential (`main.py`), and — added with the
deployment half — the per-organization **`capabilities` block** computed by the
one `lifecycle.capabilities_of()` at `main.py:771` (`_capability_block`), on the
deployment arm alone. The **deployment side**: `packages/acb_auth/acb_auth/
console_resolve.py` (the client, the read-through cache, `invalidate()`, the
projection read/write), its ONE caller
`apps/services/gateway/gateway/routes/signin.py` (`POST /signin/resolve`), the
four settings fields, the **new `signIn` callback** in
`workbench/control_plane/src/auth.ts` with the two refusal codes in
`errorCopy.ts`, and tenant migration **`177_console_resolve_projection.sql`**
(`org_membership.resolved_at`, `organization.registry_status`,
`organization.registry_capabilities`; number taken by listing `infra/postgres/`
at build time per R1 — the next free number is now **178**).
**Every done-when clause is met**; 8, 10 and 12 were split per half by the
audits and both halves of each are now closed. *The seat cap is consulted by
the product*: the box asks before admitting anybody.
**The whole path SHIPS DARK, and each side has its OWN switch** *(rewritten
2026-08-18 — repair of finding **F1**; the previous sentence claimed one
guarantee for two halves that did not have the same one)*:
- **Next side** — the `signIn` callback gates on **`CUSTOMER_CONSOLE_RESOLVE_ENABLED`
  alone**, default unset = OFF. Anything but the exact string `"true"` returns
  `true` before doing anything at all: no fetch, no latency, no new failure
  mode, **regardless of what `CUSTOMER_CONSOLE_URL` is set to** — that variable
  keeps serving the billing surface and is not read by the callback at all.
- **Gateway side** — `console_resolve.is_wired()` still requires **both**
  `CUSTOMER_CONSOLE_URL` and `CUSTOMER_CONSOLE_DEPLOYMENT_KEY`, and with either
  unset `resolve_for_signin` admits without a call, a query or a write.

Four fences pin it: `test_half_configured_is_not_wired` and
`test_an_unwired_deployment_admits_without_asking_anything` (Python), and
`signin.test.ts`'s *"is inert until `CUSTOMER_CONSOLE_RESOLVE_ENABLED` is
exactly `true`"* plus *"does not arm itself off `CUSTOMER_CONSOLE_URL` (F1)"*.
**Flag ON is a claim that the box is wired, so it fails CLOSED like one:** every
transport or provisioning failure — the gateway unreachable during a deploy
window, or the gateway's own env half-filled — refuses with
`ConsoleUnavailable`. A deployment that says it is wired while half-provisioned
is a provisioning error, and silently admitting there would give back exactly
the fail-open posture CP-0 removed.
⚠️ **That sentence was FALSE until 2026-08-18 (finding F4, closed with F5's
repair).** With the flag on and the gateway *reachable* but its own env empty,
`POST /signin/resolve` answered `admit: true` and the BFF admitted — the
half-provisioned case was the one case that did **not** refuse. What makes it
true is a check in the **route**, not in the module: reaching `/signin/resolve`
at all means somebody declared the box wired, so an unwired box refuses there
(`signin.resolve_unwired`, error level). The module's own unwired branch still
admits, deliberately, because ship-dark is a statement about a box nobody
configured — and that split is exactly what F5 was: two true statements one hop
apart, composing into a false one. Fence:
`tests/unit/test_signin_resolve_route.py`.
Issuing a real `cc_depl_` key, writing
either gateway variable into a live deployment's env, **or flipping
`CUSTOMER_CONSOLE_RESOLVE_ENABLED` on a live deployment**, remains 🔴
**OWNER-GATE** (§8 gate 7).
*(The deployment half was audited for dispatch twice on 2026-08-18 — NO-GO on
B1–B7 plus two status-drift items, then GO-NARROWED on B-a…B-g plus one
mis-anchor. All fifteen are answered in §6(d)–(k) and in the clauses, as
agent-proposed defaults the owner may overrule (D16/D17), and all fifteen are
built as answered.)*
⚠️ **One thing was decided during the build that no clause named, recorded here
rather than in a commit message: what the box does with the Console's `409` at
the seat cap.** §6(j)'s table has four outcomes and the 409 is a fifth. It is
handled as a **refusal that caches nothing**, carrying **`AccessDenied`** — at
the cap the person genuinely holds no seat and *"ask your admin"* is exactly the
remedy — and **no third refusal code was minted**, because the ticket names two
and a third is the owner's call rather than a build's. Fence:
`test_a_seat_cap_refusal_fails_closed_and_caches_nothing`. Agent-proposed
default, owner may overrule (D16/D17).
⚠️ **The amended ticket was then RE-AUDITED the same day and came back
GO-NARROWED on seven further blockers B-a…B-g plus one mis-anchor — all eight
answered 2026-08-18 in §6(g)/(i)/(j)/(k) and in clauses 5/6/7/8/11/12, again as
agent-proposed defaults (D16/D17).** What a reader must carry away from that
round, because each one changes what gets built: the tenant-side CI database
answers to **`TENANT_LADDER_DATABASE_URL`** and **never** to `DATABASE_URL`,
which would arm two existing tenant-coverage tests that fail by construction in
this job (§6(i)(2)) · the box branches on the **resolve OUTCOME** (200-with-one /
403 / 200-with-many / 200-with-none), never on a lifecycle string, and
`capabilities.sign_in` is **always `true` in a 200 body** — clause 6's dead-state
rule and clause 12's fence had been written against an input the shipped Console
cannot produce (§6(j)) · the projection write's join key is the **org SLUG**,
because the Console's `organization_id` is a UUID in a different database
(§6(k)), and that answer adds a **third** projection column,
`organization.registry_capabilities` · the internal-token residual in clause 11
is **one credential, not two**. Its earlier
re-audit history, kept because it is what made the ticket
dispatchable — **five blocking corrections C1–C5 landed 2026-08-18**:
the freshness bound was restated honestly as a **pair** (TTL while the Console is
reachable, `MAX_STALENESS` while it is not) with a cached **dead state** applying
immediately at any freshness (clauses 6/7) · seat allocation is fenced to the
**sign-in path only** with the residual internal-token blast radius recorded as a
named accepted risk (clause 11) · clause 1's exempt route set is now **exact**
(`/health` plus FastAPI's four doc routes) · the deployment-key **response schema
is specified in full and carries no `role`**, and §5.2's code block no longer
documents the forbidden shape (clause 12) · **multi-org sign-in fails closed**
with honest copy, allocating nothing (clause 9) · plus C6 housekeeping: migration
159's projection named the **cache of record**, `cc_org_status` renamed and moved
off `tenant_placement` to `organization.registry_status` (D15 — placement is
placement), the R6 no-default choice argued rather than cited, §7's R8 line
refreshed ·
two acceptance clauses are annotated rather than struck: **CP-2a's step-kill
test is OWED** and **CP-1 clause 3 was unmeetable as written** (both dated
2026-08-18 in §6) · **where it runs is an open owner decision —
[`customer_console_infrastructure.md`](customer_console_infrastructure.md)** ·
**Date:** 2026-08-18 · **verified against code 2026-08-18 by WS-31 CP-6 audit**
— the four §3.4 tables (`usage_event`, `credit_ledger`, `model_rate_card`,
`usage_rollup`) and the rest of the commercial substrate all exist today in
`infra/customer_console/001_customer_console.sql`; the ladder is 001–**007**
(`006_deployment_key.sql` taken by CP-2b's Console half, `007_payments.sql` by
CP-9's substrate half on 2026-08-18) and the next free number is **008** — R1
says list the directory at build time and re-check at merge rather than
trusting this sentence. ⚠️ **`main.py` line anchors below
moved again with CP-2b** (`resolve` gained a second scheme and two helper
functions); re-derive them at dispatch. *(The previous header's "repo-wide grep: zero
hits … none of the commercial substrate exists" was true at authoring on
2026-08-12 and false from CP-1 onward; likewise "CP-2 … spec only" — CP-2 is
**✅ BUILT**: `main.py:1283` `GET /billing/summary`, `:1315` `POST /billing/seats`,
`:1355` release *(three anchors re-measured 2026-08-20 on `ws-31-seats-mvp` from
`:603`/`:635`/`:675` — they drifted ~680 lines as CP-9/CP-2c/CP-2e landed; match
by handler name, not by number)*, pinned by `test_customer_console_seats.py:59`
(409 + buy-more, never an auto-upgrade), `test_customer_console_api.py:142`
(the cap over HTTP) and `test_customer_console_sql.py:183,217` (the partial
unique index and the cap against a real database). Line anchors re-derived
2026-08-18 **after** CP-6's edits to `main.py`; re-verify at dispatch.)*
· **Owner:** WS-31 (this spec) ·
**Decisions:** **D32** (work_plan.md §3, 2026-08-12 — eight calls, owner-directed
in session). D15, D19.2, D19.3, D22, D23, D24 are carried unchanged and must not
be re-litigated here.

> ### `The Customer Console is one central service. Tenancy is still a ROW.`
> ### `Customers buy seats and credits. They never see a model.`

⚠️ **Name disambiguation, read once.** Three near-neighbours are NOT this
system: **(a)** `workbench/control_plane/` — the tenant-side Next.js workbench,
whose directory name predates this service and deliberately keeps its path
(D41.1; renaming it is out of scope — CLAUDE.md §5: do not refactor the tree to
conform); **(b)** migration `159_control_plane.sql` — the tenant catalog inside
each Metorite deployment, which under this spec becomes a **local projection**
of the service defined here (an applied migration is never renamed);
**(c)** WS-30's **Subscription Console** — the *customer-facing* billing
surface, a client of this service. This document means the operator-side
**service**, and says "the Customer Console" for it throughout.

---

## 1. What this is, and why it is not a new idea

`saas_multitenancy.md` §0.9.2 already names three planes and defines the first as:

> **Control** — organizations, identities, placement, entitlements, subscriptions,
> usage, credit ledger. **Shared, cross-tenant by design. No RLS — it must read
> across tenants.** It is the operator's view. Never holds tenant business data, so
> a compromise exposes contracts, not customers' mail.

That plane is currently **co-resident inside each Metorite deployment**
(migration 159). With §5.1's silo rollout that means N copies, each knowing about
exactly one customer, and no place that knows all of them. This spec extracts it
into **one central service** and gives it the two jobs it did not previously have:
**selling seats** and **metering AI**.

Nothing about D15 changes. Tenancy inside Metorite remains
`organization_id` + FORCE ROW LEVEL SECURITY bound at the `get_db()` seam; the
deployment remains a *placement*. Whether a customer sits alone on a database or
pooled with fifty others is a `tenant_placement` row, exactly as migration 159
already built it. **This spec is what makes that placement indirection worth
having** — a central plane can move a customer between placements; N sovereign
deployments cannot.

## 2. Scope and non-goals

**In scope.**
1. The Customer Console service — a new deployable in this monorepo.
2. The **organization registry**: orgs, deployments, placement, and the global
   `user_identity` / `org_membership` authority.
3. The **subscription engine**: plan catalog (Center packages per D23/D24),
   subscriptions, **seats purchased vs assigned vs available**, and the hard cap.
4. The **AI Router**: tier→model binding, provider keys, the rate card,
   `usage_event`, the credit ledger, the balance gate, per-member caps.
5. The **Metorite-side rework**: `/v1` becomes a forwarder; customer-facing
   model selection is removed; tiers become the only model vocabulary.
6. The **Operator Console** surfaces backing `saas_multitenancy.md` §4.1a.
7. The **`payment_provider` seam and the money→entitlement path** — **CP-9**,
   minted 2026-08-18: `payment_order`, the provider call behind a seam, the
   signature-verified webhook, and the **one fulfilment function** through which
   both the paid and the ₹0 discount paths write `org_subscription` /
   `seat_grant` / `credit_ledger`. It lives here because those three tables live
   here, and a payment that does not write them is a receipt, not a purchase
   (`saas_multitenancy.md` §4.3: *"both writing the same tables"*).

**Non-goals.**
- ~~Payment processing itself — Razorpay integration is **CP-8**~~ ⚠️ **Wrong,
  and corrected 2026-08-18.** CP-8 is the **Operator Console and reconciliation**
  (read its body in §6); it has never been the payment ticket. The
  `payment_provider` seam D19.5 fixed and `saas_multitenancy.md` §4.3 designed
  had **no ticket body anywhere in the corpus and no code** — a repo-wide search
  on 2026-08-18 returned exactly one hit, `org_subscription.provider CHECK
  (provider IN ('razorpay','manual'))` at
  `infra/customer_console/001_customer_console.sql:163`. It is now **CP-9**,
  in scope above. Two other places carried the same mis-citation and were fixed
  in the same change: `work_plan.md` §6(b) and §3 **D35.5** / **D42.1**.
- What genuinely stays out of this spec: the **customer-facing checkout UI**
  (WS-30 SC-4a / SC-4g — this service exposes the endpoints, the workbench
  renders them) and the **tax-invoice document** (WS-30 SC-5b/SC-5c — CP-9
  records gross · discount · net · GST on the order, and issues no document).
- The customer-facing billing UI — that is **WS-30** (`subscription_console.md`),
  which becomes a *client* of this service rather than a reader of tables local to
  the Metorite deployment (migration 159's projection). *("CC-local" expanded
  2026-08-18: pre-D41 "CC" meant CommandCenter, i.e. the tenant deployment, and it
  now collides with Customer Console — the last ambiguous use in this file.)*
- Module entitlement *enforcement* — the `intersect()` seam, `ModuleGate` and the
  402-vs-403 split stay in Metorite (MT-2). This service is authoritative on
  what was **bought**; Metorite stays authoritative on what is **enforced**.
- The execution-plane sandbox (§0.9.3 / MT-0c) — untouched, still OWNER-GATE.
- Any change to the D12 visibility ladder. Tenancy is *which company*; visibility
  is *who inside it*. Two axes, no third.

## 3. The domain model

### 3.1 The three questions the engine answers

| Question | Answer lives in | Authoritative |
|---|---|---|
| *Which companies exist, and where does each one's data sit?* | `organization`, `deployment`, `org_placement` | Customer Console |
| *How many seats did they buy, how many are used, how many are free?* | `plan_catalog`, `org_subscription`, `seat_grant`, `seat_assignment` | Customer Console |
| *How much AI have they burned, and what is left?* | `usage_event`, `credit_ledger`, `model_rate_card` | Customer Console |

### 3.2 Registry and placement

```sql
organization(id UUID PK, name TEXT, slug TEXT UNIQUE, status TEXT,
             created_at TIMESTAMPTZ);            -- status: trial|active|past_due|
                                                 --   suspended|cancelled (§4.1d)
deployment(id UUID PK, label TEXT, base_url TEXT, version_sha TEXT,
           health_checked_at TIMESTAMPTZ, status TEXT);
org_placement(organization_id UUID PK, deployment_id UUID NOT NULL,
              database_target TEXT, moved_at TIMESTAMPTZ);

user_identity(id UUID PK, email CITEXT UNIQUE NOT NULL, display_name TEXT,
              created_at TIMESTAMPTZ);           -- one row per HUMAN, globally
org_membership(organization_id UUID, user_identity_id UUID, role TEXT,
               status TEXT, joined_at TIMESTAMPTZ,
               PRIMARY KEY (organization_id, user_identity_id));
```

`user_identity` and `org_membership` mirror migration 159's shapes deliberately.
**159's tables are not dropped and not renamed** (R6): they remain in each
Metorite deployment and become the **local projection** of this registry,
refreshed on sign-in and cached. That is why 159 shipping "ADDITIVE AND INERT" is
now an asset — the projection target already exists and nothing has to migrate.

`deployment.version_sha` exists because §5.1 condition 3 warns that per-box drift is
how version skew arrives. One place that knows what every box is running is the
cheapest possible defence.

### 3.3 Subscriptions and seats — "purchased, assigned, available"

```sql
plan_catalog(slug TEXT PK, name TEXT, kind TEXT, price_inr NUMERIC,
             active BOOLEAN);   -- kind: center|addon|bundle (D23/D24 shapes;
                                --   prices are DATA, never code)
org_subscription(organization_id UUID PK, status TEXT, trial_ends_at TIMESTAMPTZ,
                 current_period_start DATE, current_period_end DATE,
                 provider TEXT, provider_customer_id TEXT,
                 provider_subscription_id TEXT);
seat_grant(id UUID PK, organization_id UUID, plan_slug TEXT,
           quantity_purchased INT NOT NULL, effective_from TIMESTAMPTZ);
seat_assignment(id UUID PK, organization_id UUID, plan_slug TEXT,
                user_identity_id UUID, source TEXT, assigned_at TIMESTAMPTZ,
                released_at TIMESTAMPTZ,
                UNIQUE (organization_id, plan_slug, user_identity_id)
                    WHERE released_at IS NULL);
```

The three counts the owner named, defined once so no surface recomputes them
differently:

- **purchased** = `SUM(seat_grant.quantity_purchased)` for `(org, plan)` as of now
- **assigned** = `COUNT(seat_assignment)` for `(org, plan)` where `released_at IS NULL`
- **available** = purchased − assigned

**Membership is the Core seat (D19.3, unchanged).** A person joining an
organization consumes one Core seat; Center packages and add-ons stack on top as
`source ∈ center|plan|alacarte`. Seat consumption happens at the **first successful
identity resolution** (§5.2), not at invitation — an invited person who never signs
in costs nothing, which is the behaviour an admin expects.

**The hard cap (D19.3, verbatim and non-negotiable):** an assignment beyond
`purchased` returns **409 with a buy-more payload**. It never auto-upgrades, never
silently over-assigns, and never bills for a seat the admin did not choose to buy.
Auto-upgrade on a seat cap is how a customer discovers a charge they did not
authorise, and it is the single fastest way to lose a small business account.

`UNIQUE … WHERE released_at IS NULL` is not decoration: it is what makes double
assignment impossible under a concurrent admin action, rather than merely unlikely.

### 3.4 AI credits

```sql
llm_api_key(id UUID PK, organization_id UUID NOT NULL, prefix TEXT UNIQUE NOT NULL,
            key_hash TEXT NOT NULL, label TEXT, scopes TEXT[],
            created_by TEXT, created_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ);

-- CP-4. The Router's OWN provider accounts — deliberately not acb_llm's store,
-- which reads the TENANT database (see the CP-4 ticket).
provider_credential(id UUID PK, provider TEXT, organization_id UUID NULL,
                    secret_enc TEXT, api_base TEXT, revoked_at TIMESTAMPTZ);
                    -- organization_id NULL = the platform's account;
                    -- non-NULL = a BYOK organization (§3.4)

tier_binding(tier TEXT, model TEXT NOT NULL, effective_from TIMESTAMPTZ,
             PRIMARY KEY (tier, effective_from));   -- THE tier→model map, central

model_rate_card(model TEXT, input_credits_per_1k NUMERIC,
                output_credits_per_1k NUMERIC, cached_input_credits_per_1k NUMERIC,
                effective_from TIMESTAMPTZ, PRIMARY KEY (model, effective_from));

usage_event(id UUID PK, organization_id UUID NOT NULL, user_email TEXT, agent TEXT,
            module_slug TEXT, model TEXT, tier TEXT,
            prompt_tokens INT, completion_tokens INT, cached_tokens INT,
            provider_cost_usd NUMERIC(14,8), billed_credits NUMERIC(14,4),
            request_id TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ);

credit_ledger(id UUID PK, organization_id UUID, delta NUMERIC, reason TEXT,
              ref TEXT, balance_after NUMERIC, created_at TIMESTAMPTZ);
              -- APPEND-ONLY. Balance is SUM(delta), cached in Redis.

usage_rollup(organization_id UUID, period DATE, dimension TEXT, quantity NUMERIC,
             PRIMARY KEY (organization_id, period, dimension));

member_ai_cap(organization_id UUID, user_identity_id UUID,
              monthly_credits NUMERIC, on_exhaustion TEXT,
              PRIMARY KEY (organization_id, user_identity_id));
              -- on_exhaustion: 'degrade' | 'block'  (department_centers Phase E)
```

**Carried from D19.2, unchanged and not re-openable here:** a credit is the ₹10
purchase and display unit; each call draws **fractionally at provider cost × 2**
through the rate card; the card is denominated **natively in INR** with no runtime
FX; `provider_cost_usd` is bookkeeping only; **LLM calls and per-minute STT are
metered, embeddings and WhatsApp per-number fees are not** (they are absorbed into
the Center package prices).

**`request_id UNIQUE` is load-bearing.** Retries, stream reconnects and the
streaming usage-rebuild path in `v1_compat.py` all create double-write
opportunities. A customer billed twice for one call is a credibility event, and the
constraint is what makes it impossible rather than unlikely.

**Never `UPDATE` a balance column.** Balance is `SUM(credit_ledger.delta)`. The
audit trail is worth most at exactly the moment a customer disputes a charge, which
is exactly when a mutable balance column has already destroyed it.

**Rollover (D32.6).** Purchased credits **roll over indefinitely while the
subscription is active** — they are a ledger balance, so rollover is the default and
expiry would be the thing requiring machinery. They are not refundable in cash, and
they expire only at `cancelled` + export window (§4.1d). Draw order is FIFO by
purchase only if a future promotional grant carries an expiry; until then a single
balance needs no lot tracking, and building lot tracking before a lot exists is
speculative work.

## 4. The AI Router

### 4.1 Why the Router is the metering point

The Router sits in the provider call path, so it **already sees the tokens**.
Metering there costs one row write and no extra network hop. Metering anywhere else
means either trusting a client's self-report or adding a synchronous round trip to
every completion.

More decisively: with §5.1's silo rollout, "meter inside Metorite" means the
meter, the rate card and the credit balance all live **on the customer's own box**.
That is not a meter, it is a suggestion — and it puts your margin on hardware you do
not control. This is the argument that reverses `saas_multitenancy.md` §3.1, and it
is an argument §3.1 never weighed because it was written imagining a single pooled
deployment (see D32.1 and the banner now on §3.1).

### 4.2 The tier contract — model access, reworked

**Tiers are the only model vocabulary that exists outside the Router.** The current
four (`tier-fast`, `tier-balanced`, `tier-powerful`, `tier-stt` —
`acb_llm/client.py:86`) are kept exactly as they are; what changes is that
`_TIER_ALIAS_MAP` stops being *truth* and becomes a *cache* of what the Router
publishes.

```
GET /v1/tiers → {
  "version": "<opaque>",
  "tiers": [ { "id": "tier-balanced", "context_window": 128000,
               "max_output": 8192, "supports_tools": true,
               "supports_vision": false, "supports_cache": true } ]
}
```

**This document is not optional and it is the one genuinely new piece of
engineering.** `v1_compat.py` does real work that needs model knowledge:
`_fit_context_window` (`:168`), `_clamp_max_tokens` (`:56`), the provider-aware
prompt-cache breakpoints in `acb_llm/prompt_cache.py`, and per-provider message
sanitization (`:281`). If the Router picks the model and publishes nothing, the
gateway silently loses the context window and long conversations start failing at
the provider instead of degrading gracefully. Keep the fitting in Metorite —
it needs the messages — and drive it from this document.

A pleasant consequence: **adding a tier later is a Router data change plus a
capabilities-document entry, not a code change in every consumer.** `tier-vision`,
`tier-embed` and `tier-voice` therefore do not need to be decided now, which is why
this spec does not decide them.

**A bare model name is rejected, not coerced.** Today `_byok_default_model`
(`_model_resolution.py:35`) silently coerces an unknown model to `tier-balanced` and
logs it. Under this spec the Router returns **400** for a non-tier model id. Silent
coercion was correct for a personal application where the operator was the user; it
is wrong for a platform, where it hides a misconfigured agent behind a bill.

### 4.3 Identity and attribution

The **organization key resolves the tenant**; headers only refine attribution
*within* it.

```
Authorization: Bearer cc_live_<prefix>_<secret>   → pins organization_id
X-CC-Member:  person@customer.com                 → refines, cannot cross orgs
X-CC-Agent / X-CC-Module / X-CC-Run               → attribution only
```

**The security property, stated so it can be tested:** a forged header can
misattribute usage inside one organization; it can never bill, expose or attribute
across organizations, because the org never comes from request input — only from the
key. This is `user_management_contract.md` R11 applied at the LLM layer, and CP-3's
done-when pins it.

> 🔴 **Hard prerequisite, and it is an existing OWNER-GATE.** On the running box
> `GATEWAY_INTERNAL_TOKEN` is **byte-identical** to `LITELLM_MASTER_KEY` (work_plan
> §6, measured 2026-08-05): the `/v1` key *is* the service identity. Per-organization
> keys mean nothing until those are split, because anything holding the `/v1` key
> today holds the service identity too. The rotation is a redeploy (delivery
> recovered 2026-08-09, WS-25) and it is the **owner's** act. **An agent must not
> perform it.**

### 4.4 The balance gate and failure semantics

Pre-flight in Redis, ledger in Postgres — the gate is on the hot path of every
token, and Postgres is not where a hot-path check belongs.

Carried from §3.3, unchanged: **soft-block with a ~10% grace overdraft.** At zero,
LLM calls return a specific **402** the UI renders as "out of credits — top up", and
**the non-AI parts of every module keep working**. Alert at 80%. A hard cut-off
mid-workflow generates a support ticket and a refund request that together cost more
than the overdraft.

**Per-run circuit breaker.** An agent in a tool loop can burn a large amount in
minutes, and this codebase has retry loops and a 32k default output ceiling
(`v1_compat.py:_DEFAULT_MAX_OUTPUT_TOKENS`). A per-run spend ceiling is not
optional.

**Router unavailable ≠ Metorite down.** Metorite holds a short credit
lease and settles against it, so a Router outage degrades to "spend continues
against the lease, settlement catches up" rather than "the product stops". A
platform whose every LLM call hard-depends on a second service has traded a billing
problem for an availability problem.

**BYOK stays a tier, not an exception (§3.4, unchanged).** A BYOK organization is
**metered but not charged for tokens** — you charge the platform fee only. It
becomes an attribute of the org's Router key rather than a per-deployment key store,
and it caps your financial exposure on your largest accounts.

### 4.5 Per-member metering

The owner's requirement — *"multiple users within an organization can also be
metered"* — and `department_centers.md` Phase E (WS-16) are **the same mechanism**,
and the board already says so: *"MT-3's credit gate lands on the same choke points —
design once, serve both."* This spec is where that happens.

- Credits are an **organization pool**. `member_ai_cap` is a *policy against the
  pool*, not a sub-wallet — so unallocated headroom is never stranded.
- Default `on_exhaustion` is **`degrade`**: the member drops to `tier-fast` rather
  than stopping. The tier vocabulary is what makes that expressible, which is a
  concrete argument for keeping tiers rather than exposing models.
- Soft-warn at 80%, per Phase E. Exec exemption is a cap absent, not a special case.

## 5. What changes in Metorite

### 5.1 The LLM path

| File | Change |
|---|---|
| `apps/services/gateway/gateway/routes/v1_compat.py` | Becomes a **forwarder**: local policy + context fitting, then POST to the Router. Loses direct `acompletion`, provider selection, `ensure_model_registered` |
| `packages/acb_llm/acb_llm/client.py` | `_TIER_MODEL` init from `config.yaml` and `_compute_cost` (`:517`) move to the Router. `_emit_usage` (`:552`) stops *computing* cost and records what the Router **reported** |
| `packages/acb_llm/acb_llm/model_limits.py` | Static table replaced by the cached tier-capabilities document |
| `packages/acb_llm/acb_llm/key_store.py` | LLM provider keys move Router-side. Keeps non-LLM credentials (`credential_type`/`service` paths untouched) |
| `apps/services/orchestrator/orchestrator/_model_resolution.py` | BYOK-by-default logic survives structurally; "BYOK" now means "the Router". Coercion becomes rejection (§4.2) |
| `infra/litellm/config.yaml`, `infra/enabled_models.json`, `infra/provider_models_cache.json` | Retired — Router-managed |
| `workbench/control_plane/src/app/settings/models/page.tsx` | Provider/model/tier tabs **removed from the customer product**; the surface becomes credits, burn and per-member caps |

Net new logic in the Router is small — key lookup, rate-card multiply, ledger write,
balance check. The LLM machinery itself is **moved**, not rewritten: it is today's
`acb_llm`, which already works.

### 5.2 Identity resolution and seat consumption (D32.4)

Metorite **keeps issuing its own sessions** — NextAuth Google SSO in
`workbench/control_plane`, forwarding a verified `X-User-Email`, exactly as today. No
authentication server is built and nothing on the live auth path is cut over.

What changes is **who is authoritative for "this person exists and belongs here"**.
On sign-in, Metorite resolves the person against the Customer Console:

*(Shapes corrected 2026-08-18. The single line that stood here —
`POST /registry/resolve { org, email } → { identity_id, role, seats, status }` —
documented the **forbidden** shape for the caller this section is about: a
deployment naming its own org in the body is R11's violation, and `role` is
registry/billing vocabulary that must never cross into the tenant's permission
ladder (D12). It described the operator scheme and read as if it described both.)*

**Two callers, two shapes.** The full contract is **CP-2b** in §6; this section
points at it rather than carrying a second copy that can drift:

```
# Operator scheme — SHIPPED (main.py:525). A staff act on a NAMED customer.
POST /registry/resolve      Authorization: Bearer <operator token>
     { org_slug, email, display_name? }
  → 200 { identity_id, organization_id, role, status, seats: [...] }
  | 409 { reason: "seat_cap_exceeded", buy_more: {...} }
  | 403 "organization is deleted"

# Deployment scheme — CP-2b, BUILT on both sides 2026-08-18. The Console
# answers this shape (main.py `_resolve_for_deployment`) and the caller that
# presents the key is `acb_auth/console_resolve.py`, reached from the gateway's
# `POST /signin/resolve` and from nowhere else.
# A box asking about a person it is authenticating. It names no org: the
# org is the ANSWER, not the assertion (R11).
POST /registry/resolve      Authorization: Bearer cc_depl_<prefix>_<secret>
     { email, display_name? }              # an org_slug field here is 400
  → 200 { identity_id, organizations: [ { organization_id, slug, placement,
                                          status, seat,
                                          capabilities: {sign_in, write_seats,
                                                         use_ai} } ] }
                                           # never `role`
  | 200 { "organizations": [] }            # invisible — exactly this body
  | 409 { reason: "seat_cap_exceeded", buy_more: {...} }
  | 403 "organization is <state>"
```

*(`capabilities` added 2026-08-18 with CP-2b's B1 answer — §6(d), and built
the same day: the three booleans come from the one
`lifecycle.capabilities_of()` Console-side (`main.py:771`), and the box stores
and applies **them**, never the `status` string, because `capabilities_of` lives
in the Console package and the tenant deployable must not depend on it —
`tests/unit/test_console_dependency_boundary.py` is what keeps that true.
`status` rides along for refusal copy only.)*

⚠️ **`sign_in` is always `true` in the 200 above** *(2026-08-18, §6(j) — B-d)*.
The arm filters to admissible organizations before building the array
(`main.py:695-700`, `:755-769`), so a state that cannot sign in arrives as the
**403** line, never as a 200 entry. **The box therefore branches on the resolve
OUTCOME** — 200-with-one admits, 403 refuses, 200-with-many refuses
(`WorkspaceChooserRequired`), 200-with-none refuses (`AccessDenied`) — and on the
booleans for seat/feature behaviour. It never branches on `status`. §6(j) is the
full table; do not restate it here.

Metorite caches the answer into migration 159's `user_identity` /
`org_membership` projection **(migration 177 adds the three columns that carry
the freshness clock and the cached outcome)**, **joined on the org SLUG** — the tenant
`organization.id` is a local UUID and the Console's is a different one in a
different database (§6(k), B-e) — plus `organization.registry_status` /
`registry_capabilities`. **That projection is the cache of record *for this
entry point's fallback decision***, and for nothing wider: `159:107-109` keeps
`app_user` authoritative for identity at large until WS-29 MT-1 **H6** cuts the
general path over, and CP-2b does not touch `_ACCESS_SQL` or `resolve_identity`
(§6(h), scoped 2026-08-18). Then it proceeds. **This is what makes the seat cap
real**: a person cannot become a user
of an organization without the Customer Console allocating them a seat, because
the box asks before admitting them.

Honest cost: while the Customer Console is **reachable**, deprovisioning is as
fast as `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`; while it is **not**, a cached
person proceeds up to `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS` unless the
cached record already carries `sign_in: false` — which is what a **403** outcome
writes, at any freshness (CP-2b clause 6, §6(j)). Neither is an
architectural limit, and the pair buys not having to build and secure an identity
provider in the same quarter as everything else here.

## 6. Tickets

Each is an acceptance unit. **All are 🟢 AGENT-SAFE to build**; the OWNER-GATE items
are in §8 and are all *operational*, never *constructional*.

**CP-0 · Auth that a customer can actually use.** 🔴 **Ordered FIRST by D33 — it did
not exist when this spec was written and it blocks customer #1, not customer #2.**
Today sign-in is pinned to **one Microsoft Entra directory** (`workbench/control_plane/
src/auth.ts`: *"the tenant-level app registration ensures only users in the Fracktal
Microsoft 365 directory can sign in"*), and auth **fails OPEN** when unconfigured
(*"if no AUTH_MICROSOFT_ENTRA_ID_ID is set, middleware allows all traffic"*). A paying
customer's staff are not in your directory. Scope: accept identities from directories
we do not control; fail **closed** in every non-dev environment; correct the
`acb_auth/deps.py` docstring, which still describes a Google/`fracktal.in` mechanism the
code does not implement. **Done when:** a sign-in from a directory other than Fracktal's
succeeds against a fixture; a deployment with auth env unset **refuses traffic** in a
non-dev environment and a test pins it (verified-red-first — the current behaviour must
fail the new test before the fix); the docstring names the mechanism actually in force;
`npx tsc --noEmit && npx vitest run` green. **Registering any real IdP application is
🔴 OWNER-GATE** — build against fixtures and hand it over.

**CP-1 · The service skeleton and registry.** ✅ **BUILT 2026-08-12.**
`infra/customer_console/001_customer_console.sql` (its OWN migration ladder — not
`infra/postgres/`, which `apply_migrations.sh` replays into the *tenant*
database and `gen_tenant_migration.py` scans to demand RLS; both would be wrong
for a deliberately cross-tenant plane) · `002_seed_catalog.sql` (the D23/D24
catalog as data, with `rnd`/`support` seeded INACTIVE because their Centers are
not registered yet, and the rate card seeded UNPRICED so `rate_call` raises
rather than billing a guess as free) · `apps/services/customer_console/` ·
`tests/unit/test_platform_{seats,credits,keys,sql,api}.py`. The new engine site
is declared in `test_db_engine_seam.py::_ALLOWED_SYNC` with its reason (R5(b)). New deployable
`apps/services/customer_console/` following the existing service pattern (FastAPI, `uv`
workspace member, `infra/docker-compose.yml` entry beside `gateway`). Its own
database, no RLS (§0.9.2 — it reads across tenants by design). Tables from §3.2.
**Done when:** the service starts under compose and answers `/health`; a two-org
fixture returns both orgs from one query (proving cross-tenant reads work, which is
the *opposite* of every other plane's requirement); **R5(a) is satisfied
explicitly and reviewably** — the plane's separateness is *declared*, in
`tests/unit/test_db_engine_seam.py::_ALLOWED_SYNC`, with the §0.9.2 citation as
its written reason, and `tests/unit/test_tenant_coverage.py` stays green because
this plane's tables sit on their own ladder where the tenant scan cannot reach
them.

> ⚠️ **Clause 3 corrected 2026-08-18 — it was unmeetable as written, and meeting
> it would have made things worse.** It asked for the Customer Console's tables
> to be recorded in `gen_tenant_migration.EXEMPT`. That generator discovers
> tables from `infra/postgres/[0-9]*_*.sql` **only**
> (`scripts/gen_tenant_migration.py:63` `_MIGRATIONS`, `:175`, `:200`), so a
> Customer Console table name added to `EXEMPT` matches no discovered table,
> prints under *"exempt names that match no discovered table (stale entry, or
> the table moved)"* (`:402-407`), and pushes a map that holds **12** entries
> today through `test_tenant_coverage.py:77`'s ≤15 tripwire — a map whose whole
> purpose is to be the security review. R5(a)'s requirement is *"tenant-scoped
> or exempted with a reason"*, and what CP-1 actually shipped satisfies it by a
> stronger route: a ladder the tenant scan cannot see (`infra/customer_console/`,
> replayed by nothing in `apply_migrations.sh`) plus one declared engine site
> carrying its argument. Annotated rather than struck, because the original
> clause's *intent* — no exemption by omission — is the part that binds.

**CP-2 · Plan catalog, subscriptions and seats.** §3.3 tables plus
`GET /billing/summary` and `POST /billing/seats`. **Done when:** purchased/assigned/
available compute from the definitions in §3.3 and no surface recomputes them
locally; assignment beyond `purchased` returns **409 with a buy-more payload** and a
test pins it; unassignment frees the seat immediately; the partial UNIQUE index
makes a concurrent double-assign fail rather than duplicate; every write lands an
audit row. **R8: run against a real Postgres** — the partial-unique and the
as-of-now `seat_grant` sum are exactly the kind of SQL a hermetic fake agrees with
and a real database rejects.

**CP-3 · Per-organization keys and attribution.** ✅ **BUILT 2026-08-12, after
one FAILED verification.** §4.3. `llm_api_key` with prefix-match + hash-verify.

⚠️ **What the first attempt got wrong, recorded because the mistake is
instructive.** It put `/usage/record` under *organization-key* auth. Two
consequences, both measured on a live database by the verifier: a negative
`billed_credits` became a **positive** ledger delta (a customer-reachable
credit-minting endpoint — 989 credits became 100,989), and more fundamentally it
made **the metered party the reporter of its own usage**, contradicting §4.1's
*"trusting a client's self-report… is not a meter, it is a suggestion"* — the
argument D32.1 rests on. Now: three schemes (operator / **internal** / org key),
the org key is **read-only** (`/me`), the Router's internal token writes the
meter, every quantity is floored at zero in both the API model and a CHECK
constraint, and idempotency is scoped `(organization_id, request_id)` because a
globally-unique `request_id` let one tenant suppress another's charge and turned
`recorded:false` into a cross-tenant existence oracle (migration 003). **Done when:** a request bearing org A's key and a forged
`X-CC-Member`/`X-CC-Org` header for org B writes its `usage_event` under **org A**;
a revoked key 401s; the key secret is never logged (assert over the log record, not
by reading the code).

**CP-4 · The Router, pass-through only.** ✅ **BUILT 2026-08-12.**
`POST /v1/chat/completions` on the Customer Console, authenticated by the
organization key: resolves the tier from `tier_binding`, calls the provider,
returns the response **unchanged**, and writes one `usage_event` with
`billed_credits = 0`.

⚠️ **It does NOT reuse `acb_llm`, and that is deliberate.** This ticket said "using
today's `acb_llm` machinery", but that package's key store imports `acb_common`
and reads the **tenant** database — pointing the Router at it would put our
provider credentials on a customer's box, the precise thing D32.1 moved metering
here to avoid. So the Router carries its own encrypted `provider_credential`
table (migration 004) and calls litellm directly. The seam this must not
duplicate is the *tenant* one; it is not on that seam at all.

**Deliberately unpriced.** `billed_credits` is zero and no balance is checked —
CP-6 sets the rate card against the burn this slice measures. Metering is
best-effort and never fails the completion: an unmetered call is a revenue
problem, a failed call is a product problem, and the product problem is worse.

The provider call sits behind `router.set_provider_call` so the pass-through is
testable without a provider account — otherwise the only way to test it is to
spend money at DeepSeek on every run, which means nobody does.
Ships dark (CLAUDE.md §4) — see the amendment below for *how*. **Done when:** a
non-streaming completion through the Customer Console's `/v1` is byte-identical
for the client to one made directly against the provider; one `usage_event` row
exists per completion; a retried `request_id` writes **one** row, not two.

> ⚠️ **Done-when AMENDED 2026-08-18** — annotation, not a rewrite. Two of the
> original clauses could not be met as written, and one of them named a thing
> that does not exist.
>
> **(1) "Behind a flag, default OFF" — no such flag was ever built.** A
> repo-wide search on 2026-08-18 finds no Router-exposure flag in any Python,
> TypeScript, compose or env file. Ship-dark is nonetheless real, and is
> achieved by **absence of a caller**: nothing in Metorite posts to the Customer
> Console's `/v1`, and the only Metorite→Customer Console call anywhere in the
> tree is the workbench's read-only billing proxy
> (`workbench/control_plane/src/app/api/billing/summary/route.ts:58`, which
> calls `/me/billing`). The flag that *does* exist is CP-6's
> **`CUSTOMER_CONSOLE_SPEND_GATE`** (registered in `work_plan.md` §6), and it
> gates the two spend *refusals*, not the route. **The Router-exposure flag
> arrives with the first caller ticket**, because a flag with no caller to gate
> is a config value nobody reads and a false sense of a control. §8 gate 5
> ("flipping CP-4's Router flag ON for a real customer") is therefore that
> ticket's gate, not this one's — it is correctly registered and currently
> ungrounded in code.
>
> **(2) "the streaming path included" — UNMET, and now its own ticket.**
> `stream: true` returns **501** (`main.py:818-828`). Deliberately: CP-4
> forwarded the flag and handed litellm's `CustomStreamWrapper` to FastAPI,
> which failed to serialise it — the client got "Internal Server Error" **and** a
> phantom zero-token `usage_event` was committed for a completion nobody
> received. An explicit refusal is the honest state, and it is pinned
> (`test_customer_console_router.py:548` — 501, not 500; `:557` — a refused
> stream writes no usage row). The streaming half is **CP-4b** below, and it is
> the half that matters most: every agent runtime streams through this choke
> point, so CP-4 as it stands cannot carry a single real caller.

**CP-4b · Streaming pass-through.** 🔲 **MINTED 2026-08-18** — CP-4's owed
second half, carved out of its done-when rather than left as an unmet clause
under a ✅. Make `stream: true` work end to end on the Customer Console's
`POST /v1/chat/completions`: relay the provider's SSE frames to the client
unaltered, and meter the completion from the usage the stream reports, not from
a guess.

Two hazards decide the shape, and both are already recorded rather than
theoretical. **(a)** The provider's stream object is not JSON-serialisable — the
501 exists because returning it produced a 500. The relay is therefore a
`StreamingResponse` over an explicit iterator, and the route's `def`-not-`async
def` rule (`main.py:30-36`) has to be re-argued for a streaming route rather
than assumed: the engine is synchronous and a streaming handler holds its
connection for the life of the stream. **(b)** Usage arrives *last* (or not at
all) on a stream, and §3.4 already names this: *"Retries, stream reconnects and
the streaming usage-rebuild path in `v1_compat.py` all create double-write
opportunities."* A client that disconnects mid-stream has still cost us the
provider call.

**Done when:**
1. The frames the client receives are **byte-identical** to the frames the
   provider emitted — frame boundaries, ordering and the `[DONE]` sentinel
   included, with nothing re-serialised in between. Fence:
   `test_the_relayed_frames_are_byte_identical_to_the_providers`, driven through
   the existing `router.set_provider_call` seam (the same seam CP-4 added) so no
   provider account is spent — `main.py`'s CP-4 argument stands: a test that
   costs money at DeepSeek on every run is a test nobody runs.
2. **Exactly one `usage_event` per stream**, written after the final frame, with
   the token counts the stream reported. Fence:
   `test_one_stream_writes_one_usage_row`.
3. **A client that disconnects mid-stream is still metered** for what the
   provider actually delivered, and still writes exactly one row. Fence:
   `test_an_abandoned_stream_is_metered_once`. (Dropping it is a revenue hole
   that scales with flaky networks; writing it twice is the credibility event
   `request_id UNIQUE` exists to prevent.)
4. **A stream that fails before its first frame writes NO usage row** — the
   phantom-row defect that produced the 501, restated as a permanent fence:
   `test_a_stream_that_never_starts_writes_no_usage_row`.
5. **The balance gate and the per-run breaker refuse BEFORE the first frame**,
   with the same 402/403 bodies the non-streaming path returns — a refusal
   delivered inside an SSE frame is one every client renders as content. Fence:
   `test_a_refused_stream_never_opens_the_stream`, run with
   `CUSTOMER_CONSOLE_SPEND_GATE` on.
6. **R8** — the metering clauses run against a real Postgres 16 through
   `tests/unit/_customer_console_ladder.py`, and the new suite is added to §7's
   command list and to `pr-check.yml`'s skip-guard. A skipped R8 test proves
   nothing (CP-3's finding: CI silently skipped every DB-gated fence while
   reporting green).
7. The 501 branch and its two fences are **removed in the same change**, so the
   tree never carries a refusal and its replacement at once.

🟢 **AGENT-SAFE in full** — the provider seam makes it testable without a
provider account. 🔴 Nothing new is gated: exposure to a real customer remains
§8 gate 5.

**Non-goals:** the Metorite-side forwarder (that is CP-5 plus the first caller
ticket) · the tier-capabilities document · any rate-card price.

**CP-5 · Tier capabilities and the model-access rework.** `GET /v1/tiers`, the
**Metorite-side** cache, `_fit_context_window` / `_clamp_max_tokens` driven from
it, and removal of the customer-facing model picker. **Done when:** a tier whose
window shrinks in the Router causes **Metorite (the tenant deployment)** to fit
to the smaller window without a **Metorite** deploy; a bare (non-tier) model id
returns 400 rather than being coerced; the Settings→Models provider/model/tier
tabs are gone from the customer product and
`npx tsc --noEmit && npx vitest run` is green.

*(Disambiguated 2026-08-18: this ticket read "the CC-side cache … causes CC to
fit … without a CC deploy", where "CC" meant CommandCenter, i.e. the tenant
product. Post-**D41** the same two letters read as **C**ustomer **C**onsole —
the exact opposite party, since §4.2's whole point is that the fitting stays in
the tenant deployment because it needs the messages. Expanded, not reinterpreted.
`X-CC-Member` / `X-CC-Agent` / `X-CC-Module` / `X-CC-Run` and the `cc_live_` /
`cc_depl_` key prefixes are **wire identifiers of this service** and are
untouched.)*

**CP-6 · Rate card, ledger and the balance gate.** ◐ **MECHANISM BUILT
2026-08-18 — the two refusals ship OFF.** §3.4 + §4.4. **Done when:**
balance equals `SUM(credit_ledger.delta)` in a fixture and no code path UPDATEs a
balance column (structural fence — grep the tree, per R7's preference for
structural over example tests); a zero-balance org gets **402** with the top-up
payload while a non-AI endpoint on the same org still returns 200; **the
overdraft grace is a named config value with a test at both edges** *(corrected
2026-08-18 — the shipped policy is ABSOLUTE, `OverdraftPolicy.grace_credits =
100` with `grace_for_trial = False`, not the "~10%" this clause and §4.4
originally said; the shipped docstring's argument stands — a percentage grows
the exposure on your largest accounts forever)*; the per-run circuit breaker
trips a runaway loop.

**What shipped.** `router.resolve_rate_card` (newest card whose `effective_from`
has passed — the same shape as `resolve_tier`, so a re-price is an INSERT and a
past invoice is never recomputed) folded into the existing pure
`credits.rate_call`; the rated cost passed to `store.record_usage`, which
negates it into `credit_ledger` **in the same transaction as the usage row**, so
a retried write that inserts nothing also charges nothing; the balance gate on
`POST /v1/chat/completions` **before** the provider call, via the existing
`credits.decide_spend`, returning 402 with a `top_up` payload; the per-run
circuit breaker over `SUM(usage_event.billed_credits)` for
`(organization_id, run_id)` — migration 003's partial index, no new schema —
returning **403** (not 402: topping up does not fix a runaway loop, and not 429:
a breaker a retry loop can wait out is not a breaker).

**Both refusals are behind `CUSTOMER_CONSOLE_SPEND_GATE`, default OFF** (ship
dark). The reason is §9.2: a newly provisioned organization is `trial` with a
zero balance and **how many credits a trial starts with is still an open owner
input**, so enforcing today would refuse the first AI call of every new
customer. **Flipping it for a real customer is an owner act**, on the same
footing as §8's gate 5. Rating and the ledger draw are *not* flagged — they
compute zero until the card is priced, which is the owner's commercial act.

**The overdraft is absolute, not a percentage.** `OverdraftPolicy.grace_credits
= 100`, `grace_for_trial = False`. The §4.4 line says "~10%"; the shipped
docstring argues the percentage form grows the exposure on your largest accounts
forever. Tested at both edges of the shipped value, pure and over HTTP.

**Named values introduced:** `credits.RunCeiling.max_credits = 500` (a tripwire
on one loop, never a budget — budgets are CP-7's) and `credits.CREDIT_QUANTUM =
0.0001`, which is both the rounding step for a rated cost and the probe the
pre-flight spends, because the true cost of a completion is unknowable before
the provider answers and the only honest pre-flight question is *"is there any
headroom left at all?"*.

⚠️ **Two honest limits, recorded rather than left to be discovered.**
**(a) BYOK is not zero-rated yet** — §3.4 says a BYOK organization is metered
but not charged for tokens, and `_rate_completion` does not yet know which
credential served the call. Harmless while every card is zero; **it must be
closed before any real price is set.** **(b) The run id is an attribution
header the caller sets**, so a caller that rotates it escapes the breaker; the
balance gate is the backstop that depends on nothing the caller says. The
breaker stops the *accident*, which is what a runaway loop is.

**The tripwire that keeps both inert** — and a third, that the pre-flight
spends one `CREDIT_QUANTUM` rather than an estimate, so a single large priced
call can vault the overdraft floor in one step — is
`test_customer_console_sql.py::test_the_rate_card_ships_unpriced`, which counts
non-zero rows across the **whole** `model_rate_card` table as the ladder
applies it (R7). A migration that prices a card fails it; pricing stays an
owner act on a live system (§8 gate 4, D19.2). Do not narrow it to the seed's
`effective_from` — suites that need a price insert one inside a rolled-back
transaction or delete it in teardown.

**Deliberately not built** (each excluded for a stated reason): the Redis
balance cache of §4.4 — balance stays `SUM(credit_ledger.delta)` in Postgres, and
this plane is cross-tenant so R5(c)'s tenant-prefix wrapper does not apply; a
`balance_after` column — it would defeat the structural fence; the Metorite-side
credit lease — there is no Metorite-side Router caller yet; CP-7's per-member
caps — `credits.decide_member_cap` exists and stays unwired; alert *delivery* at
80% (D37.2 gives it to WS-30 SC-4); any real rate-card price.

**CP-7 · Per-member caps.** §4.5 + `department_centers.md` Phase E. **Done when:**
a member at 100% with `on_exhaustion='degrade'` completes on `tier-fast`; with
`'block'` receives 402; a member with no cap row is unaffected; the 80% warning
fires once, not per call.

**CP-8 · Operator Console and reconciliation.** The §4.1a read surfaces (per company:
plan and MRR, seats purchased vs assigned, credit balance and burn, trial expiry,
last-login/actives) plus the nightly drift job — **and the
`payment_event` rows whose `order_id` is NULL** *(added 2026-08-18 with CP-9's
finding F2)*. Those rows are **captures this database cannot attribute to an
order**: money received with nothing granted, most plausibly a customer paying a
provider order that a redemption replaced or detached (CP-9 §6 decision 2). They
are kept, never cleaned up, because they are the only record that the money
arrived; **CP-8 owns surfacing and clearing them**, and the webhook's `ERROR`
line (`payments.webhook_unknown_order`, carrying the amount and both provider
identifiers) is the interim alert until this console exists.

**Scope extended 2026-08-19 (adversarial-review P0(b)): the SECOND
money-received-nothing-granted shape belongs here too — a capture that lands on
an order this database has already **abandoned**.** The receipt *does* name an
order in that case, so it is not a NULL-`order_id` row and a query written for
those would miss it; what it has in common is the thing that matters, namely a
signature-verified payment of the correct amount with no entitlement written.
The interim alert is `payments.capture_after_terminal` at **ERROR**, carrying
the same three fields plus the order's status (9.5 guard 2). CP-8 surfaces both
shapes on one queue: `payment_event` rows that fulfilled nothing. The expiry sweep of
`created` orders belongs here too — CP-9 enforces `expires_at` lazily on purpose,
since a scheduler minted there would have no surface to report to.

**Shape fixed by D35:** a **separate Next.js app** in this monorepo at its own
hostname — not a gated route tree inside the workbench — so "shares tables, never
routes" is enforced by the deployment boundary rather than by a guard. It pins
**our own Entra directory** (staff-only; D33.1's multi-directory rule binds the
*customer* product and this is its inverse), and it is **exempt from the theming
engine** (`workbench/control_plane/AGENTS.md`) because "one product, one look"
exists for surfaces customers see. Both exemptions are recorded in D35.3/D35.4
precisely because a later agent would otherwise read them as defects and "fix"
them.

**Done when:** the console renders from the Customer Console alone with no
per-deployment round trip on the request path; the reconciler alerts on a seeded
drift between seat counts and subscription items; no route of the customer
workbench can reach a cross-org read.

> **Slice 1 — the live customer-management surface — BUILT 2026-08-22
> (`cp-8-operator-console`, DARK).** ✅ *renders from the Customer Console alone,
> no per-deployment round trip* — the app's BFF calls only `CUSTOMER_CONSOLE_URL`
> (the operator API), never a tenant box. ✅ *no route of the customer workbench
> can reach a cross-org read* — the cross-org read is `GET /orgs` on the Console
> behind the `Operator` scheme, and its sole consumer is `workbench/
> operator_console/` (a DIFFERENT app), so the customer workbench has no route to
> it by construction (D35's deployment boundary). ⏳ *the reconciler alerts on a
> seeded drift* — the nightly seat/subscription drift job is DEFERRED to a later
> slice, with the reconciliation queue and invoice rendering. The management MVP
> (list every customer; activate a subscription, allocate seats, grant AI
> credits, suspend/resume) is the whole of slice 1.

> **Slice 2 — provision a new customer (create-only) — BUILT 2026-08-22
> (`cp-8-provision-customer`, DARK).** A "New customer" CTA + form on the
> operator console's customers list (`workbench/operator_console/`) creates an
> organization through the Console's **operator** provision arm. A NEW BFF route
> `POST /api/operator/provision` MIRRORS `/api/operator/activate` exactly: it
> gates on staff (`gateStaff` via `proxyToConsole`) and relays the Console `POST
> /orgs/provision` status + JSON **verbatim**, through the same server-side
> Console client (`provisionOrg` in `src/lib/console.ts`) that holds the operator
> token. Body: `{slug, name, owner_email, deployment_label, gstin?,
> billing_state?, core_seats}`. `deployment_label` is **operator-named, never
> inferred and never hardcoded** (D46.6 items 1 & 3): the field is editable
> free-text, prefilled from a new server env
> `OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL` (the box's value is `gateway`) and
> required client-side; a wrong label is the Console's relayed **404**.
> Create-only by design — activate / seats / lifecycle already live on the
> customer-detail Actions, and a two-call provision+activate wizard has a
> half-provisioned failure mode. **Done when:** ✅ the BFF relays the Console's
> **200 / 400** (missing `deployment_label` under the operator scheme) **/ 404**
> (unknown label) **/ 409** (already placed on another deployment) unchanged to
> the operator; ✅ the operator token appears ONLY on the outgoing Console
> request, never in a browser response (the source-scan fence in
> `src/lib/console.test.ts`); ✅ the route is behind `gateStaff()` (unconfigured
> → 503, non-staff → 401) like every `/api/operator/*` route; ✅ after a 200 the
> new org appears in `GET /orgs`. `npm run typecheck` · `typecheck:lib` · `test`
> green — the verbatim-relay + token-not-bundled vitest fences. The Console
> provision contract itself is already R8-proven
> (`tests/unit/test_org_provisioning.py` et al.), so no new Python. 🔴
> **OWNER-GATE go-live:** deploy the app · set the operator-token env · set
> `OPERATOR_CONSOLE_DEFAULT_DEPLOYMENT_LABEL` on the box · provision a REAL
> customer through the finished UI.

**CP-9 · The payment-provider seam (Razorpay), and the money→entitlement path.**
◐ **SUBSTRATE HALF BUILT 2026-08-18** (minted the same day) — the ticket three
documents already cited and nobody had written.

> ### What is built, and what is deliberately not — 2026-08-18
>
> **Built (the substrate half the GO-NARROWED re-audit cleared):** migration
> `007_payments.sql` (`payment_order`, `payment_order_line`, `payment_event`,
> and SC-4g's `discount_code` + `discount_redemption` — one migration, because
> a redemption references an order and splitting them would create a window in
> which the FK has no target; number taken by listing the directory at build
> time, R1) · `customer_console/payments.py` (the paise conversion, the order
> state machine, the GST and discount arithmetic, the `PaymentProvider`
> protocol, `RazorpayProvider` over `httpx` with **no SDK dependency**,
> `FakeProvider` with the **real** HMAC-SHA256 signer, and the one
> `fulfil()`) · `lifecycle.can_pay` appended **last** with `STATES` converted
> to keyword construction · `credits.LEDGER_REASONS` · the order/discount SQL
> in `store.py` · `auth.organization_for_payment` (**not** a fifth scheme —
> the same key resolution, a different lifecycle gate) and
> `auth.razorpay_webhook_event` (the signature verifier, registered in
> `AUTHENTICATING_DEPENDENCIES`) · six routes in `main.py`: `POST
> /billing/orders`, `GET /billing/orders/{id}`, `GET /billing/orders`, `POST
> /billing/orders/{id}/redeem`, `POST /billing/webhooks/razorpay`, `POST
> /discounts` · **115 tests** in `tests/unit/test_customer_console_payments.py`
> (105 at the build, **+5 from the 2026-08-19 verification repair** — F1's
> completed cross product, F2's paid-orphan fence, F5's two walk-depth fences
> and F7's contents pin — **+5 from the 2026-08-19 adversarial-review repair**:
> the failed-then-captured P0 fence, the failed-attempt-leaves-the-order-open
> fence, the capture-after-abandonment ERROR fence, the failing-redeem-attempt
> log fence and the structural `test_no_code_path_drives_an_order_to_failed`;
> two existing fences were rewritten because they pinned the P0 rather than the
> design)
> against a real Postgres 16, **0 skipped**, joined to §7's command block and
> to `pr-check.yml`'s skip-guard (6 Console suites → **7**) in this same
> change. Every load-bearing fence was shown **red first** under a recorded
> mutation.
>
> **Not built, and named rather than left to drift:** SC-4a's checkout UI and
> its two write proxies · the operator code-issue *surface* (the API is here;
> rendering it is CP-8) · the invoice document (SC-5b/5c) · the four-item flip
> set. All four are the held-back surface half of §6's sequencing note.
>
> **Three decisions taken during the build that no clause named** — recorded
> here rather than in a commit message, all three agent-proposed defaults the
> owner may overrule (D16/D17):
>
> 1. ⚠️ **9.3(4)'s fence needed a SECOND exemption, and it is a deviation from
>    "exactly ONE edge is permitted".** Built against the tree, the transitive
>    fence is red on a **pre-existing** edge this ticket did not know about:
>    `POST /v1/chat/completions` is organization-key authenticated (CP-3) and
>    **CP-6 made it write the metering draw** — `chat_completions →
>    store.record_usage → store.add_credit` → `credit_ledger`. That shipped on
>    2026-08-12, six days before CP-9. It is **not** what CP-3's lesson forbids
>    (there, the metered party *reported its own usage* and a negative figure
>    minted 100,000 credits; here the customer's key opens the route but **our**
>    infrastructure decides the amount, from tokens the Router counted). The
>    fence was **not narrowed** — that is the CP-6 failure mode B1 names. The
>    edge is exempted **by name**, in a **separate** constant
>    (`METERING_EXEMPTION`) with its **own** count-and-contents fence, so
>    `test_the_fulfil_allow_list_has_exactly_one_entry` still pins exactly one
>    entry and the deviation is visible rather than smuggled into the list the
>    ticket counts. A second fence asserts the exemption is still *needed*, so
>    it is deleted rather than inherited when the draw moves behind the internal
>    token. **A finding for the board**, in the sense CLAUDE.md §5 means:
>    recorded, not refactored.
> 2. **The provider order is created at `POST /billing/orders` and REPLACED by
>    a partial redemption.** §9.4 requires the create route to 503 without
>    credentials, which means it reaches the seam; SC-4g (iv) requires the ₹0
>    path to make **zero provider calls**. Both hold: the redemption path calls
>    the provider only when money is still owed (and then for the **discounted**
>    amount — a provider order created once for the pre-discount total would
>    collect it, overcharging the customer and failing our own amount check),
>    and a 100% redemption calls nothing, sets `provider='none'` and NULLs
>    `provider_order_id`.
>
>    🔴 **Named residual — and it is NOT harmless. Corrected 2026-08-18
>    (verification finding F2).** This read *"an unpaid provider order to expire
>    at Razorpay — the harmless orphan direction"*. **The orphan has a PAID
>    direction**, and that sentence would have been read as reassurance by
>    whoever builds SC-4a. Detaching or replacing a provider order does not
>    retract it *at the provider*: order #1 keeps a live payment link, and a
>    customer who pays it produces a signature-verified capture whose
>    `order_id` matches **no row here** (the ₹0 path NULLs
>    `provider_order_id`; a partial redemption overwrites it). Measured against
>    the built code: webhook → **200** `{recorded: true, fulfilled: false}`, a
>    `payment_event` row with `order_id` NULL, **nothing granted**, and the 200
>    is precisely what stops Razorpay retrying. **A capture with no matching
>    order is money received with nothing granted** — say it that way, because
>    "orphan" makes it sound like a stray row.
>
>    Why it is nonetheless **unreachable in this slice**, stated so the severity
>    is not over-read either: nothing writes `attempted`, there is no checkout
>    UI, and no Razorpay account exists (§9.7). It becomes reachable the moment
>    SC-4a renders a payment link. What answers it, in order:
>    - **CP-8's reconciliation OWNS the NULL-`order_id` receipts** — added to
>      its scope in §6 rather than left implied. They are the only record that
>      the money arrived, so they are kept deliberately, never cleaned up.
>    - **The webhook's unknown-order arm alerts at `ERROR`**, carrying the
>      amount and both provider identifiers in its structured fields so the
>      payment is findable at the provider from the log line alone. It logged at
>      `warning` with only the event id until F2. Fence:
>      `test_a_capture_with_no_matching_order_is_kept_and_alerted_at_error`.
>    - **SC-4a must never expose a replaced order's payment link** — one
>      sentence added to its ticket, because the surface is where this is
>      actually prevented.
>
>    **Consequence worth stating:** because the create route 503s without
>    credentials, even the ₹0 flow needs the three Razorpay variables set —
>    which is an owner act (§8 gate 3).
> 3. **`CreditGrantRequest.reason` is now validated against `LEDGER_REASONS`.**
>    SC-4g (v) says the narrowing "comes first" and the `CHECK` constraint is a
>    later contract-phase migration (R6); without the narrowing the structural
>    call-site fence would have had a hole exactly where the free-form reason
>    lives. Operator-only surface; every existing caller already passed a
>    member. A non-member is now 422.
>
> Two smaller notes: `attempted` has **no writer in this slice** (the surface
> half writes it when the customer opens the provider's checkout; the edges
> exist now so that slice needs no graph change), and `expires_at` is enforced
> **lazily**, at the next write that touches the order — the sweep belongs with
> CP-8's console rather than with a scheduler that has nowhere to report. It is the seam D19.5 chose and `saas_multitenancy.md` §4.3
designed; §2's non-goal that pointed at CP-8 for it is corrected there. Every
decision below is an **agent-proposed default the owner may overrule** (the
D16/D17 convention), taken 2026-08-18 against the code rather than from the
corpus, because the corpus was the thing that was wrong.

**Why it is a whole ticket and not a wire.** The three interesting parts are not
the provider call. They are: (1) a customer-authenticated **write** path into a
service whose customer credential is read-only *by design* (CP-3's lesson);
(2) an **idempotent** capture, because a webhook is delivered more than once by
contract; (3) **one** fulfilment function, because the ₹0 discount path (WS-30
SC-4g, D42) must write byte-identical records to the paid one, and two
implementations of "grant what was bought" is how a customer ends up paying for
something the free path gave away.

**9.1 · What is sellable through it at launch: the seeded, priced subscription
ladder — and nothing else.** `002_seed_catalog.sql` is the source of the line
items: `core` ₹600, the app-bearing Centers ₹600, the slices-only Centers ₹300,
`company` ₹0, `builder` ₹500, `workflows` ₹300, `all_centers` ₹1,800,
`complete` ₹3,000 (D23/D24). **Credit packs are NOT sellable at launch** —
`plan_catalog.kind` is `CHECK (kind IN ('core','center','addon','bundle'))`
(`001_customer_console.sql:144`), no pack row exists, and no pack ladder is
priced anywhere in the corpus. Pricing one is the owner's commercial act
(D19.2 fixes the ₹10 credit *unit*, never a pack ladder; §8 gate 4, §9 item 4).
An order line whose `plan_slug` is not an **active** `plan_catalog` row is
refused **400**, so the checkout cannot sell a thing the catalog does not price.

**9.2 · `payment_order` and its state machine.** A new table on **this service's
own ladder**, `infra/customer_console/` — **the migration number is taken by
listing that directory at build time and re-checked at merge (R1)**. *(On
2026-08-18 the ladder was 001–006 and the next free number was 007. That is a
measurement of that day, not an instruction; R1 exists because three collisions
in two weeks came from sentences exactly like the one in these brackets.)*

```sql
payment_order(id UUID PK,
  organization_id UUID NOT NULL REFERENCES organization(id),
  status TEXT NOT NULL CHECK (status IN
      ('created','attempted','captured','failed','abandoned')),
  provider TEXT NOT NULL CHECK (provider IN ('razorpay','none')),
      -- 'none' = the ₹0 redemption path, which deliberately never reaches a provider
  provider_order_id TEXT UNIQUE,            -- NULL on the 'none' path
  gross_paise BIGINT NOT NULL CHECK (gross_paise >= 0),
  discount_paise BIGINT NOT NULL DEFAULT 0 CHECK (discount_paise >= 0),
  taxable_paise BIGINT NOT NULL,            -- gross - discount, the GST base
  gst_paise BIGINT NOT NULL, total_paise BIGINT NOT NULL,
  gst_split TEXT CHECK (gst_split IN ('cgst_sgst','igst')),
  customer_gstin TEXT, place_of_supply TEXT,     -- snapshot, see 9.6
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  terminal_at TIMESTAMPTZ);

payment_order_line(id UUID PK, order_id UUID NOT NULL REFERENCES payment_order(id),
  plan_slug TEXT NOT NULL REFERENCES plan_catalog(slug),
  quantity INT NOT NULL CHECK (quantity > 0),
  unit_price_paise BIGINT NOT NULL);

payment_event(provider_event_id TEXT PRIMARY KEY, order_id UUID, kind TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(), body JSONB);
```

**The state machine, with its terminal states named:**
`created → attempted → captured | failed | abandoned`. **`captured`, `failed`
and `abandoned` are terminal** — no edge leaves them, and the transition
function refuses anything not on the graph, exactly as
`lifecycle.can_transition` does for organizations (**extend that idiom, do not
invent a second one**). `abandoned` is written by **expiry**, never by the
customer: a customer who walks away tells you nothing, and an order that stays
`created` forever is the state that makes "how many orders are open" unanswerable.

⚠️ **`failed` has NO writer in this slice** *(2026-08-19, review P0(a) — see
9.5)*. It is on the graph for an explicit customer **cancel** the surface half
may add; a failed payment **attempt** is attempt-level and closes nothing,
because one provider order accepts many attempts until one captures. Two states
therefore sit on the graph unwritten today — `attempted` and `failed` — for the
same reason: the edges exist now so SC-4a needs no graph change.

**Every money column is integer paise, and the conversion happens once.**
`plan_catalog.price_inr` stays `NUMERIC(12,2)` in rupees — prices are data and
the D23/D24 ladder is denominated in rupees — and exactly one named function
(`payments.paise(price_inr) -> int`) converts, at order creation, from the
catalog row. Rationale: Razorpay's API is denominated in paise, and a rupee
amount that round-trips through JSON as a float is how ₹1,800.00 becomes
₹1,799.99 in a place nobody looks. **Fence:** a structural test that no column
in these tables is `NUMERIC`/`REAL`/`DOUBLE` and no route in the checkout path
builds an amount from a `float`.

**9.3 · The auth answer: NO fifth scheme. The org key gains exactly two writes,
neither of which can move value.** This is the question the audit stopped on —
`main.py:5-24` declares four schemes, the `cc_live_` organization key is
**read-only by design** (it reaches `/me` and `/me/billing`), every write route
takes `Operator`, and `main.py:1621-1634` (`my_billing` — *re-anchored 2026-08-20
on `ws-31-seats-mvp` from `:1386-1397`; earlier `:1213-1224`*) forbids the
workbench from holding the operator token. A checkout is a customer write, so something had to give. The
answer that gives least:

1. **The org key stays read-only for every existing route.** Nothing already
   shipped changes.
2. It gains **two** checkout writes, chosen because **neither can move value by
   itself**:
   - `POST /billing/orders` — creates a **pending intent**. It writes
     `payment_order` + `payment_order_line` and nothing else. **No entitlement,
     no seat, no ledger row, no subscription change.**
   - `POST /billing/orders/{id}/redeem` — **presents** a discount code. It
     writes `discount_redemption`, updates the order's money columns, and
     **GRANTS when — and only when — a verified operator-issued code brings
     `total_paise` to 0**, by calling the same `payments.fulfil()` the capture
     path calls (9.6). It grants nothing on its own authority.
     > ⚠️ **Corrected 2026-08-18 (repair round, B1).** This read *"it grants
     > nothing"*, and that was wrong in a way that would have been resolved by
     > the implementer in the worst direction. It contradicted **9.6's
     > two-call-site rule** — redemption **is** one of the two call sites — and
     > it made **9.3(4)'s fence red by construction**, because that fence is
     > transitive and `redeem → payments.fulfil → store.grant_seats` writes
     > `seat_grant`. An implementer who meets a fence that cannot pass narrows
     > the fence; that is the **CP-6 failure mode**, and a fence narrowed to fit
     > is worth less than no fence at all. What is true is narrower and is
     > already argued in (3): **the code is the pre-authorization.** The org key
     > still cannot mint value — it can only present a bearer secret that
     > somebody holding `Operator` issued, against an order that the key's own
     > organization owns (7).
3. **Value moves on exactly two events, and both carry an authority the caller
   does not hold:** (a) a **provider webhook whose signature verifies**, or
   (b) redemption of a **valid operator-issued code** — where the code itself
   *is* the pre-authorization, issued under the `Operator` scheme (WS-30 SC-4g
   clause 4). A customer cannot mint either.
4. **The CP-3 fence class extends — transitively, with exactly ONE named
   carve-out.** CP-3's lesson was that the org key must not reach the meter.
   The new, wider fence: **no route reachable by the organization key writes
   `org_subscription`, `seat_grant`, `seat_assignment` or `credit_ledger`** —
   and **transitively**: over `app.routes` × their dependency trees × the call
   graph of the `store`/`payments` functions those routes reach, not just the
   route body. Structural, not an example test. Fence name:
   `test_no_org_key_route_writes_an_entitlement_or_ledger_row`.

   **The carve-out, allow-listed BY NAME** *(added 2026-08-18 with B1's
   answer)*: exactly **one** edge is permitted, and it is written as a
   `(route function, callee)` **pair** —
   `("redeem_discount_code", "payments.fulfil")`. A pair rather than a
   sentence like *"redeem may write seats"*, because the sentence is a general
   licence and the pair is not: every other callee on that route, and every
   other org-key route, stays red. The allow-list lives beside the fence, in
   the test module, never in application code — a production allow-list is a
   switch somebody flips under deadline.

   **The carve-out's own fence:**
   `test_the_fulfil_allow_list_has_exactly_one_entry` — it asserts the count
   **and the pair's contents** (exactly `("redeem_discount_code",
   "payments.fulfil")` — a count-only assertion would pass on a swapped pair
   such as `("create_order", "payments.fulfil")`; hardening added 2026-08-18
   at re-audit). Mutating the list to add **any** second path goes **red**,
   and its failure message names the two authorised ways to move value (issue
   a code, or capture a payment) so the next agent reads a decision rather
   than an obstacle. An allow-list that grows quietly is the fence deleting
   itself one entry at a time.

   ⚠️ **The pair licenses a SUBTREE, so `fulfil`'s contents are pinned too**
   *(added 2026-08-18, verification finding F7)*. The walk **stops descending**
   at an allow-listed callee — that is what "permitted" means — so everything
   reachable *inside* `payments.fulfil` is unfenced **from that route**: a
   fourth entitlement writer added inside `fulfil` would land under the
   customer's own key with no fence going red anywhere. Closed by a second
   contents pin, `test_fulfil_reaches_exactly_the_three_named_writers`:
   `fulfil`'s transitively reachable writer set is **exactly**
   `{store.activate_subscription, store.add_credit, store.grant_seats}` — §9.6's
   three, no more. Red-first evidence: a transient `store.release_seat` call
   inside `fulfil` fails it with `store.release_seat` in the difference. If a
   fourth is ever wanted, the question is not whether to widen the set; it is
   whether a customer-presented discount code should be able to cause that
   write.

   ⚠️ **The walk's DEPTH is fenced separately** *(added 2026-08-18, finding
   F5)*. The transitive claim rides one loop, and that loop's depth was
   unfenced: narrowing it to direct callees left all 105 tests green, because
   the fence above asserts an **empty** list and a walk that goes nowhere
   returns one. Two fences now feed the walk graphs whose answer is known and
   non-empty — `test_the_walk_reports_a_three_hop_chain_on_a_graph_we_built`
   (three synthetic modules parsed by the real graph builder, writer three hops
   out) and
   `test_the_real_walk_crosses_intermediates_when_nothing_is_permitted` (the
   real tree with the permitted set emptied, where both licensed edges reappear
   through an intermediate). Both go red under the depth-1 narrowing; the fence
   they defend does not.
5. **The lifecycle gate for paying is NOT `can_use_ai`.** Measured 2026-08-18:
   `auth.organization_from_key` 403s when
   `lifecycle.capabilities_of(status).can_use_ai` is false
   (`auth.py:217`), so a `suspended` or `cancelled` organization cannot present
   its key at all — i.e. the customer who most needs to pay is the one the door
   is shut on. That contradicts `lifecycle.py`'s own stated doctrine (*"a
   suspended customer who cannot log in cannot pay you"*). CP-9 therefore adds
   **`OrgCapabilities.can_pay`** to the **one** state machine (never a second
   frozenset beside it): true for `trial | active | past_due | suspended |
   cancelled`, false for `deleted` only. The checkout dependency reuses
   `organization_from_key`'s key resolution and gates on `can_pay`.
   **Fence:** `test_a_suspended_org_can_create_an_order_and_a_deleted_one_cannot`.

   ⚠️ **HOW `can_pay` is added matters, and the trap is positional** *(recorded
   2026-08-18, repair-round nit 3)*. `lifecycle.STATES` is built with
   **positional** arguments — `OrgCapabilities("trial", True, True, True, True)`,
   six rows at `lifecycle.py:64-78`. A field inserted anywhere but **last**
   silently re-maps every existing row's booleans, and every existing test keeps
   passing while `suspended` quietly becomes AI-enabled. **Rule: append
   `can_pay` LAST in the dataclass AND convert the six `STATES` rows to
   keyword construction in the same edit**, so the field after this one cannot
   be added wrongly. Two comments in the tree count *"four booleans"* and go
   stale the moment it lands; they are listed under *Build-slice edits* below.

   **What `can_pay` buys — and what it deliberately does NOT** *(answered
   2026-08-18, B9; agent-proposed default, D16/D17)*. It grants **exactly**:
   create an order, read your own orders (9.3a), redeem a code. Nothing else,
   and in particular:
   - **Capture does NOT transition `organization.status`.** A suspended org
     that pays is still `suspended` the microsecond after the webhook returns
     200. Fulfilment writes `org_subscription`, `seat_grant` and (later)
     `credit_ledger`; it never touches the `organization` row.
   - **The reason is CP-2a's transition-graph discipline.** `POST /orgs/lifecycle`
     is `Operator`-only (`main.py:637`, `set_lifecycle` — *re-anchored
     2026-08-19 from `:500`*) and `assert_transition` refuses every
     off-graph move precisely so the lifecycle has one writer. Letting a
     **webhook** drive it makes an *outside system* the writer of our account
     state: a replayed capture, a provider bug, or a captured-then-refunded
     payment would reinstate an account nobody decided to reinstate. An
     unattended callback must not drive a state machine whose whole value is
     that a human is on every edge.
   - **Reinstatement stays a manual operator act**, and the expected flow is
     the deliberate **two-step**: *customer pays → the operator sees a
     `captured` order for that org → the operator posts `suspended → active`
     through `POST /orgs/lifecycle`.* Today the operator sees it through the
     `Operator` scheme's reads; **CP-8 is the console that renders it and CP-8
     is UNBUILT** — so at launch step 2 is a query, not a screen. Said plainly
     rather than implied.
   - **Named residual:** a customer who pays at 2am stays suspended until an
     operator acts. Accepted at one-customer volume (D36) and revisited by
     giving the operator an **alert/queue** (CP-8), never by wiring the webhook
     into `lifecycle`.
6. **Named residual, not hidden:** an organization-key holder can create
   **unlimited pending orders** — and, extended 2026-08-18 (repair-round nit 4),
   make **unlimited redeem attempts** against those orders. Both are bounded by
   construction: an order has no value effect and expires, so its cost is table
   rows; a redeem attempt is a guess at a **bearer secret** whose prefix and
   256-bit secret come from `keys.py`'s `mint_key`, answered by a refusal shape
   that is the same for unknown and wrong-org (7, and SC-4g done-when 4), so it
   is neither an existence oracle nor a feasible search. **Rate limiting is
   deliberately deferred for both** and named here rather than discovered
   later; it belongs with the first abuse signal, and inventing a limiter with
   no data is how you pick the wrong number. What would change that call is a
   *measured* attempt rate — so the redeem route logs refusals with the
   presented **prefix only**, which is the signal a limiter would later be
   sized from.
   ⚠️ **The log line therefore runs BEFORE the code is verified** *(corrected
   2026-08-19, adversarial-review P2-1)*. As shipped it sat *below*
   `_verified_code`, which raises on all four refusal shapes — malformed,
   unknown prefix, wrong secret, wrong organization — so the "measured attempt
   rate" this clause defers the decision to was **zero under exactly the
   probing traffic a limiter would be sized against**; the only attempts
   counted were the ones that succeeded. Prefix-only still binds: the secret
   never lands in a log. **Fences:**
   `test_a_redeem_attempt_logs_the_prefix_and_only_the_prefix` (the success
   case) · `test_a_failing_redeem_attempt_is_logged_and_carries_no_secret`.
7. **Every order route resolves the organization FROM THE KEY, and a foreign
   order is indistinguishable from a missing one** *(added 2026-08-18, B5 —
   there was no ownership predicate anywhere in this ticket)*.
   `POST /billing/orders/{id}/redeem` and **every** order read (9.3a) take the
   organization from `organization_from_key`'s resolution — never from the
   path, the body or a header (R11) — and answer **404 with a byte-identical
   body** for both *"that order belongs to another organization"* and *"no such
   order"*. One shape, one status, one body: `{"detail": "no such order"}`,
   naming nothing. A 403-for-foreign / 404-for-unknown split is a membership
   oracle over other tenants' order ids, which is CP-3's lesson in the one
   place order ids are guessable (they are UUIDs, but the refusal shape is the
   control, not the entropy). The operator-side idiom stays as it is —
   `_org_id`'s 404 names the slug (`main.py:449-455` — *anchor re-measured
   2026-08-19 at the slice-4 re-audit to `:436-442`, then shifted +13 by the
   same PR's `ProvisionRequest` insert and re-corrected at review; it
   originally said `:276-282`, an unrelated order-read model*) because the
   operator is cross-org **by design**; that is the contrast, not the
   precedent.
   **Fence:** `test_a_foreign_order_and_an_unknown_order_refuse_identically` —
   two organizations, org A attempts to read and to redeem against org B's
   order id and against a random UUID, and the four responses are compared
   **status and body bytes**, not merely "both are 4xx".

**9.3a · Reading an order back — the two routes B6 found missing.** *(Added
2026-08-18. As minted, this ticket wrote orders and gave nothing the power to
read one, so **WS-30 SC-4a done-when 5** — "a failed or abandoned payment grants
nothing and **says so** on the page, naming what to do next" — was unbuildable:
the page had no way to learn a state that only exists in this service. These are
**reads on a credential that already reads** (`/me`, `/me/billing`), so they mint
no scheme, need no carve-out, and touch 9.3's "exactly two writes" not at all.)*

| Route | Auth | Answers |
|---|---|---|
| `GET /billing/orders/{id}` | organization key, `can_pay` | one order — **own org only**, 9.3(7)'s predicate |
| `GET /billing/orders` | organization key, `can_pay` | that org's orders, newest first |
| `GET /billing/catalog` | organization key, `can_pay` | the **active** catalog rows, `sort_order` — §6 item (f), built 2026-08-19 |

*(The third row is §6 item **(f)**'s catalog read, not an order read. It lives
in this table because this is where the customer-key **read** surface is
written down, and a second table would be a mirror. Same credential, same
`can_pay` door, same "mints no scheme" argument; its shape and its two fences
are in item (f) itself.)*

**Response shape — the single order.** `{ id, status, provider, gross_paise,
discount_paise, taxable_paise, gst_paise, total_paise, gst_split, expires_at,
created_at, terminal_at, lines: [{ plan_slug, quantity, unit_price_paise }],
discount: { code_prefix, discount_paise } | null }`. Integer paise on the wire,
as everywhere else (9.2) — the browser formats, it never arithmetics.
**Deliberately absent:** `provider_order_id` and any provider payload. The
customer's browser has no use for the provider's identifiers, and a field
nothing reads is a field somebody eventually reads (`_capability_block`'s
argument, `main.py:791`). `code_prefix` never carries the code's secret, per
SC-4g (i).

**The list.** Same objects without `lines`, ordered `created_at DESC`, capped at
a **named** page size (50) with an explicit `next` cursor — never an unbounded
`SELECT *` on a table a customer can grow without limit (9.3(6)'s residual is
exactly that). Filter parameters are `status` only, validated against 9.2's
state set; an unknown value is **400**, not silently ignored.

**Fences:**
- `test_an_order_read_is_scoped_to_the_key_and_refuses_identically` — 9.3(7)'s
  shared fence covers both routes; the list of org A **never** contains an order
  of org B, asserted by seeding both and comparing ids, not counts.
- `test_the_order_read_carries_no_provider_identifiers` — structural over the
  response model, so a later field addition has to argue with a red test.

**9.4 · The provider seam, and what ships without an account.**
`customer_console/payments.py`: a `PaymentProvider` protocol with
`create_order(...)`, `verify_webhook(raw_body, headers) -> Event | None`, and a
`RazorpayProvider` implementing it **over the HTTP API with `httpx`** — the
service's existing HTTP client. **No `razorpay` SDK dependency is added**: the
integration is two endpoints and one HMAC, and adding a package to a
*cross-tenant* service is a supply-chain decision rather than a convenience. If
the build finds an HTTP call genuinely insufficient, add the dependency then and
say why in the PR.

Credentials come from env with **no defaults** —
`CUSTOMER_CONSOLE_RAZORPAY_KEY_ID`, `_KEY_SECRET`, `_WEBHOOK_SECRET`, following
D41.1's naming — and **absent means the seam REFUSES**: `POST /billing/orders`
returns **503** naming what is missing (the `route.ts:34-36` posture, not a
localhost default), and the webhook route 503s rather than accepting a body it
cannot verify. That, plus `purchaseEnabled` staying `False` at `main.py:1438`
(*re-anchored 2026-08-19 from `:1266`, then again the same day for §6 item
(f)'s route*),
is how this ships dark: **absence of credentials and absence of a UI**, both
observable, neither a flag nobody reads (CP-4's amendment is the precedent).

**The agent-safe half is everything:** a `FakeProvider` with **recorded response
fixtures** and a **fake signer that computes the real HMAC-SHA256 signature over
the raw body** with a test secret. Only the network is fake — the signature
algorithm under test is the shipped one. Same shape as `router.set_provider_call`
(CP-4), for the same reason: a test that needs a real account is a test nobody
runs, and here it is worse — nobody may *create* the account (9.7).

**9.5 · The webhook: signature first, idempotent second.**
`POST /billing/webhooks/razorpay`.

- **Signature verification is MANDATORY and happens before the body is parsed as
  anything but bytes.** Unsigned or mis-signed ⇒ **400**, logged, nothing read,
  nothing written. Never 200-and-ignore: a provider that receives 200 stops
  retrying, so "accept and drop" silently loses captured payments.
  ⚠️ **The comparison is over BYTES, not over `str`** *(corrected 2026-08-19,
  adversarial-review P2-2)*. `hmac.compare_digest` on two `str` arguments is
  **ASCII-only** and raises `TypeError` otherwise, and Starlette decodes header
  bytes as latin-1 — so a single byte above 0x7F in `x-razorpay-signature` was
  an unhandled **500** on the one route that carries no bearer token by design
  (reachable the moment credentials are configured). A hostile header is a
  refusal, because a non-hex value cannot equal a hex digest. **Fence:**
  `test_a_non_ascii_signature_header_is_refused_not_a_crash`.
- ⚠️ **It must register its verifier in `auth.AUTHENTICATING_DEPENDENCIES`**
  (`auth.py:344-349`; the set is declared at `:344` and a new entry goes inside
  it, at `:345-348` — anchor re-measured 2026-08-18). The webhook is a door with
  no bearer token, so expressed naively it would make CP-2b clause 1's fence
  (`test_the_unauthenticated_route_set_is_exactly_health`) go red — correctly.
  The signature check **is** this route's authenticating dependency; express it
  as one and the existing fence covers it on the day it lands.
- **Two guards, and they are NOT the same guard.** *(Rewritten 2026-08-18, B8.
  The previous text attributed duplicate-safety to `provider_event_id` alone,
  which is wrong for the provider D19.5 chose — and wrong in the direction that
  double-grants.)*
  1. **`payment_event.provider_event_id` PRIMARY KEY = TRANSPORT-level dedup.**
     It makes the *same* delivery, delivered twice, a no-op. That is the retry
     case and nothing more. This is CP-3's `(organization_id, request_id)`
     idempotency applied one layer out.
  2. **The terminal-state rule = the MONEY guard.** **Razorpay sends DIFFERENT
     event ids for one capture** — `payment.captured` and `order.paid` are two
     events, two ids, one payment — so the primary key does not see them as
     duplicates and never will. What makes the second one harmless is 9.2's
     state machine: `captured` is **terminal**, so a fulfilment attempt against
     an already-`captured` order is a **no-op that returns 200** and logs at
     info. Both events are *recorded*; exactly one *fulfils*.

     🔴 **The no-op clause is scoped to `captured` and to NOTHING else.**
     *(Corrected 2026-08-19, adversarial-review P0(b). As written it said
     "terminal", and the code took it literally: one `except TransitionRefused`
     arm logged `payments.already_fulfilled` at **info** for every terminal
     state.)* A fulfilment refused because the order is `captured` is the
     benign duplicate. A fulfilment refused for **any other** reason means a
     signature-verified payment of the correct amount arrived and **nothing was
     granted** — the same class as the NULL-`order_id` receipt, and it takes
     the same severity and the same three structured fields
     (`amount_paise`, `provider_order_id`, `provider_payment_id`):
     `payments.capture_after_terminal` at **ERROR**. Reachable today only via
     `abandoned` — a capture landing after the TTL sweep ran. **Fence:**
     `test_a_capture_after_abandonment_alerts_at_error`.

  Together these are what SC-4a's *"a duplicate webhook credits once"* actually
  rests on — the event-id key alone does not deliver it.
  **Fence:** `test_two_different_event_ids_for_one_order_fulfil_exactly_once` —
  deliver `payment.captured` and `order.paid` for one order and assert **two**
  `payment_event` rows and **one** set of written records. ⚠️ A fence that
  delivers the *same* event id twice does not test this at all, and is the
  fence this ticket would otherwise have shipped; keep both, they answer
  different questions.
- 🔴 **A failed payment ATTEMPT is not a failed ORDER.** *(Added 2026-08-19,
  adversarial-review P0(a) — the shipped code had it the other way and it was
  a money-losing defect, not a wording one.)* **One Razorpay order accepts many
  payment attempts until one captures**: a UPI collect that times out, a card
  the issuer declines, a 3DS step the customer abandons. `payment.failed` is
  therefore an **attempt-level** event — it is **recorded** (the receipt is what
  SC-4a's *"a failed payment says so"* reads) and **logged at info**
  (`payments.attempt_failed`), and it **transitions nothing**. The order stays
  open for the next attempt until it is captured or the TTL abandons it.
  **Order-level failure is `abandoned`, written by the clock** (9.2).
  Consequently **nothing in this slice drives an order to `failed`**: the state
  stays on 9.2's graph because an explicit *customer cancel* is a real
  order-level failure the surface half (SC-4a) may add, and the edges exist now
  so that slice needs no graph change — the same reason `attempted` has no
  writer here. **Fences:**
  `test_a_failed_attempt_then_a_successful_capture_fulfils` (the behaviour:
  deliver `payment.failed` then a correct `payment.captured` for one provider
  order → captured, granted, exactly once) ·
  `test_a_failed_attempt_does_not_close_the_order` ·
  `test_no_code_path_drives_an_order_to_failed` (AST over the package's
  `transition_order` call sites; the only two targets anything writes are
  `captured` and `abandoned`).
  *What it cost as shipped:* the customer retried inside the same Checkout, the
  capture succeeded, and the second webhook found a **terminal** order — ₹1,416
  taken, zero grants, a false `payments.already_fulfilled` info line, and a
  **200** that stopped Razorpay retrying. No test in the suite delivered
  failed-then-captured for one order, which is how it shipped green.
- **Order-state coupling:** a webhook for an order already in a terminal state
  fulfils nothing — and **which** terminal state decides whether that is benign
  or an incident (guard 2 above). A capture whose amount disagrees with
  `payment_order.total_paise` is **refused and alerted**, never fulfilled — an
  amount mismatch is either a bug or an attack and it must not be resolved in
  the customer's favour silently.

**9.6 · Capture → the ONE fulfilment function.**
`payments.fulfil(conn, *, order_id, reference)` — one function, one transaction,
called by **both** the webhook capture path and SC-4g's ₹0 redemption path. It
writes exactly what the order's line items imply:

- `org_subscription` — status/period for the purchased term;
- one `seat_grant` per package line (`store.grant_seats`, the existing seam);
- one `credit_ledger` row per credit-pack line — **zero rows at launch**, since
  9.1 sells no packs; the branch exists so packs are a catalog row later and not
  a second code path. ⚠️ **Because that count is zero, the ledger cannot be what
  the ₹0-vs-paid distinguishability contract rides on at launch** — it rides on
  `discount_redemption` + `seat_grant.reason` instead (below, and WS-30 SC-4g
  done-when 6, both corrected 2026-08-18 with B3).

**The reference, its CARRIER, and what "identical" excludes.** *(Answered
2026-08-18, B4. As first written — "`reference` is the only difference" — the
fence was **unsatisfiable**: `seat_grant.id`, `seat_assignment.id` and
`credit_ledger.id` are `gen_random_uuid()` defaults and every timestamp column
defaults to `now()`, so two runs of the same path already differ. A fence that
cannot pass gets narrowed by whoever meets it.)*

1. **The carrier is `seat_grant.reason`** — it exists today
   (`001_customer_console.sql:178`) and `store.grant_seats(...)` already takes
   `reason` as a keyword (`store.py:103-104`), so nothing new is minted to hold
   it. `payments.fulfil` passes it through.
2. **The format is `<reason>:<ref>`, composed from SC-4g (v)'s ONE vocabulary**:
   `purchase:order:<uuid>` on the paid path, `discount_redemption:redemption:<uuid>`
   on the ₹0 / partial path. Symmetric and mechanical — the left half is a
   `LEDGER_REASONS` member, the right half is exactly the string that would go
   in `credit_ledger.ref`, and `reason.split(":", 1)` recovers the pair. One
   vocabulary spanning both tables, so a packs-era `credit_ledger` row and a
   launch-era `seat_grant` row say the same word for the same event.
3. **Excluded from the comparison, by CLASS and not by field list** — because
   the database writes them, not the code under test:
   **(a) surrogate ids** (`gen_random_uuid()` primary keys: `seat_grant.id`,
   `seat_assignment.id`, `credit_ledger.id`) · **(b) clock columns**
   (`created_at`, `updated_at`, `effective_from`, `assigned_at`). Stated as
   classes so a column added later is covered by the rule instead of quietly
   escaping a hand-list.
4. **`org_subscription`'s provider columns differ, and that difference is
   EXPECTED and ASSERTED — never excluded.** The paid path writes
   `provider='razorpay'` plus `provider_customer_id` / `provider_subscription_id`;
   the ₹0 path leaves **all three NULL**, because no provider was involved and
   NULL is the honest record of that. ⚠️ **Do not write `'none'` here**: that
   value belongs to `payment_order.provider` (9.2); `org_subscription.provider`
   is `CHECK (provider IN ('razorpay','manual'))`
   (`001_customer_console.sql:163`) and `'none'` violates it.

**The equivalence fence, restated** —
`test_the_free_path_and_the_paid_path_write_identical_records` runs both paths
over an identical basket and compares the written `org_subscription`,
`seat_grant`, `seat_assignment` and `credit_ledger` rows field by field, minus
class (3), and asserts **exactly two** differences: the `seat_grant.reason`
prefix (2) and `org_subscription`'s three provider columns (4). **A third
difference fails** — that is still the whole point, D42's failure mode is two
paths that drift into two products; it is now a comparison that can actually
hold.

**GST is captured at order time**, from the organization's CP-2a fields (GSTIN +
registered state, D33.4a), snapshotted onto the order row: split
`cgst_sgst` when the customer's state equals ours, `igst` otherwise; computed on
`taxable_paise`, i.e. **after** any discount (WS-30 SC-4g). Snapshotted rather
than joined, because SC-5e lets an admin edit the billing state and *"changing
the state changes the tax treatment of the NEXT invoice and never a past one"*.

**9.7 · Gates — and the one that constrains the build.**
🔴 **Creating the Razorpay account is OWNER-GATE — the TEST account too.** Any
external commercial account is the owner's act
(`customer_console_infrastructure.md` §7 gates "creating any cloud account" and
"Razorpay credentials"; §5 item 5 lists the account itself). 🔴 **Live keys are
`work_plan.md` §6(b)**; **test-mode keys reach CI or a deployment's env only by
the owner**. 🟢 **Everything else is AGENT-SAFE** — the tables, the state
machine, the seam, the signature verifier, the webhook, the fulfilment function
and every fence, all against the fake provider. ⚠️ Consequently **the
capture-against-real-test-mode rehearsal WS-30 SC-4g clause 2 requires is not
in an agent's reach**: build it green against the fake, then hand the owner a
named, scripted rehearsal. Say so in the PR rather than reporting a rehearsal
nobody ran.

**Done when:** *(**every clause below carries its own mark** — the fence that
carries it is named beside it, and each load-bearing fence was shown red first
under the mutation recorded in the PR. ⚠️ This read *"every clause is ✅ MET
unless marked otherwise"* until 2026-08-19, and clause 2 was the one clause of
17 with **no** mark: an unmarked clause then read as met, which is the reading
finding F1 found to be false. An absent mark is now a defect in this list, not a
default.)*
1. ✅ `payment_order`/`payment_order_line`/`payment_event` exist on the Customer
   Console ladder with the CHECK constraints above, the migration number taken
   at build time (R1); replaying the whole ladder twice is a no-op
   (the `_schema` fixture's existing discipline). — `007_payments.sql`; the
   ladder was 001–006 on disk and on every branch, so **007**, re-checked at
   merge. Next free number: **008**. Fences: `TestTheSchema`,
   `TestTheMigrationFile`. Three CHECKs beyond the sketch enforce the
   arithmetic (`taxable = gross − discount`, `total = taxable + gst`,
   `discount ≤ gross`) in the database.
2. ✅ The transition function refuses every edge not on 9.2's graph, and **no edge
   leaves `captured`/`failed`/`abandoned`** — proven by parametrising over the
   state set, not by three examples. — `test_every_state_pair_agrees_with_the_specs_graph`,
   over the **full 5 × 5 cross product**, each pair compared against
   `SPEC_ORDER_GRAPH`: a transcription of this graph that lives in the test
   module, so widening *or* narrowing the shipped dict goes red (reading
   `payments._ORDER_TRANSITIONS` would assert the graph equals itself).
   ⚠️ **This clause was the one clause of 17 carrying no mark, and the
   preamble's *"every clause ✅ unless marked otherwise"* made that read as met
   when it was not** *(repaired 2026-08-18, finding F1)*. As first built the
   test asserted only inside `if state in ORDER_TERMINAL_STATES`, so the **ten**
   pairs with a non-terminal source asserted **nothing** — `created → created`,
   `attempted → created` and `attempted → attempted` were fenced nowhere in the
   tree, and the parametrisation reported 25 green cases while testing 15.
   Red-first evidence: admitting `created → created` in
   `payments._ORDER_TRANSITIONS` now fails exactly `[created-created]`, and
   nothing else.
3. ✅ `POST /billing/orders` under the **organization key** creates an order for a
   priced, **active** catalog line and **changes no balance, no subscription and
   no seat** — asserted by snapshotting `credit_ledger`, `seat_grant`,
   `seat_assignment` and `org_subscription` before and after and diffing.
   A non-existent or inactive `plan_slug` is **400**. — `TestCreatingAnOrder`;
   `rnd`/`support` are in the parametrisation, since INACTIVE is the case a
   slug-existence check would miss. An order whose total is **0** at creation
   is also 400: the ₹0 path is reached by *redeeming*, never by ordering only
   free rows.
4. ✅ `test_no_org_key_route_writes_an_entitlement_or_ledger_row` — structural and
   **transitive** per 9.3(4), and it must go **red** under a deliberate mutation
   that points a grant-writing route at the key dependency. Its **one**
   allow-listed pair (`redeem_discount_code` → `payments.fulfil`) is pinned by
   `test_the_fulfil_allow_list_has_exactly_one_entry`, which goes **red** when a
   second pair is added. — Red-first evidence: pointing `POST /billing/seats`
   at the key dependency fails with
   `[('assign_seat', ('main.assign_seat', 'store.try_assign_seat'))]` — **two
   hops**, which a route-body scan would have missed; swapping the pair to
   `("create_order", "payments.fulfil")` fails **both** fences.
   ⚠️ **A SECOND exemption was needed and is a declared deviation** — CP-6's
   pre-existing metering draw on `POST /v1/chat/completions`. It is named in a
   separate constant with its own count-and-contents fence rather than added to
   the allow-list this clause counts; the full argument is in the build box
   above.
5. ✅ A **suspended** organization can create an order; a **deleted** one is
   refused (9.3(5)), and `can_pay` lives in `lifecycle.py` with the rest —
   **appended LAST**, with `STATES` converted to keyword construction in the
   same edit. — `TestTheLifecycleGate`, which also asserts the AI door is
   *still shut* for the same key (one machine, two questions, and they must not
   have converged) and reads the six rows' construction from the **source**,
   because nothing behavioural can see a positional argument.
6. ✅ **A mis-signed webhook is refused before its body is parsed** — proven by
   sending a body whose parsing would itself be observable (a malformed JSON
   payload that returns 400-for-signature, not 422-for-schema), with **no**
   `payment_event` row written. — plus a substitution case: a *correct*
   signature over a *different* body is refused, which a fake signer returning
   a constant would have passed.
7. ✅ **A duplicate webhook is a no-op**: two deliveries of one `provider_event_id`
   ⇒ one `payment_event`, one fulfilment, one set of records. Re-delivered after
   the order is terminal ⇒ still one. **And the case that key does not cover
   (B8):** `payment.captured` **and** `order.paid` for one order ⇒ **two**
   `payment_event` rows, **one** fulfilment —
   `test_two_different_event_ids_for_one_order_fulfil_exactly_once`. — Both
   fences kept, as the ticket asks. Red-first: deleting the terminal-state
   check inside `fulfil` fails the second one and not the first.
8. ✅ A **capture whose amount ≠ `total_paise`** does not fulfil and raises an alert.
   — 409 with `payments.amount_mismatch` at ERROR, asserted over the log
   record. The refusal deliberately rolls its own receipt back, so a corrected
   re-delivery is evaluated afresh rather than deduped into silence.
9. ✅ `payments.fulfil` is called from **exactly two** call sites (webhook capture,
   SC-4g redemption) — structural fence, so a third path cannot quietly appear.
   — `main._handle_webhook_event` and `main._apply_redemption`, asserted as an
   exact list.
10. ✅ With **no Razorpay env set**, `POST /billing/orders` is **503** naming the
    missing variables and the webhook is **503**; no code path invents a default
    endpoint or key. Fence at both positions.
11. ✅ **R8** — 9.2's constraints, the partial/unique indexes, the idempotency key
    and the fulfilment transaction run against a **real Postgres 16** through
    `tests/unit/_customer_console_ladder.py`. The new suite
    (`tests/unit/test_customer_console_payments.py`) is added to §7's command
    block **and** to `pr-check.yml`'s hand-maintained skip-guard list in the
    **same PR** — a skipped R8 test proves nothing (CP-3). — **115 tests, 0
    skipped.** The suite additionally reads `pr-check.yml` and both owning
    specs and fails if its own name is ever dropped from them, which is the
    closest a hand-list gets to defending itself.
12. ✅ `uv run ruff check .` clean; the existing Console suites stay green,
    `test_the_unauthenticated_route_set_is_exactly_health` included. — Ruff's
    **blocking** selection (`F821,F601,F602,F502,F7,B006`, `pr-check.yml:51`)
    passes repo-wide, and the full report gains **zero** new findings for the
    touched files. All ten Console suites green: **387 tests** (382 at the
    build, **+5 from the 2026-08-19 verification repair**), **0 skipped**.
    ⚠️ **One existing fence was AMENDED, minimally and with the argument in
    place**: `test_a_deployment_key_reaches_resolve_and_nothing_else` asserted
    **401** for every non-resolve route, which was exactly right while every
    door was a bearer-token door. The webhook is not one — it authenticates by
    HMAC over the raw body — so it is named in a
    `_SIGNATURE_AUTHENTICATED_ROUTES` set of one and asserted to refuse as
    `{400, 503}`. The property (*a deployment key is not admitted*) is
    unchanged; relaxing it to "anything but 2xx" would have passed on a 500.
    `test_the_unauthenticated_route_set_is_exactly_health` is untouched and
    green — which is the point of registering the verifier in
    `AUTHENTICATING_DEPENDENCIES`.

*Clauses 13–17 added 2026-08-18 with the repair round's answers.*

13. ✅ **Ownership is a predicate, not a convention (B5, 9.3(7)):** org A reading
    or redeeming against org B's order id and against a random UUID gets four
    responses that are **byte-identical** in status and body —
    `test_a_foreign_order_and_an_unknown_order_refuse_identically`. — A fifth
    case rides along: a **malformed** id is the same 404, not a driver 500 and
    not a third distinguishable shape. Red-first: resolving the order without
    the org scope and 403-ing a foreign one fails the fence.
14. ✅ **An order can be read back (B6, 9.3a):** `GET /billing/orders/{id}` and
    `GET /billing/orders` answer under the organization key, scoped to that org,
    carrying no provider identifiers, with `failed` and `abandoned` visible —
    which is what makes WS-30 SC-4a done-when 5 buildable at all. —
    `test_the_order_read_carries_no_provider_identifiers` is structural over
    `OrderView.model_fields` and pins the field set exactly, so an addition
    argues with a red test.
15. ✅ **The ₹0 and paid paths differ in exactly two places (B4, 9.6):** the
    `seat_grant.reason` prefix and `org_subscription`'s three provider columns.
    The excluded classes are surrogate ids and clock columns, expressed as
    classes; a third difference fails. — Both classes are **predicates over the
    value's type**, not name lists: a name rule would have swallowed
    `provider_customer_id` and `provider_subscription_id`, the two columns the
    fence exists to assert. Building it found a third clock column the ticket
    did not list (`trial_ends_at`), which the class covers without an edit —
    which is what stating classes buys. The two fixtures share one owner
    identity so `seat_assignment.user_identity_id` is equal **by construction**
    rather than by exclusion, and the period columns are asserted equal
    separately so the excluded class cannot hide a difference in the term.
    ⚠️ **Named residual on the paid path:** `provider_customer_id` carries
    whatever the capture payload does. Razorpay sends `customer_id` only when a
    Customer object exists there, so a real capture may write NULL and the
    difference then narrows to two columns. Recorded rather than papered over
    with an invented identifier — a payment id in a customer column is worse
    than a NULL. `provider_subscription_id` carries the provider's **order**
    id, since a one-time order sells the term and there is no Razorpay
    Subscription object until SC-5f's mandates.
16. ✅ **Capture does not move the lifecycle (B9, 9.3(5)):** a `suspended` org
    whose order is captured is **still `suspended`** afterwards — asserted
    directly on `organization.status` — and the only writer of that column
    remains `POST /orgs/lifecycle` under `Operator`.
    `test_a_capture_does_not_transition_the_organization`. — The test also
    asserts the *purchased term* landed (`org_subscription.status = 'active'`):
    the two rows disagreeing is the expected intermediate state, and the
    two-step is the operator's.
17. ✅ **A discount code is minted through `keys.py` and never stored in the
    clear** (SC-4g (i)): the issue response is the only place the token exists,
    and no audit row, log line or read route contains anything but the prefix.
    — Asserted by feeding the issued token back through the shared
    `split_key`/`hash_secret` seam and searching the stored row, the audit rows
    and the read route's response for the secret.

*Clause 18 added 2026-08-19 with §6 item (f)'s build — the held-back item that
turned out to be this ticket's, and the only one of the six a fake provider and
a real Postgres can close on their own.*

18. ✅ **A customer credential can read the priced catalog (§6 item (f)):**
    `GET /billing/catalog` answers the **active** `plan_catalog` rows as
    `slug · name · kind · price_paise · sort_order` under the organization key
    on the `can_pay` door — so WS-30 SC-4a's launch done-when 1 (*no hard-coded
    price ladder in TypeScript*) has a data source, and a **suspended**
    organization can read the thing it must buy. — Two fences, both shown red
    first: `test_the_catalog_read_never_boards_an_inactive_row` (a seeded
    inactive row plus the two seeded INACTIVE Centers are absent, and three
    active slugs are present so an empty answer cannot pass) and
    `test_the_catalog_read_carries_no_per_org_state_and_paise_only` (the field
    set pinned exactly on the model *and* on the wire; `price_paise` compared
    against the NUMERIC rupees in the database, not a constant; two different
    organizations get a byte-identical answer; a `deleted` org 403s).
    `active` sits in `store.active_plans`'s WHERE clause, not in the caller —
    `priced_plan`'s rule stated once more — and the ONE `payments.paise` does
    the conversion, so quote and charge cannot drift into two denominations.
    **No migration:** a read over tables 001 and 007 already ship.

*Clause 19 authored 2026-08-20 (WS-31, `ws-31-seats-mvp`) with §6 item (g) — the
customer seats read the post-signup MVP needs. **✅ BUILT 2026-08-20**: `GET
/me/seats` (`main.py`, `my_seats`), the `SeatsView`/`SeatPlanView` pair, and the
two red-first R8 fences below; suite 119/119 against a real Postgres 16, 0
skipped. Seat WRITES / Console deploy / flags stay owner-gated (§8 gate 4).*

19. ✅ **BUILT — A customer credential can read its OWN seats (§6 item (g)):**
    `GET /me/seats` answers, for each plan the calling organization holds a grant
    or a live assignment on, `plan_slug · purchased · assigned · available ·
    oversubscribed` under the organization key on the **`can_pay`** door — so the
    post-signup MVP's seat grid (`subscription_console.md` SC-1a) has a
    customer-reachable data source, and a **suspended** organization can see the
    seats it is deciding whether to pay for; a **deleted** one 403s. The four
    counts come from the ONE seat vocabulary — `store.seat_rows` folded through
    `seats.seat_counts` (§3.3, D32.5), the same computation `billing_summary`
    runs — never a second SQL, and the frontend renders them verbatim. — Two
    fences, both to be shown red first, both R8 against a real Postgres on the
    Console ladder, beside `TestTheCatalogRead` in
    `tests/unit/test_customer_console_payments.py` (already on §7's list and
    pr-check's skip-guard, so the read rides item (f)'s suite):
    `test_the_seats_read_is_scoped_to_the_key` (two orgs; org A's read never
    carries org B's rows) and
    `test_the_seats_read_carries_the_seat_vocabulary_and_nothing_else` (the field
    set pinned exactly on the `SeatPlanView` model *and* on the wire; the counts
    equal `seat_counts` fed the same `seat_rows`; a `suspended` org reads it, a
    `deleted` org 403s). **No migration:** a read over the seat tables in `001`
    already ships.

*Clause 20 authored + ✅ **BUILT** 2026-08-21 (WS-31, `ws-31-members-read`) with
§6 item (i) — the customer member-list read the manage-seats surface (SC-2b)
needs; a separate agent built it against these done-whens and an independent
audit validated this clause first. The READ is agent-safe (fixtures / real
Postgres); Console deploy / flags / a live key carrying it stay owner-gated
exactly as item (g)'s, and the owner ratifies the door (the PII/gating note
below) at flip-time.*

20. ✅ **BUILT — A customer credential can read its OWN members (§6 item (i)):**
    `GET /me/members` answers, for each membership row in the calling
    organization, `email · role · status` under the organization key on the
    **`can_pay`** door — the exact sibling of item (g)'s `GET /me/seats` — so the
    manage-seats surface (SC-2b, `subscription_console.md` SC-2) has a
    customer-reachable roster to assign seats against, and a **suspended**
    organization can see whom to seat while it decides whether to pay; a
    **deleted** one 403s. The roster comes from the ONE new membership-list read
    `store.org_members` (the established `org_membership ⋈ user_identity` join
    idiom, `store.org_owned_by_other`/`deployment_visible_orgs`' own join),
    org-scoped by `caller.organization_id` — never a second SQL, no per-member
    recompute, no `org_slug` on the wire (R11). Member EMAILS are PII: this read
    rides the same "any signed-in member" hardening posture item (g) flags
    (`seats/route.ts:26-28`, its own ticket); admin-gating the roster is a
    recorded owner-overrulable alternative (§6 item (i)). No per-member seat
    summary (a second `seat_assignment` query — DEFERRED). — Two fences, both to
    be shown red first, both R8 against a real Postgres on the Console ladder,
    beside `TestTheSeatsRead` in `tests/unit/test_customer_console_payments.py`
    (already on §7's list and pr-check's skip-guard, so the read rides item
    (f)/(g)'s suite): `test_the_members_read_is_scoped_to_the_key` (two orgs; org
    A's read never carries org B's members) and
    `test_the_members_read_carries_the_membership_triple_and_nothing_else` (the
    field set pinned exactly `{email, role, status}` on the `MemberView` model
    *and* on the wire; the roster equals `store.org_members`; a `suspended` org
    reads it, a `deleted` org 403s). **No migration:** a read over `org_membership`
    + `user_identity` in `001` already ships.

*Clause 21 authored + ✅ **BUILT** 2026-08-21 (WS-31, `cc-manual-activate`) with
§6 item (j) — the MANUAL / bank-transfer activation, the offline twin of
`payments.fulfil`. R8 against a real Postgres 16; the Console deploy, the operator
token and issuing it stay owner-gated (§8), and the route ships DARK.*

21. ✅ **BUILT — An operator can activate a PAID plan a customer paid for OUT OF
    BAND (§6 item (j)):** `POST /billing/subscriptions/activate` (Operator-only,
    `main.py` `activate_subscription_manual`) writes `org_subscription`
    `status='active'` with `provider='manual'` (the value
    `001_customer_console.sql:163`'s CHECK pre-provisioned and nothing wrote
    until now, NULL provider ids), grants the plan's PAID seats
    (`grant_seats`, `reason='manual'`), and — when the request carries AI credits
    — adds them (`add_credit`, `reason=credits.LEDGER_REASON_MANUAL`, the bank
    reference as `ref`), all in ONE transaction over the SAME store seams
    `payments.fulfil` uses, minus the order — no new seam. It does **not** touch
    `organization.status` (like `fulfil`, a suspended org that pays holds an
    active term and stays suspended until an operator posts the transition,
    done-when 16). The operator's free-text `reference` and `reason='manual'` land
    in the `control_audit` detail (the table has no `reason` column). **The
    double-grant guard:** activating grants paid capacity and `grant_seats` /
    `add_credit` are append-only, so an org that already holds an **active**
    subscription is refused **409** (an operator adjusts a live term through the
    seat/credit routes, never by re-activating) — a `trial` (or any non-active)
    org, and one with no subscription row, activate. The 409 is a
    check-then-grant, so it is **serialised by an org-keyed advisory lock**
    (`store.lock_org_activation`, the `lock_seat_capacity` / `lock_discount_capacity`
    idiom — `pg_advisory_xact_lock(hashtext('activation:<org_id>'))`, taken as the
    first statement of the transaction, before the status read, auto-released at
    txn end): without it two concurrent activations of one fresh org would both
    pass the 409 and both grant (the append-only INSERTs have no conflict target),
    so the one-grant guarantee holds under **concurrency**, not merely a sequential
    repeat. Per-org key, so different orgs never serialise. An unknown/inactive
    `plan_slug` is **400** (priced active rows only, as the checkout), an unknown
    org **404**. Not an org-key route, so
    `test_no_org_key_route_writes_an_entitlement_or_ledger_row` stays green — this
    grant never rides a customer credential. — Fences in
    `tests/unit/test_customer_console_payments.py::TestTheManualActivation`
    (already on §7's list and pr-check's skip-guard), all R8 against a real
    Postgres: the happy path (active + `provider='manual'` + period; paid seats;
    credits; the audit reference), idempotency (a repeat activate 409s and
    double-grants nothing) **and its concurrency proof** (two threads activating
    one fresh org, released on a barrier, land exactly one grant / credit /
    subscription — red-first without `lock_org_activation`, the
    `test_a_concurrent_double_redeem` idiom), authz (Operator required — org key / deployment key /
    no auth each 401, mutation-proved via `dependency_overrides`, and the
    operator IS admitted), plan validation (unknown/inactive → 400, nothing
    written), and the provider + ledger-reason fences unweakened
    (`credits.LEDGER_REASONS` gains exactly `'manual'`). **No migration:** the
    `'manual'` provider value already ships in `001`; the ledger reason is not
    CHECK-constrained (R6, `credits.py`).

**Build-slice edits this repair round could NOT make — it is docs-only.** Listed
so the implementer does not have to rediscover them, and because **nothing tests
a comment**. *(All three ✅ made 2026-08-18 in the build.)*

1. ✅ `customer_console/lifecycle.py` — append `can_pay` **last**; convert the
   six `STATES` rows (`:64-78`) to keyword construction (9.3(5)). The field
   takes **no default**, so a state row that forgets it fails to construct
   rather than inheriting the permissive answer; fenced by
   `test_the_states_table_is_keyword_constructed`, which reads the source
   because nothing behavioural can see a positional argument.
2. ✅ `customer_console/main.py` — `_capability_block`'s docstring now says
   **five**, and says why `can_pay` is not on the deployment's wire (it is a
   Console-side door; a deployment decides nothing with it).
3. ✅ `tests/unit/test_customer_console_resolve.py` — the same count in a
   comment, same edit, same reason.

**CP-9 findings recorded rather than fixed** — *2026-08-19, raised by the
adversarial review that produced the P0/P2 repairs above. None is decided by any
clause of this ticket, so none was decided in the repair; each is a line for the
board rather than a drive-by.*

1. **`store.activate_subscription` resets `current_period_start` on every
   capture** — a second purchase **restarts** the term instead of extending it.
   Which of the two is correct is a commercial call (SC-5's problem, and the
   renewal/proration ticket is where it belongs); at one-customer volume with
   one purchase there is no observable difference yet. *(2026-08-19)*
2. **`CreateOrderRequest`'s `quantity` and `lines` are unbounded** — nothing
   caps either, so a customer-authenticated route can be handed a quantity that
   overflows `BIGINT` on the paise arithmetic and returns a **driver 500**
   rather than a 400. No value moves (an order grants nothing) and the row is
   never written, so it is a refusal-shape defect, not a money one. *(2026-08-19)*
3. **`GET /billing/orders` runs `store.redemption_for_order` once per row** —
   51 queries for a full page. Harmless at launch volume; it is on the list
   before it becomes a page nobody can load. *(2026-08-19)*

**Non-goals of CP-9:** the checkout **UI** (WS-30 SC-4a) · the **discount-code**
tables and their semantics (WS-30 SC-4g — CP-9 consumes a validated redemption,
it does not define codes) · the **tax-invoice document** and its gapless serials
(WS-30 SC-5b/5c) · **dunning** and mandates (SC-5f) · **credit packs** (9.1) ·
Stripe as the seam's second implementation (D19.5 — when the first international
customer appears, not before).

**CP-2a · Signup and provisioning.** ✅ **BUILT 2026-08-13** — lifecycle state machine (`customer_console/lifecycle.py`), `POST /orgs/lifecycle` (transitions only, never free-form status writes), trial subscription + resumable `provisioning_run` at provision, and lifecycle enforcement on sign-in, seat writes and the AI path. ⚠️ **Fracktal signs up through this same flow (D36)** — there is no first-party bypass, because a bypass makes the customer path the one nobody tests. Still open: the self-serve signup *form* (this is the API beneath it) and certified deletion. *(Added by D33 — §4 finding 4: no signup route
exists anywhere in the app tree; the only way in is `ensure_owner_bootstrap()` promoting
an `EXECUTIVE_EMAILS` address.)* Self-serve signup creating the org, its first owner, its
placement and its trial, plus **GST fields captured at signup** (GSTIN + registered
state — `saas_operations_doctrine.md` §3.1: retrofitting a GSTIN onto an already-invoiced
org is a customer conversation, not a migration). **Done when:** provisioning is
**idempotent and resumable** — a run interrupted after any step and re-run produces one
org, not two, and **⚠️ a test kills it at each step — OWED, see below**; the
lifecycle states of §4.1d are enforced with `suspended` keeping **login working**
while features lock; an org reaching `cancelled` retains data through a named
export window.

> ✅ **A second, larger gap — CLOSED 2026-08-19 by `saas_multitenancy.md` §11
> MT-1j slice 4 (operator arm).** The record is kept rather than deleted,
> because CP-2a's ✅ and this fact contradicted each other for a day and the
> next reader needs to know which way it resolved.
>
> *The gap, as measured 2026-08-19:* **`POST /orgs/provision` did not write
> `org_placement`, and nothing else in the tree did either.** The route wrote
> `organization`, `user_identity`, seats, `org_membership`, `org_subscription`,
> `provisioning_run` and an audit row — and no placement. The only
> `INSERT INTO org_placement` anywhere was a test fixture
> (`tests/unit/test_customer_console_resolve.py`), whose docstring named the gap
> and correctly declared closing it a non-goal *of CP-2b*. **The consequence
> composed with CP-2b's inner join** (`store.py`,
> `JOIN org_placement p … WHERE p.deployment_id = :dep`): an organization
> provisioned through this route could never be resolved by any deployment key,
> so a provisioned customer's sign-in failed closed in a way that read as a
> Console outage. Under **D36.1** — Fracktal onboards through this same route —
> that was customer zero's path. Neither CP-2a nor CP-2b was wrong to leave it:
> each disclaimed it to a ticket that, until that date, did not exist.
>
> *How it closed:* `ProvisionRequest` gained a **required `deployment_label`**
> and the route writes exactly one `org_placement` row for the named deployment,
> `database_target` NULL, `ON CONFLICT (organization_id) DO NOTHING` —
> provisioning places, it never moves (409 on a different label, 404 on an
> unknown one, ~~422~~ **400** on a missing field even with exactly one
> deployment seeded).
>
> ⚠️ **AMENDED 2026-08-19 by CP-2c slice 1 — the missing-field answer is now
> 400, not 422** (D46.6 item 1 as amended below in CP-2c; landed with slice 1's
> PR). A two-arm route cannot keep `deployment_label` required AT THE MODEL,
> because the deployment-key arm has to *refuse* a presented label and a
> required field makes that unexpressible. So the field is `str | None` on the
> `ResolveRequest.org_slug` precedent, both arms' rules moved into the handler,
> and the operator arm's missing-label refusal moved with them. **What was lost
> is a property, not just a status code:** the 422 meant *pydantic refuses
> before the handler runs, so no code path can consult a count*. That is gone;
> the handler now runs with no label. Slice 4's mutation-proved no-inference
> fence was amended red-first in the same PR to pin the weaker property — *the
> handler refuses without consulting a count* — and re-mutated both ways
> (a working sole-deployment guess in the handler, and the original
> `count(*) = 1` fallback in `deployment_by_label`); both go red.
>
> This is a **breaking change to `POST /orgs/provision`**, free because the
> Console is deployed nowhere; the six CP-2a-era suites were updated in the same
> PR. The end-to-end clause — *provision, then a deployment key resolves that
> org* — is fenced by
> `test_customer_console_resolve.py::TestProvisioningIsWhatPlacesAnOrg`.
> The **deployment-key arm** (the key names itself) was CP-2c's and **BUILT
> 2026-08-19 as CP-2c slice 1**; *issuing or widening a REAL key* stays behind
> §8 gate 7 / §6 gate (f), which is the act, not the code.
>
> ⚠️ **One acceptance clause is OWED, not met — recorded 2026-08-18 rather than
> struck.** "a test kills it at each step" is **not implemented**.
> `tests/unit/test_customer_console_lifecycle.py` proves *re-provision
> idempotence* — `test_re_provisioning_does_not_create_a_second_run_or_org`
> (`:155`), plus `test_provisioning_records_a_resumable_run` (`:147`) — which is
> the whole-run replay case. What no test does is **interrupt the run between
> steps and resume it**, which is the case the clause was written for and the
> only one that can leave a half-provisioned org: a `provisioning_run` row
> exists precisely because a run can die in the middle. The ✅ stands for what
> shipped; this clause is a **known gap with a name**, closed by a future
> `test_a_run_killed_after_each_step_resumes_to_one_org` parametrised over the
> step list (so a step added later is covered without anyone remembering).
> Deliberately not struck: an acceptance criterion that turns out to be
> unbuilt is evidence about the build, and deleting it deletes the evidence.

**CP-2b · Sign-in resolve: the product calls the registry.** ✅ **BUILT
2026-08-18 — CONSOLE HALF then DEPLOYMENT HALF, the same day.** §5.2's wiring —
prose since 2026-08-12, and now code on both sides of the wire.

**What is built (the Console side of the wire).** The fourth auth scheme
`cc_depl_<prefix>_<secret>` beside the three that existed, with a capability set
of exactly `{resolve}` *(as this ticket shipped it — CP-2c slice 1 minted
`provision` beside it 2026-08-19; a key holds only what its issuer granted,
enforcement stays per-capability)* enforced as a dependency
(`customer_console/auth.py` — `DeploymentCaller`, `deployment_or_operator`,
`AUTHENTICATING_DEPENDENCIES`); the sibling table
`infra/customer_console/006_deployment_key.sql`; and `POST /registry/resolve`
answering **two schemes with two response shapes chosen by the credential**
(`customer_console/main.py` — `_resolve_for_operator`,
`_resolve_for_deployment`, `_allocate_core_seat`), with the visibility predicate
in `store.deployment_visible_orgs` and the key lookup in
`store.resolve_deployment_key`; and — landed with the deployment half —
`_capability_block` (`main.py:771`), which puts the three
`lifecycle.capabilities_of()` booleans on the **deployment arm's** wire and only
there. Fenced by `tests/unit/test_customer_console_resolve.py` (**52** tests, was
45) against a real Postgres 16, every fence shown **red first** by reverting the
behaviour it pins — including a real two-thread race against the seat cap.

**What is built (the deployment side).** ✅ *2026-08-18.*
`packages/acb_auth/acb_auth/console_resolve.py` — the HTTP client, the
read-through cache in front of the projection, `invalidate(email=None)`, and the
projection read/write, branching on the **resolve outcome** and never on a
lifecycle string. Its **one** caller,
`apps/services/gateway/gateway/routes/signin.py` (`POST /signin/resolve`),
mounted in `gateway/main.py`, covered by the app-wide `require_authenticated`
and absent from `PUBLIC_ROUTES` — and, since the 2026-08-18 review, **refusing
by itself when the gateway is not wired** (F5; the route is not inert, the BFF
flag is). The four settings fields. Tenant migration
`177_console_resolve_projection.sql`. The **new `signIn` callback** in
`workbench/control_plane/src/auth.ts` reaching hop 3 through
`headersActingAs()`, with `ConsoleUnavailable` and `WorkspaceChooserRequired` in
`errorCopy.ts`. **The seat cap is now consulted by the product**, which is the
claim §5.2 rests on (*"the box asks before admitting them"*).

**Every clause is met.** 1, 2, 3, 4, 5, 6, 7, 9, 11 · 8 (both halves) · 10 (both
ladders) · 12 (shape **and** the `capabilities` block), plus the two R7 fences
the Console implementation itself owes and the ones the deployment half owes.

**The deployment half's artifacts, each with a path** *(named 2026-08-18 in
answer to blocker B6 — "primary artifacts homeless" is not a dispatchable
ticket; ✅ marks what landed)*:

| Artifact | Path | New or changed |
|---|---|---|
| Resolve client + cache | `packages/acb_auth/acb_auth/console_resolve.py` | ✅ **new module**; added `httpx>=0.27` to `packages/acb_auth/pyproject.toml` — §6(e) |
| The one caller | `apps/services/gateway/gateway/routes/signin.py`, `POST /signin/resolve` | ✅ **new route module**, mounted in `gateway/main.py` beside the others; authenticated by the app-wide `require_authenticated` and **not** added to `PUBLIC_ROUTES` (`main.py:487`) — §6(e). The existing `test_default_deny_auth.py` sweep covers it without a new entry, which is what "auth by construction" is for |
| Tenant-side config | `packages/acb_common/acb_common/settings.py` — `customer_console_url`, `customer_console_deployment_key`, `customer_console_resolve_ttl_seconds`, `customer_console_resolve_max_staleness_seconds` | ✅ **four new fields**, beside `crm_auto_lead` — §6(f) |
| Projection columns | ✅ **`infra/postgres/177_console_resolve_projection.sql`** — number taken by listing `infra/postgres/` at build time (R1; `176_people_skills.sql` was the highest on disk) and re-checked at commit. **Next free number: 178** | `org_membership.resolved_at`, `organization.registry_status`, and — **added 2026-08-18 by §6(j)** — `organization.registry_capabilities JSONB`, because B-d's cached outcome needs a durable carrier and a *string* must never be one. All three nullable with **no default**; zero rows added to `gen_tenant_migration.EXEMPT` |
| BFF hop | `workbench/control_plane/src/auth.ts` — a **new `signIn` callback**, gated by **`CUSTOMER_CONSOLE_RESOLVE_ENABLED`** (Next-side env, read by the workbench only — **not** an `acb_common` settings field; default unset = OFF, and only the exact string `"true"` arms it) | ✅ §6(g), flag added 2026-08-18 by the F1 repair |
| Refusal copy | `workbench/control_plane/src/app/signin/errorCopy.ts` | ✅ two new keys — §6(g) |
| R8 suite (tenant DB) | `tests/unit/test_deployment_resolve_cache.py` | ✅ **new (31 tests; 50 after the 2026-08-18 review repairs)**, + `tests/unit/_tenant_ladder.py`, + one line in `tests/conftest.py` beside `:16` snapshotting `TENANT_LADDER_DATABASE_URL` at launch — §6(i) |
| Structural fences (no DB) | `tests/unit/test_console_dependency_boundary.py` | ✅ **new (4 tests)** — §6(d) manifest + import scan, §6(e)/clause 11's single-caller ratchet, §6(j)'s no-lifecycle-string scan |
| **Route fences (no DB)** | `tests/unit/test_signin_resolve_route.py` | ✅ **new (7 tests), 2026-08-18 — finding F5's repair.** The unwired refusal, its `signin.resolve_unwired` error line, the module NOT consulted, the wired pass-through, the 401 for an anonymous caller, and R11 (the address comes from the context, never the body). ⚠️ Before it, **nothing in the tree tested this route** — which is how a P0 shipped on it |
| Frontend fence | `workbench/control_plane/src/app/signin/signin.test.ts` (extended) | ✅ five new fences — §6(g) |
| CI | `.github/workflows/pr-check.yml` — a second Postgres service on **`pgvector/pgvector:pg16`** exporting **`TENANT_LADDER_DATABASE_URL`** (⚠️ **never `DATABASE_URL`** — §6(i)(2)), plus a skip-guard entry | ✅ §6(i), with its own reachability assertion **and a second `grep`**: the guard's single hard-coded grep on the *Console's* skip string would have stayed green while the tenant suite skipped, which is the CP-3 failure class one layer up |

**One slice or two — implementer's choice, both fenced identically.** *(Added
2026-08-18 with the B-a…B-g answers: the re-audit confirmed a dispatchable
narrow slice inside this table. **Built as six commits**, beginning with exactly
the boundary-and-config commit described below.)* The deployment half may land
as **one** change, or it may **begin** with a boundary-and-config commit — the
two structural fences of §6(d) (`test_console_dependency_boundary.py`), the four
settings fields of §6(f), the `httpx>=0.27` addition to
`packages/acb_auth/pyproject.toml` and the
two `errorCopy.ts` strings of §6(g) — which touches no database, mints no
credential, changes no runtime behaviour, and is green on day one by
construction. Nothing in the acceptance list is relaxed either way: the
boundary commit satisfies the parts of clauses 6/11/12 whose fences are
structural and leaves every DB-gated clause open and named. Splitting is a
review-size choice, not a scope choice.

**Findings recorded rather than fixed** (none is decided by any clause, so none
was decided in the build): `org_membership.status` is **not** consulted by the
visibility predicate — clause 4 states the criterion as *holds a membership* and
clause 5 enumerates three invisible cases, none of which is "membership was
removed", so a `removed` member still resolves; and `deployment.status`
(`active|draining|retired`) does not affect its keys.

📌 **Named follow-up, opened 2026-08-18 by the P1-2 repair — record a refusal
where the FACT lives.** `{"sign_in": false}` is a statement about a **person**,
and today it is written onto `organization.registry_capabilities`, because the
403 names no organization and the org row is the only thing the box can join to.
The consequence is collateral by construction: the org-scoped fallback read
serves that refusal to **every** member of the organization. The P1-2 repair
time-bounds the damage (`MAX_STALENESS`, so it self-heals within 24h); it does
not remove it. **Removing it means moving the boolean to `org_membership`**, the
person↔org row — an expand/contract migration plus a read-path rewrite (R6), so
it is a ticket of its own rather than a drive-by. Until it is taken, the
interim is the ceiling and the blast radius is written down here rather than
discovered during an incident.

⚠️ **Two pre-existing defects on shipped surfaces, found by the CP-2b review
(2026-08-18) and deliberately NOT fixed here.** Both are inherited, both are
recorded so the next reader does not discover them in a billing incident:

1. **The operator arm of `POST /registry/resolve` ignores `can_write_seats`.**
   `POST /billing/seats` refuses a `suspended`/`cancelled` organization with
   403 (`main.py`, `capabilities_of(state).can_write_seats`); the operator
   resolve does not, so it allocates NEW seats to an organization whose seats
   are supposedly locked. Its current behaviour is pinned by
   `test_customer_console_lifecycle.py:170`, so changing it is a behaviour
   change to a shipped surface and wants its own ticket. **The CP-2b deployment
   arm does consult it** — a suspended org returns `seat: "not_allocated"` and
   writes nothing, login still open — fenced by
   `test_a_suspended_org_allocates_no_new_seat_on_sign_in` and
   `test_an_existing_seat_survives_suspension_reporting`. The two arms therefore
   differ on purpose and the difference is this line.
2. **`POST /billing/seats` does not take the seat-capacity advisory lock.** The
   cap was check-then-insert everywhere: `seat_rows` reads at READ COMMITTED and
   the partial unique index enforces one seat per *person*, not N per
   *organization*, so two concurrent first assignments with one seat left both
   landed (measured, 10 races on a real server). CP-2b adds
   `store.lock_seat_capacity` — `pg_advisory_xact_lock` on
   `(organization_id, plan_slug)`, taken before the count — and routes **both
   arms of resolve** through the single `_allocate_core_seat` path that takes
   it, fenced by
   `test_two_concurrent_first_resolves_cannot_oversubscribe_the_cap`.
   `POST /billing/seats` remains unserialised; closing it is one call and one
   ticket, and `seats.py`'s `oversubscribed` docstring names the gap rather than
   claiming a guarantee the tree does not have.

The 2026-08-18 audit found **three undecided questions** blocking dispatch, and
the **second** audit that day — of the deployment half alone, after the Console
half shipped — found **seven blockers B1–B7**. All ten are answered below as
**agent-proposed defaults, owner may overrule** — the
D16/D17 convention (`work_plan.md` §3: *"proposed defaults, adopted unless the
owner objects"*). They are named as proposals because each has a defensible
alternative; none of them is a commercial call, so none needs to wait.
**(a)–(c) answer the first audit; (d)–(i) answer B1–B7 and are dated
2026-08-18.** Where a proposed default was *overturned* during the write-up
because the code contradicted it, the argument is in the text rather than in a
commit message — there are two, in (e) and (g), and both are flagged
⚠️ **DEVIATION**.

**(a) Which credential — a FOURTH scheme: the deployment key (`cc_depl_…`).**
The endpoint is Operator-auth today (`main.py:526`, `_: Operator`), and the
service itself says why a tenant deployment must not hold that token
(`main.py:946`): *"the workbench must **not** hold the operator token — a tenant
deployment holding a cross-organization credential is the whole thing D32/D35
are arranged to avoid."* Neither other existing scheme fits either: `Internal`
(`auth.py:108`) is the Router's meter-writing token and is equally
cross-organization, and the **organization key is org-scoped**, which is right
for `/me/billing` and wrong here — a **pooled** deployment must resolve *any*
email to *its* org, and the org is not known before the answer.

So mint `cc_depl_<prefix>_<secret>`: issued **per deployment** by the operator in
the Customer Console, stored in that deployment's env, with a capability set of
**exactly `{resolve}`** — one endpoint, nothing else, enforced as a dependency
rather than a per-route `if`. *(⚠️ Updated 2026-08-19, CP-2c slice 1: the
capability VOCABULARY now also carries `provision` — the signup arm of
`POST /orgs/provision` — so "one endpoint, nothing else" describes what a
DEFAULT-issued key reaches, not the vocabulary's ceiling. A key reaches exactly
the endpoints its issued set names; widening a real key's set stays §8 gate 7 /
§6(h).)* What it can learn about an organization is bounded
to what sign-in needs: **org id, slug, placement, lifecycle status, and
seat-existence for the presented email.** Never a balance, never a credit
figure, never an invoice, and never the existence of an organization this
deployment does not serve.

*Storage and verification reuse `keys.py` verbatim* — `mint_key` (`keys.py:50`),
`hash_secret` (`:62`, SHA-256 over a 256-bit machine secret, with its written
argument against a KDF on the hot path), `verify_secret` (`:74`) and
`split_key` (`:84`, split from the LEFT with a bounded count — the flaky-auth
bug that function's docstring records). `mint_key` already takes `env=`, so
`cc_depl_` is a parameter, not a fork.

**A sibling table, not a column on `llm_api_key`:** that table's owner is an
`organization` (`001_customer_console.sql:212-214`, `organization_id UUID NOT
NULL`), and a deployment key belongs to a **`deployment`** (`:82`) — hanging it
off a nullable org id would make "which kind of key is this" a property of a
NULL, and every query would have to remember. So:

```sql
deployment_key(id UUID PK, deployment_id UUID NOT NULL REFERENCES deployment(id),
               prefix TEXT NOT NULL UNIQUE, key_hash TEXT NOT NULL,
               label TEXT, capabilities TEXT[] NOT NULL DEFAULT '{resolve}',
               created_by TEXT, created_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ);
```

⚠️ **R1 — take the migration number at build time.** ✅ **Written as
`infra/customer_console/006_deployment_key.sql`**, the number taken by listing
`infra/customer_console/` at build time (`001`–`005` on disk) and re-checked at
commit; the ladder's own duplicate-number guard
(`tests/unit/_customer_console_ladder.py:62-71`) is the tripwire. Nothing had to
be added to any suite's ladder list — that module reads the directory. The next
free number is **007**, and the next ticket must re-derive it rather than trust
this sentence.

**(b) The R11 shape — the request carries the EMAIL and the KEY, and no org.**
The deployment has already run its own OAuth flow and verified the address
(`workbench/control_plane/src/auth.ts:80-103`, the NextAuth jwt/session
callbacks); what it asserts to the Customer Console is *"this verified person is
signing in"*. It asserts **nothing about which company they belong to** — that
is the answer, derived server-side from `org_membership` intersected with
`org_placement`. This is `user_management_contract.md` **R11** satisfied at its
strongest available reading: not "the tenant claim is validated against the
caller" but **"the caller makes no tenant claim at all"**, which is the same
property `organization_from_key`'s docstring already argues for the org key
(`auth.py:147-157`: *"there is no `X-CC-Org` parameter and no `org_slug` body
field"*).

*Why the existing body-slug shape stays legal — for the operator scheme only.*
`ResolveRequest` (`main.py:119-122`) carries `org_slug`, and under **operator**
auth that is not an R11 violation: R11 forbids taking the tenant from *request
input* when a caller has a tenant of their own to be inferred from. The operator
credential has no tenant — it is cross-organization by design and by §0.9.2 —
so there is nothing for the slug to override; naming the org is the operator
*performing an act on a named customer*, exactly as `POST /billing/seats`
(`main.py:1315`, `assign_seat`) and `POST /credits/grant` (`main.py:1370`,
`grant_credits`) already do *(both re-measured 2026-08-20 from `:635`/`:690`)*. Under a
**deployment** key the same field is the forbidden shape, because that caller
*does* have an identity from which the answer must be derived. **One endpoint,
two schemes, one shape rule:** a deployment key presenting `org_slug` is
refused **400**, never silently ignored — an ignored field is a caller who
believes it worked. A second endpoint is refused for the CLAUDE.md §5 reason: it
would be a second way to do an existing thing.

**(c) Failure semantics — fail closed, degrade bounded.** What happens when the
Customer Console is unreachable at sign-in is the question that most needs
deciding before a line is written, because the tempting answer ("admit them,
we'll sort it out") recreates **D33.1** — auth that fails **open** — which CP-0
existed to remove.

- **No cached resolution → REFUSE.** Fail closed. The refusal page says the
  service is **temporarily unavailable**, not "access denied": the person has
  done nothing wrong, and a wrong-looking denial generates a support ticket and
  a password reset that fix nothing.
- **A cached resolution → PROCEED on the cache.** A successful resolve is cached
  in the deployment, with two named bounds:
  - **`CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`** — freshness. *Agent-proposed
    default `900` (15 minutes).* Past it, a reachable Customer Console is
    re-consulted; an unreachable one falls through to the ceiling below.
  - **`CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`** — the hard ceiling.
    *Agent-proposed default `86400` (24 hours).* Past it sign-in **fails
    closed even for a cached person**. A cache with no ceiling is not a cache,
    it is a second identity system that never expires — and the day it matters
    is the day an account is suspended for non-payment.
  Both are named config values living beside the existing settings
  (`packages/acb_common/acb_common/settings.py`, the
  `crm_auto_lead: bool = False` shape at `:140`), so a test pins the number by
  reading it rather than by re-deriving it.
- **A cached DEAD state is a fact, not a stale hint — it applies immediately,
  at any freshness.** *(Added 2026-08-18. The clause that stood here said
  "`suspended`/`deleted` take effect no later than the TTL", which is **false**
  and contradicted the bullet directly above it: the TTL only bounds anything
  while the Customer Console is **reachable**. On a partitioned box the real
  bound is `MAX_STALENESS`, so as written a `deleted` organization's user could
  keep signing in for 24 hours.)* The rule that closes it: a cached answer whose
  recorded capabilities are more restrictive than "admit" is applied **at once,
  without re-consulting and without any freshness grace**. Staleness may only
  ever make the cache *more* restrictive — a record can be relaxed by a
  successful re-consult and never by expiry.
  ⚠️ **Re-stated in outcome terms 2026-08-18 (B-d, §6(j)), because the previous
  wording — *"a cached answer whose lifecycle state is `suspended`, `cancelled`
  or `deleted`"* — described the box reading a lifecycle STRING, which (d)
  forbids by name, and named two states that do not refuse anything.** The
  carrier is concrete now: a **403** outcome writes `{"sign_in": false}` into
  `organization.registry_capabilities`, and **that boolean** is what refuses at
  any freshness **up to `MAX_STALENESS`** *(bound added 2026-08-18 — review
  finding P1-2; unbounded it locked out every member of the organization
  permanently, and §6(j) row ii carries the argument and the follow-up)*.
  `suspended`/`cancelled` arrive in a **200** with
  `sign_in: true`; what they cache is `write_seats: false` / `use_ai: false`,
  which change no sign-in decision in this ticket (clause 7, B5).
  - ⚠️ **Applied through the ONE state machine, not a second copy of it.**
    `lifecycle.capabilities_of()` is authoritative and CP-2a shipped it that way
    on purpose (`lifecycle.py:10-28,64-78`): `deleted` is the only state with
    `can_sign_in=False`, so it is the state that **refuses sign-in**;
    `suspended` and `cancelled` keep the door open deliberately (a customer who
    cannot log in cannot pay you, and `cancelled` *is* the export window) while
    `can_use_ai` / `can_write_seats` go false — and **those locks** are what
    land immediately off the cache. Writing "cached `suspended` refuses" into
    this ticket would have minted the permissive-copy-drifts failure that
    module exists to prevent, in the restrictive direction.
  - Fences: `test_a_cached_dead_org_refuses_without_asking_the_console` (drive a
    **403** first — `deleted` placed here — then make the Console unreachable and
    assert the refusal inside *and* outside the TTL),
    `test_a_cached_suspended_org_keeps_login_and_loses_write_seats`
    (a **200**; login still works, `use_ai`/`write_seats` false), and
    `test_staleness_never_relaxes_a_cached_state`. *(Second fence renamed
    2026-08-18 from `…_locks_features_without_asking_the_console`: the box
    records the booleans, it does not lock features — B5, clause 7.)*
- **So the honest bound is a pair, not a number.** **Console reachable →
  `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`.** **Console unreachable →
  `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`**, with the dead-state rule
  above cutting the `deleted` case to *immediate* inside that ceiling. §5.2
  states the same pair; the two must be edited together. The two TTLs are
  **settings fields, not bare env reads** — see (f) for their names.
- ⚠️ **"Unreachable" is a BEHAVIOUR, not a socket** *(2026-08-18 — review
  finding P1-1, §6(j) row vi)*. A 5xx, a `401`, a `408` or a `429` means no
  answer was produced, so it takes the unreachable path above. Only a status in
  which the Console *decided* (403, 409) is a refusal. Reading a transport
  failure as a refusal is how one nginx hiccup told every user of every tenant
  their account was not authorized.
- ⚠️ **Freshness is measured across TWO CLOCKS, and the arithmetic says so**
  *(2026-08-18 — review finding P2)*. `resolved_at` is the **database's**
  `now()`; the comparison happens against the **app process's** clock, and in
  the ordinary deployment those are different machines (measured against this
  suite's own containers: 0.40 s apart). So the age function is honest about
  sign — a record stamped materially in the FUTURE is **stale**, never
  "maximally fresh" — with a named `_CLOCK_SKEW_TOLERANCE_SECONDS = 60` for
  ordinary jitter, because a hard zero floor makes every freshly-written record
  read as stale and silently disables the read-through cache. Fences:
  `test_a_record_stamped_in_the_future_is_not_treated_as_fresh`,
  `…_is_RE_CONSULTED`, and
  `test_ordinary_sub_minute_skew_does_not_disable_the_cache` for the other side
  of the bound.
- **Org re-placement is an explicit `invalidate()` trigger.** Moving an
  organization off this deployment must revoke promptly rather than wait out a
  ceiling, so the resolve cache exposes `invalidate(email=None)` in the same
  shape the access cache already does (`acb_auth/access.py:73-98` — "*every
  admin write path calls this. Without it the 60s TTL becomes the latency of a
  permission change*"), and it is called on **(a)** any resolve answer that no
  longer lists an organization this deployment previously served, and **(b)** the
  operator's placement-move runbook, against the **losing** deployment. Stated
  honestly: **(a)** needs the Console reachable, and **(b)** is an operator act
  — there is no Console→deployment push channel in this ticket and building one
  is a non-goal. Absent both, the bound is `MAX_STALENESS`. Fences:
  `test_invalidate_drops_a_cached_resolution`,
  `test_an_org_no_longer_placed_here_is_dropped_from_the_cache`.

---

**(d) The response carries CAPABILITY BOOLEANS; the box never interprets a
status string.** *(2026-08-18 — answers **B1**, and **B5** with it.)* Clause 6
mandated `lifecycle.capabilities_of()` as the one state machine, and
`capabilities_of` lives in `customer_console/lifecycle.py:101` — inside the
Console package. The deployment must **not** import it, so as written the clause
mandated something with no buildable path: either a second copy of the state
machine (forbidden by name in clause 6) or a dependency the tenant deployable
must not have.

*The answer, and it is the one that keeps the single state machine:* the
capability decision is made **where the state machine is**, and only its
**result** crosses the wire. Clause 12's per-organization entry gains

```json
"capabilities": {"sign_in": true, "write_seats": false, "use_ai": false}
```

computed Console-side by the one `capabilities_of()` (`lifecycle.py:64-78` is
the table; `:72-75` is `suspended`/`cancelled`). The box **stores the three
booleans and applies them**. `status` stays in the response, and its only job on
the deployment is **refusal copy** — it is the word the person is shown, never
the input to a decision. A deployment that branched on the string would be a
second copy of the state machine spelled as an `if`, which is exactly what
clause 6 forbids; a deployment that branches on `sign_in` cannot drift, because
there is nothing to drift from.

*This is an ADDITIVE change to a surface that shipped hours earlier the same
day,* and the deployment-half slice therefore carries a **small Console-side
commit**: `_resolve_for_deployment` in `customer_console/main.py` adds the block,
and `tests/unit/test_customer_console_resolve.py` gains its fence. Clause 12's
schema block below is updated now and dated, so the two halves cannot be built
against different shapes. Additive because every existing key keeps its meaning
and no consumer exists yet outside the fences.

⚠️ **B5, answered here because it is the same mistake in the other direction.**
Clause 7 promised that a cached `suspended` *"locks features on the
deployment"*, and the non-goals two screens below forbid *"**enforcing** seats
or lifecycle in product surfaces beyond sign-in"*. Both cannot be true, and
there is no enforcement point on the box to make the first one true. **The
deployment RECORDS the booleans and its SIGN-IN behaviour follows them; nothing
else on the box reads them in this ticket.** Feature enforcement stays MT-2's
`intersect()` seam (§2), unchanged and still a non-goal. The words "locks
features" are struck from clause 7's deployment side.

**A new fence, and it is the one that keeps the dependency honest**
(`tests/unit/test_console_dependency_boundary.py`, R7):

1. **The tenant deployable's dependency closure excludes the Console.** Read
   `apps/services/gateway/pyproject.toml` and `packages/acb_auth/pyproject.toml`
   as TOML and fail on a `customer-console` requirement.
   Fence: `test_the_gateway_does_not_depend_on_the_customer_console`.
2. **No module under `packages/` or `apps/services/gateway/` imports
   `customer_console`.** A source-level scan in the established style of
   `tests/unit/test_db_engine_seam.py` — the failure mode is a *new* import,
   which no runtime assertion sees until the container is built.
   Fence: `test_no_tenant_module_imports_customer_console`.

Both are **ratchets** in the established style of `test_db_engine_seam.py`, so
they pass on day one — that is the point, and it is not evidence they work.
Show each **red first** the way this spec's other fences were: add the offending
`customer-console` requirement (and the offending `import customer_console`),
watch it fail, take it back out.

⚠️ **Why a fence at all, when the import would obviously fail at runtime — the
root-venv trap, recorded so the fence's reason survives.** It would **not**
fail. `pyproject.toml:27-30` installs `customer-console` into the **root**
workspace venv on purpose (*"the root test suite imports `customer_console`, so
it must be installed into the shared venv by `uv sync` or CI fails with
ModuleNotFoundError"*). So `from customer_console.lifecycle import
capabilities_of` inside `acb_auth` is **green in pytest and broken in the
deployable** — the gateway image installs the gateway's own closure, which does
not contain it. That is the single most likely way this ticket gets built wrong,
and it is invisible to every test that does not look at the packaging.

**(e) The resolve client is `packages/acb_auth/acb_auth/console_resolve.py`,
and it has exactly one caller.** *(2026-08-18 — answers **B6(i)**.)* Identity
resolution is `acb_auth`'s job: the seam that already owns *"somebody is
knocking at the front door"* is that package, and putting the decision in a
gateway-service module would place an identity decision outside the identity
seam. The module owns the HTTP call, the read-through cache, the
`invalidate(email=None)` escape hatch and the projection read/write. `httpx>=0.27`
is added to `packages/acb_auth/pyproject.toml` — today that package declares
only `fastapi`, `acb-common` and `sqlalchemy` and pulls **no** HTTP client, so
this is a real dependency addition and not a transitive freebie. (The gateway
service already has `httpx>=0.27`; relying on that would make `acb_auth`
importable-but-broken anywhere else, which is the same class of trap as (d).)

⚠️ **DEVIATION from the proposed default, and the code is why.** The default
offered to this ticket read *"wired behind `resolve_access` at
`access.py:249`"*. **Refused.** `resolve_access` is not the sign-in path — it
has **six** production call sites, and one of them is
`gateway/routes/rooms.py:215`, `{e: await resolve_access(e) for e in emails}`,
which fans it over every participant of a room. The others are
`gateway/routes/chat.py:605`, `orchestrator/executor.py:1644`,
`access.py:466`, `access.py:526` (`resolve_session_access`, folding over session
subjects) and `deps.py:281`. Hanging a seat-allocating cross-service call there
would fire one Console request **and one seat allocation** per participant per
room load — precisely the farmable cap clause 11 exists to prevent, and
precisely what the module's own docstring warns about for `record_request`
(`access.py:258-271`: *"neither is somebody knocking at the front door"*). So:
`console_resolve` is called from `apps/services/gateway/gateway/routes/signin.py`
and from nowhere else; `resolve_access`, `_with_resolved_access` and `_ACCESS_SQL`
are **untouched**.

*What "the resolve cache extends `access.py`'s rather than duplicating it"
means, now that it has to be built.* Not the same dict: `_cache`
(`access.py:47`) maps email → `EffectiveAccess`, and a second value type in it
would break `_cache_get`. It means the same **idiom and the same escape hatch** —
a module-level dict keyed on `lower(email)` with a monotonic deadline and a
public `invalidate(email=None)` in the shape of `access.py:73-98` — living in
`console_resolve.py`, with the **projection row as the authoritative store** and
the dict as a read-through in front of it. These are two caches of two different
things, on the two axes D12 already separates: `access._cache` caches *what this
person may do inside a tenant*; `console_resolve`'s caches *which tenant, and
what the registry last said about it*. One cache for both would be the second
scoping doctrine root `AGENTS.md` §11 forbids.

**(f) Tenant-side config: four fields in `acb_common/settings.py`, no bare env
reads.** *(2026-08-18 — answers **B6(ii)**.)* `Settings` declares **no**
`env_prefix` (`settings.py:15-20` — `env_file`, `env_file_encoding`, `extra`,
`case_sensitive` and nothing else), so a field name maps directly to the
upper-cased environment variable:

| Field | Env var | Default |
|---|---|---|
| `customer_console_url: str = ""` | `CUSTOMER_CONSOLE_URL` | `""` — unset means *not wired*, and the box says so rather than guessing a host |
| `customer_console_deployment_key: str = ""` | `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` | `""` |
| `customer_console_resolve_ttl_seconds: int = 900` | `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS` | 15 minutes, per (c) |
| `customer_console_resolve_max_staleness_seconds: int = 86400` | `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS` | 24 hours, per (c) |

They sit beside the existing `crm_auto_lead: bool = False` shape (`:140`), so a
test pins a default by **reading the field**, not by re-deriving the number.

`CUSTOMER_CONSOLE_URL` is **deliberately the same name the BFF already uses**
(`workbench/control_plane/src/app/api/billing/summary/route.ts:34`) — one
Console, one address, two readers. The **key** is a different name from the
BFF's `CUSTOMER_CONSOLE_ORG_KEY` (`route.ts:36`) because it is a different
credential with a different scheme and a different blast radius: `cc_live_` is
org-scoped and read-only, `cc_depl_` is deployment-scoped with capability
`{resolve}`. Reusing one name for two credentials is how a deployment ends up
presenting the wrong one and getting a 401 nobody can explain.
`CUSTOMER_CONSOLE_DEPLOYMENT_KEY` is read on the **gateway**, never in Next, so
`workbench/control_plane/src/lib/gateway.test.ts:191`'s allow-list gains
**nothing** — a fourth entry there would mean the key had reached the browser
tier, and that test failing is the correct alarm.

⚠️ **A fifth variable exists and it is deliberately NOT in the table above:
`CUSTOMER_CONSOLE_RESOLVE_ENABLED`** *(added 2026-08-18 by the F1 repair;
agent-proposed default, owner may overrule — D16/D17)*. It is a **Next-side
environment variable read by the workbench only** — `auth.ts`'s `signIn`
callback and nothing else — so it is not an `acb_common` settings field and
must not become one: `acb_common.Settings` describes the **gateway's**
configuration, and a field there would be a second, silent switch for a
decision the workbench makes. Default **unset = OFF**, and the gate is an
equality against the exact string `"true"`, so
`CUSTOMER_CONSOLE_RESOLVE_ENABLED=false` — what an operator writes while
debugging a sign-in outage — is OFF rather than a truthy string.

**Why it had to exist**, stated once so nobody re-derives the one-liner it
replaces: the callback's original gate was `CUSTOMER_CONSOLE_URL` alone, while
`is_wired()` requires **two** variables. `CUSTOMER_CONSOLE_URL` is *already* a
live Next-side variable on any Console-connected deployment (the billing proxy
reads it, `app/api/billing/summary/route.ts:34`), so **"URL set, key unset" is
what this deployment looks like the moment the hop merges** — measured, that
combination issued a fetch and, with the gateway momentarily unreachable,
refused sign-in with `ConsoleUnavailable` where the pre-CP-2b code admitted.
The hop armed itself during an ordinary deploy window with nobody flipping
anything. **Reading the deployment KEY in `auth.ts` is the other obvious fix
and is forbidden** by this section and by `signin.test.ts`'s
`not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY")`: that credential is
gateway-only. Hence one variable in the callback, and it is the flag.

**(g) The BFF hop is a NEW `signIn` callback in `auth.ts`, and it carries the
refusal.** *(2026-08-18 — answers **B4** and **B6(iii)**.)*

⚠️ **DEVIATION from the proposed default, and again the code is why.** The
default named the **`jwt`** callback's `account`-present branch
(`workbench/control_plane/src/auth.ts:85-93`, verified exact). That branch does
fire once per fresh sign-in — but it **cannot refuse**. It runs *after* the
sign-in decision is already made, and there is no return value from it that
produces an error code the sign-in page can render. Clauses 6, 7 and 9 all end
in a refusal with **honest copy**; a call site that cannot refuse cannot satisfy
them, and the fallback (return `null` from `jwt` to invalidate the session)
drops the person back on the sign-in page with no explanation at all — the
wrong-looking denial D33.1 exists to remove.

*Re-verified against the installed version, as the default instructed.*
`next-auth@5.0.0-beta.31` / `@auth/core@0.41.2`. The `signIn` callback's type is
`(params: {user, account, profile, email, credentials}) => Awaitable<boolean |
string>` (`@auth/core/index.d.ts:198` — the **return type**; `:177-197` is the
parameter object, and the earlier citation of the range for the return type was
cosmetically wrong, corrected 2026-08-18), its doc comment says *"Returning a
string will redirect the user to the specified URL"* (`:161`), and the
implementation confirms it: `handleAuthorized`
(`@auth/core/lib/actions/callback/index.js:393-409`) returns `await redirect({
url: authorized, baseUrl: config.url.origin })` when the callback returned a
string, and throws `AccessDenied` when it returned falsey. **The mechanism is
available**, so it is what this ticket writes.

`auth.ts` today has **no** `signIn` callback — only `jwt` and `session`
(`auth.ts:84-103`, verified 2026-08-18). The ticket adds one, and it is the
right home on four counts, each of which the `jwt` branch gets only partially:

- It runs **only** in the sign-in flow, never on a token refresh — structurally,
  not by an `if (account)` condition somebody can widen later.
- It receives the provider-verified `profile.email`, the same value the existing
  `jwt` callback already trusts (`auth.ts:86-88`, `if (profile?.email)`). Never
  request input — `user_management_contract.md` R11.
- It is the **only** callback whose return value can both admit and refuse, so
  the resolve call and the decision it drives are one function rather than two
  that have to agree.
- It fires **before** a session exists, which settles how it reaches the
  gateway: `gatewayHeaders()` (`lib/gateway.ts:160`) calls `auth()` and would
  find nothing. The correct existing seam is **`headersActingAs(email)`**
  (`lib/gateway.ts:137-152`) — *"Internal bearer + identity headers for an
  ALREADY-VERIFIED member … for library code that was handed an email by a route
  which established it"*, which is exactly this case. No new bearer is minted,
  so `gateway.test.ts`'s two fences (the no-second-`GATEWAY_INTERNAL_TOKEN`-reader
  test and the `:191` allow-list) stay green untouched.

⚠️ **Hop 2 closes an import CYCLE, it is survivable, and the workaround an
implementer will reach for is FORBIDDEN by name.** *(2026-08-18 — answers
**B-g**.)* `lib/gateway.ts:54` already imports `{ auth, isAuthEnabled }` from
`@/auth`, so `auth.ts` importing `headersActingAs` from `lib/gateway.ts` makes
`auth.ts ⇄ lib/gateway.ts` a **cycle**. It is survivable as-is and this ticket
takes it deliberately: ESM hoists both module records, neither module uses the
other's binding at **top level** (`gateway.ts`'s `auth()` calls are inside
`currentIdentity()`; `auth.ts`'s new call is inside the `signIn` callback), so
whichever module the bundler evaluates first finds a live, initialised binding by
the time either function runs. `src/proxy.ts:31` pulls `auth.ts` into the proxy
bundle, which is the one place the cycle is most visible — and it is already
there today, because `proxy.ts` imports `@/auth` and `@/auth` is what
`lib/gateway.ts` imports.
**FORBIDDEN: inlining `GATEWAY_INTERNAL_TOKEN` (or an `Authorization: Bearer`
built from any secret) inside `auth.ts` to back away from the cycle.** That is
the tempting fix, it works, and **`gateway.test.ts` cannot see it** — its sweep
is built from `API_DIR = ../app/api` (`gateway.test.ts:137-152`), so both the
"mints no gateway bearer of its own" fence (`:161-167`) and the `:191`
allow-list scan `src/app/api/**` and nothing else. `auth.ts` is outside both.
So the fence is **extended in this ticket**: `gateway.test.ts`'s two route-surface
checks gain `src/auth.ts` (and `src/proxy.ts`) to the file set they sweep, under
the new name `"no module outside lib/gateway.ts mints a gateway bearer"` — the
second bearer-reader is the thing being fenced, not the directory it lives in.
If the cycle ever does bite, the sanctioned answer is to lift `headersActingAs`
and `INTERNAL_TOKEN` into a leaf module that imports `@/auth` from nowhere and
have both sides import *that* — one seam, moved; never a second copy of the
bearer.

**The two new refusal codes**, carried as `/signin?error=<code>` in the string
the callback returns, with copy added to
`workbench/control_plane/src/app/signin/errorCopy.ts` — the one refusal-copy
seam, keyed on the `?error=` value and already carrying a `default` arm, so an
unmapped code degrades to `Authentication error: <code>` rather than crashing:

| Code | When | Copy (D33.1: never blame the person) |
|---|---|---|
| `ConsoleUnavailable` | clause 6 — Console unreachable and nothing cached, or cached past `MAX_STALENESS` | *"Sign-in is temporarily unavailable. This is a problem on our side, not with your account — please try again in a few minutes."* |
| `WorkspaceChooserRequired` | clause 9 — the resolve returned more than one visible organization | *"Your account belongs to more than one organization on this deployment, and there is no way to choose between them yet. Please contact your operator."* |

*(Minor deviation, stated rather than smuggled: the proposed default asked the
multi-org copy to **name the organizations count**. `errorCopy.ts` maps a code
to a **static** string and the count would have to ride the query string to
reach it — a second field on a public URL, for a number that changes nothing the
person can act on. The copy names the **cause** instead, which is what D33.1
actually asks for. If the owner wants the count, it is one more query parameter
and a signature change to `signInErrorMessage`.)*

⚠️ **Auth.js's own vocabulary is fixed, and that is why these are custom codes
rather than reused types.** v5 emits error **types** — `OAuthCallbackError`,
`Configuration`, `AccessDenied` — and returning `false` from `signIn` produces
`AccessDenied` and nothing else. `AccessDenied`'s shipped copy is *"Your account
isn't authorized for this workspace. Ask your admin for an invite."*
(`errorCopy.ts`), which is **the exact phrasing D33.1 forbids for both of these
cases**: a person refused because a service is down has not been denied access,
and a person in two organizations has not failed an authorization check. Two
distinguishable reasons therefore need two codes Auth.js does not define, and
the string-return path is the mechanism that carries them. `deleted` (clause 6's
dead state) keeps `AccessDenied` — there the copy is true.

⚠️ **The Next side ships dark on its OWN flag, `CUSTOMER_CONSOLE_RESOLVE_ENABLED`**
*(2026-08-18 — repair of finding **F1**, raised by independent verification;
agent-proposed default, owner may overrule per D16/D17)*. The callback gates on
**that variable alone**: unset, or anything but the exact string `"true"`, and
it returns `true` as its first statement — no fetch, no latency, today's
behaviour byte for byte, **whatever `CUSTOMER_CONSOLE_URL` holds**. The full
argument for a dedicated flag (rather than gating on the URL, or on the
gateway-only deployment key) is in §6(f) under the settings table.

**Flag ON is a claim, and the box is held to it.** With the flag on, every
failure fails **closed** with the D33.1-compliant `ConsoleUnavailable` copy —
that is the intended behaviour of a deliberately-wired deployment, clause 6's
contract unchanged, and it **includes the deploy-window case** where the
gateway is briefly unreachable. It also includes the *half-provisioned* case:
flag on in Next while the gateway's own `CUSTOMER_CONSOLE_URL` /
`CUSTOMER_CONSOLE_DEPLOYMENT_KEY` are missing, where `/signin/resolve` cannot
answer. **That is a provisioning error and it refuses like any other transport
failure** — a box that claims to be wired while it is half-provisioned must not
silently admit, because "admit when unsure" is precisely the fail-open posture
CP-0 removed. The consequence is stated plainly so nobody is surprised by it:
turning this flag on is an act with an availability cost, which is why it is
🔴 OWNER-GATE (§8 gate 7).

⚠️ **Where that refusal actually lives, and why it is not where you would look**
*(2026-08-18 — repair of findings **F4** and **F5**; F5 was a P0)*. The
paragraph above described the intent and the code did the opposite: with the
gateway *reachable* and its env empty, `resolve_for_signin` returned
`admit=True, source="unwired"` — its ship-dark contract, correct for a module
nobody configured — and `POST /signin/resolve` passed it through, so the BFF
admitted. Every sign-in succeeded with **no seat allocated, no Console
consulted and no log line**, because the route logged only refusals. Two true
statements one hop apart composed into a false one, and the topology makes it
the *likely* misconfiguration rather than an exotic one: the Next flag and the
gateway's env are in different containers with different env files, so flipping
one does not imply the other.

The rule, and it is a rule about the ROUTE:

> **Reaching `POST /signin/resolve` at all means somebody declared this box
> wired.** So the route checks `is_wired()` **itself**, before calling the
> module, and answers `{"admit": false, "code": "ConsoleUnavailable", "source":
> "unwired"}` with a `signin.resolve_unwired` line at **error** level.

Ship-dark is untouched: with `CUSTOMER_CONSOLE_RESOLVE_ENABLED` off the BFF
never calls the route, so a deployment that has not opted in is unaffected. The
module's fail-open branch stays as it is — it is the module's contract, not the
product's guarantee, and reading it as the latter is precisely what F5 was.
Fence: **`tests/unit/test_signin_resolve_route.py`** (new; before it, nothing
in the tree tested this route at all, which is how a P0 lived on a shipped
surface for a day).

Fences (extend `workbench/control_plane/src/app/signin/signin.test.ts`, which
already reads a sibling as source and already tests `signInErrorMessage` — a new
file would be a second home for one subject):
`"resolve fires only from the signIn callback"` (source-level over `auth.ts`:
the resolve call appears inside the `signIn` callback and in neither `jwt` nor
`session`, and appears in no route file), `"the resolve email comes from the
provider profile, never from request input"`, `"errorCopy speaks the two
CP-2b codes"` asserting neither new string matches
`/access denied|isn't authorized/i`, `"is inert until
CUSTOMER_CONSOLE_RESOLVE_ENABLED is exactly \"true\""` (the flag is read FIRST,
before the call, and by equality not truthiness) and — the F1 repair's own
fence — `"does not arm itself off CUSTOMER_CONSOLE_URL (F1)"`, a scan of the
callback's body for the arming-by-accident channel. Added 2026-08-18 with the
review notes: `"encodes the code it puts in the redirect URL"` — `answer.code`
arrives from another service and lands in a URL, so **every** interpolation into
`/signin?error=` must go through `encodeURIComponent`, swept rather than
asserted once.

⚠️ **All of these are source-regex fences, and that is forced rather than
chosen** *(measured 2026-08-18)*: vitest in `workbench/control_plane` is
node-env, and `import("@/auth")` fails inside `next-auth/lib/env.js` — *"Cannot
find module …/node_modules/next/server"* — before any callback can be invoked.
So **no test in this tree executes the callback**; what is pinned is the gate's
shape. F1 is what that blind spot cost once (a shape that read plausibly and
armed itself), so the behavioural half is a **review** check and is recorded
here as one rather than left to look covered.

**(h) This entry point reads the projection ITSELF; `app_user` stays
authoritative everywhere else.** *(2026-08-18 — answers **B2** and status-drift
item 2.)* The audit is right that migration 159's tables are a shadow copy
nothing reads, and that the sign-in decision path reads `app_user`
(`access.py:191`, `_ACCESS_SQL` ends `FROM app_user u`; `access.py:369-372`,
`resolve_identity` queries `app_user`). 159 says so in its own words at
`159:107-109`: *"app_user remains authoritative until MT-1a-2 cuts the auth path
over; **this is a shadow copy, not a replacement**."*

The scope that makes this buildable without colliding with WS-29:

- **CP-2b writes the projection** (`user_identity`, `org_membership.resolved_at`,
  `organization.registry_status`) on every fresh answer from the Console, and
  **reads it only on its own fallback path** — Console unreachable, or inside
  the TTL window — to answer *one* question: *may this sign-in proceed, and with
  what capabilities*.
- **CP-2b does not touch `_ACCESS_SQL`, `resolve_identity`, or any general
  identity read.** After this ticket, `app_user` is still what
  `resolve_identity` returns and still what every authenticated request
  resolves against.
- **The general cutover is WS-29 MT-1 H6 and stays there, by name.** Wiring the
  projection into the identity path at large is H6's ticket, it is open, and
  this ticket must not pre-empt it. One owner per topic (`work_plan.md` §4).

⚠️ **Status-drift item 2, fixed.** The heading below named 159's projection *"the
CACHE OF RECORD"* full stop, while 159 itself declares those tables a shadow
copy — the spec and the migration disagreed about which table decides sign-in.
They do not disagree any more, because the claim is now scoped: **"cache of
record" means *the store against which THIS entry point's freshness bounds and
fallback decision are evaluated*, and nothing wider.** It is not a claim about
identity, it is not a claim about `app_user`, and it does not move the authority
159 reserves for H6.

**(i) Clause 10 splits per half, and the tenant half has a named mechanism.**
*(2026-08-18 — answers **B3** and status-drift item 1.)* Clause 10 was marked
met, and it was met **for the Console ladder only**: its mechanism is
`tests/unit/_customer_console_ladder.py`, which reads `infra/customer_console/`
and can never build a tenant schema. `pr-check.yml` provisions **one** Postgres
(`platform-postgres`, `:113`) exporting **one** DSN
(`CUSTOMER_CONSOLE_DATABASE_URL`, `:127`); `DATABASE_URL` is set **nowhere** in
that job, so a tenant-side R8 suite written today would skip in CI and report
green — the CP-3 disarmed-gate failure class, which is the one thing this spec
has already been burned by once. Four things, all in the build slice:

⚠️ **First, the correction that outranks all four: tenant-ladder machinery
ALREADY EXISTS in CI, and this ticket adds a second mechanism on purpose.**
*(2026-08-18 — answers **B-c**. The sentence below clause 10 read "it needs
machinery that does not exist", and that was **false**.)* `pr-check.yml:235-291`
is a whole job called **`migrations`** that replays the entire tenant ladder:
`pgvector/pgvector:pg16` (`:240`), `createdb ladder` + `psql -f
infra/postgres/01_schema.sql` (`:269-272`), then `scripts/apply_migrations.sh`
three times (`:274-291`) to prove the ledger makes repeats no-ops. It is the
same ladder, applied in the same order, and it is *not* the thing this ticket
needs. The divergence, argued rather than assumed, per root `CLAUDE.md` §5 (*do
not invent a second way to do an existing thing*):

- **A pytest fixture cannot shell out to `psql` mid-suite.** The `migrations`
  job's replayer is a bash script driving the `psql` client through
  `docker exec`/local modes, with a ledger table, a pre-migration backup hook and
  its own environment contract (`PG_MODE`, `PG_USER`, `PG_DB`, `APP_DIR`,
  `MIGRATIONS_DIR`). A test suite needs a **programmatic** schema on a
  SQLAlchemy connection it already holds, inside the transaction it controls.
- **The two answer different questions.** `migrations` proves the ladder is
  **idempotent against a real server** — replay it, replay it again, assert 0
  applied. `_tenant_ladder.py` proves a suite's assertions run against **the
  schema the ladder produces** rather than against a fake. Neither substitutes:
  a green `migrations` job says nothing about whether
  `test_deployment_resolve_cache.py` skipped.
- **One ladder, two readers, and the ladder is discovered by both.** Neither
  transcribes a file list. That is the property that matters, and it is why this
  is a second *mechanism* and not a second *source of truth* — the failure
  `_customer_console_ladder.py`'s docstring records (five hand-copied lists,
  three stale) is structurally impossible for either reader.
- **The order is mirrored, not re-invented.** `_tenant_ladder.py` applies
  `01_schema.sql` FIRST and then the numbered ladder sorted on the leading
  integer, which is exactly what `apply_migrations.sh` does across its
  init-only skip (`:222-226`, `case 00_*|01_*) continue`) and its numeric sort
  (`:206-211`, `sort -V`, whose comment records the "100 before 99" bug that
  makes lexical sorting wrong). Same R1 refusals as the Console ladder — empty
  ladder and duplicate number (`_customer_console_ladder.py:55-60`, `:62-71`).
  If the two ever disagree about order, the bash script is the fact and the
  Python is the defect.

1. **`tests/unit/_tenant_ladder.py`** — the tenant analogue of
   `_customer_console_ladder.py`, discovered and never transcribed: list
   `infra/postgres/`, match `^(\d+)_.*\.sql$`, sort on the **leading integer**
   (not the string — `010` sorts before `002` lexically), and carry the same two
   R1 refusals, `RuntimeError` on an empty ladder and `RuntimeError` on a
   duplicate number (`_customer_console_ladder.py:55-60`, `:62-71` — anchors
   re-derived 2026-08-18, was `:56`/`:63-71`).
   ⚠️ **Two differences from the Console ladder, both load-bearing.** The tenant
   ladder is **176 numbered files** (counted 2026-08-18; highest is
   `176_people_skills.sql`, and R1 says re-derive it rather than trust this
   sentence), and `00_create_databases.sql` /
   `01_schema.sql` are **init-only and not re-runnable** — `apply_migrations.sh`
   skips them for exactly that reason (its comment at `:195-201`, the skip itself
   at `:222-226`) because initdb
   lays them down on first boot. So `apply_ladder(conn)` applies `01_schema.sql`
   once against the empty database, then `02+` in numeric order.
   **`00_create_databases.sql` is applied NEVER** — it is a
   `docker-entrypoint-initdb.d` script for creating sibling databases (its own
   header, `:1-3`) and has nothing to say to a connection already inside one.
   And
   `schema.generated.sql` must **never** be replayed (a raw `pg_dump` snapshot,
   non-idempotent) — the numbered-prefix regex already excludes it, and the
   `generated/` subdirectory is not read.
   *Rejected alternative, named so it is not re-proposed:* seeding CI from
   `schema.generated.sql` is faster and proves the **snapshot**, not the
   migrations — and the column this ticket adds arrives in a file newer than any
   snapshot, so the suite would test a schema without it.
2. **A second Postgres service in `pr-check.yml`'s `test` job, on
   `pgvector/pgvector:pg16`, exporting `TENANT_LADDER_DATABASE_URL` — and
   `DATABASE_URL` is set NOWHERE in that job.** *(Both halves of this item were
   rewritten 2026-08-18: the image answers **B-b**, the variable name answers
   **B-a**, and the second is the load-bearing one.)*

   **The image.** `postgres:16` — what the existing `platform-postgres` service
   uses (`:114`) — **cannot build this schema**: `infra/postgres/01_schema.sql:5-6`
   is `CREATE EXTENSION "uuid-ossp"` **and** `CREATE EXTENSION vector`, and stock
   Postgres ships neither pgvector nor a vector type. The repo already answers
   this: the `migrations` job runs `pgvector/pgvector:pg16` (`:240`) for exactly
   this file. Name the same image; do not discover the failure at first red.

   ⚠️ **The variable, and why it must NOT be `DATABASE_URL`.** The `test` job
   runs `uv run python -m pytest tests/unit/ -x -v` (`:157`) — the **whole
   directory**. Setting `DATABASE_URL` in that job's `env:` block would arm
   `tests/unit/test_tenant_coverage.py`'s `_needs_db` gate (`:200-205`, reading
   the launch snapshot `_ACB_DATABASE_URL_AT_LAUNCH` seeded at
   `tests/conftest.py:16`) and turn ON **two tests that fail by construction in
   this job**, neither of which is CP-2b's to make pass:
   - **`test_app_role_cannot_bypass_rls`** (`:241-258`) asserts
     `NOT rolsuper` for `current_user`. A GitHub Actions Postgres service
     container's `POSTGRES_USER` **is** the superuser — that is what initdb
     creates — so the assertion is red for the runner's own DSN, whatever the
     schema. Making it green needs a second, non-superuser application role and
     a grant plan, which is **MT-1c**'s work, not a sign-in ticket's.
   - **`test_live_catalog_has_column_force_and_policy`** (`:208-238`) walks
     `pg_class` and demands `organization_id` + `relrowsecurity` +
     `relforcerowsecurity` + a policy on every non-`EXEMPT` table. Those come
     from `infra/postgres/generated/04_policies.sql`, which covers **140 tables**
     (`04_policies.sql:9`) and which `apply_migrations.sh` deliberately does not
     replay — its own header says so (`:19-22`: *"NOT a numbered migration …
     Promoting it is a deliberate act taken against a database in a maintenance
     window"*), and `test_tenant_coverage.py:169-182` fences that separation. So
     the replayed ladder produces a schema with **no** FORCE-RLS policies and the
     test lists every one of those tables. Promoting `04_policies.sql` is
     **MT-1b/MT-1c**'s owner-gated cutover — the file's own comment calls it *a
     cliff* — and a sign-in ticket that arms it would either turn CI red or
     pressure somebody into promoting it to go green.

   Both tests skip in CI **today** and must keep skipping **exactly as today**.
   Hence a separate name, `TENANT_LADDER_DATABASE_URL`, which nothing else in
   the tree reads. The job's `env:` block gains it beside
   `CUSTOMER_CONSOLE_DATABASE_URL` (`:126-127`), and the reachability assertion
   (`:146-154`) gains its twin — that step exists because *"the job would go
   back to silently skipping the moment the service container failed to come
   up"*, and one DSN proven reachable says nothing about the other.

   **The gate idiom is extended, not forked.** `tests/conftest.py` gains one
   line beside `:16` snapshotting `TENANT_LADDER_DATABASE_URL` at launch
   (`_ACB_TENANT_LADDER_URL_AT_LAUNCH`), for the identical reason the first
   snapshot exists — `import litellm` calls `load_dotenv()` mid-collection and a
   dev machine's `.env` must not be able to point an R8 gate at a local
   database. One idiom, two variables; **never** a raw `os.environ` read at
   module scope.

   ⚠️ **The suite still has to reach the seam, and that is the subtle part.**
   `console_resolve.py`'s projection write goes through `acb_common.db`, whose
   `async_database_url()` reads `os.environ["DATABASE_URL"]` first
   (`db.py:50-58`). So the suite's session-scoped fixture sets `DATABASE_URL`
   **in-process** from `TENANT_LADDER_DATABASE_URL` via `monkeypatch.setenv`,
   and resets `acb_common.db._ENGINE` / `_SESSION_FACTORY` on teardown so no
   later test in the same process inherits a live engine. **This does not re-arm
   `test_tenant_coverage.py`, for two independent reasons** — its gate reads the
   launch snapshot `_ACB_DATABASE_URL_AT_LAUNCH`, which `conftest.py:16` set to
   `""` before any test module imported, and `_needs_db` is a module-scope
   `skipif` evaluated at collection. That snapshot exists to stop exactly this
   class of mid-run mutation from reaching a gate, which is what makes the two
   disciplines separable at all. Fence:
   `test_the_ladder_dsn_does_not_leak_out_of_this_suite` — after the fixture
   tears down, `acb_common.db._ENGINE is None` and `os.environ.get("DATABASE_URL")`
   is what it was before.
3. **A skip-guard entry.** The R8 assertion step (`pr-check.yml:167-178`) is
   hand-maintained and says so in its own comment (*"⚠️ This list is
   HAND-MAINTAINED and nothing discovers it … Add the file in the same commit
   that creates it"*). `tests/unit/test_deployment_resolve_cache.py` is added
   there, and its skip reason must be greppable the same way the Console suites'
   is.
4. **The suites are NAMED.** `tests/unit/test_deployment_resolve_cache.py` is
   the R8, tenant-Postgres-gated suite (clauses 6, 7, 8's `resolved_at` half, 9's
   deployment half, 12's `capabilities` half), gated on
   `TENANT_LADDER_DATABASE_URL` through the launch snapshot of item 2 — **not**
   on `DATABASE_URL`, which stays unset in that job by design. The structural
   fences of (d) and (g) live in `tests/unit/test_console_dependency_boundary.py`
   and `signin.test.ts` **and take no database**, deliberately: folded into the
   R8 suite they would skip whenever the DSN is unset, which is the failure this
   whole clause is about.

§7's command block is updated in the same change with the suite and the second
DSN — and the two exports are shown **separately**, because collapsing them into
one variable is precisely the mistake item 2 exists to prevent.

*(**B7** — four Console-side anchors this ticket's own Console half moved under
it were refreshed in the clauses below on 2026-08-18, each re-derived against
the tree rather than copied from the audit: `main.py:542-547` → **the OPERATOR
arm's** dead-org gate is now `main.py:598-603` (`:542` is the `/orgs/lifecycle`
response's `capabilities_of` call) — ⚠️ **and that is the whole of what B7
measured; the DEPLOYMENT arm's gate is `main.py:702-711`, which is the one every
CP-2b clause means. The sentence that stood here read "the dead-org gate is now
`main.py:598-603`" full stop, and the block below had to correct it in five
places. Struck and restated at source, 2026-08-18**; `main.py:594-600` → the
operator response shape is now
`main.py:645-651`; `seats.py:135-146` → the buy-more payload is now
`seats.py:147-158`; `seats.py:129-130` → the `already_assigned` no-op is now
`seats.py:141-142` (`:129-130` is docstring prose about it).
`lifecycle.py:64-78` and `:72-75` were re-verified and are unchanged.)*

⚠️ **That B7 refresh fixed the number and lost the ARM, and clauses 5 and 12
inherited the mistake.** *(Corrected 2026-08-18, answering the re-audit's
mis-anchor finding; re-derived line by line against the tree, old recorded
beside new.)* `POST /registry/resolve` has **two** arms and **two** dead-org
gates, and they are 100 lines apart:

| Arm | Its lifecycle gate | What it is |
|---|---|---|
| **Operator** (`_resolve_for_operator`, `main.py:580-651`) | `main.py:594-603` — read `organization.status`, `capabilities_of(state)`, `raise 403` when `not caps.can_sign_in` | the arm B7 measured |
| **Deployment** (`_resolve_for_deployment`, `main.py:662-769`) | **`main.py:702-711`** — `if not admissible: if refused: raise HTTPException(403, f"organization is {refused[0]['status']}")` | **the arm CP-2b's clauses are about** |

So: clause 5's ⚠️ and clause 12's *"Dead org placed here"* row both cited
`main.py:598-603` — **the operator arm's gate** — for a refusal only the
deployment arm can produce. Both are corrected below to `main.py:702-711`
(**was `:598-603`, and before that `:542-547`**). Clause 12's operator-shape row
also claimed `:594-600` "is now the deployment arm's lifecycle capability gate";
it is not — `:594-600` is the **operator** arm's own status read and
`capabilities_of` call, three lines above its own raise. Corrected there too.
The two gates return the **same** 403 body shape, which is why a wrong citation
survived a review: the shapes agree and the code paths do not.

---

**(j) The box branches on the RESOLVE OUTCOME, never on a lifecycle string —
and `capabilities.sign_in` is ALWAYS `true` in a 200 body.** *(2026-08-18 —
answers **B-d**. Agent-proposed default, owner may overrule; D16/D17.)*

The shipped Console makes `capabilities.sign_in: false` **unreachable in a 200
body**, and clause 6's dead-state rule plus clause 12's parametrised fence were
written against an input that cannot occur. Re-derived: `_resolve_for_deployment`
partitions `visible` into `admissible` / `refused` on `capabilities_of(status).
can_sign_in` (`main.py:695-700`); when `admissible` is empty and anything was
refused it raises **403** (`:702-711`); and the 200 body is built **from
`admissible` alone** (`:755-769`). A `deleted` organization therefore never
appears in a 200 — it is either the 403, or it is not in the list. Adding the
`capabilities` block of (d) does not change that: in a 200 body `sign_in` is
`true` for every entry, by construction.

*The answer, and it is the one that needs no lifecycle string anywhere on the
box:* **the deployment branches on the outcome of the HTTP call**, in four
cases, and the booleans it stores gate seat/feature behaviour rather than
admission:

| # | Resolve outcome | Sign-in | What is cached |
|---|---|---|---|
| i | **200**, exactly **one** organization | **admit** | `{organization_id, slug, capabilities{…}, registry_status, resolved_at}` — the whole record |
| ii | **403** (`main.py:702-711`) | **refuse** — **`AccessDenied`** *(code named 2026-08-18: this row left it blank, and §6(g) had already supplied `AccessDenied` for `deleted`. Carried in so the table is complete — the copy is TRUE here, exactly as in row iv)* | `{capabilities: {"sign_in": false}}` + `resolved_at`, on the org row this box previously resolved this person into. **No `registry_status`** — see below |
| iii | **200**, **more than one** organization | **refuse** — `WorkspaceChooserRequired` | **nothing written** |
| iv | **200**, **zero** organizations | **refuse** — `AccessDenied` | **nothing written**, and `invalidate(email)` fires |
| **v** | **409 at the seat cap, or any other status in which the Console ANSWERED and the answer was not an admission** (400, 404, 422 — a request this box built wrong) | **refuse** — `AccessDenied` | **nothing written** *(added 2026-08-18 during the build: §6(j)'s four rows did not cover the 409 clause 12 documents. At the cap the person genuinely holds no seat and "ask your admin" is the remedy, so the shipped copy is true; a THIRD refusal code was deliberately not minted. Agent-proposed default, owner may overrule. Fence: `test_a_seat_cap_refusal_fails_closed_and_caches_nothing`. ⚠️ **Wording narrowed 2026-08-18** — it used to read "any other status this box cannot read", which is row vi's job)* |
| **vi** | **5xx · 401 · 408 · 429** — the box got **no answer**: the Console or something in front of it is broken, or *this box's own credential* is wrong | **the UNREACHABLE path** — degrade on the cache up to `MAX_STALENESS`, else refuse with `ConsoleUnavailable` | **nothing written** *(added 2026-08-18, repair of review finding **P1-1**. Row v used to sweep these in and answer `AccessDenied`: an nginx 502 told even cache-fresh users "your account isn't authorized" — their cache was never consulted — and a rotated `cc_depl_` key told **every user of every tenant** the same, which is the wrong-looking denial D33.1 forbids by name. The identical outage over a **closed port** already degraded gracefully, so one event had two spellings and two opposite behaviours. The line is now drawn on **whether an answer was produced**, never on whether a status was recognised. Fences: `test_a_console_5xx_degrades_to_the_cache_like_any_other_outage`, `test_an_unreadable_status_with_nothing_cached_says_unavailable` (parametrised over 500/502/503/504/408/429), `test_a_rotated_deployment_key_is_an_outage_not_a_denial`, and `test_a_403_and_a_409_still_mean_what_they_meant` as the control)* |
| **vii** | **row iv's outcome ALONE (`console-empty`, `signup_eligible=True`), seen by the `signIn` CALLBACK while `SELF_SERVE_SIGNUP_ENABLED === "true"` (Next-side env)** | **admit an ORG-LESS session** — the callback keys on the gateway's `signup_eligible` signal (NOT on the `AccessDenied` code) and returns `true`; the user lands where `/signup` is reachable and every tenant-bound surface stays behind the gateway's per-request resolution + fail-closed tenant binding | **nothing written** — row iv's cache-nothing rule intact, `invalidate(email)` still fires; rows i–vi are behaviourally unchanged under both flag positions save for one ADDITIVE field *(NARROWED to zero-org-only 2026-08-20 by CP-2c's slice-3 repair, review P1. The first slice keyed the funnel on `code === "AccessDenied"` alone — which rows ii/v and the `cache-dead` short-circuit ALL carry — so a SUSPENDED/non-paying or seat-capped org, which the registry suspension keeps out at THIS door alone, would have regained access via an org-less session. The fix adds a purely-additive, load-bearing gateway signal `ResolveDecision.signup_eligible`, True on the zero-org return ALONE and set directly there (never inferred from `source`, which stays log-only), surfaced by `gateway/routes/signin.py`. `console_resolve.py` reads NO signup flag; the admit decision stays the CALLBACK's. Fences: the callback branch pinned in `signin.test.ts` (positive `signup_eligible === true` trigger + a NEGATIVE pin that `answer?.code === "AccessDenied"` is absent from the guard); and — because the security half must RUN — `test_deployment_resolve_cache.py::TestSignupEligibleIsZeroOrgOnly` asserts against the LIVE DB that `console-empty` is the ONLY `signup_eligible=True` outcome while `console-refused`, `cache-dead` and `console-error` are all False)* |

Case by case, because each carries a decision:

- **(i) Admit.** `sign_in` is true and the box knows it without reading it; what
  it stores the booleans *for* is clause 7's record and, later, MT-2's
  `intersect()` seam. `registry_status` is stored as **refusal/UI copy only** —
  it is the word a person is shown, never an input to a branch ((d)).
- **(ii) Refuse, and this is the dead-state rule's concrete carrier.** The 403
  proves exactly one thing — *no organization this box can see admits this
  person* — so exactly that is recorded: `sign_in: false`. **`write_seats` and
  `use_ai` are NOT written**, because the 403 body does not carry them and a
  value the Console never sent is minted information. A missing key means *not
  observed*, never *false*. **A cached `sign_in: false` refuses immediately at
  any freshness *up to the staleness ceiling*** — inside the TTL, outside it,
  Console reachable or not — which is what §6(c)'s dead-state rule asked for and
  now has a row to live in.
  ⚠️ **Bounded by `MAX_STALENESS`, 2026-08-18 — repair of review finding
  **P1-2**, and the unbounded version was an unrecoverable lockout of every
  member of the organization.** Read the two facts together: the 403 names no
  organization, so the write targets the **org row** this box last resolved the
  person into; and the fallback read is org-scoped, so it serves that
  `{"sign_in": false}` to **every member with a non-NULL `resolved_at`**. A
  person fact written on an org row, applied ahead of every freshness bound at
  any age forever, and clearable only by a successful 200 that the
  short-circuit itself prevented from being requested — recovery was a manual
  `UPDATE` on the tenant database. (`lifecycle.capabilities_of` returning
  `STATES["deleted"]` for any **unrecognised** status string is the amplifier: a
  typo in the Console's column 403s a paying customer.) Past the ceiling the
  record is re-consulted like any other, which relaxes **nothing** — an
  unreachable Console refuses on the uncached path anyway, and a genuinely dead
  organization 403s again and re-arms the record. What it buys is that a wrong
  one heals within 24h instead of never. Fences:
  `test_a_dead_record_past_the_ceiling_is_re_consulted_and_heals`,
  `…_still_refuses_with_no_console`, `…_re_arms_on_a_second_403`, and
  `test_one_persons_403_does_not_lock_their_COLLEAGUES_out_forever`.
  📌 **Named follow-up, deliberately NOT done in the repair round: record the
  refusal where the fact lives.** `{"sign_in": false}` is a statement about a
  *person*, and its home is `org_membership` (the person↔org row), not
  `organization`. Writing it there removes the collateral blast radius
  altogether rather than time-bounding it. That is a schema change with an
  expand/contract migration and a read-path rewrite (R6), so it is a ticket of
  its own; the ceiling is the correct interim and is what ships today.
  ⚠️ **`registry_status` is NOT written on a 403 either, and the reason is the
  same discipline one level down.** The body is
  `{"detail": "organization is <state>"}` — a **human sentence**, not a field.
  Parsing a word out of it to populate a column documented as
  `trial|active|past_due|suspended|cancelled|deleted` couples this box to the
  Console's message wording, and the day somebody improves that sentence the box
  writes garbage into a typed column. The refusal copy the person sees comes from
  `errorCopy.ts`, not from the Console's `detail`, so nothing needs it.
  ⚠️ The 403 also **names no organization**, so the write targets the org row this
  box previously resolved this person into (§6(k)'s slug key, joined through the
  projection). When there is **no** prior row there is nothing to write and
  nothing is lost: a person this box never admitted has no cached admission to
  fall back on either, so the next unreachable-Console sign-in refuses anyway.
  Fence: `test_a_403_records_only_the_fact_it_proved` — asserts `sign_in` is
  `false` and that `write_seats`, `use_ai` and `registry_status` are all
  **untouched**, not set to a default.
- **(iii) Refuse, cache nothing — and do NOT invalidate.** The answer still
  lists organizations this deployment serves, so §6(c)'s invalidate trigger does
  not fire. Stated honestly rather than glossed: a person who has *since* become
  multi-org keeps an older single-org admitted row, so if the Console later goes
  unreachable they are admitted into the one organization this box last resolved
  them into, bounded by `MAX_STALENESS`. That is deliberate — it admits them
  somewhere they demonstrably belong, never somewhere they do not, and the
  alternative (poisoning the cache on a refusal that is about *ambiguity*, not
  about entitlement) locks a paying user out on a Console outage. Fence:
  `test_a_multi_org_refusal_does_not_poison_the_cache`.
- **(iv) Refuse with `AccessDenied`, and the shipped copy is CORRECT here.**
  This is the genuine not-authorized case: no membership visible to this
  deployment at all. D33.1 forbids *"Your account isn't authorized for this
  workspace"* for the **unreachable-Console** and **chooser** cases — where the
  person did nothing wrong and the sentence is a lie — and it is simply true
  here. §6(g) already says so for `deleted`; it is the same reasoning. This
  outcome is also §6(c)'s named `invalidate()` trigger *(a)*: an answer that no
  longer lists an organization this deployment previously served.

**Consequences for the clauses, all applied below.** Clause 6's fence list is
rewritten against outcomes. Clause 12's `deleted` fence asserts **the 403**, not
a 200 body carrying `sign_in: false`. And
`test_the_deployment_never_branches_on_a_lifecycle_string` remains satisfiable
and remains worth having, because the branch inputs are now (a) the HTTP status,
(b) `len(organizations)`, and (c) three booleans — no tenant-side module compares
against `"suspended"` / `"cancelled"` / `"deleted"`, and the only place
`registry_status` is read is the string handed to refusal copy.

**(k) The projection write's join key is the org SLUG, and creating the local
`organization` row is PROVISIONING's act, not this ticket's.** *(2026-08-18 —
answers **B-e**. Agent-proposed default, owner may overrule; D16/D17.)*

The write had no key. Re-derived: tenant `org_membership.organization_id`
REFERENCES the tenant `organization(id)` (`159:83-96`, the FK at `:84`), whose
rows are **local** UUIDs (`130:37-38`, `id UUID PRIMARY KEY DEFAULT
gen_random_uuid()`) seeded with exactly one row, `slug='default'`
(`130:49-51`). The Customer Console's `organization_id` is a **different UUID in
a different database on a different plane** (§0.9.2). Writing one into the other
would either violate the FK or, worse, insert a second `organization` row and
split the tenant in half.

- **The key is `slug`.** The resolve answer carries it — `store.
  deployment_visible_orgs` returns `"slug": r[1]` (`store.py:537-548`) and
  `_resolve_for_deployment` puts it on the wire (`main.py:760`) — and the
  tenant's `organization.slug` is **`TEXT UNIQUE NOT NULL`** (`130:39`),
  **verified before writing this line**, so the lookup is
  `SELECT id FROM organization WHERE slug = :slug` resolving through the implicit
  unique index Postgres creates for that constraint. One row or none; never two.
- **The Console `organization_id` is NOT persisted, and that is deliberate.** It
  rides the in-process read-through record (§6(j) row i) because the client has
  it in hand, and it is **never** written to the projection: `organization`
  already carries two identifiers (`id`, `slug`) and a third — from another
  database, joinable by nothing local — is a foot-gun the next reader will
  mistake for a foreign key. If an operator-facing correlation column is wanted
  later it is a named later ticket, not a quiet addition here. Fence:
  `test_the_console_uuid_is_never_written_to_the_projection`.
- **When no local `organization` row matches the slug: SKIP the cache write,
  FORGET any organization this box previously resolved them into, log ONE
  structured warning, and sign in on the fresh Console answer unchanged.**
  Not an error, not a refusal, not an insert. The box then has **no fallback
  cache for that person until the row exists** — which degrades to §6(c)'s
  uncached case, i.e. fail-closed on the next Console outage, which is the safe
  direction.
  ⚠️ **The forget was missing until 2026-08-18 (review finding P1-3), and its
  absence made this bullet false.** The branch returned before `_forget_others`,
  so a person the Console had **moved** to an organization placed here but not
  yet bootstrapped kept a live `resolved_at` on the organization they *left* —
  and the next outage admitted them back into it for up to `MAX_STALENESS`, into
  an org the registry no longer places them in. "Nothing to cache" and "keep the
  last thing we cached" are different answers and only the first degrades in the
  direction argued here; §6(c)'s trigger (a) fires with an empty keep set. Fence:
  `test_a_move_to_an_unprovisioned_org_clears_the_OLD_admission`. Fence:
  `test_a_resolve_for_an_unprovisioned_org_signs_in_and_writes_nothing`, asserting
  the sign-in succeeds, the projection is untouched, and the warning fired once.
- ⚠️ **Creating the local `organization` row is out of scope, by name.**
  It belongs to **provisioning** — CP-2a's lifecycle/`provisioning_run` path and
  **WS-29 `saas_multitenancy.md` §11 MT-1j slice 3** *(the referent this bullet
  called "WS-29's tenant bootstrap"; it did not exist until it was minted
  2026-08-19, and MT-1j slice 4 also owns the Console-side `org_placement` write
  this ticket's inner join at `store.py:625` depends on)* — and inventing it here
  would put tenant creation in
  a sign-in callback, which is both the wrong layer and an unauthenticated-ish
  write driven by whoever can reach the resolve route. Naming it out also keeps
  the R8 fixture honest: **the fixture creates the local `organization` row the
  way provisioning would** (an explicit `INSERT … (slug, display_name)`), and
  then exercises the write. A fixture that let the code under test create the
  row would be testing a path this ticket refuses to build.

---

**Where the cache lives — migration 159's projection tables are the CACHE OF
RECORD *for this entry point's fallback decision*.** *(Named explicitly
2026-08-18; **scoped** the same day by (h) above — `159:107-109` keeps
`app_user` authoritative for identity at large, and this ticket does not move
that.)* Every bound above — `resolved_at`,
the TTL, the staleness ceiling, the last-seen lifecycle state — is evaluated
against **rows**, because a 24-hour ceiling is unenforceable in a per-process
dict: `acb_auth`'s `_cache` is an in-process dictionary with a **60-second** TTL
(`access.py:37`, `CACHE_TTL_SECONDS = 60.0`), per **worker**, wiped on every
restart. That dict stays exactly what it is — a **read-through** in front of the
projection, not a second store — and the resolve cache extends it rather than
duplicating it (`access.py:73-98`). *(What "extends" means concretely — the same
idiom and the same `invalidate()` escape hatch in `console_resolve.py`, **not**
a second value type inside `access._cache`, which would break `_cache_get` — is
argued in §6(e), 2026-08-18.)* Consequences to build to: two workers may
each hold a copy for up to 60s, so `invalidate()` is per-process and the
authoritative revocation is the row; and a restart clears the dict but **must
not** re-consult the Console for everyone at once, because the rows still carry
the answer.

The projection's real column names are not the ones this spec originally
assumed. Verified 2026-08-18 against `infra/postgres/159_control_plane.sql`:

| Resolve answer | Projection column | ⚠️ |
|---|---|---|
| `email`, `display_name` | `user_identity.email`, `.display_name` (`159:67-79`) | `TEXT` + `UNIQUE INDEX ON (lower(email))`, **not** the Customer Console's `CITEXT` (`001:110`) — match on `lower(email)` or mint a second human on a UPN case change (R10) |
| membership status | `org_membership.status` (`159:83-96`) | CHECK is `invited\|active\|suspended\|removed` — a **membership** status, not the org lifecycle. Do not overload it |
| which person | `org_membership.`**`user_id`** (`159:85`) | the Customer Console calls the same column `user_identity_id` (`001:123`). Two names, one thing — name it in the mapping code or it becomes a silent no-op |
| placement | `tenant_placement.tier`, `.target` (`159:40-53`) | the Customer Console answers `org_placement` (`001:96`); the tenant plane's row is the projection of it |
| org slug | `organization.slug` (`130_org_access_control.sql:39`) | already exists, and **it is the JOIN KEY** — `TEXT UNIQUE NOT NULL`, re-verified 2026-08-18. The Console's `organization_id` is a UUID in a **different database** and must never be written into `organization.id` or `org_membership.organization_id` (§6(k) — B-e) |
| **freshness** | *— none —* | **new nullable column** `org_membership.resolved_at TIMESTAMPTZ`, the per-person TTL clock |
| **org lifecycle** | *— none —* | **new nullable column** `organization.registry_status TEXT`, last-seen `trial\|active\|past_due\|suspended\|cancelled\|deleted`. ⚠️ **Refusal copy only** — never a branch input ((d)) |
| **the cached OUTCOME** | *— none —* | **new nullable column** `organization.registry_capabilities JSONB` — the capability object **exactly as the wire carried it**, `{"sign_in":…,"write_seats":…,"use_ai":…}`. *(Added 2026-08-18 by §6(j): B-d's cached outcome needs a durable carrier, and `registry_status` must not be one because it is a string.)* JSONB rather than three boolean columns so a fourth capability is a Console-side change and not a tenant migration; and it matches `organization.settings JSONB` (`130:42`), the shape already on this table. **A missing key means NOT OBSERVED, never false** — the 403 case writes `{"sign_in": false}` alone |

⚠️ **The lifecycle column moved, 2026-08-18.** It was specified as
`tenant_placement.cc_org_status`. Two things were wrong with that. **Placement is
placement** (D15: a deployment is *where the rows sit*, never *what the customer
bought*), so hanging a commercial lifecycle off `tenant_placement` (`159:40-53`)
puts a billing fact in the table whose whole job is the data-plane address — and
the next reader of that table has to know that. And `cc_` as a prefix now parses
two ways at once (D41: CommandCenter vs Customer Console), which is the exact
ambiguity this file spent a correction pass removing. Home instead:
**`organization` (`130_org_access_control.sql:37`)**, the tenant plane's row for
the company itself, as **`registry_status`** — named for what it is, *the
registry's last-seen answer*, and therefore obviously not the local truth.

**All three new columns sit on tables in `gen_tenant_migration.EXEMPT`, and that
is correct for them.** *(Two became three on 2026-08-18 with §6(j)'s
`registry_capabilities`; the argument is unchanged and covers it, because it is
an argument about the **tables**.)* `organization` ("*the tenant list itself*",
`scripts/gen_tenant_migration.py:72`) and `org_membership` ("*control plane — the
tenant-scoped half; org_id is its PK*", `:75`) are already exempt from the RLS
generator, alongside `tenant_placement` (`:73`) and `user_identity` (`:74`). A
column added to an exempt table needs **no** new exemption entry and must not add
one: it inherits the table's reason, and the reason still holds — a policy that
hid the tenant list from the connection resolving which tenant this is would make
the box unable to answer its own first question. This ticket therefore adds
**zero** rows to `EXEMPT` and leaves `test_tenant_coverage.py`'s map untouched —
the same finding that reworded CP-1 clause 3. R5's source gate is satisfied for
the same reason and by the same fence
(`tests/unit/test_tenant_coverage.py::test_every_table_is_scoped_or_exempt_with_a_reason`,
`:61`): **no new table is created by this ticket at all**.

All three new columns are **nullable with NO default and nothing renamed**. That is
R6's expand half, and the missing default is a **deliberate, argued deviation**
from R6's letter (*"new columns nullable with a default"*, `work_plan.md` §1):
here **NULL is load-bearing** — it means *"this box has never had an answer from
the registry for this row"*, which is a different state from every value the
column can hold, and it is the state a fail-closed sign-in path must be able to
see. A default would make every pre-existing row assert a registry answer nobody
ever received. R6's purpose — old code meets new schema safely — is met either
way, because the code running before this migration reads neither column. Fence:
`test_an_unresolved_row_reads_as_never_resolved_not_as_a_state`. The migration is
a **tenant** one whose number is taken at build time by listing `infra/postgres/`
and re-checked at merge (**R1** — the highest on disk on 2026-08-18 was
`176_people_skills.sql`; do not write a number into this ticket).

**Role is deliberately NOT projected.** The Customer Console's
`org_membership.role` (`001:124`, `owner|admin|member`) is a *registry/billing*
role; the tenant's permission vocabulary is `org_role` + the access ladder that
`acb_auth/access.py` resolves. Copying one into the other creates a second grant
vocabulary, which CLAUDE.md §5 forbids by name. The Customer Console stays
authoritative on what was **bought**; Metorite stays authoritative on what is
**enforced** (§2, unchanged).

**Where the caller goes — the gateway's identity path, not the Next BFF.** The
resolve writes the tenant database, and the workbench BFF neither holds nor
should hold a tenant DB connection; the seam that already owns "somebody is
knocking at the front door" is `acb_auth`, where exactly one caller passes
`record_request=True` into `resolve_access`
(`packages/acb_auth/acb_auth/access.py:249-271`, called from
`acb_auth.deps._with_resolved_access`). That module already carries a TTL cache
with an `invalidate()` escape hatch (`:73-98`), which is the shape the resolve
cache should extend rather than duplicate. The deployment key is therefore a
**server-side env value that never reaches a browser** — the same posture as
`CUSTOMER_CONSOLE_ORG_KEY`, which the workbench's own fence already
allow-lists by name (`workbench/control_plane/src/lib/gateway.test.ts:191`).

⚠️ **But NOT on the per-request path, and that distinction is the whole of
clause 11.** *(Added 2026-08-18 — the paragraph above named the right module and
the wrong function.)* `_with_resolved_access` runs on **every authenticated
request** (`deps.py:391` and `:435`), and the email it carries is the
`X-User-Email` header, trusted because the internal Bearer proves the caller is
the Next proxy — the code says so in its own words: *"still trust Next.js but flag
domain mismatch"* (`deps.py:393`) and *"whoever holds the internal token can
already assert any X-User-Email, so a narrower set would be theatre"*
(`deps.py:402-404`). Hang a **seat-allocating** call there and the seat cap
becomes farmable: internal token + deployment key ⇒ resolve any address you like
⇒ burn a paying customer's cap to `409` ⇒ their staff cannot sign in, and the
customer's remedy is to buy seats they did not need. So the resolve fires from
**one** site, the completion of a sign-in, with an email that came from the
session — never from a header a caller chose.

Concretely, and kept deliberately small: this ticket adds **one** gateway-side
entry point whose only job is *"a sign-in is completing for this address"*, and
it is the only thing in the tree that holds the `cc_depl_` key or calls the
resolve client. The BFF reaches it from the sign-in callback and nothing else
does; `_with_resolved_access` keeps doing exactly what it does today and gains
nothing. A **second** caller is what clause 11's fence exists to fail.

⚠️ **Both ends of that sentence now have a path** *(2026-08-18, B6/B4)*: the
entry point is `apps/services/gateway/gateway/routes/signin.py`
(`POST /signin/resolve`), the client it calls is
`packages/acb_auth/acb_auth/console_resolve.py`, and the BFF end is a **new
`signIn` callback** in `workbench/control_plane/src/auth.ts` reaching it through
`headersActingAs()`. See (e) and (g) — including why the client is **not** wired
behind `resolve_access` as first proposed, and why *"a sign-in just completed"*
became *"is completing"*: the resolve has to be able to **refuse**, so it runs
inside the decision rather than after it.

**Done when** — each clause is separately testable, and each names the test that
will fence it (R7); the tests are created by this ticket:

1. **The deployment key authenticates resolve and nothing else** — over an
   **exact** exempt set. A `cc_depl_…` key presented at any other route 401s,
   parametrised so a route added tomorrow is covered without anyone remembering.
   ⚠️ *Exempt set named 2026-08-18, because "parametrised over `app.routes`" as
   first written is **red on day one**.* Enumerated from the live app that date,
   `app.routes` holds **20** entries and **five** of them authenticate nothing:
   `/health` (`main.py:383`, an `APIRoute`, docstring *"Liveness. Deliberately
   unauthenticated and deliberately says nothing"*) and FastAPI's four
   auto-generated documentation routes — `/openapi.json`, `/docs`,
   `/docs/oauth2-redirect`, `/redoc` — which are plain
   `starlette.routing.Route`, not `APIRoute`, and carry no dependency to fail.
   So the parametrisation is: **every `fastapi.routing.APIRoute` in `app.routes`
   except `/health`**, and `/health` is excluded **by name** in a one-entry
   constant with its reason, so adding a second unauthenticated route is an
   edit somebody has to justify rather than a silent widening. Filtering on
   `APIRoute` is what excludes the doc routes, and it holds without a list.
   Fences: `test_a_deployment_key_reaches_resolve_and_nothing_else`,
   `test_the_unauthenticated_route_set_is_exactly_health`.
   *(Observed while deriving this and deliberately NOT fixed here: those four
   doc routes serve the full OpenAPI schema of a cross-tenant service to any
   caller who can reach the port. Pre-existing, unrelated to this ticket, and a
   finding for the board — not a line of this one.)*
2. **The org is the answer, never the assertion.** Under a deployment key the
   body is `{email}`; a body carrying `org_slug` is **400**, not ignored. Fences:
   `test_a_deployment_key_may_not_name_an_org`,
   `test_the_org_comes_from_membership_not_from_the_body`.
3. **The operator shape stays legal, and only for the operator.** The existing
   operator + `org_slug` calls still pass unchanged
   (`test_customer_console_api.py:121,134,144` and
   `test_customer_console_lifecycle.py:173,189` are the regression), and an
   operator token is *not* accepted as a deployment key nor vice versa. Fence:
   `test_the_two_schemes_do_not_substitute_for_each_other`.
4. **Two-deployment isolation, stated so the pooled case does not falsify it.**
   Fixture: deployments A and B; org X placed on A, org Y on B;
   `person@y.example` a member of Y only. Key A resolving that email returns the
   **empty** answer. Re-place Y onto A and the *same key* resolves it — not a
   leak, but the definition of pooled. The claim under test is therefore
   precisely: **a deployment key resolves an email if and only if that email
   holds a membership in an organization whose current
   `org_placement.deployment_id` equals the key's deployment.** *Placement is
   the boundary, not the organization.* Fences:
   `test_a_deployment_key_sees_only_the_orgs_placed_on_it`,
   `test_a_pooled_deployment_resolves_every_org_placed_on_it`.
5. **No cross-org existence oracle.** Three cases return the **same status and
   the same body shape** (`200 {"organizations": []}`): an email with no
   membership anywhere; an email whose every membership is in an organization
   placed on a *different* deployment; an email whose organization does not
   exist at all. Fence: `test_the_invisible_cases_are_indistinguishable` —
   CP-3's `recorded:false` lesson (a distinguishable negative *is* a cross-tenant
   read) applied here before a verifier has to find it.
   ⚠️ **Deliberately NOT in that set:** an organization placed **on this
   deployment** whose lifecycle is `suspended`, `cancelled` or `deleted`. The
   deployment already serves that customer, so telling it the state reveals
   nothing it does not have, and it needs the state to refuse correctly — the
   existing 403-on-`deleted` (**`main.py:702-711`**, the deployment arm's
   `if not admissible: if refused: raise` — **corrected 2026-08-18, was
   `:598-603`, and before that `:542-547`**; `:598-603` is the **operator**
   arm's gate, a hundred lines away and returning the same body shape, which is
   how the wrong citation survived two passes) is the right shape and stays.
   Fence: `test_a_dead_org_placed_here_is_named_not_hidden` — and it asserts the
   **403**, never a 200 body carrying a dead org, because the 200 body is built
   from `admissible` alone (`main.py:755-769`) and cannot carry one (§6(j)).
6. **Fail closed, degrade bounded — both TTLs pinned.** With the Customer
   Console unreachable: an uncached email is **refused** with the
   service-unavailable copy (never "access denied"); a cached email within
   `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS` proceeds; one past
   `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS` is **refused**. Fences:
   `test_an_uncached_person_is_refused_when_the_console_is_unreachable`,
   `test_a_cached_person_proceeds_inside_the_ttl`,
   `test_a_cached_person_is_refused_past_the_staleness_ceiling` — the last one
   reading the named config value, so changing the default changes the test's
   input and not its meaning.
   ⚠️ **And a cached dead state overrides all three** *(added 2026-08-18)*: a
   cached record already carrying `sign_in: false` refuses sign-in
   **immediately**, Console reachable or not, inside the TTL or outside it — a
   dead state in the cache is a fact, not a stale hint, and staleness may only
   make the cache more restrictive, never less.
   ⚠️ **The box caches and applies the BOOLEANS, never the status string**
   *(2026-08-18, §6(d) — B1)*. `capabilities_of` lives in the Console package
   (`lifecycle.py:101`) and the tenant deployable must not depend on it, so the
   decision is made Console-side and only its result crosses the wire.
   `status` is cached too, and its **only** use on the box is the word shown in
   refusal copy. A deployment that reads `status == "deleted"` and decides is a
   second copy of the state machine written as an `if`; a deployment that reads
   `sign_in` cannot drift.

   ⚠️ **REWRITTEN 2026-08-18 (B-d, §6(j)): the fence list below used to describe
   an input that cannot occur.** `capabilities.sign_in` is **always `true` in a
   200 body** — `_resolve_for_deployment` partitions on `can_sign_in`
   (`main.py:695-700`), 403s when nothing is admissible (`:702-711`) and builds
   the body from `admissible` alone (`:755-769`). A `deleted` organization
   reaches the box as an **HTTP 403**, never as a 200 entry, so a fence that fed
   a 200 body with `sign_in: false` was fencing a fiction. The rule survives; its
   input changes. **What the box caches on a 403 is
   `{"sign_in": false}` and nothing else** — the only fact that outcome proved —
   and *that* record is what refuses at any freshness. `suspended`/`cancelled`
   still arrive in a **200** with `sign_in: true` and
   `write_seats`/`use_ai` false, because `lifecycle.capabilities_of()` is the one
   state machine and this ticket does not mint a second copy of it.
   Fences, restated against outcomes:
   - `test_a_cached_dead_org_refuses_without_asking_the_console` — the fixture
     drives a **403** first (dead org placed here), then makes the Console
     unreachable and asserts the refusal inside *and* outside the TTL.
   - `test_a_cached_suspended_org_keeps_login_and_loses_write_seats` — a **200**
     with one org, `sign_in: true`, `write_seats`/`use_ai` false.
   - `test_staleness_never_relaxes_a_cached_state`.
   - `test_a_403_records_only_the_fact_it_proved` — `write_seats` and `use_ai`
     are **absent** from the stored capability object, not `false`.
   - `test_the_deployment_never_branches_on_a_lifecycle_string` (the structural
     half, in `tests/unit/test_console_dependency_boundary.py`: no tenant-side
     module compares against `"suspended"`/`"cancelled"`/`"deleted"`). Still
     satisfiable and still worth having — the branch inputs after §6(j) are the
     **HTTP status**, `len(organizations)` and three booleans, and
     `registry_status`'s only reader is refusal copy.
   - `test_the_four_resolve_outcomes_are_each_handled` — §6(j)'s table,
     parametrised: 200-with-one admits, 403 refuses, 200-with-many refuses
     `WorkspaceChooserRequired`, 200-with-none refuses `AccessDenied` and fires
     `invalidate()`. A fifth outcome added later has to be named here — and
     one was, during the build: the **409** at the seat cap (§6(j) row v),
     which refuses, caches nothing and carries `AccessDenied`. Its own fence is
     `test_a_seat_cap_refusal_fails_closed_and_caches_nothing`.
   *(Fence renamed 2026-08-18 from `…_locks_features_without_asking_the_console`
   — the box does not lock features, see clause 7 and B5.)*
7. **A lifecycle change is RECORDED on the deployment within the stated bound —
   and the bound is a pair.** *(Corrected twice on 2026-08-18. First: this clause
   read "lands within the TTL", which contradicted clause 6 and overstated what a
   partitioned box can promise. Second, **answering B5**: it read "**locks
   features** on the deployment", which contradicted the non-goals below —
   *"enforcing seats or lifecycle in product surfaces beyond sign-in"* — and
   there is no enforcement point on the box to make it true. The verb is now
   **records**, and the only behaviour that follows the record in this ticket is
   sign-in.)* **Console reachable** → an organization moved to `suspended` has
   `write_seats: false` / `use_ai: false` stored on the deployment no later than
   `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`, with `sign_in` still true and login
   still working (§4.1d, `lifecycle.py:72-75`). **Console
   unreachable** → the change lands no later than
   `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`, because a box that cannot
   ask cannot learn — *unless* it already holds `sign_in: false`, in which case
   clause 6's rule fires at once. `deleted` is the only state that refuses
   sign-in, and `capabilities_of` is the only place that decides that
   (`lifecycle.py:64-78`).
   ⚠️ **Where the record LANDS, named 2026-08-18 (B-e, §6(k)).** "Stored on the
   deployment" means `organization.registry_capabilities` (JSONB, the wire
   object verbatim) and `organization.registry_status` (the word, for copy) on
   the row found by **`organization.slug = <the slug in the resolve answer>`**,
   with `org_membership.resolved_at` moved for the person. The Console's
   `organization_id` is a UUID in a different database and is never the key. If
   no local `organization` row carries that slug, the record is **skipped** with
   one structured warning and sign-in follows the fresh answer unchanged —
   creating that row is provisioning's act, named out of scope in §6(k).
   ⚠️ **What this clause does NOT promise.** No product surface on the
   deployment consults `use_ai` or `write_seats` after this ticket. **Feature
   enforcement stays MT-2's `intersect()` seam** (§2) and remains a non-goal
   below. Anyone reading this clause as "suspension disables the AI on the box"
   is reading the thing B5 removed. Fences (and they assert the record and the
   sign-in decision, nothing more):
   `test_a_suspension_is_recorded_on_the_deployment_within_the_ttl`,
   `test_a_partitioned_deployment_refuses_a_cached_person_at_the_ceiling`,
   `test_a_recorded_suspension_changes_no_product_surface`,
   `test_the_record_is_keyed_on_the_slug_not_on_the_console_uuid`.
   *(First fence renamed 2026-08-18 from
   `test_a_suspension_reaches_the_deployment_within_the_ttl` for the same
   reason — "reaches" was ambiguous between arriving and being enforced.)*
8. **Idempotent projection upsert.** Resolving the same person five times leaves
   exactly **one** `user_identity` row (matched on `lower(email)`) and **one**
   `org_membership` row, with `resolved_at` moved and nothing else rewritten.
   ✅ **BOTH HALVES MET 2026-08-18.** The two halves live in two
   databases and both now exist: the Console's own `user_identity` is `CITEXT
   UNIQUE` (`001:110`) and `store.ensure_identity` is `ON CONFLICT … DO UPDATE`,
   so five resolves leave one row and one membership — fence
   `test_resolving_five_times_writes_one_console_identity_row` *(renamed from
   `…_one_projection_row`, which named the tenant plane's table and would have
   read as green for a thing nobody built)*. The `resolved_at` clock and the
   `lower(email)` match belong to migration 159's projection and land with the
   deployment half — in `tests/unit/test_deployment_resolve_cache.py`, against a
   real **tenant** Postgres (§6(i)), fence
   `test_resolving_five_times_writes_one_projection_row_and_moves_resolved_at`.
   ⚠️ `159:67-79`'s `user_identity.email` is `TEXT` with `UNIQUE INDEX ON
   (lower(email))`, **not** the Console's `CITEXT` (`001:110`): the projection
   upsert matches on `lower(email)` or a UPN case change mints a second human
   (R10). And the person column is `org_membership.user_id` (`159:85`) where the
   Console calls the same thing `user_identity_id` (`001:123`) — two names, one
   thing, named in the mapping code or it is a silent no-op.
   ⚠️ **The ORGANIZATION half of that upsert had no key at all, and now does**
   *(2026-08-18 — B-e, argued in full at §6(k))*. `org_membership.organization_id`
   REFERENCES the **tenant** `organization(id)` (`159:83-96`), a **local**
   `gen_random_uuid()` (`130:37-38`) seeded only with `slug='default'`
   (`130:49-51`) — a different value space from the Console's `organization_id`
   entirely. **The join key is the SLUG**: `organization.slug` is
   `TEXT UNIQUE NOT NULL` (`130:39`, verified 2026-08-18), the resolve answer
   carries it (`main.py:760`, from `store.py:537-548`), and the upsert resolves
   `SELECT id FROM organization WHERE slug = :slug` through that constraint's
   unique index. Writing the Console UUID into either column is the defect this
   ⚠️ exists to stop. **No matching local row → the write is SKIPPED** with one
   structured warning; sign-in proceeds on the fresh answer, and the box simply
   has no fallback cache for that person until provisioning creates the row.
   The R8 fixture creates it the way provisioning would — an explicit
   `INSERT INTO organization (slug, display_name)` — and then exercises the
   write, so nothing under test is also the thing that made the row.
   Fences: `test_the_projection_upsert_joins_on_slug_not_on_the_console_uuid`,
   `test_a_resolve_for_an_unprovisioned_org_signs_in_and_writes_nothing`.
9. **Seat semantics unchanged, and multi-org allocates nothing.** Exactly one
   visible organization → a Core seat is allocated on first resolve and **not
   re-burned** on the next, and the cap still returns **409** with the buy-more
   payload. The operator-auth equivalents of both are already pinned
   (`test_customer_console_api.py:131` — five resolves, one seat, the drift bug;
   `:142` — the cap and its payload); this ticket adds the **deployment-key**
   equivalents rather than assuming the scheme is transparent to them.
   **More than one visible organization → the Console allocates nothing and the
   deployment REFUSES the sign-in.** *(The admit/refuse half was undefined until
   2026-08-18: the clause said what the Console does and left what the box does
   with the answer unwritten, which is the half that decides whether somebody
   gets in.)* Two halves, stated separately because they are two systems:
   - **Console side —** `200` with the full `organizations` list and **no seat
     allocated in any of them**. Allocating a seat in every organization a
     person can see would bill an admin for a login they did not make.
   - **Deployment side —** sign in **nobody**. Fail closed rather than guess an
     org: `acb_auth.resolve_identity` returns *exactly one* `organization_id`
     (`access.py:358`, `-> tuple[str | None, str | None]`), so admitting this
     person requires **choosing** an organization, and no human made that
     choice. The copy is honest about the cause and does not blame the person —
     *"your account belongs to more than one organization on this deployment;
     please contact your operator"* — the same rule as clause 6's
     service-unavailable copy: never "access denied" for a state the person did
     not create.
     ⚠️ **The mechanism, named 2026-08-18 (B6(iii)).** The refusal is carried by
     the `signIn` callback returning `"/signin?error=WorkspaceChooserRequired"`,
     with the copy in `errorCopy.ts`; clause 6's unavailable case returns
     `"/signin?error=ConsoleUnavailable"`. Two distinguishable reasons need two
     codes, and **Auth.js v5's vocabulary is fixed** — returning `false` yields
     `AccessDenied` and nothing else, whose shipped copy (*"Your account isn't
     authorized for this workspace"*) is the phrasing D33.1 forbids for both of
     these. See §6(g) for the version check that says the string-return path
     exists.
   Choosing among them is the **chooser**, which is a non-goal below and stays
   one. Fences: `test_a_multi_org_resolve_allocates_no_seat`,
   `test_a_multi_org_person_is_refused_sign_in_and_told_why`,
   `"errorCopy speaks the two CP-2b codes"` (`signin.test.ts`).
10. **R8 — proven against a real Postgres.** ✅ **BOTH HALVES MET 2026-08-18**
    — split by B3 + status-drift item 1 (this clause had been listed among
    "clauses met", which was true of the Console ladder and of nothing else),
    and both ladders now have a replayer a pytest fixture can call and a CI
    database to run it against.
    - ✅ **Console half.** Every clause above that reads or writes the *Console*
      database runs against a real Postgres 16 via
      `tests/unit/_customer_console_ladder.py`;
      `tests/unit/test_customer_console_resolve.py` is in §7's command list
      **and** in `pr-check.yml`'s skip-guard (`:167-178`), because CP-3's finding
      was that CI skipped every DB-gated fence while reporting green.
    - ✅ **Deployment half — `tests/unit/_tenant_ladder.py`, and it is a second
      MECHANISM over one ladder rather than a second source of truth.** *(Rewritten 2026-08-18,
      answering **B-c**: this bullet said "it needs machinery that does not
      exist", which was **false** and is the kind of claim that gets a second
      mechanism built without an argument.)* `pr-check.yml:235-291` — the
      `migrations` job — replays the whole tenant ladder on
      `pgvector/pgvector:pg16` (`:240`) via `psql -f infra/postgres/01_schema.sql`
      (`:269-272`) and `scripts/apply_migrations.sh` ×3 (`:274-291`). What is
      genuinely missing was a replayer a **pytest fixture** can call: that job is
      bash driving the `psql` client, and `_customer_console_ladder.py` reads
      `infra/customer_console/` and can never build a tenant schema. §6(i)'s
      opening ⚠️ carries the divergence argument (CLAUDE.md §5) — two mechanisms,
      one ladder, neither transcribing a file list. Separately, `pr-check.yml`'s
      `test` job provisioned **one** Postgres and exported **one** DSN, so a
      tenant-side R8 suite written then would have skipped there and reported
      green. All four mandated items landed 2026-08-18:
      (i) `tests/unit/_tenant_ladder.py`, applying `01_schema.sql` first and then
      `infra/postgres/` sorted on the leading integer, with the same two R1
      refusals (empty ladder, duplicate number —
      `_customer_console_ladder.py:55-60`, `:62-71`) and the init-only 00/01
      split `apply_migrations.sh:222-226` already makes, ordered the way
      `:206-211`'s `sort -V` does; (ii) a **second Postgres service** in
      `pr-check.yml`'s `test` job on `pgvector/pgvector:pg16` exporting
      **`TENANT_LADDER_DATABASE_URL`** — ⚠️ **never `DATABASE_URL`**, which would
      arm two tenant-coverage tests that fail by construction in that job
      (§6(i)(2) names both) — with the reachability assertion (`:146-154`) given
      its twin; (iii) a **skip-guard entry** so the new suite failing to run
      fails the job; (iv) the suite **named**:
      `tests/unit/test_deployment_resolve_cache.py`, gated on the launch snapshot
      of `TENANT_LADDER_DATABASE_URL` taken beside `tests/conftest.py:16` — the
      same idiom `test_tenant_coverage.py:188-205` uses for its own variable,
      never a raw `os.environ` read.
      The structural fences of §6(d) and §6(g) take **no** database and live
      outside this suite on purpose — folded in, they would skip with it.
      ⚠️ **A fifth thing was needed and is now built: the skip-guard's `grep`
      was hard-coded to the CONSOLE suites' skip string** (`"CUSTOMER_CONSOLE_
      DATABASE_URL unset"`), so naming the tenant suite in the guard's file list
      would have left the guard green while that suite skipped every test — the
      CP-3 failure class reintroduced one layer up, inside the very step that
      exists to catch it. The step now greps for **each gate variable's own
      skip string**, plus a third line asserting the database-free fences never
      report a skip at all. *(Found by the dispatch audit as non-blocking nit 1;
      built 2026-08-18.)*
      ⚠️ **And one thing the ladder module had to solve that nobody had hit:**
      running a migration through SQLAlchemy's `exec_driver_sql` hands psycopg3
      an (empty) parameter set, so it scans the SQL for placeholders — and this
      ladder is full of `LIKE '%status%'`. The Console ladder never met it
      because that SQL contains no `%`. `_tenant_ladder.py` therefore executes
      each file on the raw DBAPI cursor with **no parameters at all**, which
      also selects the simple query protocol and is what lets one file carry
      many statements. Escaping every `%` across 176 files of production DDL was
      rejected: it is a text transformation on shipped migrations and it is
      wrong the day one of them legitimately contains `%%`.
11. **Resolve is called from the sign-in path and from nowhere else, with a
    provider-verified email.** *(Added 2026-08-18 — without it the seat cap is
    farmable, see the ⚠️ above. **Both ends named 2026-08-18, B4/B6:** the clause
    described a hop that had no route, no module and no fence.)* The chain, end
    to end, and nothing else may appear in it:

    | Hop | Where | What carries the email |
    |---|---|---|
    | 1 | `workbench/control_plane/src/auth.ts` — a **new `signIn` callback** | `profile.email`, provider-verified. Never request input (R11) |
    | 2 | `lib/gateway.ts` `headersActingAs(email)` (`:137-152`) | the existing "already-verified member" seam; `gatewayHeaders()` cannot be used, `auth()` returns nothing before a session exists |
    | 3 | `apps/services/gateway/gateway/routes/signin.py` — `POST /signin/resolve` | the one route; the only holder of `CUSTOMER_CONSOLE_DEPLOYMENT_KEY` |
    | 4 | `packages/acb_auth/acb_auth/console_resolve.py` | the client, the cache, the projection write |

    ⚠️ **The proposed default named the `jwt` callback's `account`-present branch
    (`auth.ts:85-93`) and this ticket overturns it** — that branch runs *after*
    the sign-in decision and cannot refuse, so it cannot satisfy clauses 6, 7 or
    9. §6(g) carries the argument and the version check.
    The email is **never** taken from `X-User-Email` on an
    ordinary request, and resolve is **never** hung off
    `_with_resolved_access`, which runs per request (`deps.py:391`, `:435`), nor
    behind `resolve_access` (`access.py:249`), which has six production callers
    including one that fans it over a list of emails (`rooms.py:215` — §6(e)).
    Fences — a **structural** fence is acceptable and preferred here (R7 prefers
    structural to example), and there are now two because the chain crosses a
    language boundary:
    - `test_resolve_is_reachable_only_from_the_signin_path`
      (`tests/unit/test_console_dependency_boundary.py`): assert over the tree
      that `console_resolve` has exactly **one** caller and name it, so a second
      call site added later fails rather than quietly doubling the seat surface.
    - `"resolve fires only from the signIn callback"` (`signin.test.ts`):
      source-level over `auth.ts` — the call appears inside `signIn` and in
      neither `jwt` nor `session`, and in no route file — plus `"the resolve
      email comes from the provider profile, never from request input"`. Without
      the second fence the Python one is satisfied by a BFF that calls the route
      from anywhere.
    🔓 **Named accepted risk, pre-existing and NOT inherited silently — and it
    is ONE credential, not two.** *(Restated 2026-08-18, answering **B-f**. The
    version that stood here said "a holder of **both** the internal token **and**
    the deployment key", which **understated the residual by one credential** and
    made it sound like a two-key compromise.)* The `cc_depl_` key never leaves
    the box: it lives in the gateway's env and `POST /signin/resolve` presents
    it. That route sits behind the app-wide `require_authenticated`, which
    accepts the internal Bearer plus an `X-User-Email` of the caller's choosing
    and trusts it by design — the code's own words, *"still trust Next.js but
    flag domain mismatch"* (`deps.py:391-396`, where an off-domain address is
    **logged and then used**, `:390` `email = None` followed by
    `email or x_user_email` at `:393`) and *"whoever holds the internal token can
    already assert any X-User-Email, so a narrower set would be theatre"*
    (`deps.py:402-404`, granting `SERVICE_ACCESS`, `access.py:42-45`). So the
    honest statement is: **a holder of the INTERNAL TOKEN ALONE can drive resolve
    for arbitrary addresses, using the box's own deployment key**, and burn a
    customer's cap to `409`. The attacker never needs the `cc_depl_` key; the box
    presents it for them.
    Clause 11 reduces the surface from *every authenticated request* to *one call
    site*; it does not remove it, and nothing in this ticket does. The residual is
    bounded by seat idempotence (`decide_assignment(already_assigned=True)` is a
    no-op that succeeds, `seats.py:141-142`; **re-derived 2026-08-18** —
    `:129-130` is the docstring prose *about* that branch, not the branch), so
    the cost is one seat per
    **distinct** address, which is cheap for an attacker who already holds the
    box's internal token. **This risk predates CP-2b and is a property of the
    internal-token design; the gate that NARROWS it is §4.3's
    `GATEWAY_INTERNAL_TOKEN` / `LITELLM_MASTER_KEY` split (work_plan §6, §8 gate
    1) — today one secret opens both doors.** It is **accepted, named and
    unchanged by this ticket**, not fixed by it.
    ⚠️ **What the frontend fence does and does not cover, said plainly:** the
    `signin.test.ts` fences of §6(g) constrain **`auth.ts`** — that resolve fires
    only from the `signIn` callback, with a provider-verified email — and
    `gateway.test.ts`'s extended sweep (§6(g), B-g) constrains where a bearer may
    be minted. **Neither can constrain a caller who already holds the internal
    token**, because that caller is not running our TypeScript. Only the token
    split does. Recorded here so the next reader does not discover it inside a
    billing incident, and does not mistake a green vitest run for a closed hole.
12. **The deployment-key response schema is exactly this, and it carries no
    `role`.** ✅ **MET 2026-08-18 — shipped shape AND the `capabilities`
    block**, the latter added to the deployment arm by `_capability_block`
    (`main.py:771`).
    *(Added 2026-08-18 — the shape was unspecified, and §5.2's code
    block still documented the forbidden one until the same change. **Amended
    the same day (B1)**: the entry gains `capabilities`, so the clause drops from
    ✅ to ◐ and the deployment-half slice carries a small additive Console-side
    commit. All four line anchors below were **re-derived 2026-08-18** — three
    had moved under the Console half's own edits.)*
    - **Success:** `200` with `identity_id` and an `organizations` array; each
      entry carries `organization_id`, `slug`, the **placement target** it
      resolves to, the **lifecycle status**, the **seat outcome**
      (`allocated` | `already_held` | `not_allocated`), and — **new, open** —
      **`capabilities`**:

      ```json
      {"organization_id": "…", "slug": "acme", "placement": "primary",
       "status": "suspended", "seat": "already_held",
       "capabilities": {"sign_in": true, "write_seats": false, "use_ai": false}}
      ```

      The three booleans are computed Console-side by the one
      `lifecycle.capabilities_of()` (`lifecycle.py:101`, table at `:64-78`) and
      are **what the deployment stores and applies**; `status` is present for
      refusal copy and is never a decision input on the box (§6(d) — B1).
      ⚠️ **In a 200 body `sign_in` is ALWAYS `true`** *(stated 2026-08-18, B-d /
      §6(j))*: the arm partitions on `can_sign_in` (`main.py:695-700`), 403s when
      nothing is admissible (`:702-711`) and builds this array from `admissible`
      alone (`:755-769`). `sign_in` rides the wire anyway — it is the box's
      record and MT-2's future input, and a field that is constant *today*
      because of a filter *upstream* is exactly the field to send explicitly. But
      **no fence may feed a 200 body with `sign_in: false`**: that input cannot
      occur, and a test that manufactures it is testing our fixture. The
      names are the box's vocabulary, deliberately not `OrgCapabilities`'
      four-field shape: `data_retained` is a Console-side retention fact with no
      deployment behaviour behind it, and shipping a field nothing reads invites
      somebody to read it. **No `role` field, ever
      —** `org_membership.role` (`001_customer_console.sql:124`,
      `owner|admin|member`) is *registry/billing* vocabulary; the tenant's
      permission vocabulary is `org_role` plus the ladder `acb_auth/access.py`
      resolves, and a second grant vocabulary is forbidden by name (D12,
      CLAUDE.md §5).
    - **Empty:** `200 {"organizations": []}` and **no other key** — in
      particular **no `identity_id`**, which would otherwise distinguish "this
      email is known to the Console" from "it is not" and hand back exactly the
      cross-org existence oracle clause 5 forbids.
    - **At cap:** `409` with the buy-more payload, byte-compatible with the
      shipped seats path — `{"reason": "seat_cap_exceeded", "buy_more":
      {plan_slug, purchased, assigned, additional_seats_required, price_inr}}`
      (`seats.py:147-158`, the `decide_assignment` cap return; pinned today at
      `test_customer_console_api.py:142`. **Re-derived** — `:135-146` is
      docstring).
      Reachable only in the single-visible-org case, since clause 9 allocates
      nothing when there are several.
    - **Dead org placed here:** `403` with the existing shape,
      `{"detail": "organization is <state>"}` — **`main.py:702-711`**, the
      DEPLOYMENT arm's `if not admissible: if refused: raise`.
      ⚠️ **Anchor corrected 2026-08-18** *(the re-audit's mis-anchor finding)*:
      this row cited **`:598-603`** (and before that `:542-547`), which is the
      **operator** arm's gate inside `_resolve_for_operator` — a hundred lines
      away, reached only by an operator token naming an `org_slug`, and returning
      an identical body shape, which is how the wrong citation survived. Named,
      not hidden, per clause 5's ⚠️.
    - **The operator scheme keeps its current shape unchanged**
      (`identity_id`, `organization_id`, `role`, `status`, `seats`,
      `main.py:645-651`. ⚠️ **Corrected 2026-08-18** — the note here read
      "`:594-600` is now the deployment arm's lifecycle capability gate"; it is
      **not**. `:594-603` is the **operator** arm's own status read,
      `capabilities_of(state)` and raise. The deployment arm's equivalent is
      `:695-711`.). One endpoint, two schemes, two response shapes
      chosen by the credential — and the regression for that is clause 3's.
      **`capabilities` is added to the DEPLOYMENT arm only**; adding it to the
      operator arm would change a shipped surface for no caller.
    Fences: `test_the_deployment_answer_never_carries_a_role`,
    `test_the_empty_answer_carries_nothing_but_an_empty_list`,
    `test_the_deployment_at_cap_returns_the_shipped_buy_more_payload`,
    `test_the_operator_response_shape_is_unchanged` (which now also proves the
    operator arm did **not** gain `capabilities`), and — new —
    `test_the_deployment_answer_carries_capability_booleans_from_the_one_state_machine`
    in `tests/unit/test_customer_console_resolve.py`, parametrised over
    `lifecycle.STATES` so a state added later is covered without anyone
    remembering.
    ⚠️ **That parametrisation asserts one of TWO things per state, not one**
    *(corrected 2026-08-18, B-d)*. For a state whose `capabilities_of(state).
    can_sign_in` is **true** it asserts the 200 entry's `capabilities` block
    matches `capabilities_of(state)` field for field. For a state whose
    `can_sign_in` is **false** — `deleted` is the only one today
    (`lifecycle.py:64-78`) — it asserts the **403**, because that state cannot
    appear in a 200 body at all. The version that expected a 200 carrying
    `sign_in: false` for every state would have been red on `deleted` from the
    first run, against code that is correct.

**Gate split.** 🟢 **AGENT-SAFE:** the fourth scheme, the migration, the
endpoint change, the projection columns, the gateway-side caller, every fence,
and the whole thing exercised dark against fixtures. *(Re-stated 2026-08-18 for
the deployment half: the `capabilities` addition to the Console response, the
new `signIn` callback, `console_resolve.py`, `routes/signin.py`, the four
settings fields, the tenant migration (**three** columns since §6(j)),
`_tenant_ladder.py`, the `conftest.py` snapshot line, the second Postgres
service in `pr-check.yml`, the `gateway.test.ts` sweep extension of §6(g) and
every fence named above are **all inside the
agent-safe half** — none of them mints a real credential, reaches a live box or
flips anything. Minting `cc_depl_` keys **against fixtures** is agent-safe and
§8 gate 7 says so.)* 🔴 **OWNER-GATE — refuse by
name:** **issuing a real `cc_depl_` key and setting it in a live deployment's
env** (registered as §8 gate 7). It is a credential issuance plus a deployment
env write, i.e. two existing gate classes at once, and a deployment key is the
credential that lets a box ask about people. **Setting
`CUSTOMER_CONSOLE_DEPLOYMENT_KEY` or `CUSTOMER_CONSOLE_URL` in a live
deployment's env is the same gate** — declaring the settings fields is not.
**And, added 2026-08-18 with the F1 repair: setting
`CUSTOMER_CONSOLE_RESOLVE_ENABLED=true` on a live deployment is the same gate
too** — it is the Next-side switch that arms the hop, and arming it makes every
Console or gateway outage a sign-in refusal by design. Declaring the flag,
defaulting it OFF and fencing both positions is agent-safe.

**Non-goals** (each is a later ticket, named so this one does not grow):
placement **heartbeat** on the deployment key — plausibly the second capability
it earns, and explicitly not now, because a capability set of one is the only
one that is obviously right · entitlement/module sync to the deployment ·
**enforcing** seats or lifecycle in product surfaces beyond sign-in (MT-2's
`intersect()` seam stays where it is, §2) · the multi-organization chooser for a
person visible in two orgs on one deployment · retiring the operator-auth shape
· any Router or metering change.

**CP-2c · The self-serve signup flow — the form over CP-2a's API.** ◐ **MINTED
2026-08-19 (D46, owner directive); SLICES 1–4 BUILT (2026-08-19/20).**
⚠️ **Updated 2026-08-24 by WS-29 MT-1f slice 1's branch — two additions to the
shipped surface, both recorded here because this ticket owns the signup door:**
(i) **done-when 4a**, the **`ReservedSlug`** refusal (owner ruling B7 — a
LIVE-DEFECT fix: `api`, `app` and `www` were registrable through the public form),
with the reserved vocabulary shared cross-language and a NAMED gap left open at
the Console's own provision door; and (ii) **done-when 8a**, `/signup` with no
session **redirects to `/signin`** — defence in depth behind `proxy.ts`, which
already refuses the page to a signed-out visitor (both clauses were re-worded in
repair round 1, 2026-08-24: the reserved set grew to **21** labels and 8a's check
is `currentIdentity()`, never `await auth()`). Slice 1 is the Console deployment-key provision arm (done-when 6 and
done-when 8's Console half) and it carries the D46.6-item-1 amendment; the
record is in the slice list at the end of this ticket. Nothing of the *form*
exists yet: no `/signup` route, no `SELF_SERVE_SIGNUP_ENABLED` flag, no gateway
`POST /signup/provision`. The owner's
words: a basic website on `metorite.com` where users can sign up (that page is
**WS-33**, `specs/marketing_site.md` — its CTA lands here); on signup *"they
are taken through a full signup flow. They have to set up an organisation, and
the first user becomes the admin and can then log into the application."*
Verified 2026-08-19: **no** `signup`/`register`/`onboarding` route exists in
`workbench/control_plane/src/app/` (the only unauthenticated surface is
`/signin`), no `SELF_SERVE*`/`SIGNUP*` flag exists repo-wide, and migration
179's `provision_organization` has **no production caller**. This ticket is the
first caller of both provisioning halves and closes the loop four specs left
open.

**The flow** *(agent-proposed defaults, the D16/D17 class — owner may
overrule any numbered item)*:

1. **Entry `app.metorite.com/signup`** — an unauthenticated Next route beside
   `/signin`, rendered from the same `configuredProviders` seam
   (`authPosture.ts:79`). **Identity is IdP-attested BEFORE any form**: the
   visitor authenticates with Google first, so the email is never typed
   (R11 posture) and arrives verified — which is exactly what makes CP-2d's
   email-OTP deferrable.
2. **Post-auth branch.** An email that already belongs to an organization is
   refused with honest copy naming *the caller's own* membership ("this
   account already belongs to <their org> — sign in instead"), never any other
   org's existence (CP-3's `recorded:false` oracle lesson). No chooser (CP-2b
   non-goal), no second org for one email: `app_user` is globally unique on
   `lower(email)` and `provision_org_owner` refuses cross-tenant moves
   (`179_org_provisioning.sql:222-229`).
3. **The form**: organization display name · slug (derived, editable,
   validated by the same rules 179 enforces) · **registered state (REQUIRED —
   GST place-of-supply, `saas_operations_doctrine.md` §3.1)** · GSTIN
   (optional, shape-validated when present). The GST fields land on the
   Console org row (`001_customer_console.sql:68-69`) — ✅ re-verified at the
   2026-08-19 audit: `ProvisionRequest.gstin`/`billing_state` already exist
   (`main.py:169-170` post-slice-1; were `:139-140` pre-build) and thread to
   `store.ensure_organization` (`store.py:73`), so no Console change is needed
   for the fields.
   **The shapes, ruled here because no document in the corpus had one
   (audit B5):** *GSTIN* = the 15-character structural form
   `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$` (state code · PAN ·
   entity · literal Z · check character), validated **structurally only** at
   MVP — the checksum needs a lookup table and a checksum-bad GSTIN is a
   customer conversation, not a security boundary (argued; tighten later).
   *Registered state* = required NON-EMPTY at the **gateway route** (the
   requirement is the self-serve flow's — the Console's `billing_state` stays
   optional so the operator arm's semantics are untouched). *Slug* = required
   NON-EMPTY **and** a DNS-label-safe subdomain
   `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$` (`re.fullmatch`: lowercase
   alphanumeric + internal hyphens, no leading/trailing hyphen, ≤63 chars),
   validated at the **gateway route** BEFORE step 0/1/2 — an **agent-proposed
   default (D16/D17)**, forward-compatible with MT-1f's per-tenant
   `<slug>.metorite.com`; the owner may overrule the exact charset. 179 only
   guarantees non-empty, so without this a blank slug reached
   `provision_local_organization("")` → 179's generic `P0001` → the broad step-1
   `except` → a FALSE **200 `ConsoleUnavailable`** ("retry") for a PERMANENT
   shape error (slice-2 P2, repaired at landing), and a spaced slug (`"a b"`)
   reached the cross-plane join key unvalidated. Refusals: **400**
   `InvalidGstin` / **400** `MissingState` / **400** `MissingSlug` / **400**
   `InvalidSlug` / **400** `ReservedSlug`, fenced red-first in
   `test_signup_provision_route.py`; the form mirrors all five client-side
   (advisory — the route is the fence).

   ⚠️ **RESERVED LABELS — added 2026-08-24, owner ruling B7 (`saas_multitenancy.md`
   §11 MT-1f). This is a LIVE-DEFECT FIX, not a forward-looking nicety, and it
   applies regardless of any flag.** `_SLUG_RE` is a *shape* rule and shape was
   never the whole rule: measured 2026-08-24, the gate admits **`api`**, **`app`**
   and **`www`**, so a self-serve customer can today register the slug that names
   the gateway's own hostname — and the moment MT-1f's wildcard exists, that slug
   is a hostname collision with a live service, minted by a stranger through a
   public form. The reserved set is B7's thirteen —
   `{app, api, www, admin, console, signin, signup, mail, static, cdn, status,
   help, docs}` — **plus eight added additively in repair round 1, 2026-08-24**:
   `{assets, auth, billing, dev, login, operator, staging, ws}`, all names the
   platform already uses or has ticketed (`operator` = the Operator Console D35,
   `billing` = the subscription surface, `auth`/`login` = sign-in hostnames,
   the rest infrastructure). **21 in total**, and safe to widen only because
   self-serve signup is dark and nobody can already hold one — refused at the
   **route**, in the same shape-violation class as
   `InvalidSlug`, with the new code **`ReservedSlug`** (400) so the copy can say
   *"reserved"* rather than *"malformed"*. Comparison is on the already-lowercased
   slug, after the `_SLUG_RE` check, so a reserved label and a malformed one are
   never confused. **It is not an existence oracle**: the set is static, public and
   identical for every caller, which is exactly what distinguishes it from
   `SlugTaken`.
   **ONE vocabulary, two languages.** The canonical list is
   `workbench/control_plane/src/lib/subdomain.ts`'s `RESERVED_LABELS` (it exists
   because of DNS, so the host parser owns it); the gateway holds the runtime
   literal it enforces, and `tests/unit/test_subdomain_host_vocabulary.py`
   **parses the TypeScript** and pins the two sets equal — the
   `test_seed_status_colours_match_the_shared_vocabulary` idiom, chosen for the
   same reason (*a mirror goes stale and then lies*).
   ⚠️ **NAMED GAP, not closed here: the Console-side provision door.**
   `POST /orgs/provision` (`apps/services/customer_console/`) accepts a slug on
   both arms and applies **no** reserved-label check of its own; migration 179's
   `provision_organization` accepts any slug too. Every *self-serve* path reaches
   the Console **through** this gateway route, so the public door is closed — but
   an operator-arm provision, or any future caller that does not transit
   `routes/signup.py`, is not. Closing the Console door belongs to the branch that
   owns `apps/services/customer_console/` and is recorded here so it has a
   referent rather than a disclaimer chain (the CP-2c/MT-1j failure mode).
4. **Submit → a NEW gateway route `POST /signup/provision`** (mirror of
   `/signin/resolve`'s posture: BFF-internal bearer, **session-derived email
   only, a body email/tenant is 400 never ignored** — CP-2b clause 11's rule).
   ⚠️ **The caller is an ORGLESS session, and the hazard is stated correctly
   here (audit B3 corrected the first draft):** `require_authenticated`
   (`deps.py:568-602`) checks only that the session carries an email — an
   orgless session already passes it today, so no new dependency class is
   minted. The real constraint is that an unresolved identity **binds no
   tenant** (`deps.py:299-300`): this route is **identity-only by
   construction** — it must never bind or assume a tenant, and any accidental
   call into a tenant-bound path fails closed with `TenantUnbound`, which is
   the boundary, not a bug. **Refusal shapes, two classes ruled apart (audit
   B4):** outcome refusals answer **200 `{"admit": false, "code": …}`** —
   `signin.py:85-88`'s idiom, "the refusal is the ANSWER" — with exactly three
   codes: `SignupDisabled` (flag off) · `ConsoleUnavailable` (unwired or
   Console unreachable, reusing CP-2b's copy) · `AlreadyMember` (step-0's
   branch) · **`SlugTaken`** (step-1's slug-unavailable branch, audit blocker
   1). SHAPE violations (a body email, org, or `deployment_label`) answer
   **400, never ignored**. `errorCopy.ts` gains `SignupDisabled`,
   `AlreadyMember` and `SlugTaken`; `ConsoleUnavailable` is reused. **Homes, named (audit B2
   — the same blocker CP-2b's first audit raised):** the Console-provision
   client is a NEW FUNCTION inside `packages/acb_auth/acb_auth/console_resolve.py`
   — the ONE Console httpx client and the only reader of
   `CUSTOMER_CONSOLE_URL`/`CUSTOMER_CONSOLE_DEPLOYMENT_KEY` (`is_wired()`,
   `:232-243`); a second client would be the CLAUDE.md §5 defect by name. The
   single-importer AST fence `test_console_dependency_boundary.py:224` grows
   its allow-list to EXACTLY two named importers — `routes/signin.py` and the
   new `routes/signup.py` — with the original reason restated (resolve behind
   `resolve_access` = farmable seat burn; both allowed importers are
   session-email-only routes).

   ⚠️ **ORCHESTRATION ORDER — the three-blocker cluster the slice-2 audit
   returned NO-GO on (2026-08-19), resolved here. Agent-proposed default
   (D16/D17 class — owner may overrule).** The audit's finding: *"what the two
   planes do when an email or slug already exists"* was undefined, and Console-
   first (the earlier draft's order) leaves a **dangling Console org** whenever
   the tenant plane then permanently refuses. The fix has one idea — **put the
   plane that holds the hard "one email, one org" guard FIRST, so a permanent
   refusal writes nothing anywhere** — and it collapses all three blockers:

   ⚠️ **This block's mechanism was CORRECTED 2026-08-20 (CP-2c slice 2's
   RE-AUDIT, which returned NO-GO on the first remediation's false premise):**
   the first remediation claimed 179 *"refuses slug taken by another org"* on
   the tenant plane — **it does not.** `provision_org_owner`
   (`179:222-229`) refuses only *email-belongs-elsewhere*; a FRESH email
   claiming an EXISTING slug is silently made a co-owner (the slice-1 P0, tenant
   side). So the tenant guard the reorder relies on **had to be built** — it is
   now **MT-1j slice 7** (`saas_multitenancy.md` §11; migration 180 +
   `provision_org_owner` create-only guard + `access.org_owner_of` +
   `provision_local_organization` raising two DISTINCT catchable types),
   **owned there because it fixes 179's own invariant** (D46.6 one-seam-one-owner)
   and **a HARD PREREQUISITE that lands before this slice**. With slice 7 in
   place the mechanism below is real; without it, do not build this route.

   **Step 0 · a READ-ONLY pre-flight on the tenant plane** — TWO classifying
   reads, both plain identity reads (not `resolve_identity`/`resolve_access`,
   which burn seats and sit behind the two-importer fence; not a Console call):
   **(0a)** `acb_auth.access.membership_of(email) -> (org_slug,
   org_display_name) | None` — does this session email already have an
   `app_user` account? (`app_user` is globally unique on `lower(email)` since
   162.) Present → **`200 {"admit": false, "code": "AlreadyMember",
   "org_name": …}`**, naming only the caller's OWN org (no third-party oracle —
   done-when 3), the name read from the tenant `organization` row the helper
   joins. **(0b)** `acb_auth.access.org_owner_of(slug) -> owner_email | None`
   (slice 7's read) — is the requested slug already owned by someone who is not
   this email? → **`200 {"admit": false, "code": "SlugTaken"}`** (audit blocker
   1 — "choose a different organization name"; the fourth outcome code, in
   `errorCopy.ts`). Both classify BEFORE any write, so the browser never depends
   on parsing a raised DB error.

   **Step 1 · the TENANT plane, FIRST (was step (c)) — the hard guard.** Call
   slice 7's now-guarded seam `provision_local_organization(slug, display_name,
   owner_email=<session email>, …)` (this route must not invoke 179's SQL
   directly — one seam, not two). Slice 7's guard makes the two permanent-refusal
   causes — *email belongs elsewhere* and *slug owned by another* — raise on the
   FIRST write with **nothing on the Console**, as two DISTINCT catchable types
   (`OwnerBelongsElsewhere` → `AlreadyMember`, `SlugOwnedByAnother` →
   `SlugTaken`). Step 0's pre-flight classifies the common case cleanly; step 1's
   typed raises are the **TOCTOU-safe backstop** for a signup that raced its own
   pre-flight — mapped to the SAME two codes (never a raw-prose parse), so a race
   is indistinguishable from the pre-flighted case at the wire, and a resubmit
   converges. A raise that is NEITHER typed cause is a genuine error →
   `ConsoleUnavailable`/500, not a false `SlugTaken`.

   **Step 2 · the CONSOLE plane, SECOND (was step (a)) — the registry mirror.**
   `POST /orgs/provision` against the Console **deployment-key arm** (✅ slice 1,
   2026-08-19: registry org + GST fields + trial + resumable `provisioning_run`
   + owner membership + `org_placement`), authenticated by the deployment
   credential whose capability set is `{resolve, provision}` (the D43-2 answer;
   issuing/expanding a real key stays §8 gate 7). **For a fresh tenant-born slug
   the Console arm cannot permanently refuse**: its create-only guard
   (`store.org_owned_by_other`, slice 1) rejects only an org already owned by a
   DIFFERENT identity, and this slug's owner is the same session email the
   tenant plane just made owner — so the guard passes. The only Console failures
   left are **transient** (unwired / unreachable / 5xx) → **`ConsoleUnavailable`**,
   and a **resubmit converges** because both planes are idempotent on the slug
   (179's header: *"the natural key is what a retrying signup form resends"*) and
   the Console's `provisioning_run` covers its own step-resumability. **The
   atomicity contract (audit blocker 3), true only WITH slice 7's tenant guard:**
   the ONLY divergent window is *tenant committed, Console pending* after a
   transient Console failure — the org works dark immediately (the tenant
   `app_user` admits sign-in while the resolve flag is OFF), and the Console
   catches up on the next resubmit; there is no window in which a **permanent**
   refusal leaves a half-built org on the OTHER plane, because slice 7 makes
   every permanent refusal (email-elsewhere, slug-owned-by-another) raise on
   step 1 before step 2 runs. Done-when 5 parametrizes both: a
   transient-Console-failure resubmit converges to one org/one owner on both
   planes; a permanent-refusal attempt (either cause) refuses with nothing
   written on EITHER plane.

   ⚠️ **Adjudicated 2026-08-19 (D46.6, after slice 4's NO-GO
   audit): the provision route has TWO arms, and each ticket owns exactly
   one.** MT-1j slice 4 ships the **operator arm** — the operator names the
   deployment via a required `deployment_label`; provisioning never MOVES a
   placement (same-label re-run no-ops, different-label is 409); the
   sole-deployment heuristic forbidden by name. CP-2c builds the
   **deployment-key arm**: the key IS the deployment identity, so this arm
   carries NO `deployment_label` — presenting one is **400, never ignored**
   (the same R11 rule as the resolve arm's `org_slug`) — and every placement
   write semantic is inherited from slice 4 unchanged.
   ⚠️ **D46.6 item 1 is AMENDED here, in writing (audit B1) — the model
   consequence nobody had stated:** a two-arm route cannot keep
   `deployment_label` required AT THE MODEL (`main.py:135`), so
   `ProvisionRequest.deployment_label` becomes `str | None = None` on the
   **`ResolveRequest.org_slug` precedent** (`main.py:154-171`): optionality
   lives on the model, BOTH arms' rules live in the handler, and the operator
   arm's missing-label answer moves **422 → 400**. This deletes the
   Pydantic-refuses-before-the-handler property that slice 4's
   mutation-proved fence
   (`test_customer_console_lifecycle.py:332-374`,
   `test_nothing_infers_the_deployment_from_there_being_exactly_one`) rests
   on — so THAT FENCE IS AMENDED IN THE SAME PR, red-first: it pins **400**
   and re-proves no-inference through the handler path (the `count(*)=1`
   mutation must still go red), and the model docstring (`main.py:130-134`)
   is rewritten. Ruled here, not by an implementer in silence — amending an
   adjudicated item is exactly the "one seam, two owners" failure D46.6
   exists to prevent.
   *(The Console arm's own `org_placement` write, and its 404/409 label refusals,
   are MT-1j slice 4's ✅ built behaviour — the deployment-key arm carries NO
   `deployment_label`, so those label refusals cannot arise on this route; the
   arm places the org on the KEY's own deployment. The step-0/1/2 ordering block
   above supersedes the earlier "(a) Console first → (c) tenant last" wording —
   the reorder IS the blocker-3 fix.)*
5. **Outcome**: the first user is owner on both planes (Console
   `org_membership.role='owner'`; tenant `owner` role, `app_user` active) and
   is redirected into the app. While `CUSTOMER_CONSOLE_RESOLVE_ENABLED` is
   OFF, the tenant-side `app_user` row is what admits them
   (`deps.py:261-294`) — **the flow works dark**; the resolve flip adds
   registry enforcement, it does not enable signup.
6. **The CP-2b interaction this ticket must amend — the row is AUTHORED in
   §6(j) by this remediation (audit B3: the first draft cited a row that did
   not exist and claimed an unenumerable "only usable surface"):** once the
   resolve flag is armed, an unknown email's resolve returns the EMPTY
   outcome, which the gateway maps to `admit=False, code=ACCESS_DENIED`
   (`console_resolve.py` row iv) — refusing the very session
   signup needs. **The limbo *decision* is the `signIn` CALLBACK's, Next-side,
   and `console_resolve.py` reads no signup flag** — but the callback keys on a
   purely-additive gateway signal, so the gateway DID change additively (slice-3
   repair, §6(j) row vii): under `SELF_SERVE_SIGNUP_ENABLED === "true"`, the
   callback returns `true` when the gateway's `signup_eligible` is True — the
   zero-org outcome ALONE, NOT every `AccessDenied` refusal — a session with no
   org, **nothing cached** (row iv's cache-nothing rule intact) — and the
   ordinary redirect lands the user where `/signup` is reachable. A
   suspended/seat-cap refusal carries `AccessDenied` too but `signup_eligible`
   False, so it stays refused. **No new gating
   claim is made**: `AccessGate.tsx:10-12` is presentation by its own header;
   the enforcement boundary stays the gateway's per-request access resolution
   plus fail-closed tenant binding (item 4's `TenantUnbound` note). See
   §6(j) row **vii**.
7. **Flag: `SELF_SERVE_SIGNUP_ENABLED`** — minted here (exists nowhere,
   verified); the exact-string-`"true"` idiom of `auth.ts:163`; default unset
   = OFF; **exactly three readers, two containers — named because CP-2b's F1
   defect was precisely an accidental fourth reader in the wrong container
   (audit B3):** (i) the `/signup` Next page (redirects to `/signin` when
   off), (ii) the `signIn` callback's limbo branch (item 6), both reading the
   Next env; (iii) the gateway route (answers `SignupDisabled` when off),
   reading its own settings field from the gateway env. Each fails closed
   independently; the deploy note must set BOTH env files
   (`/opt/acb/app/.env` and `workbench/control_plane/.env.local`) or the
   halves disagree — stated here so the flip is one documented act.
   `acb_auth/console_resolve.py` does NOT read this flag — the gateway's
   resolve path is byte-identical under both positions.

**Done when** *(each clause names its fence; R8 binds every SQL clause —
real Postgres on both ladders, 0 skips, both shells; red-first everywhere;
mutation testing on auth/tenancy clauses)*:

1. ✅ **MET — slice 4, 2026-08-20.** `/signup` **redirects to `/signin`** when
   the flag is not exactly `"true"` (one behaviour, everywhere — the first draft
   said "404s" in one place and "redirect" in another; redirect is the ruling,
   audit B4a) and renders under it. Fence: `src/app/signup/signup.test.ts` pins
   both positions (the `signin.test.ts:209` source-pin pattern — the page is
   `force-dynamic`, derives providers from `configuredProviders`, redirects when
   the flag `!== "true"` and renders `<SignUpForm>` under it), shown red-first
   against the redirect branch removed. `next-auth`/`next/navigation` cannot load
   in this tree's node-env vitest, so the flag-off REDIRECT and flag-on RENDER are
   pinned by SHAPE and the behaviour is the reviewer's manual gate.
2. ✅ **MET — slice 2, 2026-08-20.** `POST /signup/provision` answers
   **200 `{"admit": false, "code": …}`** for the FOUR outcome refusals —
   `SignupDisabled` · `ConsoleUnavailable` · `AlreadyMember` · **`SlugTaken`**
   (the Console/tenant "slug unavailable" mapping, audit blocker 1) — and
   **400** for shape violations (body email/org/label), per item 4's two-class
   ruling. Fence: `test_signup_provision_route.py::TestTheOutcomeCodes`,
   `::TestStepOnesTypedRaisesAreTheBackstop`, `::TestTheShapeClasses` — every
   code and both classes; red-first, and the flag/R11/exception-mapping
   mutations killed.
3. ✅ **MET — slice 2, 2026-08-20.** Already-a-member → `AlreadyMember` names
   only the caller's own org (from step-0a's `access.membership_of`, `org_name`
   on the wire); a slug already owned by another org → `SlugTaken` (from
   step-0b's `access.org_owner_of`, slice 7); **neither leaks any third-party
   existence beyond the bare "this name is unavailable"** (both classify on the
   tenant plane before any write — the Console never sees the slug in the
   refusal case, so no cross-deployment oracle). Fence: `test_signup_provision_
   route.py::TestTheTwoOrgClassification`, the two-org R8 case — one org owned
   by A, A's own session gets `AlreadyMember` naming A's org, a stranger's
   session attempting A's slug gets `SlugTaken` naming nothing, and the Console
   is asserted NEVER reached. **Prereq MT-1j slice 7 is merged** (`org_owner_of`
   + the create-only guard both present).
4. ✅ **MET — slice 2, 2026-08-20 (slug shape gate added at landing).**
   Registered state required (**400 `MissingState`** at the route), GSTIN
   optional but structurally valid when present (**400 `InvalidGstin`**, the
   item-3 regex), and both values land on the Console org row. Slug shape
   validated at the route BEFORE step 0/1/2 (**400 `MissingSlug`** for
   missing/blank/whitespace, **400 `InvalidSlug`** for a non-DNS-label-safe
   value, item-3's `fullmatch` regex) — the slice-2 P2 repair: a blank slug had
   reached `provision_local_organization("")` and surfaced as a false **200
   `ConsoleUnavailable`**, and `"a b"` reached the join key unvalidated. Fences:
   `::TestTheShapeClasses` red-first for all four 400s (the slug fences show the
   blank case returning `ConsoleUnavailable` and the spaced case 200-admitting
   before the gate); the client-threading test (`::TestTheConsoleProvisionClient`
   — no `deployment_label`, GST fields on the wire) plus `::TestGstLandsOnThe
   ConsoleOrgRow` (R8 against the Console ladder, the deployment-key arm) for
   the landing.
   ✅ **4a MET — 2026-08-24 (MT-1f slice 1's branch).** A **reserved** slug is
   **400 `ReservedSlug`**, refused at the route after the `_SLUG_RE` shape check
   and before step 0/1/2 (item 3's reserved-label block; owner ruling B7). Fences:
   `::TestTheReservedLabels` in `test_signup_provision_route.py` (hermetic,
   red-first — `api` provisioned an organization before the gate, and the Console
   is asserted never reached), plus the cross-language parity fence
   `tests/unit/test_subdomain_host_vocabulary.py`, which reads
   `RESERVED_LABELS` out of `src/lib/subdomain.ts` and pins the gateway's set
   equal to it.
5. ✅ **5b MET · 5a MET at plane level; the missing route-level convergence is
   deferred to CP-2e (an out-of-band sweep, not a route retry) —
   slice 2, 2026-08-20. The two-plane orchestration converges or
   refuses cleanly (audit blocker 3), parametrised over BOTH directions**
   (built — `pr-check.yml` provisions both Postgres services; MT-1j slice 7
   merged): **(a)** a TRANSIENT Console failure after the tenant commit leaves
   the tenant org created and a resubmit converges to one org / one owner on
   both planes (idempotent-on-slug — proved on the tenant ladder by
   `::TestTheTenantPlaneConverges` and on the Console ladder by
   `::TestTheConsolePlaneConverges`); **(b)** a PERMANENT refusal (email belongs
   elsewhere, OR slug owned by another org — both forced through slice 7's typed
   raise on step 1 by a pre-flight-miss stand-in for the TOCTOU race) leaves
   **nothing written on EITHER plane** — asserted directly on the tenant ladder
   (`::TestTheStepOneBackstopWritesNothing`: the pre-existing org's ownership
   unchanged, the racing new org rolled back, no new tenant owner for the
   signer) and the Console never reached. ⚠️ **Resolution of the "resubmit
   converges on both planes" wrinkle:** on a literal route resubmit step 0a's
   `membership_of` now returns the caller's own org (the tenant plane created
   it) and short-circuits to `AlreadyMember`, so convergence is asserted at the
   PLANE level — the idempotence of BOTH provision operations on the slug, which
   is exactly what "idempotent-on-slug" names — rather than by replaying the full
   route. The org works dark meanwhile (the tenant `app_user` admits sign-in
   while the resolve flag is OFF). ⚠️ **NAMED KNOWN GAP (verify finding,
   2026-08-20), because "works dark meanwhile" is not "converges":** a transient
   Console failure DURING signup leaves the org live on the **tenant** plane but
   **never mirrored to the Console** — a literal route resubmit short-circuits to
   `AlreadyMember` at step 0a, so nothing in slice 2 re-drives step 2. The org is
   un-metered / absent from the billing registry until reconciled, and **no
   reconciliation path exists in this slice.** It cannot bite until BOTH the
   Console is deployed (D47, owner-gated, not done) AND signup is flipped on AND a
   Console failure races the tenant commit — so it ships dark, but it is a real
   window, not a non-issue. **Its closer is `CP-2e` — the signup Console-mirror
   reconciler, ◐ BUILT 2026-08-20, see the CP-2e ticket above** (an out-of-band
   SWEEP over tenant orgs whose Console mirror is missing plus a new
   `organization.console_mirrored_at` marker, re-driving `provision_org_on_console`
   idempotent-on-slug; owner-decided mechanism, agent-safe to BUILD, owner-gated
   to RUN). NOT built here; named so the dark-ship is honest rather than silent.
   ⚠️ This is CP-2c's flow-level test;
   CP-2a's own per-step `provisioning_run` kill test stays OWED under CP-2a and is
   not absorbed silently.
6. ✅ **MET — slice 1, 2026-08-19.** A `{resolve}`-only deployment key calling
   provision is refused and the refusal is logged; a `{resolve, provision}` key
   provisions. Fence: Console
   R8 suite, red-first by issuing the narrow key. **No Console migration is
   needed and none may be minted for this** — `deployment_key.capabilities`
   is `TEXT[]` with no CHECK (`006_deployment_key.sql:56`), so the wide set
   is insertable today; the enforcement is `deployment_or_operator(capability)`
   (`auth.py`), already generic, registered via
   `AUTHENTICATING_DEPENDENCIES`. *(Built as specified: `PROVISION_CAPABILITY`
   beside `RESOLVE_CAPABILITY`, `_provision_dependency` from the same factory,
   registered — and the registration is mutation-proved: removing it makes the
   clause-1 fence report `{'/health', '/orgs/provision'} == {'/health'}`.
   Fences in `test_customer_console_lifecycle.py::
   TestTheDeploymentKeyProvisionArm`, seven R8 tests. Zero migrations.)*
7. ✅ **MET — slice 3, 2026-08-20, NARROWED to zero-org-only after a P1**
   (BUILD+TEST half only; the live `SELF_SERVE_SIGNUP_ENABLED` flip stays §8
   gate 8). §6(j) **row vii**: under the signup flag, the `signIn` callback
   admits an org-less session ONLY for the genuinely org-less person, caching
   nothing; flag unset/other value → row iv verbatim, byte-identical to today.
   *(Built as repaired: the branch `answer?.signup_eligible === true &&
   process.env.SELF_SERVE_SIGNUP_ENABLED === "true"` → `return true`, inserted
   after the admit check and before row iv's redirect; the callback caches
   nothing by construction. `SELF_SERVE_SIGNUP_ENABLED` is a SEPARATE Next-side
   env from CP-2b's `CUSTOMER_CONSOLE_RESOLVE_ENABLED`, read only here.*
   ⚠️ **The funnel keys on the gateway's load-bearing `signup_eligible` signal,
   NOT on the `AccessDenied` code.** The first slice keyed on
   `code === "AccessDenied"` alone, which review rejected as a P1: that code is
   also carried by a SUSPENDED/non-paying org (`console-refused`, `cache-dead`)
   and a seat-capped one (`console-error`), and because registry suspension is
   enforced ONLY at the resolve door, funnelling those into an org-less session
   would hand a non-paying customer back full access on re-sign-in. So
   `console_resolve.ResolveDecision.signup_eligible` was added — True on the
   zero-org `console-empty` return ALONE, set directly there (never inferred
   from `source`, which stays log-only) — and `gateway/routes/signin.py` passes
   it through. **The wider "admit every `AccessDenied` refusal" reading is
   REJECTED-for-security, not settled**: it was a surfaced agent-proposed
   default that the OWNER may overrule with an explicit directive, but the
   shipped behaviour keeps suspended/seat-cap orgs refused. **This done-when
   DOES touch the gateway additively** — `console_resolve.py`, `signin.py` and
   `test_deployment_resolve_cache.py` all changed; the earlier claim that
   "nothing gateway-side changes / `test_deployment_resolve_cache.py` untouched"
   is corrected here. `console_resolve.py` still reads NO signup flag; the admit
   decision stays the callback's. Fences: `signin.test.ts` (positive
   `signup_eligible === true` trigger + a NEGATIVE pin that
   `answer?.code === "AccessDenied"` is absent from the guard, so a
   suspended/seat-cap refusal cannot admit) AND — because the security half must
   RUN, not just be shape-pinned — `test_deployment_resolve_cache.py::
   TestSignupEligibleIsZeroOrgOnly`, an R8 suite that proves against the LIVE DB
   that `console-empty` is the only `signup_eligible=True` outcome while
   `console-refused`, `cache-dead` and `console-error` are all False; plus the
   three route-dict updates in `test_signin_resolve_route.py`.)*
8a. ✅ **MET — 2026-08-24 (MT-1f slice 1's branch; wording corrected the same day in
   repair round 1).** `/signup` reached with **no session redirects to `/signin`**,
   from the page itself. The check is a server-component **`await currentIdentity()`**
   — the same `lib/gateway.ts` seam the `/api/signup` hop's `requireIdentity()` sits
   on, so "may this render" and "will the submit work" cannot drift, and it carries
   the laptop bypass — placed beside the flag gate and ordered **flag first, session
   second** (an un-opted-in box must not disclose that the surface exists behind a
   sign-in). ⚠️ **Not `await auth()`**: this clause said so until 2026-08-24 and the
   shipped code never did; `signup.test.ts` asserts the page contains **no**
   `await auth()` and no `from "next-auth"` import, so the old wording described a
   shape its own fence forbids.
   ⚠️ **It is DEFENCE IN DEPTH, not the repair of a reachable dead end** — the
   second correction. The earlier motivation ("a signed-out visitor used to be shown
   a four-field form and told nothing until submit") is **false**: `/signup` is
   absent from `proxy.ts`'s `PUBLIC_PAGES`, so the proxy already 307'd a signed-out
   page navigation to `/signin` and the form was never reachable without a session.
   What the check buys is that the guarantee no longer depends on a set in another
   file — adding `/signup` to `PUBLIC_PAGES` is a one-line edit somebody will make
   the day the marketing CTA lands. The form's `needsSignIn` arm stays for the case
   neither gate can see: a session that expires *between render and submit*.
   Fence: `signup.test.ts` — a source pin on both gates, their order, and the
   negative `await auth()` pin; `signin.test.ts:209`'s idiom, because
   `next-auth`/`next/navigation` cannot load in this tree's node-env vitest; the
   behaviour is the reviewer's manual gate.
8. No new engine sites (R5(b)); all tenant writes through slice 4's seam
   function; **zero migrations on BOTH ladders** (tenant: nothing to add;
   Console: done-when 6's no-CHECK fact — if something genuinely needs one,
   next free numbers at build time per R1, and the PR must argue why).
   ◐ **Console half MET — slice 1, 2026-08-19**: no migration on either ladder
   and no new engine site (the arm reuses the route's existing
   `get_engine().begin()`; `test_db_engine_seam.py` 31/31 unchanged). The
   tenant half rides slice 2, which is the ticket that first calls the seam
   function.

**Gates** (registered in §8 as gate 8 and in `work_plan.md` §6(h)):
setting `SELF_SERVE_SIGNUP_ENABLED=true` on a live deployment — it opens org
creation to the public internet and is the productized version of §6(f)'s
availability trade · expanding a REAL deployment key's capability set (gate
7's class) · deploying the Customer Console anywhere (§8 gate 2 — **this
ticket cannot go LIVE before that owner decision**,
`customer_console_infrastructure.md`) · H3's RLS promotion before a real
org #2 exists (D43-3 — same execution gate as MT-1j). Building everything
against fixtures, both flag positions fenced, is AGENT-SAFE.

**Non-goals**: email/password auth, email OTP, TOTP 2FA (**CP-2d**, below) ·
the marketing page (WS-33) · payment at signup — a self-serve org starts on
CP-2a's trial; seats/credits purchases are in-app afterwards (WS-30 SC-4a,
owner's point 3) · the multi-org chooser · certified deletion (CP-2a's open
half) · invite emails (`members.py:149` — its own small ticket) · per-tenant
subdomains (MT-1f).

**Dependencies, in dispatch order**: **MT-1j slice 4** (the Console↔tenant
seam INCLUDING the `org_placement` hole — step 4(b) above IS that seam, and
**slice 4 lands FIRST — adjudicated, D46.6**: neither ticket absorbs the
other's half. Slice 4 owns the operator arm, the placement write and the
tenant seam function; CP-2c owns the deployment-key arm and the form. The
earlier "or this ticket absorbs it explicitly" escape hatch is struck — it
was how one seam got two owners. ✅ **slice 4 MERGED 2026-08-19**) → CP-2c
build (agent-safe, dark) → the owner's live set (Console deployed · key
issued with `{resolve, provision}` · `SELF_SERVE_SIGNUP_ENABLED` ·
optionally the resolve flip).

**CP-2c dispatches as FOUR ordered slices, never as one** *(auditor-ruled
2026-08-19: four surfaces, two languages, two databases, a new credential
capability and an amendment to a shipped auth path — CP-2b was strictly
smaller and took two slices and 14 blockers)*:
1. ✅ **Slice 1 — the Console deployment-key provision arm — BUILT 2026-08-19**
   (done-whens 6 + 8's Console half; carries the D46.6-item-1 amendment: model
   → `str | None`, both arms' rules in the handler, operator missing-label
   422→400, slice 4's no-inference fence amended red-first in the same PR).
   **What shipped:** `auth.PROVISION_CAPABILITY` + `_provision_dependency`
   (the same `deployment_or_operator` factory — unchanged, it was already
   generic) + the `ProvisionCaller` alias, **registered in
   `AUTHENTICATING_DEPENDENCIES`**; `ProvisionRequest.deployment_label` →
   `str | None = None` with both arms' rules in the handler (operator: label
   required, **400** when absent, 404 unknown; deployment key: the box is
   `caller.deployment_id` and a presented label is **400, never ignored**,
   refused on SHAPE before the value is read so naming its own box and naming
   a nonexistent one are one refusal). Every placement-write semantic is
   inherited from MT-1j slice 4 unchanged. **Zero migrations on either
   ladder, zero new engine sites.** Evidence: **409 passed / 0 skipped** across
   §7's ten Console suites on a real Postgres 16 (baseline 402, +7 new), 31/31
   on `test_console_dependency_boundary.py` + `test_db_engine_seam.py`, 89/89
   on the two tenant-ladder suites; every fence red first (7 new ERROR on the
   capability not existing, the amended clause (a) `assert 422 == 400`, the
   clause-1 fence `assert 401 == 403`); **six mutations, all killed** —
   capability check disabled (3 red), registration removed (the clause-1 fence
   reports `{'/health', '/orgs/provision'}`), the `count(*) = 1` fallback in
   `deployment_by_label`, a WORKING sole-deployment guess in the handler
   (the amendment's own risk — clause (a) still bites), the refusal log
   removed, and a presented label ignored rather than refused. Ruff parity
   with the branch point: identical findings by file and rule.

   **Two agent-proposed defaults, the D16/D17 class — owner may overrule
   either.** (i) *"the refusal is logged"* had no event name in the spec:
   `deployment_or_operator` raised a bare 403 and logged nothing, so a key
   reaching for a capability it was not issued for was unobservable. The name
   chosen is **`deployment_key.capability_refused`**, at **warning** (a refusal
   is the door working — the level `payments.webhook_refused` takes), carrying
   `capability` and `key_prefix` and **never the secret**, which the fence
   asserts over the whole captured log text rather than trusting. It fires for
   *both* capabilities: the resolve arm's behaviour is unchanged (same status,
   same body) and only gains the line it never had. (ii) The audit row's
   **`actor` distinguishes the two arms** — `"deployment"` under the key,
   `"operator"` unchanged — with the deployment recorded by id in both arms and
   the acting key's prefix beside it. `_audit`'s own contract is the argument:
   a trail calling every act "operator" *"would misattribute the one class of
   write we most need to tell apart later"*, and a box provisioning a
   self-serve customer is exactly that class — the one provisioning act with
   no human in it.

   **Inherited unchanged, by name:** `resolve_deployment_key` and
   `deployment_by_label` still ignore `deployment.status`, so provisioning
   onto a `retired`/`draining` box 200s. That is the WS-29 slice-4 reviewer's
   P2-1, recorded as its own ticket, and slice 1 deliberately does not fix it —
   it is now reachable by one more door, which raises its priority without
   changing its owner.

   **Independent verify (2026-08-19): FAIL-then-repaired, doc sweep only —
   zero functional defects.** The verifier re-derived every clause on FRESH
   scratch databases with its own 45-check probe and its own minted keys
   (45/45), replayed seven mutations (all killed, including its own novel
   "key arm HONOURS a presented label" plant), matched every count exactly
   (409/0 · 31/31 · 89/89 · ruff identical by file and rule), and confirmed
   the mutation pair covers disjoint paths (the lookup-side sole-deployment
   fallback is unreachable once the handler 400s — which is why the
   handler-side mutation exists). The FAIL: **eight sentences across the two
   owning specs still described the pre-slice-1 world** (five in
   `saas_multitenancy.md` §11 including the "Until that PR" conditional this
   very PR was named to close; three "exactly `{resolve}`" claims in this
   file) — all swept in the repair commit, dated in place. **Two verifier
   observations, recorded not fixed:** (i) an explicit JSON `null`
   `deployment_label` under a deployment key answers 200 — judged correct
   (a `null` asserts no identity, so R11's hazard does not arise and nothing
   is ignored) but unfenced and the prose does not distinguish it; (ii) a
   `{provision}`-only key is unspecified — it could create an org it can
   never resolve; harmless while issuance is gate 7's owner act, and the
   issuance surface ticket should refuse or warn on that set.

   **Adversarial review (2026-08-19): one P0, one P1, one P2 — all repaired
   before merge; the P0 was a live privilege-escalation the 409/0 suite and
   the independent verify both missed.** **P0 — the deployment-key arm could
   HIJACK an existing org.** It inherited slice 4's idempotent-on-slug
   re-provision wholesale, so a `provision` key POSTing an *existing* slug with
   a different `owner_email` was granted an `owner`/`active` membership and a
   Core seat in a stranger's org — a 200 (reviewer measured it live). Repaired
   at the CONSOLE door (R11 — the slug is user-supplied from an unauthenticated
   signup form in slice 2, so the gateway cannot be trusted to guard it): the
   deployment-key arm now **creates only, never joins** via new
   `store.org_owned_by_other` (keys on ownership, `CITEXT`-safe) — an org owned
   by a DIFFERENT identity is refused writing nothing; the same-owner retry and
   the crash-before-membership resume (org with no owner yet) both still
   complete. **P1 — the 409/200 split was an existence oracle** over the global
   slug namespace (`§5`'s forbidden "existence of an organization this
   deployment does not serve"). Repaired: the key arm's placed-elsewhere AND
   owned-by-other cases collapse to ONE bare **`409 "slug unavailable"`** naming
   no placement/owner/deployment. *(Agent-proposed default, D16/D17 — owner may
   overrule. Residual flagged honestly: slug **availability** stays observable
   at any signup-provision door because global-unique-slug is a hard constraint
   a form must report; this narrows the oracle to "taken: yes/no" and removes
   the placement/ownership discrimination.)* The operator arm's 404/409
   (cross-org staff, by design) is **unchanged**. **P2** — the ninth stale
   sentence (`saas_multitenancy.md` §11 slice-4 item 4 still called the arm
   "CP-2c's and unbuilt") — swept. Noted repairs: two test docstrings that said
   `deployment_label` is "REQUIRED" (true only under the operator arm now); and
   `owner_email` added to the `org.provision` audit detail so a create is
   legible in the trail (a hijack now cannot exist to be confused with one — it
   is refused before any audit row — but the field names WHO owns what, by
   which credential). **Repair verified:** 444 passed across the ten Console
   suites + the two boundary fences (0 skipped, real PG 16, up from 409 — four
   new red-first fences: hijack-refused-writes-nothing, same-owner-idempotent,
   ownerless-resume-completes, uniform-refusal-reveals-nothing); the ownership
   guard mutation-proved (supervisor re-ran it: disabling
   `org_owned_by_other` re-opens the 200 hijack and reddens both the P0 and P1
   fences); ruff parity with the branch base.
2. **Slice 2 — gateway `POST /signup/provision` + the Console-provision
   client in `console_resolve.py`** (done-whens 2, 3, 4, 5; the
   two-importer fence amendment). ⚠️ **Dispatch-audited NO-GO 2026-08-19 →
   REMEDIATED same day** (three blockers, one cluster — "what the two planes do
   when an email or slug already exists"): the orchestration is now **tenant-
   plane FIRST** (step 0 read-only pre-flight `access.membership_of` →
   `AlreadyMember`; step 1 tenant seam = the hard one-email-one-org guard, its
   refusals leave nothing on the Console; step 2 Console mirror, which cannot
   permanently refuse a fresh tenant-born slug), `SlugTaken` is the fourth
   outcome code, and done-when 5 parametrizes both convergence and clean-refusal
   directions. ⚠️ **RE-AUDIT 2026-08-20 returned NO-GO on the first
   remediation's false premise** (179 does NOT refuse slug-owned-by-another —
   the tenant plane would HIJACK, the slice-1 P0's tenant twin). Corrected: the
   tenant create-only guard is **MT-1j slice 7** (`saas_multitenancy.md` §11;
   migration 180 + typed raises + `access.org_owner_of`), a HARD PREREQUISITE
   that lands BEFORE this slice; step 0 now does TWO pre-flight reads
   (`membership_of` → `AlreadyMember`, `org_owner_of` → `SlugTaken`). **Dispatch
   order: MT-1j slice 7 → CP-2c slice 2.** Re-audit slice 2 after slice 7 merges.
3. **Slice 3 — the `signIn` callback limbo branch** (done-when 7; §6(j) row
   vii is already authored — the build is the callback branch + its
   `signin.test.ts` pins).
4. ✅ **Slice 4 — the `/signup` form UI — BUILT 2026-08-20** (done-when 1 +
   done-when 4's client half; BUILD+TEST only, dark). A server `page.tsx`
   (`force-dynamic`, providers from `configuredProviders`) redirecting to
   `/signin` off-flag and rendering the dark client `SignUpForm` on it (org name ·
   slug · registered state · optional GSTIN); a `/api/signup` Next hop forwarding
   ONLY the four fields with the session identity (R11 — never
   email/org/deployment_label), because the gateway's provision route is
   BFF-internal and session-email-only; outcome codes through the ONE `errorCopy`
   seam; DESIGN_SYSTEM primitives only (`Input`/`Select`/`Button`/`Icon`,
   conformance-clean). Fence `signup.test.ts` (both flag positions + the three
   signup codes + the hop's forbidden-key drop, red-first);
   `test_signup_provision_route.py` unchanged and green. The live
   `SELF_SERVE_SIGNUP_ENABLED` flip stays §8 gate 8.

**CP-2e · The signup Console-mirror reconciler — the closer for CP-2c
done-when 5's NAMED KNOWN GAP.** ◐ **BUILT 2026-08-20 (this section — build +
R8 fence; RUN/deploy/key/flag stay owner-gated).** CP-2c done-when 5 ships a real
dark window (verify finding,
2026-08-20): a **transient** Console failure DURING signup leaves an org live
on the **tenant** plane but **never mirrored to the Console**, and step 0a's
`access.membership_of` → `AlreadyMember` short-circuits any literal route
resubmit (`signup.py:231-234`), so nothing in slice 2 re-drives step 2. The org
is **un-metered / absent from the billing registry until reconciled, and no
reconciliation path exists**. This ticket is that path. The gap cannot bite
until the Console is deployed (D47, §8 gate 2, not done) AND signup is flipped
(§8 gate 8) AND a Console failure races the tenant commit — so it is built dark,
like the flow it repairs.

**Mechanism — owner-decided 2026-08-20, DO NOT RE-OPEN: an out-of-band SWEEP
plus a marker column.** Deliberately NOT a retry-that-does-not-short-circuit
inside the signup route: re-entering the two-plane orchestration would re-touch
the tenant create-only guard for an org that already exists on the tenant plane,
and it would couple the fix to the request path. Instead: a **callable
`reconcile` function** that finds tenant orgs not yet mirrored and re-drives the
Console mirror through the ONE existing seam
`acb_auth.console_resolve.provision_org_on_console(slug, name, owner_email, *,
gstin, billing_state)` (`console_resolve.py:515`, idempotent-on-slug, create-only
on the Console arm via slice 1's `store.org_owned_by_other`), and a **new
nullable marker column** `organization.console_mirrored_at` that both the signup
route and the reconciler set to `now()` on a Console-mirror SUCCESS and that the
sweep selects on being NULL.

**Where it lives (Place Before Building).** The reconcile function EXTENDS the
existing Console seam, `acb_auth.console_resolve` — the module that already owns
the ONE Console httpx client and `provision_org_on_console`, and already reads
the tenant plane cross-tenant through the unbound `get_session_factory` idiom
("the tenant is the ANSWER", `console_resolve.py:146-156`). Reusing that module
is mandatory, not incidental: a second Console client anywhere is root
`CLAUDE.md` §5's defect by name, and the reconciler must re-drive the SAME
create-only-guarded arm the signup route does. It recovers `owner_email` with
the SAME owner join `access._ORG_OWNER_SQL` uses (`access.py:387-395` —
`organization ⋈ org_role 'owner' ⋈ user_role ⋈ app_user`), so no second owner
grammar is minted. **No new engine site (R5(b))** — every read and the marker
write go through `get_session_factory`, the seam idiom, exactly as
`console_resolve` and `acb_common.provisioning` already do; **no tenant/identity
from request input (R11)** — the sweep takes nothing from a caller, it reads the
tenant plane and re-drives what is already persisted there.

**The trigger is a management CLI, not a gateway endpoint** — an operator-run
script under `scripts/` (e.g. `uv run python -m scripts.reconcile_console_mirror`),
a DIFFERENT job from the existing CRM `scripts/reconciler.py` (source-of-truth
diff / escalation) and never merged into it. **Justification (the choice was
between an operator/admin gateway endpoint and a CLI):** (i) the gap is itself
out-of-band, so the fix belongs off the request path — a CLI needs no new
authenticated route, no `PUBLIC_ROUTES` entry, and no operator-auth wiring, and
adds zero public attack surface for a job that never needs HTTP reachability;
(ii) RUNNING the sweep is the owner-gated act, and an on-box operator one-off is
exactly the shape of the existing `scripts/` operational convention
(`scripts/reconciler.py`, `scripts/apply_migrations.sh`); (iii) it cannot even
FUNCTION until §8 gate 2 (Console deployed) and gate 8 (a `{provision}` key
wired — the capability growth beyond `{resolve}`, §6(h), NOT gate 7's bare
issuance) are satisfied — `is_wired()` is False otherwise, so
`provision_org_on_console` raises `ConsoleProvisionUnavailable("unwired")` and
the pass is a logged no-op — so running it against a live Console is
transitively owner-gated by the same acts that gate the whole signup-live path.

**The data-availability finding, and the migration shape it forces (the critical
design question, resolved against the code on this branch).** The reconciler must
reconstruct the FULL `provision_org_on_console(slug, name, owner_email, gstin,
billing_state)` call FROM TENANT-PLANE DATA ALONE, because the Console row is
exactly what is missing. What the tenant `organization` row + its owner
role/`app_user` persist today:

* **name — RECOVERABLE.** `organization.display_name` (migration 130:40, `TEXT
  NOT NULL`, written by 179's `provision_organization` defaulting to the slug,
  `179:303-306`). The signup route passes `display_name or slug` as the Console
  `name` (`signup.py:278-284`, the arg on `:280`); `organization.display_name` is
  never blank, so it
  reconstructs that argument faithfully.
* **owner_email — RECOVERABLE.** `organization ⋈ org_role 'owner' ⋈ user_role ⋈
  app_user.email`, the `access._ORG_OWNER_SQL` join (`access.py:387-395`). A
  signup-born org always has an owner (step 1 passes the session email;
  `provision_org_owner` writes the `app_user` and the `owner` role,
  `180:129-155`).
* **gstin / billing_state — NOT RECOVERABLE. This is the blocker the migration
  must clear.** Verified: `gstin` and `billing_state` occur **nowhere** under
  `infra/postgres/` — the tenant `organization` INSERT writes only
  `slug, display_name, domain` (`179:303`), and the signup route threads
  `gstin`/`billing_state` ONLY to the Console in step 2
  (`signup.py:278-284`). So they are **lost the instant step 2 fails** — the
  exact rows the sweep needs. → **The marker migration therefore also persists
  them tenant-side**, and the signup route writes them at signup so the sweep can
  re-drive faithfully.

Resulting migration (tenant ladder, `infra/postgres/`; **number taken by listing
the directory at build time — highest on disk at spec time was
`180_org_provisioning_create_only_guard.sql` — and re-checked at merge, R1; do
NOT hardcode a number**):

```
ALTER TABLE organization
    ADD COLUMN IF NOT EXISTS console_mirrored_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gstin               TEXT,
    ADD COLUMN IF NOT EXISTS billing_state       TEXT;
```

**R6 EXPAND, forward-only, no backfill, NO DEFAULT — on migration 177's
precedent, argued not copied.** All three are nullable and default-free. On
`console_mirrored_at` NULL is **load-bearing** — it means *"this org has never
been mirrored to the Console"*, which is the sweep's whole selection predicate; a
`DEFAULT now()` would stamp every pre-existing row "already mirrored" and hide
the very gap this ticket closes (177's identical argument for `registry_status`).
On `gstin`/`billing_state` NULL means *"not supplied / pre-dates this column"*,
which is the honest state for the seeded `default` org and any pre-CP-2e row. Old
code meets new schema safely (R6's purpose): nothing running before this
migration reads any of the three columns.

**R5 source gate — satisfied by inheritance, no new EXEMPT row, no new table.**
This adds COLUMNS to `organization`, already in `scripts/gen_tenant_migration.py`'s
EXEMPT map ("the tenant list itself" — a policy that hid it from the connection
resolving *which tenant this is* would break the box's first question). A column
on an exempt table inherits the table's reason and must add no exemption entry
(177's ruling); `tests/unit/test_tenant_coverage.py`'s map is left untouched.
Zero new tables, zero new engine sites.

**The sweep predicate EXCLUDES first-party orgs** — `WHERE console_mirrored_at IS
NULL AND first_party = false`. Migration 157 backfilled the operator's own
`default` org (Fracktal Works) to `first_party = true`; it is the bootstrap org,
not a metered self-serve customer, and mirroring it into the customer billing
registry would route the first-party path through the customer Console — the
bypass shape D36.2/D36.3 forbids. Every signup-born org is `first_party = false`
by construction (the 157 default), so the predicate catches exactly them. *(An
agent-proposed refinement, D16/D17 class — the owner may overrule if Fracktal is
to be mirrored as customer-zero, but that is a deliberate separate act, never a
sweep side-effect.)*

**Where signup writes the two new values.** Additive, no signature churn on
179/180. In the signup route, **immediately after step 1** (the tenant org now
exists) and **before** step 2, a seam helper persists the GST profile on the
tenant org (`UPDATE organization SET gstin = :gstin, billing_state =
:billing_state WHERE slug = :slug`), so a step-2 failure still leaves them
recorded for the sweep. On step-2 SUCCESS the route sets the marker
(`UPDATE organization SET console_mirrored_at = now() WHERE slug = :slug AND
console_mirrored_at IS NULL`) — the **SAME marker writer the reconciler calls**,
one function, no second idiom (place it beside the tenant write in
`acb_common.provisioning`). *(Residual, stated: a crash between the org insert
and the profile write leaves `gstin`/`billing_state` NULL; the sweep still
re-drives faithfully on what persisted. ⚠️ **Known rare gap (CP-2e review,
2026-08-20):** if the step-1.5 profile write AND step 2 BOTH fail for one signup,
the tenant GST stays NULL and a later sweep FIRST-provisions the Console org with
NULL `gstin`/`billing_state` — and because the Console mirror `ensure_organization`
is create-only on those columns (`ON CONFLICT (slug) DO UPDATE SET updated_at =
now()`, never re-writing GST), the NULL is then FROZEN: there is no tenant→Console
GST-update path in this slice, so it is unrecoverable here — NOT "suppliable
later". `billing_state` drives IGST-vs-CGST/SGST, so this is a tax-correctness
gap, not cosmetic. It needs a rare DOUBLE fault, is dark (Console deployed
nowhere, signup flag OFF), and `reconcile` does not yet surface a NULL-profile
mirror count. Its closer is a follow-up ticket: a Console-side profile-update path
(so a re-drive refreshes GST on an existing row) PLUS a NULL-profile counter in
`ReconcileSummary` for observability. Folding the profile write into
`provision_organization` as two NULL-defaulting R6-expand params — one
transaction — would shrink the pre-profile window to the org insert itself, an
allowed tightening, not MVP, and only if 179's fences are re-proved in the same
PR.)*

**Done when** *(each clause names its fence; R8 binds every SQL clause — real
Postgres on BOTH ladders, 0 skips; red-first; the create-only and marker clauses
mutation-proved)*:

1. **One pass mirrors and marks.** Given a tenant org with `console_mirrored_at
   IS NULL`, `first_party = false`, and NO Console row on its slug, ONE
   `reconcile` pass calls `provision_org_on_console` with the reconstructed
   `(slug, display_name, owner_email, gstin, billing_state)`, the Console
   `organization` row EXISTS on the slug afterward, and `console_mirrored_at` is
   set non-NULL. Fence: `test_signup_reconciler.py::TestOnePassMirrorsAndMarks`,
   red-first by asserting the Console row absent + marker NULL before, present +
   set after; the reconstructed `gstin`/`billing_state` are asserted to equal
   what signup persisted (proving the tenant-side persistence carried them).
2. **A second pass is a no-op.** After clause 1 the marker excludes the org from
   the sweep SELECT, so a second pass selects zero rows and makes NO Console call
   (idempotent-on-slug is the backstop; the marker is the primary excluder).
   Fence: `::TestSecondPassIsANoOp`, asserting the Console client is invoked zero
   times on pass 2 (a `httpx.MockTransport` request count, the
   `console_resolve._new_http_client` seam).
3. **A mirrored org is never re-driven.** A row whose `console_mirrored_at` is
   already non-NULL is not selected and `provision_org_on_console` is never
   called for it. Fence: `::TestMarkedOrgIsNeverReDriven`.
4. **The reconciler never mints a second org/owner and never moves ownership.**
   By construction it performs NO tenant org/owner write — only the marker — so
   it cannot fork the tenant plane. On the Console plane it re-drives the SAME
   create-only-guarded arm: `store.org_owned_by_other` (slice 1's P0 fix,
   `main.py:794` — the Console twin of tenant migration 180's guard) refuses a
   slug already owned by a DIFFERENT identity, so a forced attempt (the Console
   seeded with the slug owned by a stranger) is refused, writes no second
   org/owner, and LEAVES THE MARKER NULL. Fence: `::TestReconcilerCannotHijack`,
   mutation-proved — disabling `store.org_owned_by_other` re-opens the hijack and
   reddens it. Because the reconciler passes the owner_email the tenant plane
   already recorded, the guard passes for the legitimate case.
5. **Signup step-2 SUCCESS sets the marker; a step-2 FAILURE leaves it NULL.** A
   clean signup leaves NOTHING for the sweep (marker set, so the SELECT skips it);
   a signup whose step-2 raises `ConsoleProvisionUnavailable` leaves the marker
   NULL AND the `gstin`/`billing_state` persisted, so the sweep picks it up and
   re-drives faithfully. Fences: `::TestSignupSuccessSetsMarker` and
   `::TestSignupConsoleFailureLeavesMarkerNull` (the latter forces a transient
   Console failure and then runs one reconcile pass to green — the end-to-end
   proof that the gap is closed). Red-first by removing the step-2 marker write
   (a clean signup then reads NULL and the sweep re-drives an already-mirrored
   org).

**The R7 fence: `tests/unit/test_signup_reconciler.py`** (R8, real Postgres, both
ladders — `pr-check.yml` already provisions the tenant and Console services for
the CP-2c suites). What makes it fail red, by clause: the Console row / marker
assertions (1), the pass-2 request-count spy (2), the marked-row exclusion (3),
the `org_owned_by_other` mutation (4), and the removed step-2 marker write (5). A
new **structural** clause is added to
`tests/unit/test_console_dependency_boundary.py` only if the reconcile function
is placed in a NEW `packages/`-side module rather than inside `console_resolve`
itself — the seat-cap allow-list (`signin.py` + `signup.py`) is scoped to
`packages/` and `apps/services/gateway`, and the `scripts/` CLI is OUTSIDE those
roots, so a CLI trigger does NOT grow it; keeping the function IN `console_resolve`
keeps the fence green with exactly two tenant-source callers. **Honor the pytest
hazards (root `CLAUDE.md` §6): run the fence by naming the file
(`uv run pytest tests/unit/test_signup_reconciler.py`), never the `tests/unit/`
directory.**

**Gates.** 🟢 **AGENT-SAFE — BUILD:** the `reconcile` function, the tenant marker
+ GST-profile migration, the signup step-1 profile write and step-2 marker write,
the CLI trigger, and the R8 fence — all against fixtures / scratch Postgres, dark.
🔴 **OWNER-GATE — refuse by name:** RUNNING the reconciler against a live Console
(transitively gated — it is inert until the two acts below land) · deploying the
Customer Console anywhere (§8 gate 2, D47) · widening a real deployment key to
`{resolve, provision}` (the capability growth beyond `{resolve}` — §8 gate 8 /
§6(h); its bare issuance is gate 7) · flipping
`SELF_SERVE_SIGNUP_ENABLED` on a live deployment (§8 gate 8). No NEW gate number
is minted: running the sweep is covered by gates 2 + 8, and registering any
operational note on the board is the supervisor's act (`work_plan.md` §2/§6), not
this file's.

**Non-goals.** Does NOT flip any flag · does NOT deploy the Console or reach a VPS
· does NOT run against a live Console · does NOT change the create-only guard on
either plane (it REUSES `store.org_owned_by_other` / migration 180 unchanged) ·
does NOT re-enter the signup route's two-plane orchestration or re-touch the
tenant guard · does NOT allocate a seat (it calls `provision_org_on_console`,
never `resolve_for_signin`) · not a scheduler — cadence is the operator's, the
function is a single idempotent pass · not the CRM `scripts/reconciler.py`.

**CP-2d · Passwordless email OTP via Resend — MINTED + SLICE 1 BUILT 2026-08-22,
SLICE 2 BUILT 2026-08-23, SLICE 2 REPAIRED (review rounds 1 and 2) 2026-08-23
(owner directive, D46.3).** ◐ **Both slices ship DARK —
the flag and the key are unset everywhere — but the flag now ARMS the feature
rather than being inert.** *(Owner's words
2026-08-19: "Eventually, we should also have a two-factor authentication and
email OTP system set up. Not made it by MVP, but documented for eventual build
up." Dispatched 2026-08-22 as the email-OTP half; TOTP/passkey step-up stays
deferred — see anti-scope.)*

**What this slice IS.** A **passwordless email one-time-code** sign-in provider:
the person enters an address, Resend emails a 6-digit numeric code, and the code
round-trip verifies email ownership (the anti-spam gate). Identity stays
IdP-attested for OAuth; this adds a **non-OAuth** identity path — and it does so
by riding the **ONE** Auth.js v5 NextAuth session (`workbench/control_plane/src/
auth.ts`), never a parallel auth/session store. **OTP, not magic-link** (owner's
ask): Auth.js's built-in `Resend` provider is magic-link by default, and the two
overrides `generateVerificationToken` (→ `generateOtp`, a crypto-strong 6-digit
code) and `sendVerificationRequest` (→ emails THAT code) turn it into numeric OTP
without leaving the one provider mechanism.

**Where it lands.** The framework-free pure half — code generator, message, the
Resend transport (injectable so tests never touch the network), and the config
gate — is `workbench/control_plane/src/lib/emailOtp.ts`. The Auth.js wiring is a
conditional block in `auth.ts` mirroring the Google `if (process.env.
AUTH_GOOGLE_ID)` idiom. The surface reuses the ONE provider-rendering seam:
`authPosture.ts::configuredProviders` gains a `resend` entry (`kind:"email"`)
when configured, and `signin/SignInForm.tsx` renders an email field for the
email-kind provider through the shared `Input` primitive. Slice 1 took **zero
migrations**; **slice 2 takes exactly one** (tenant-plane **186**,
`auth_email_otp_token`, RLS-EXEMPT — clauses 7 and 8) and still adds **no new
engine, no DB driver in the Next tier and no Redis site** (R5, clause 6): the
Next-side adapter is a thin object over new gateway routes, and the gateway
reaches the row through the existing unbound `get_session_factory()` idiom.

**Flag: `EMAIL_OTP_ENABLED`** — minted here (existed nowhere, verified);
default unset = OFF; the exact-string-`"true"` idiom of `auth.ts:163`, never
truthiness. `EMAIL_OTP_FROM` optionally overrides the verified sender.

🛑 **The flag alone does NOT register the provider — and that is a safety
requirement, not a nicety** *(hardening from the CP-2d review, 2026-08-22)*. An
Auth.js email provider registered **without a database adapter** makes
`@auth/core`'s `assertConfig` return **`MissingAdapter`** on **every**
`/api/auth/*` request — so it **500s ALL sign-in, Google and Microsoft
included**, not merely OTP. Gating registration on `EMAIL_OTP_ENABLED` alone
would make one documented owner flag a **site-wide auth outage**. So the real
gate is `isEmailOtpProviderReady` = env configured (`EMAIL_OTP_ENABLED ===
"true"` **AND** `RESEND_API_KEY`) **AND** `EMAIL_OTP_ADAPTER_READY` — a single
module constant, `false` until slice-2 wires the adapter. It is read by both
`auth.ts` (the provider) and `configuredProviders` (the button), so a half-armed
flag (key set, adapter absent) registers **nothing**: no provider, no button, no
`MissingAdapter`, no outage, `/api/auth/providers` unchanged, byte-identical to
today. `isEmailOtpConfigured` (the env half) stays separate and fully tested.

**Done when** *(each clause names its fence; the fences that CAN run do, in this
tree's node-env vitest — the framework-bound ones are source-regex + a review
note, the `signin.test.ts:38-45` reason)*:

1. ✅ **MET 2026-08-22.** Flag off OR key absent ⇒ the provider is NOT
   registered and the sign-in surface offers no email option — byte-identical to
   pre-CP-2d. Fence: `src/lib/emailOtp.test.ts` — the EXECUTED gate matrix
   (`isEmailOtpConfigured` off unless flag `=== "true"` **and** key present; every
   truthy-string reading of the flag stays OFF) and the seam proof
   (`configuredProviders` yields no `resend` entry until configured), shown
   red-first by mutating the gate to truthiness. The RUNTIME "`/api/auth/
   providers` unchanged" is the source guard in `signin.test.ts`
   (*"registers the Resend provider ONLY under the isEmailOtpProviderReady gate"*)
   plus the reviewer's manual gate (next-auth cannot load here).
1a. ✅ **MET 2026-08-22 (hardening — the flag cannot cause a site-wide auth
   DoS).** Even with `EMAIL_OTP_ENABLED=true` **and** `RESEND_API_KEY` set, the
   provider is NOT registered and the button does NOT appear while the adapter is
   unwired (`EMAIL_OTP_ADAPTER_READY === false`, today) — so flipping the flag
   before slice-2 is inert, never `MissingAdapter`. Fence:
   `src/lib/emailOtp.test.ts` — *"the adapter guard makes the flag
   un-footgun-able"* (env configured ⇒ `isEmailOtpConfigured` true but
   `isEmailOtpProviderReady` false and `configuredProviders` has no `resend`
   entry; the combined gate is exactly env-AND-adapter). Removing the
   `&& adapterReady` term from either gate turns it RED.
2. ✅ **MET 2026-08-22.** The provider only admits a **verified** email: the
   recipient is Auth.js's resolved `identifier` and the session email is the
   verified `user.email` (jwt callback), never a request body field (R11).
   Fence: `signin.test.ts` — *"takes the verified email from Auth.js, never from
   request input (R11)"*.
3. ✅ **MET 2026-08-22.** No parallel session/auth path: the provider rides the
   single `NextAuth({…})` config with the JWT strategy unchanged, and is the
   Auth.js email provider, not a hand-rolled `Credentials` door. Fence:
   `signin.test.ts` — *"rides the ONE NextAuth session"* + *"uses the Auth.js
   email provider, never a hand-rolled credentials path"*.
4. ✅ **MET 2026-08-22.** The sender is **Resend** and is never invoked over the
   network in tests (injected + mocked); a non-2xx from Resend fails **CLOSED**
   (throws, so a sign-in cannot silently appear to succeed). Fence:
   `emailOtp.test.ts` — the fake-`fetch` transport assertions and the 422
   fail-closed case.
5. ✅ **MET 2026-08-22.** The Resend key never reaches the browser tier: the
   surface derives from the seam props, and neither `signin/page.tsx` nor
   `SignInForm.tsx` reads `RESEND_API_KEY`/`EMAIL_OTP_ENABLED`. Fence:
   `signin.test.ts` — *"keeps the Resend key out of the browser tier"*.

---

**SLICE 2 — arming OTP.** *(Clauses 6–16, authored 2026-08-23 from the owner's
three answers B1/B2/B3 and the dispatch audit's rulings H-A…H-F; the slice was
NO-GO before them purely because it had no testable acceptance.)* Slice 2 is
what makes `EMAIL_OTP_ENABLED` mean something: the Auth.js **database adapter**,
the **rate limiter** that is the only defence a 6-digit code has, the
**code-entry page**, and the flip of `EMAIL_OTP_ADAPTER_READY` to `true` — all in
one change, because any subset of them is either inert or unsafe.

6. ✅ **MET 2026-08-23 — no database in the Next tier (R5, ruling H-A).** The
   adapter is a **thin custom object** whose persisting methods call new gateway
   BFF-internal routes; the control plane gains **no** `pg` / `postgres` /
   `@auth/*-adapter` dependency and opens no connection. It reaches the gateway
   through the ONE existing seam (`lib/gateway.ts::headersActingAs`,
   `gateway.ts:137`) and mints no second copy of the internal bearer.
   ⚠️ **The object lives in TWO files, and the split is load-bearing** *(repair
   round 1, finding P0)*: its **behaviour** — the method set and what each method
   answers — is `src/lib/emailOtp.ts::emailOtpAdapterOver(call)`, parameterised
   by the one outbound call, because that module is framework-free and therefore
   **executable** in this tree's node-env vitest; `src/lib/emailOtpAdapter.ts` is
   reduced to building that call out of `headersActingAs` and is the file the
   bearer sweeps scan. The reason is finding P0 itself: the adapter's shape had
   been argued in a docstring, the docstring was wrong, and no source-regex could
   have seen it. This is the same seam, moved so it can be tested — never a
   second one. Fences (**R7, the fence this rule did not have**):
   `src/lib/emailOtpAdapter.test.ts` — an EXECUTED scan of `package.json` for a
   pg/postgres/adapter dependency in either dependency block, an EXECUTED scan of
   every file under `src/` for a `pg`/`postgres` import, a pin that the pure half
   stays framework-free (a value import of `lib/gateway.ts` there would silently
   stop every executed fence from running), and a source pin that the adapter's
   only outbound call goes through `headersActingAs` and names no token env var
   of its own. ⚠️ **The second half of that double-fence did not exist until the
   repair** *(finding F1)*: both docstrings claimed `gateway.test.ts`'s bearer
   sweep covered this module, and it did not — `NON_ROUTE_GATEWAY_CALLERS` was
   pinned to `auth.ts` and `proxy.ts` with an exact-length assertion. The sweep
   now names `src/lib/emailOtpAdapter.ts` and pins the length at
   `ROUTES.length + 3`.
7. ✅ **MET 2026-08-23 — the token lives on the TENANT plane and is RLS-EXEMPT
   (owner answer B1, ruling H-B).** ⚠️ **This corrects a recorded defect in this
   ticket**: the slice-1 text said the table would be *"tenant-scoped by R5"*,
   and that is **wrong and would have bricked OTP on the day it shipped**. The
   row is minted **before any session exists**, for an address that may belong to
   no organization at all (the `console-empty` signup funnel is the whole reason
   the address is being verified), so there is no `organization_id` to stamp and
   an org-scoped policy would make every verification read return zero rows under
   H3's now-LIVE phase-4 RLS. R5(a)'s exempt-identity-table permission is the
   applicable clause — the same one `user_identity` and `org_membership` sit
   under. Migration **186** (`infra/postgres/186_auth_email_otp_token.sql`, the
   next free number taken at build time and re-checked at merge, **R1**) creates
   `auth_email_otp_token`, and the table joins `gen_tenant_migration.EXEMPT` with
   the reason *"belongs to an email, minted pre-session; no organization_id exists
   to stamp"*. *(Repair round 1 added a nullable `reserved_at` to the same
   migration — the send-slot claim of clause 11 — with an
   `ALTER TABLE … ADD COLUMN IF NOT EXISTS` beside the idempotent create, so a
   box that already applied the first cut of 186 gets the column on the next
   ladder replay. 186 was and remains the next free number; **max is still 185**
   elsewhere, re-checked 2026-08-23.)* Fences:
   `tests/unit/test_tenant_coverage.py` (source gate + the
   exemption-reason and exemption-count tripwires) and
   `tests/unit/test_tenancy_boundary.py` (the partition assertions, which read the
   same map).
8. ✅ **MET 2026-08-23 — the name is PREFIXED and the migration fails LOUDLY on a
   clash (ruling H-C).** The table is `auth_email_otp_token`, **never** a bare
   Auth.js-conventional `verification_token`: the staging box's database also
   carries Langfuse's schema, the collision is real, and
   `CREATE TABLE IF NOT EXISTS` on a colliding name is a **silent no-op** that
   leaves the adapter writing into somebody else's table. 186 therefore opens with
   a `DO` block that raises when a relation of that name exists without this
   migration's own column set, and only then runs the idempotent create. Fence:
   `tests/unit/test_email_otp_token.py::TestTheMigrationRefusesAForeignTable`
   (R8 — it plants a foreign `auth_email_otp_token` on a scratch database and
   asserts the migration RAISES, shown red by removing the guard).
9. ✅ **MET 2026-08-23 — the session strategy is PINNED in the same change that
   passes an adapter (ruling H-D).** `NextAuth({ … })` now carries an explicit
   `session: { strategy: "jwt" }`. Without it `@auth/core@0.41.2` (`lib/init.js:74`)
   derives `strategy: config.adapter ? "database" : "jwt"`, so merely *passing* an
   adapter silently converts the whole product — Google and Microsoft included —
   to **database sessions**, and `assertConfig` does **not** catch it (with an
   email provider present it demands only the three `emailMethods`, never the ten
   `sessionMethods`). The failure would therefore be a runtime `createSession is
   not a function` on every sign-in, not a config error. Fence: `signin.test.ts` —
   *"pins the jwt session strategy in the same config that takes an adapter"*,
   which reds **both** ways (adapter present without the pin, and the pin
   removed).
10. ✅ **MET 2026-08-23 (REWRITTEN in repair round 1) — the adapter is
   STATELESS-TRANSPARENT and implements every method the pinned
   `@auth/core@0.41.2` can invoke under this configuration (ruling H-E).**

   🛑 **What this clause said before, and why it was a site-wide outage waiting
   on a flag flip (finding P0).** It claimed a MINIMAL five-method set —
   `createVerificationToken`, `useVerificationToken`, `getUserByEmail`,
   `getUser`, `updateUser` — on the premise that *"`createUser`, `linkAccount`
   and `getUserByAccount` are reachable only from the `!useJwtSession` branches
   or from the webauthn/oauth arms, none of which this configuration can enter."*
   **That premise is false**, and it was fenced INTO place by an assertion that
   the adapter must *not* declare `getUserByAccount`:

   - `lib/actions/callback/index.js:56-62` destructures and calls
     `getUserByAccount` on **every** OAuth callback the moment an adapter is
     present. Missing method ⇒ `TypeError` ⇒ `CallbackRouteError` on every Google
     and Microsoft sign-in — not merely OTP — from the instant the owner sets
     `EMAIL_OTP_ENABLED=true`. `handle-login.js:175-178` calls it a second time.
   - Worse, adding that one method would not have fixed it. With
     `getUserByAccount` answering null, `handle-login.js:231-250` consults
     `getUserByEmail(profile.email)` — and the old adapter's `getUserByEmail` was
     deliberately **total** (it never returned null). A truthy answer there with
     no `allowDangerousEmailAccountLinking` is **`OAuthAccountNotLinked`**, so
     Google would still have been refused, and the "totality" the clause called
     load-bearing was the thing doing the refusing.

   **What it is now.** With the adapter present, behaviour must equal behaviour
   with **no adapter at all**, plus a verification-token store for the email arm
   — because the adapter is passed to the ONE `NextAuth` config that also serves
   Google and Microsoft. So the user-side methods are stateless and honest:
   `getUserByEmail` · `getUserByAccount` · `getUser` answer **null** (there is no
   user store to look in), and `createUser` · `updateUser` · `linkAccount`
   **echo** what they are handed and persist nothing. Walked against the pinned
   source under `jwt` that reproduces the adapter-less path exactly: on the OAuth
   arm `createUser` echoes `userFromProvider` (name and image intact, so
   `session.user.name` and CP-2b's `display_name` are unchanged); on the email
   arm `getUserByEmail`'s null is what makes `index.js:156-160` derive
   `{ id: randomUUID(), email: identifier }`, which `createUser` then echoes, so
   the verified address still reaches the `jwt` callback. The one measurable
   difference is `isNewUser`, `true` where the adapter-less path left it
   `undefined` — read by nothing here (no `events`, no `pages.newUser`, and the
   `jwt` callback ignores `trigger`).

   Session methods (`createSession`, `getSessionAndUser`, `deleteSession`) and
   `createAuthenticator` stay absent, but on a checked basis rather than an
   asserted one: the pinned source shows every call site of the first three
   behind a `useJwtSession` guard and the fourth inside the webauthn arm, and no
   webauthn provider is registered.

   ⚠️ **The adapter persists NO user rows and that is the design**: identity in
   this product is the tenant plane's (`app_user`/`user_identity`, reached
   through CP-2b's resolve), and an Auth.js `users`/`accounts`/`sessions` table
   set would be the second identity store root `CLAUDE.md` §5 forbids by name.
   ⚠️ **The database never holds the raw code**:
   `lib/actions/signin/send-token.js` stores `createHash(`${token}${secret}`)` —
   a SHA-256 of the code salted with `AUTH_SECRET` — so a database reader cannot
   sign in, and **the rate limiter of clause 11 is the whole defence against
   guessing**.

   Fences, and the shape of them is the lesson: **(a)** `emailOtpAdapter.test.ts`
   derives the reachable set FROM `node_modules/@auth/core` — it enumerates every
   adapter method the three reachable arm files name (by call or by
   destructuring), subtracts only those whose guard it also asserts by regex,
   and requires the object's own `Object.keys` to equal the remainder; it
   additionally pins the FILE LIST in `@auth/core` that mentions the adapter at
   all, so a version bump that adds a call site in a new file reds before any of
   the reasoning is trusted, and it re-asserts the premises (`jwt` pinned, no
   webauthn provider, no `allowDangerousEmailAccountLinking`).
   **(b)** `emailOtp.test.ts` **replays both arms** — transcribed line-by-line
   from `callback/index.js` and `handle-login.js` with citations, raising a
   `TypeError` for a missing method exactly as the destructure-then-call does —
   against the real object, and asserts a Google callback completes with the
   provider's user, that `getUserByEmail` never answers on the OAuth arm, that
   the email arm still yields the derived user through `createUser`, and that a
   full replay of both arms touches only the consume route. The old
   `not.toContain("getUserByAccount")` assertion is **deleted**: it forbade the
   fix.

   ⚠️ **The `updateUser` trap is fenced STRUCTURALLY since repair round 2**
   *(2026-08-23)*. `updateUser` is a deliberate echo and is only safe because
   `handle-login.js:68` reaches it *exclusively* when `getUserByEmail` answers —
   `@auth/core` calls it with `{ id, emailVerified }` alone, so a reachable one
   returns a user with **no address** and the session comes out **anonymous**.
   The fence for that coupling was a value check: `getUserByEmail` returns null
   for three literal addresses. A store-backed lookup that simply *missed* on
   those three would have stayed green while the trap was armed. The
   implementation is now the exported constant `NO_STORED_USER` and the adapter
   assembles it **by reference**, so `emailOtp.test.ts` identity-compares the two
   (`expect(adapter.getUserByEmail).toBe(NO_STORED_USER)`) and **any** change
   away from constant-null reds — including a behaviourally identical inline
   rewrite (measured red 2026-08-23), which is the point: the property is the
   shape, not the answer. The value assertions and the echo's address-less
   consequence are asserted beside it.
11. ✅ **MET 2026-08-23 — the rate limit is SERVER-SIDE in the gateway, with the
   owner's numbers (owner answer B3, ruling H-F).** Enforcement is in the gateway
   routes, **never** page-level: the Auth.js callback URL
   (`/api/auth/callback/resend?token=…&email=…`) is directly reachable by anyone,
   so a limiter on the entry page limits nothing. The numbers, all per
   **identifier**: **5 verification attempts per 10-minute window**; on exhaustion
   the outstanding token is **invalidated** *and* further attempts answer **429**
   for the remainder of the window; **3 code-sends per hour**. The counter lives
   **on the token row** (`attempts`, `last_attempt_at`) and is read and written
   inside one transaction under `pg_advisory_xact_lock` keyed on the identifier —
   **no new Redis surface**, because the tenant Redis seam deliberately has no
   unbound fallback and this row exists before any tenant does.

   ⚠️ **The send budget is enforced by an atomic SLOT CLAIM, not by the
   in-flight exclusion — and believing otherwise was a real bypass** *(repair
   round 1, finding P1b)*. Both legs of one send name it by the same `expires`
   instant, and the count excludes it so the two legs agree. But `expires` is
   `Date.now() + maxAge`: **two different sends started in the same millisecond
   carry the same instant**, so they excluded each other, both passed a budget of
   three, both upserted the one row, and **two emails went out for one slot** —
   repeatable per millisecond, i.e. unbounded mail to a chosen address. The send
   leg now claims the row in one statement (`INSERT … ON CONFLICT … DO UPDATE SET
   reserved_at = now() WHERE reserved_at IS NULL`, migration 186's new nullable
   `reserved_at`) and is refused with a new code `SendDuplicate` when the claim
   finds the slot taken; the adapter turns that 429 into a throw, and the throw
   is what keeps `sendVerificationRequest` from reaching Resend. The two legs
   still converge on one row: the token leg creates the row **unclaimed** when it
   wins the race, so the send leg claims rather than being refused. The two gates
   now count different things on purpose — the send leg counts CLAIMED rows
   (mails), the token leg counts ROWS (so a caller driving `/signin/otp/token`
   alone, the clause-12 residual, is still bounded).

   ⚠️ **The burst's ONE email must carry a code that verifies, and it did not**
   *(repair round 2, finding P2)*. Refusing the duplicate send's **mail** was
   only half of P1b. `send-token.js` starts `sendVerificationRequest` and
   `createVerificationToken` in ONE `Promise.all`, so a rejected send leg cancels
   nothing: the slot **loser's** `createVerificationToken` still landed on the
   shared `(identifier, expires)` row, and `_UPSERT_SQL`'s
   `COALESCE(EXCLUDED.token, …)` overwrote the winner's hash with it —
   `EXCLUDED.token` is never NULL (`record_token` rejects an empty one), so the
   docstring's claim that "`COALESCE` keeps an already-written hash" described
   the **opposite** of the delivered SQL. The one email that went out therefore
   carried a code that could never verify, with nothing anywhere reporting an
   error.

   The property is now whole-system: **under every interleaving of two
   same-`expires` sends the row's hash at mail time is the SLOT WINNER's.** Two
   statements make it true. `reserve_send(identifier, token, expires)` takes the
   winner's hash and `_CLAIM_SEND_SQL` writes it **inside the statement that
   decides who won**; `_UPSERT_SQL` is **first-writer-wins**
   (`COALESCE(auth_email_otp_token.token, EXCLUDED.token)`) so no token leg can
   replace it. The browser tier can supply that hash because `@auth/core` hands
   the send leg the **raw** code: `emailOtp.ts::otpWireHash` recomputes
   `createHash(`${token}${secret}`)` (`send-token.js:46,61`) and `auth.ts`
   resolves the salt the same way (`provider.secret ?? AUTH_SECRET`, now a single
   named const so the two reads cannot drift). A "`record_token` must not
   overwrite a CLAIMED row" fix alone would **not** have sufficed — in the
   record→reserve interleaving the loser's write lands while there is no claim
   yet. Nothing new crosses the wire (it is the same value `/signin/otp/token`
   already carries), no migration changed, and a token-less send is refused on
   shape by the route and by `ValueError` in the store.

   ⚠️ **A duplicate 6-digit code no longer 500s sign-in** *(finding P2)*.
   `(identifier, token)` is deliberately not unique — two live codes for one
   address can collide (≈1e-6 per pair) — but the consume `UPDATE … RETURNING`
   then matched two rows and was read with `.one_or_none()`, which raises
   `MultipleResultsFound`. It now updates `WHERE id = (SELECT … ORDER BY
   created_at DESC, id DESC LIMIT 1)`, consuming the newest match.

   Fence: `tests/unit/test_email_otp_token.py` (**R8, real Postgres** —
   create/consume round-trip, single-use, expiry, the **6th attempt refused even
   with the correct code**, the 429 window, the 4th send refused, **two sends
   sharing an `expires` millisecond mailing once** and the burst spending exactly
   one of the three, the token leg claiming no slot, and **two live codes with
   the same hash consuming one row**), plus — since repair round 2 —
   `TestTheBurstsOneMailCarriesAWorkingCode`, which drives **three orderings**
   (reserve→record→reserve→record, record→reserve→reserve→record and
   record→record→reserve→reserve) and asserts in each that the winner's code
   **consumes successfully**, the loser's does not, the burst is still ONE row
   and ONE of the three slots, and that a hash-less send raises. Measured red
   2026-08-23 by restoring `COALESCE(EXCLUDED.token, …)` on the token leg (one
   case) and by dropping the `token` write from the claim's conflict arm (the
   other two). The browser half — that the recomputed hash equals `@auth/core`'s
   own — is `emailOtp.test.ts`, which **imports `createHash` out of the pinned
   `node_modules`** and compares outputs, and pins the expression `send-token.js`
   hashes and where it resolves the secret.
12. ✅ **MET 2026-08-23 — the pre-auth route residual is ACCEPTED and NAMED
   (owner answer B2).** The three new gateway routes sit behind the app-wide
   `require_authenticated` and are deliberately **not** in `PUBLIC_ROUTES`, the
   posture `routes/signin.py:23-40` and `routes/signup.py:31-36` already state.
   They inherit those routes' accepted risk in as many words: **a holder of the
   internal token ALONE can drive them for arbitrary addresses**, because
   `deps.py` trusts an `X-User-Email` presented with that bearer by design. What
   that buys an attacker is narrower than either predecessor — no seat is
   allocated and no organization is created — and it is bounded on purpose:
   **tokens are single-use**; the **expiry is short** (Auth.js `maxAge`, which
   this deployment sets to the module default of **10 minutes**,
   `DEFAULT_OTP_MAX_AGE_S`); and **the rate limit applies to every caller,
   internal bearer included** — there is no bypass path, which is why the limiter
   is in the route rather than in the browser tier. The residual is therefore
   *up to three emails an hour to a chosen address, and a ten-minute denial of
   email-OTP sign-in for an address whose attempts are burned*. **The structural
   narrowing remains §4.3's `GATEWAY_INTERNAL_TOKEN` / `LITELLM_MASTER_KEY` split**
   (`work_plan.md` §6, §8 gate 1) — referenced, not built here.

   ⚠️ **The DENIAL half needs no token at all, and attributing it to the
   internal bearer understated it** *(repair round 1, finding F5)*. `proxy.ts`
   passes `/api/auth/**` through **unauthenticated** — it must, or nobody could
   sign in — so **anyone on the internet** can drive
   `GET /api/auth/callback/resend?token=<junk>&email=<victim>`. Each request
   reaches `POST /signin/otp/consume` through the BFF's own bearer, spends one of
   the victim's five attempts, and the fifth **invalidates their outstanding
   code**. Five anonymous requests therefore deny email-OTP sign-in to a chosen
   address for ten minutes. That is **accepted and unchanged**: not charging
   attempts for unauthenticated callers would be a limiter with a documented
   bypass, and OAuth sign-in for the same person is unaffected. Per-IP throttling
   is the answer and belongs at the reverse proxy (already in this ticket's
   deferred list). P1b's slot claim narrows the *mail* half of the residual; the
   attempt-burn denial is untouched by it and stays accepted.

   ⚠️ Two smaller residuals, recorded so they are not rediscovered as findings.
   **(i)** The send slot is reserved BEFORE the mail is handed to Resend, so a
   **Resend transport failure spends the slot** — three failed sends lock an
   address out for an hour. That is the fail-CLOSED direction and is deliberate:
   releasing the slot on a transport error would make "make the transport fail"
   the bypass. **(ii)** Every call here drives `deps._with_resolved_access` with
   a **caller-chosen address** (that is what `require_authenticated` does with an
   `X-User-Email`); for an address belonging to no member the resolver degrades
   to no-access and logs a swallowed warning, so a novel address per request
   writes a warning line per request on a phase-4-promoted catalog. Log volume,
   not an access decision.
13. ✅ **MET 2026-08-23 — the resolve interaction is IDENTICAL to Google, and the
   one place it cannot be is stated.** An OTP sign-in for an org-less address
   flows into the same `signIn` callback → `console-empty` → `signup_eligible` →
   signup-limbo funnel as Google, with no branch of its own; a suspended or
   seat-capped org is refused with the same codes. Two facts make that true rather
   than merely intended. **(a)** On the email path there is no OAuth `profile`;
   the callback already falls back to `user?.email` (`auth.ts:233-236`), which is
   Auth.js's own resolved identifier, so the resolve gets the same verified
   address it gets from Google. **(b)** ⚠️ **The email provider calls the `signIn`
   callback TWICE** — once from `send-token.js` with `email.verificationRequest`
   set, before the person has proved anything, and once from the callback leg
   after the code round-trip. **Only the second leg resolves.** A resolve on the
   first would allocate a seat for an address supplied by an anonymous visitor to
   the sign-in form, which is precisely the farmable seat cap
   `routes/signin.py`'s docstring exists to prevent — reduced there from *every
   authenticated request* to *the completion of a sign-in*, and the send leg is
   not a completion. The verification email is still sent (the refusal is the
   answer, delivered after the code is entered), and the volume is bounded by
   clause 11's three-per-hour. Fence: `signin.test.ts` — *"the send leg never
   resolves"* (the guard is present and precedes the flag gate) plus the existing
   CP-2b/CP-2c shape pins, which stay green unchanged.
14. ✅ **MET 2026-08-23 — the code-entry page reuses slice 1, and never forks it.**
   `pages: { verifyRequest: "/signin/code" }` (previously absent, so a person was
   left on Auth.js's built-in "check your email" page with nowhere to type a
   code). The route carries the identifier and submits the typed code to the
   Auth.js callback as a plain `GET` form, which is byte-for-byte the URL shape
   `send-token.js` builds for a magic link. The 6-digit code and the email body
   remain `src/lib/emailOtp.ts`'s `generateOtp`/`otpEmail` — reused, never
   reimplemented — and the page adds `/signin/code` to `proxy.ts`'s
   `PUBLIC_PAGES`, without which the entry page redirects a not-yet-signed-in
   person back to `/signin` and the flow cannot complete.

   ⚠️ **The submitted address is CANONICAL, and it was not** *(repair round 1,
   finding P1a)*. `@auth/core`'s send leg normalises through
   `send-token.js`'s `defaultNormalizer`, so the token is minted for
   `ada@customer.example`; the hand-built `GET` form submitted what was typed.
   `callback/index.js:151-152` compares `invite.identifier !== paramIdentifier`
   **verbatim** and throws `Verification` — but only **after**
   `useVerificationToken` has consumed the row and charged an attempt. So
   `Ada@Customer.Example` spent the person's code, told them it was wrong, and
   burned one of their five attempts per retry until the address was locked out
   for ten minutes. Two fixes, both required: **(a)** a
   `canonicalOtpIdentifier` mirroring `defaultNormalizer` (lowercase, trim,
   domain truncated at the first comma; never throws, unlike the original) is
   applied at **both** browser write sites — the `sessionStorage` hand-off in
   `SignInForm.tsx` and the code form's submitted field, which is now a single
   hidden input carrying the canonical value with the visible box deliberately
   unnamed; **(b)** `useVerificationToken` echoes the **requested** identifier
   rather than the wire's canonical one, so the comparison is structurally
   incapable of stranding a person on a code they have already spent. That is
   safe because the comparison is not what enforces ownership here: the gateway
   scopes the consume to the caller's `X-User-Email`, which *is* that identifier.

   ⚠️ **The no-stashed-address arm was UNUSABLE, and the cause was a decision
   taken over the LIVE input value** *(repair round 2, finding P1)*. The
   component computed `known = canonicalOtpIdentifier(email) !== ""`, and `"a"`
   canonicalises to `"a"` — so the **first keystroke** flipped `known` true and
   unmounted the very field being typed into. A person arriving without a stash
   (a different tab, a cleared store, private mode — the case the arm exists for)
   could never finish entering their address; the only escape was *Start again*.
   The property is now explicit: **`known` derives ONCE from the mount-time read
   succeeding — `?email=`, else the `sessionStorage` hand-off — and never from
   what is being typed**, while the typed value may only influence what is
   SUBMITTED, still canonically. Both variables come from one pure helper,
   `emailOtp.ts::codeEntryState(stash, typed) → { known, submitEmail }`, placed in
   the lib layer for a measured reason: **this package has no DOM renderer** (no
   jsdom, no testing-library, and adding one is out of scope), so a decision
   written inside the component has no fence at all. The component holds `stash`
   with exactly ONE writer (the storage effect) and feeds the visible box into a
   separate `typed`.

   Fence: `src/app/signin/code/code.test.ts` (source-regex: the callback path is
   `/api/auth/callback/${EMAIL_OTP_PROVIDER_ID}`, the form is a `GET`, the page
   reads no secret, it is in `PUBLIC_PAGES`, there is exactly ONE control named
   `email` and it carries the helper's `submitEmail`, the `sessionStorage` write
   is canonical too, and — since round 2 — that the component **destructures**
   `known` out of `codeEntryState(stash, typed)`, binds it nowhere else, never
   calls the canonicaliser itself, and has exactly one `setStash(` call site;
   comments are stripped before the negative pins so the docstring may quote the
   defect it forbids) — vitest here is node-env and cannot render the page, the
   `signin.test.ts:38-45` reason. The executed half is in `emailOtp.test.ts`,
   which drives the wire shape with `Ada@Customer.Example`, proves the identifier
   the adapter returns is the one the callback will compare, carries the
   canonicaliser's own matrix, and — since round 2 — drives `codeEntryState`
   across the **whole typing sequence** (`""`, `"a"`, `"ad"`, `"ada@"`, the full
   address, mixed case, whitespace) for both stash-present and stash-absent,
   including the one-assertion form of the property: hold the stash, vary the
   input, and `known` must not move. Measured red 2026-08-23 by deriving `known`
   from the live value in the helper (3 cases) and by recomputing it in the
   component (1 case).
15. ✅ **MET 2026-08-23 — `EMAIL_OTP_ADAPTER_READY` is `true`, in this same
   change.** That constant is what clause 1a's guard reads, so flipping it here
   is what converts `EMAIL_OTP_ENABLED` from an inert documented flag into a real
   owner switch. Fence: `emailOtp.test.ts`'s existing matrix, with **exactly one
   assertion inverted and two re-parameterized** *(corrected in repair round 1,
   finding F3 — the sentence here had said "two inverted and one strengthened",
   which does not describe the diff)* — the **inverted** one is
   `EMAIL_OTP_ADAPTER_READY` itself, now asserted `true`; the **re-parameterized**
   two are `isEmailOtpProviderReady(configured, false)` and
   `configuredProviders(configured, false)`, which drive the adapter half through
   the **parameter** rather than through the constant, so the "adapter not ready
   ⇒ no provider, no button" guard stays EXECUTED instead of becoming a tautology
   on the day it was armed. Everything else in the file's pre-existing matrix is
   untouched; repair round 1 then APPENDED the executed adapter fences clause 10
   names, which are new tests rather than changes to old ones.
16. ✅ **MET 2026-08-23 — both flag positions stay fenced; a dark box is
   unchanged.** With `EMAIL_OTP_ENABLED` unset or `RESEND_API_KEY` absent — the
   state of every environment — no provider is registered, `adapter` is passed as
   `undefined` (which `@auth/core` treats exactly as absent, `assert.js:157`), the
   sign-in surface offers no email option, and `/signin/code` is an orphan route
   nothing links to. The one unconditional change is the `session: { strategy:
   "jwt" }` pin, which is the value `@auth/core` already derives for an
   adapter-less config. Fence: the pre-existing `emailOtp.test.ts` matrix stays
   green with only clause 15's three adapter-state assertions changed, plus
   `signin.test.ts`'s dark-ship pins (the provider push is still inside the
   `isEmailOtpProviderReady` gate, and the `adapter:` option is ternaried off the
   same predicate). *(Repair round 1, finding F2: that last pin used to carry a
   dead `if (authSrc.includes("adapter:")) { …the same unconditional assertion
   again… }`, which could never add a failure. It now asserts what the pin is
   actually about — exactly ONE `adapter:` option in the file, matched at the
   start of a line so prose cannot satisfy it, and both it and the strategy pin
   inside the ONE `NextAuth({…})` config.)*

**Gates unchanged, and the flag now bites.** 🔴 Providing `RESEND_API_KEY` on a
real deployment and flipping `EMAIL_OTP_ENABLED=true` on a live box remain the
owner's acts (§8). ⚠️ **What CHANGED with slice 2 is the consequence of those
flips**: before it, either was a documented no-op; now the pair genuinely turns
on a non-OAuth sign-in path. That is the point of the ticket, and it is why the
rate limiter, not the code length, is what the review should be aimed at.

**Deferred out of slice 2, deliberately** *(recorded so the next agent does not
read them as omissions)*: a **reaper** for consumed/expired `auth_email_otp_token`
rows (the table grows at three rows per address per hour at most, and a `DELETE`
job is its own ticket with its own tenant-plane cadence question) · **resend-code
UX** on `/signin/code` (the person returns to `/signin`; a "send another code"
button would need its own view of the 3/hour budget) · **per-IP** limiting (the
gateway sees the BFF's address, not the caller's, so an IP limiter here would
throttle the deployment, not the attacker — it belongs at the reverse proxy) ·
**TOTP/passkey step-up** and **SMS OTP**, unchanged anti-scope.

**Gates** (🔴 OWNER-GATE): providing `RESEND_API_KEY` on a real deployment (a
**secret**, §6(f)'s class) and flipping `EMAIL_OTP_ENABLED=true` on a live box
(an **enforcement flip**) are the owner's acts; building the provider dark is
agent-safe. ⚠️ *(Corrected 2026-08-23 — the sentence that stood here said either
flip "before slice-2 is a no-op". **Slice 2 has landed**, so both flips now arm a
live non-OAuth sign-in path. The adapter guard has done its job and is no longer
what makes a flip safe; clause 11's rate limiter is.)*

**Anti-scope.** **TOTP/passkey step-up** stays DEFERRED (its shape is unchanged:
it rides the Auth.js session and is enforced at the gateway identity seam
`deps.py`, never per-app, behind its own default-OFF flag with both positions
fenced). **SMS OTP** is out (cost + SIM-swap surface) unless the owner asks by
name. Neither slice flips a flag or deploys. *(Slice 1's anti-scope — "does NOT
add a database adapter, does NOT build the code-entry page" — is retired: those
are exactly what slice 2 built, clauses 6–16.)*

**Sequencing.** **CP-0** → CP-1 → CP-2 → **CP-2a** → **CP-2b** → **CP-2c** → **CP-2e** → CP-3 → CP-4 →
CP-5 → CP-6 → **CP-9** → CP-7 → CP-8, with **CP-4b** owed out of order: CP-6
shipped before it, and it must land before the first Router caller, because
every agent runtime streams. CP-4 is
where revenue-relevant data starts existing (real per-org burn, unpriced), and it is
worth reaching before CP-6 sets a rate card, because a rate card set on estimates is
a rate card you change on customers.

**CP-9 sits after CP-6 on purpose**, and the reason is D35.5's *"revenue order
is enforcement, then checkout"*: CP-6 is what makes a purchased credit
limitable, and shipping checkout first means taking money for something we
cannot yet limit. Note the distinction that governs dispatch — **CP-6's
mechanism is BUILT** (its refusals ship OFF behind
`CUSTOMER_CONSOLE_SPEND_GATE`), and D35.5 binds the **flip** to live checkout,
not the dark build. CP-9 is therefore dispatchable now; going live is the flip
set in WS-30 SC-4a.

*(Sequence line updated 2026-08-18 — CP-2b inserted after CP-2a, CP-4b noted;
**CP-9 inserted after CP-6** the same day when the payment seam was minted;
**CP-2c inserted after CP-2b 2026-08-19 (D46)** — it is the form over CP-2a's
API and consumes CP-2b's resolve semantics, and it dispatches only after
MT-1j slice 4. The board row in `work_plan.md` §2 carries the same line —
updating it is the supervisor's act, not this file's. **CP-2e inserted after
CP-2c 2026-08-20** — the out-of-band Console-mirror reconciler that closes
CP-2c done-when 5's named un-metered-org gap; it dispatches only after CP-2c is
built, and going live is transitively the owner's, §8 gates 2 + 7.)*

**What is dispatchable in CP-9's FIRST slice, and what is held back** *(the
re-audit returned **GO-NARROWED** on 2026-08-18; this is that narrowing, written
down so the next agent does not have to infer it).*

**🟢 The substrate half — dispatch this.** ✅ **BUILT 2026-08-18** (see the CP-9
ticket's build box for what landed, and for the three decisions no clause
named). Everything server-side, all of it
reachable with a fake provider and a real Postgres 16: 9.2's three tables and
the state machine · the paise conversion · 9.3's two writes, `can_pay`, the
transitive fence and its one named carve-out, the ownership predicate · 9.3a's
two reads · 9.4's `PaymentProvider` seam, `RazorpayProvider` over `httpx`, the
`FakeProvider` and the real HMAC signer · 9.5's webhook with both guards ·
9.6's single `payments.fulfil` with the reference carrier · **WS-30 SC-4g
(i)–(v) server-side**: `discount_code` + `discount_redemption`, the split-key
storage, percent-against-the-pre-GST-base, the redeem route and the ₹0 path ·
every fence named in this ticket. Its acceptance is CP-9 done-when 1–17 and
SC-4g done-when 1–8. *(Item **(f)** below was then built on 2026-08-19 as its
own small slice and carries **done-when 18**; item **(g)** — the customer seats
read, ✅ built 2026-08-20 — carries **done-when 19**; item **(i)** — the customer
member-list read, ✅ built 2026-08-21 — carries **done-when 20**, so the list is
1–20, and the substrate half's own acceptance is unchanged at 1–17.)*

**🔴 The surface half — held back, and named rather than left to drift.**
**(a)** WS-30 **SC-4a's checkout UI** and its two write proxies, **because
B7's gate is an open decision that reaches the tenant plane's capability
vocabulary** (`acb_auth/permissions.py`) — a different seam, a different review,
and one that must not ride in on a payments PR. **(b)** The **operator
code-issue surface** (issuing is `Operator`-scheme API in this slice; rendering
it is CP-8). **(c)** The **invoice document** — SC-5b/5c, hard prerequisite for
the DOCUMENT and explicitly not for the checkout (SC-4g (vi)). **(d)** The
**flip set** — all four items are owner acts (SC-4a's flip-set box).

**(e) The browser→provider HAND-OFF — `SC-4h`, jointly owned by WS-30 and
WS-31/CP-9** *(added 2026-08-19 by SC-4a's NO-GO dispatch audit; the full
argument is in `subscription_console.md` SC-4a's remediation box, F-A, and is
not restated here)*. Measured against this tree: `create_order` posts to the
**Orders** API and returns `ProviderOrder` with no payment link
(`payments.py:449-473`, `:333-338`); the `PaymentProvider` protocol is
`create_order` + `verify_webhook` and nothing else (`:414-425`); Razorpay's
browser Checkout needs `key_id` **and** `order_id` in the page, and this service
exposes neither — `key_id` reaches no response, and `OrderView` is pinned to an
**exact 14-name** field set by
`test_the_order_read_carries_no_provider_identifiers`. So the hand-off is three
decisions on **this service's** response surface — whether a customer-key
response may carry `key_id`, which provider identifier goes on the wire and
under what auth, and the **`OrderView` fence amendment** that makes either
possible — plus CP-9 §9.5's capture leg against a real account. **They are
CP-9's to decide, which is why the WS-30 ticket names them and does not take
them**, and why SC-4a's launch slice was narrowed to the ₹0 path instead of
inventing a hand-off inside a UI PR. Gated behind `work_plan.md` §6(b) (the
Razorpay account, owner-side even in test mode).

**(f) One SMALL prerequisite the same audit found, and it belongs to this
service** — ✅ **BUILT 2026-08-19 (WS-31, branch `ws-31-catalog-read`)**, one
route, one store function, two fences, no migration: `GET /billing/catalog`
(`main.py::billing_catalog`) under `PayingCaller`, over
`store.active_plans`, shaped by `CatalogPlanView`'s exact five fields with
`payments.paise` doing the one conversion. The suite is **117** tests, 0
skipped, against a real Postgres 16; the Console block is **395** (the
live-route parametrisation in `test_customer_console_resolve.py` picked the
new route up on its own — 21 → 22 parametrised cases, measured by collection on
both commits after the first write-up said "52 → 53", a hand-transcribed count
the verifier caught — and refuses a deployment key there).
Both fences shown red first — dropping `WHERE active` fails
`test_the_catalog_read_never_boards_an_inactive_row`; emitting
`int(price_inr)` instead of `payments.paise(...)` fails
`…_carries_no_per_org_state_and_paise_only` on `600 == 60000`, and adding an
`organization_id` field to `CatalogPlanView` fails its field-set equality.
*The finding as recorded:* *no route exposes the priced `plan_catalog` to a
customer credential.* `GET /billing/summary` (`main.py:1283`) is `Operator`, cross-org,
and returns only slugs the org already holds seats on; `GET /me/billing`
(`:1621`) and `GET /me` (`:1447`) carry no catalog. *(All three re-measured
2026-08-20 on `ws-31-seats-mvp` from `:1048`/`:1386`/`:1212` — the 2026-08-19
build's numbers drifted ~235 lines as CP-2c/CP-2e landed above them; matched by
handler name.)* SC-4a launch done-when 1
forbids a hard-coded ladder in TypeScript, so **without a customer-key catalog
read the launch slice cannot meet it honestly.** Recorded here as WS-31's, not
built by WS-30. *(Made buildable 2026-08-19 after the dispatch confirmation
found the first draft too loose to build from — agent-proposed defaults, D16/D17:)*

- **Route:** `GET /billing/catalog`.
- **Auth:** the **`can_pay` dependency** (`organization_for_payment`), NOT the
  `can_use_ai`-gated `KeyCaller` — the nearest read to copy (`/me/billing`)
  403s a `suspended` org at `auth.py:217`, which is exactly the "the customer
  who most needs to pay is the one the door is shut on" defect §9.3(5) exists
  to fix. A suspended org may read the catalog; a deleted one may not.
- **Payload:** the `active` rows of `plan_catalog` as
  `slug · name · kind · price_paise · sort_order` — **one money field, integer
  paise on the wire**, converted from the rupee `NUMERIC` by the ONE
  `payments.paise()` (§9.2's rule; exposing `price_inr` rupees next to a
  paise-denominated order API is the rupee/paise ambiguity §9.2 exists to
  prevent). `active = TRUE` in the `WHERE` clause per `store.py:596-603`'s own
  argument (`rnd`/`support` are seeded INACTIVE and must never board a
  customer response). No per-org pricing, no entitlement state (MT-2/SC-1a's).
- **Fences (R7), in `tests/unit/test_customer_console_payments.py`** (already
  on §7's list and pr-check's skip-guard):
  `test_the_catalog_read_never_boards_an_inactive_row` (seed an inactive row,
  assert absent) and
  `test_the_catalog_read_carries_no_per_org_state_and_paise_only` (field-set
  equality incl. `price_paise`; a suspended org reads it; a deleted org 403s).

**(g) One more SMALL customer-key read the same MVP needs, and it belongs to this
service** — ✅ **BUILT 2026-08-20 (WS-31, `ws-31-seats-mvp`)**: one route
(`GET /me/seats`, `main.py` `my_seats`, on the `can_pay` door), one response-model
pair (`SeatsView`/`SeatPlanView`), no new store function, no migration; two
red-first R8 fences (below), suite 119/119 against a real Postgres 16, 0 skipped.
The post-signup in-app MVP needs the customer to SEE its seats —
purchased, assigned, available, oversubscribed **per plan** — but the only read
that computes them today is `GET /billing/summary` (`main.py:1283`,
`billing_summary`), which is the **Operator**, cross-org door: the wrong door for
a customer, and it takes an `org_slug` a customer must never name. The customer
read `GET /me/billing` (`main.py:1621`, `my_billing`) returns credits + invoices
and **no seats at all**, so a customer's own page cannot render its seat grid.
*(Both anchors re-measured 2026-08-20 on `ws-31-seats-mvp`.)* Decided here with
the same agent-proposed defaults item (f) used (D16/D17):

- **Route:** a dedicated `GET /me/seats`, the sibling of `GET /billing/catalog`
  (item (f)) — **not** a `seats` block bolted onto `GET /me/billing`. That
  billing read sits on `KeyCaller`/`can_use_ai`, which **403s a `suspended`
  organization** (`organization_from_key`, `auth.py:265-289`); the seats read
  must be reachable by exactly that organization (next bullet), so hanging seats
  on `/me/billing` would either shut the door on the customer who most needs it
  or force a re-door of a shipped route — a separate behavioural change this
  ticket does not take. A new `PayingCaller` read is the same move item (f) made
  and the SAME seam, not a parallel one. *(The alternative — extend `/me/billing`
  and re-door it to `can_pay` so the page makes one call — is left as an owner
  decision, §9's residuals: it widens who may read credits/burn on a shipped
  route and wants its own fence, so it does not ride a seats-read PR.)*
- **Auth:** the **`can_pay` door** (`organization_for_payment` / `PayingCaller`,
  `auth.py:292-309`) — NOT the Operator door and NOT `KeyCaller`. A **suspended**
  organization may read its seats, because it is the organization deciding
  whether to buy more — the §9.3(5) reasoning item (f) used one route along — and
  a **deleted** one 403s. The organization is a property of the credential
  (`caller.organization_id`), so no `org_slug` reaches the wire and org A can
  never name org B.
- **Payload:** for each plan the org holds a grant or a live assignment on,
  `plan_slug · purchased · assigned · available · oversubscribed` — the **exact
  five** `billing_summary` already emits (`main.py:1298-1304`), computed by the
  ONE seat vocabulary and **nothing else**: `store.seat_rows(conn,
  org_id=caller.organization_id, plan_slug=…)` folded through `seats.seat_counts`
  (§3.3, D32.5). **No recompute on any surface** — the handler calls the ONE
  shared `_seat_grid(conn, org_id)` helper (extracted from `billing_summary`'s
  loop; `billing_summary` calls it too) with the org id taken from the credential
  instead of `_org_id(conn, org_slug)`, and a plan the org never touched is
  skipped exactly as `billing_summary` skips it (`main.py:1295-1296`), not
  emitted as a zero row. Shape it with a `SeatsView` / `SeatPlanView` pair
  mirroring `CatalogView` / `CatalogPlanView` (`main.py:349-376`), so the field
  set is pinned on the model as well as on the wire.
- **Tenant isolation:** the read is scoped to `caller.organization_id` from the
  org-key door — by construction, not a `WHERE` a reader has to remember, the
  same property `my_billing` relies on (`main.py:1635`). Org A's read never
  returns org B's rows.
- **Fences (R7), in `tests/unit/test_customer_console_payments.py`** — already on
  §7's list and pr-check's skip-guard, so the seats read rides item (f)'s suite
  and there is **nothing new to register** — both shown red first and both **R8**
  against a real Postgres 16 on the Console ladder, sitting beside
  `TestTheCatalogRead`:
  - `test_the_seats_read_is_scoped_to_the_key` — provision two orgs, grant and
    assign seats on each, and assert org A's `GET /me/seats` never carries a plan
    row or count belonging to org B (the two-org isolation precedent is
    `test_an_order_read_is_scoped_to_the_key`). Mutation evidence to run and
    revert: driving the read from an `org_slug` query param instead of
    `caller.organization_id` returns org B's seats — red.
  - `test_the_seats_read_carries_the_seat_vocabulary_and_nothing_else` — the
    field set is **exactly** `{plan_slug, purchased, assigned, available,
    oversubscribed}` on the `SeatPlanView` model AND on the wire; the four counts
    equal `seats.seat_counts` fed the same `store.seat_rows` for a known fixture
    (e.g. purchased 3 / assigned 1 / available 2, plus an oversubscribed
    assigned > purchased case), never a second SQL; a **suspended** org reads it,
    a **deleted** org 403s. Mutation evidence: recomputing `available` inline
    without the zero-clamp fails the oversubscribed case; adding an `org` or
    price field to `SeatPlanView` fails the field-set equality.
- **Gate label:** the READ is 🟢 **AGENT-SAFE** (fixtures / real Postgres). The
  live seat **writes** — `POST /billing/seats` (`main.py:1315`, `assign_seat`)
  and its release — stay 🔴 **OWNER-GATE** (§8 gate 4), as do deploying the
  Console and flipping any flag. Reading seats moves no value and mints nothing.
- **No migration:** a read over the seat tables in `001` already ships.

The split is not cosmetic: the substrate half is verifiable end-to-end by an
agent against a real database and a fake provider, and the surface half is not
(it needs a capability decision, an account, or an owner's flip). Shipping them
together is how a reviewable PR becomes an unreviewable one.

**(h) The customer-authenticated seat WRITE — the write-side twin of item (g)'s
read.** ◐ **BUILT 2026-08-21 (WS-31, `ws-31-seat-assign`)** — endpoint + door +
seven red-first R8 fences (`TestTheSeatAdminWrite`), green against a real
Postgres 16, 0 skipped. **No migration.** Ships dark: no live key carries
`seat_admin`. The **live seat write against a real customer org** and **adding
`seat_admin` to a real deployment key** stay owner-gated (§8 gate 4 / gate 8),
as does deploying the Console; the customer-facing "manage seats" SURFACE is
WS-30's (§9 residual 7). *(All code anchors below re-measured 2026-08-21 on
`ws-31-seat-assign`; the §0 note's "~680 lines of drift" caveat applies — match by
handler/symbol name, not by the line number a stale citation carries.)* Item (g)
gave a customer credential a way to **see** its seats; the owner's "invite users
to fill seats" needs the matching **write** — an org's own admin assigning a
purchased seat to a member of THEIR org, and releasing it. Today the only seat
writes are `POST /billing/seats` (`main.py:1370`, `assign_seat`) and
`POST /billing/seats/release` (`main.py:1410`, `release_seat`), both `Operator`
(cross-org staff) and both taking an `org_slug` a customer must never name. There
is **no** customer-self-serve path: every seat write goes through the cross-org
Operator door. This item designs the missing door and endpoint. Decided with the
same agent-proposed defaults items (f)/(g) used (D16/D17 — the owner may overrule;
the rejected alternative and the residual are recorded).

**⚠️ The crux — investigated before designing, because the answer decides the
door.** Two facts about the code as it stands:

- **A customer credential today is *the org*, with no in-org role.** The
  organization key (`cc_live_…`) resolves to an `organization_id` and nothing
  else — `store.resolve_key` (`store.py:1337`) returns `(organization_id,
  key_hash, org_status)`, the key table is org-scoped (`llm_api_key`,
  `001_customer_console.sql:212-224`), and `Caller.member` is **attribution only,
  "never used to select rows, never used to authorise"** (`auth.py:143-144`). So
  the console cannot tell, from a `cc_live_` credential, *which member* is calling
  or whether they are an admin. R11 forbids fixing this with a body field: an
  `actor_email` the caller asserts "makes the caller the authority on who they
  are" (`auth.py:59-62`), and is trivially spoofable by anyone holding the org
  key. **The org-key door therefore cannot enforce "admin, not any member" —
  structurally, not by omission.**
- **But an admin CONCEPT already exists console-side, as its own vocabulary.**
  `org_membership(organization_id, user_identity_id, role ∈ {owner,admin,member},
  status ∈ {invited,active,suspended,removed})` ships in `001` (`:121-131`), is
  **populated** — provisioning writes the founder as `role='owner', status=
  'active'` (`main.py:889-899`) — and is labelled in the code as exactly this:
  *"`org_membership.role` is registry/billing vocabulary"* (`main.py:1146`),
  deliberately distinct from the tenant's permission vocabulary (`org_role` +
  `acb_auth`, D12). Using it to gate a **billing** write is using it for its
  stated purpose, not inventing a second grant vocabulary. What is missing is not
  the role — it is a **customer credential that binds to a member** so the role is
  reachable. The one credential that resolves a *member* is the deployment key,
  via `store.deployment_visible_orgs` (`store.py:697`), the placement-bounded join
  the sign-in resolve path already trusts.

- **The DOOR (the core decision) — a deployment-key `seat_admin` capability,
  console-authoritative.** Add a **third capability** to the existing
  deployment-key scheme — `seat_admin`, beside `resolve`/`provision`
  (`auth.py:106,125`) — and mint the write door from the **same
  `deployment_or_operator` factory** (`auth.py:333`) that already serves
  `POST /registry/resolve` and the provision arm. No new auth seam (root
  `CLAUDE.md` §5); a third `deployment_or_operator("seat_admin")` closure,
  registered in `AUTHENTICATING_DEPENDENCIES` (`auth.py:513`) so CP-2b clause 1's
  unauthenticated-set fence covers it the day it lands. The console then enforces,
  in its own code, everything the org-key door cannot:
  1. a valid deployment key carrying `seat_admin` (create-only authorization,
     exactly `provision`'s shape — holding it lets a box do seat admin for the
     orgs placed on it, and makes it no operator; `auth.py:112-124`);
  2. the **acting admin** and the **org** are derived together from
     `deployment_visible_orgs(deployment_id, actor_email)` — the org is the
     placement∩membership **join result, never a body field** (R11-clean the same
     way `_resolve_for_deployment` is, `main.py:1151-1160`), and it must resolve to
     **exactly one admissible org** or the write refuses, the chooser being a
     named non-goal exactly as at resolve (`main.py:1210-1214`);
  3. the acting member's `org_membership.role ∈ {owner,admin}` **and**
     `status='active'` in that org — the console reads its OWN registry vocabulary
     (`main.py:1146`), so a plain `member` is refused **by the console**, not only
     by an upstream tier. This is an **ADDED read** on the resolved
     `(org_id, identity_id)` (the precedent is the resolve path's own
     `SELECT role, status FROM org_membership`, `main.py:1103-1109`), because
     `deployment_visible_orgs` returns **neither** `role` nor `status` — its join
     does not consult them (`store.py:720-724`) — so the join answers *which org*
     but never *what standing*, and the standing is the whole point of this clause;
  4. the **target** member holds a membership in the SAME org (reject unknown /
     cross-org emails — do NOT `ensure_identity`-create a global identity for an
     arbitrary typed-in email, which the operator path does and self-serve must
     not);
  5. the seat invariants below.
  The trust boundary, stated honestly (the doctrine `store.py:203-206`/
  `seats.py:70-77` set for seat writers): the deployment key is the trusted party,
  vouching that the box authenticated the human whose email it presents — the
  **same** trust the sign-in resolve path already extends, now for a write.
  Minting a real key with `seat_admin` is OWNER-GATE (§8 gate 8's class — the
  capability-growth gate), and there is deliberately no route that issues or edits
  a key's set, so the door **ships dark by construction**: no live deployment key
  carries the capability until the owner adds it by hand.

- **The ALTERNATIVE, recorded and NOT chosen — an org-key `can_pay` write door
  (`POST /me/seats`) with the admin gate pushed into the control-plane (Next) and
  a carve-out to the CP-3 org-key fence.** It fits the *current* billing wiring
  (Next holds `CUSTOMER_CONSOLE_ORG_KEY`, not a deployment key), and it is the
  same door item (g)'s read uses. It is rejected as the default for two reasons,
  both structural: (a) the console **cannot** enforce the admin axis on the org
  key (crux above), so "admin, not any member" would live **entirely** in Next —
  acceptable for a read (`GET /me/seats` is already "any signed-in member",
  `seats/route.ts:26-28`), but a money-adjacent write should be authorised where
  it lands; and (b) a seat write reaches `store.try_assign_seat` /
  `store.release_seat`, both writers of `seat_assignment` ∈ `ENTITLEMENT_TABLES`,
  so an org-key seat route turns
  `test_no_org_key_route_writes_an_entitlement_or_ledger_row` **red** — the
  mutation this fence records by name (`test_customer_console_payments.py:830-833`:
  *"pointing `POST /billing/seats` at the organization-key dependency turns this
  red"*). Making it pass would mean **relaxing the CP-3 read-only-org-key lesson**
  with a third named carve-out beside `FULFIL_ALLOW_LIST`/`METERING_EXEMPTION`
  (`:86,118`), which is an owner-ratified deviation (§9 residual, the shape
  `METERING_EXEMPTION` took), not an agent's to add. The chosen deployment-key
  door needs **neither** relaxation: it is not an org-key route, so the fence
  stays green — and that green is the proof the org key stayed read-only.

- **Assign / release semantics** — the write-side of item (g)'s vocabulary, no
  second seat computation:
  - **Assign** takes `{member_email, plan_slug}` (plus the actor resolved from the
    credential+`actor_email`), refuses `plan_slug='core'` (membership IS the Core
    seat, D19.3 — "Core seats are not managed here", `subscription_console.md`
    SC-2; a self-serve `source` is one of `center|plan|alacarte`, never `core`),
    then runs the **existing** cap path: `store.lock_seat_capacity` **before** the
    count (this new writer takes the advisory lock the operator `assign_seat`
    still does not — closing the race `seats.py:70-77` / §6 CP-2b names, rather
    than inheriting it), `store.seat_rows` → `seats.seat_counts` →
    `seats.decide_assignment`. **Refuse at the cap (no self-serve
    oversubscription):** `available == 0` and not already-assigned → **409 with the
    `buy_more` payload** `decide_assignment` already builds (`seats.py:147-158`);
    the customer buys more seats first, and oversubscription stays the
    Operator-only escape hatch. **Idempotent:** an already-assigned member is
    `decide_assignment(already_assigned=True)` → allowed no-op, and
    `store.try_assign_seat`'s `ON CONFLICT … DO NOTHING` (`store.py:243-258`) makes
    it a 200 that consumes nothing.
  - **Release** takes `{member_email, plan_slug}`, calls `store.release_seat`
    (`store.py:261-280`, sets `released_at`, frees capacity immediately, D19.3),
    and a release of an unassigned member is a **200 no-op `{released:false}`**,
    mirroring the operator twin (`main.py:1410-1422`). Release does not gate on
    `can_write_seats` (freeing a seat is safe when suspended), matching the
    operator twin; assign DOES (403 for `suspended`/`cancelled`, exactly
    `assign_seat`'s `capabilities_of(state).can_write_seats` check,
    `main.py:1378-1381`).
  - **Reconciles with the read:** after an assign, `GET /me/seats` shows
    `assigned+1 / available−1`; after a release, `assigned−1 / available+1` — the
    SAME `_seat_grid` (`main.py:524`) folded through the SAME `seat_counts`, no
    parallel arithmetic. `_audit` records the act with `actor` set to the
    customer/actor rather than `"operator"` (`main.py:561-577`, the kwarg CP-9
    added for exactly this "first customer-credentialled write" reason).
- **What stops org A writing org B:** the org is the placement∩membership join,
  bounded to the deployment's own placements — a key placed for A resolves only
  A's members (`deployment_visible_orgs`, `store.py:697-755`, clauses 4-5), and a
  body naming an org is 400, never honoured (R11). **What stops a non-admin
  member writing:** the console's own `org_membership.role`/`status` check on the
  resolved actor (default (h)-3) — refused *by the console*.
- **No migration.** `org_membership.role` already ships (`001:124-125`);
  `seat_assignment` and its partial-unique/isolation indexes already ship
  (`001:186-207`) with `source ∈ {core,center,plan,alacarte}`; and `seat_admin`
  needs **no** column — `deployment_key.capabilities` is `TEXT[]` with no `CHECK`
  (`006_deployment_key.sql:56`), so the string is insertable and the enforcement
  is entirely the door, **exactly** the precedent `provision` set and recorded as
  a rule (`auth.py:119-124`: *"No migration carries this string and none may be
  minted for it"*). *(R1 note for completeness: were a migration ever needed, the
  next free Customer-Console number is taken at build time by listing
  `infra/customer_console/` — `007` is the highest on disk today — never written
  ahead; this item needs none.)*
- **Fences (R7), all shown red first, all R8 against a real Postgres 16 on the
  Console ladder.** Behavioural fences beside `TestTheCatalogRead`/the seats-read
  fences in `tests/unit/test_customer_console_payments.py` (already on §7's list
  and `pr-check.yml`'s skip-guard — **nothing new to register**); the structural
  ones join the no-DB suites already in §7:
  - `test_a_seat_admin_key_assigns_a_member_and_the_read_reflects_it` — a
    `seat_admin` deployment key placed for org A assigns an org-A member to an
    available seat; the `seat_assignment` row exists and `GET /me/seats` for A
    shows `assigned+1 / available−1`. Mutation: skipping the write leaves the read
    unchanged — red.
  - `test_a_seat_admin_assign_refuses_at_the_cap` — `available == 0`, unassigned
    member → **409 `buy_more`**, no row written, `assigned` unchanged (no
    oversubscription via self-serve). Mutation: dropping the `decide_assignment`
    guard writes an over-cap row — red.
  - `test_a_seat_admin_key_cannot_write_another_deployments_org` — a key placed
    for A, naming a member of B → refused, and B's seats never move (the
    placement bound; the two-org precedent is
    `test_the_seats_read_is_scoped_to_the_key`). Mutation: dropping the
    placement join lets A write B — red.
  - `test_a_non_admin_actor_is_refused` — the resolved actor's
    `org_membership.role='member'` (or `status!='active'`) → **403**, nothing
    written. Mutation: removing the role check lets a plain member assign — red.
    **This is the fence that proves the console, not only Next, enforces "admin,
    not any member".**
  - `test_an_unknown_or_cross_org_target_member_is_refused` — a `member_email`
    with no membership in the resolved org → refused, and no `user_identity` is
    created for it (self-serve must not `ensure_identity`-mint arbitrary emails).
  - `test_a_seat_admin_release_frees_the_seat` — release drops `assigned` by one
    and the read reflects it; releasing an unassigned member is a 200 no-op.
  - `test_the_seat_admin_door_needs_the_capability` — a deployment key holding
    only `{resolve}` is **403** at the write, logged, exactly the shape
    `deployment_or_operator` already enforces for `provision`
    (`auth.py:387-410`); an org key (`cc_live_…`) is refused at the door (401),
    proving the org key gained no write.
  - **Structural, no DB (already in §7's list):** the two write routes read as
    **authenticating** (the new `_seat_admin_dependency` closure is in
    `AUTHENTICATING_DEPENDENCIES`, so `test_the_unauthenticated_route_set_is_exactly_health`
    — which lives in **`tests/unit/test_customer_console_resolve.py`**, NOT
    `test_console_dependency_boundary.py` — stays green, and that suite's
    parametrised clause-1 fence now maps both routes to `seat_admin` in
    `_CAPABILITY_GATED_ROUTES`), and
    `test_no_org_key_route_writes_an_entitlement_or_ledger_row` (in
    **`test_customer_console_payments.py`**) **stays green** — the write is not an
    org-key route, which is the whole reason the deployment-key door was chosen.
    Both suites are already named in `pr-check.yml`'s skip-guard, so **nothing new
    to register** — the behavioural fences ride `test_customer_console_payments.py`
    and the structural ones the two suites above.
- **Route naming (agent default, refinable at build):** site the two writes in
  the deployment-key namespace beside `POST /registry/resolve` —
  `POST /registry/seats` (assign) and `POST /registry/seats/release` — because the
  DOOR is the registry (deployment) credential, not the org key; `/me/seats` is
  reserved for the org-key read whose `/me` means "the credential's own org", which
  a pooled deployment key is not. The builder may choose a `/billing/seats/*`
  variant instead; the door, the vocabulary and the fences do not depend on the
  path.
- **Gate label.** The endpoint + the `seat_admin` capability door + the fences,
  built against fixtures and a real Postgres with a **test** `seat_admin` key, are
  🟢 **AGENT-SAFE** (ship dark; no live key carries the capability). The **live
  seat write against a real customer org** stays 🔴 **OWNER-GATE (§8 gate 4)**, as
  does **adding `seat_admin` to a real deployment key** (🔴 **§8 gate 8**'s
  capability-growth class — the same door that gates `provision`), deploying the
  Console, and any flag flip. Assigning a seat moves an entitlement; doing it on a
  live org is the owner's act.
- **Surface follow-up (WS-30), and the residual it carries.** The customer-facing
  "manage seats" UI (assign/release from the SC-1a seats panel,
  `subscription_console.md` SC-2) is **WS-30's**, not this item's — this ticket is
  the backend write + door. Two things WS-30 must own, flagged so it does not
  rediscover them: (1) the write is **admin-gated at the surface** on the tenant
  billing-admin capability — a *hard* done-when, NOT inherited as the read's
  "known-open any signed-in member" posture (`seats/route.ts:26-28`), because a
  write is not a read; and (2) the transport, **RESOLVED 2026-08-21 to the gateway
  tier** (§9 residual 7) — the deployment key is fenced OUT of the Next/browser
  tier (`gateway.test.ts:232`, §6(f): *"read on the GATEWAY and never in Next"*),
  so a browser "manage seats" action reaches this deployment-key door through the
  **gateway tier** (where the key already lives and already speaks to the Console
  at sign-in resolve, mirroring the CP-2c signup hop), not through the Next
  billing BFF: a Next hop forwards the SESSION identity only (R11), and a gateway
  seat-admin proxy attaches the deployment key + `actor_email = session email` and
  calls `POST /registry/seats`, so the admin gate stays console-side. The rejected
  alternative — a dedicated member-authenticated Console credential — is recorded
  in §9 residual 7. The two-slice split and the transport done-whens are
  `subscription_console.md` SC-2.

**(i) One more SMALL customer-key read the manage-seats surface needs, and it
belongs to this service — the member-list read.** ✅ **BUILT 2026-08-21
(WS-31, `ws-31-members-read`)** — a separate agent built it and an independent
audit validated this clause first; the READ is agent-safe, Console deploy /
flags / a live key carrying it stay owner-gated and the owner ratifies the door
(the PII/gating consideration below) at flip-time. It is the **exact sibling of
item (g)'s seats read**: item (g) gave a customer credential a way to see its
seats *(how many)*; the "manage seats" surface (SC-2b — the assign-a-seat panel,
`subscription_console.md` SC-2) also needs to see **WHO** to assign them to. Item
(h) shipped the seat WRITE, whose assign step takes a `member_email`; a surface
cannot offer that email without a roster to pick from. Today **no
customer-reachable read returns an organization's member list.** The membership
reads that exist are all single-member or by-email-across-orgs, never a per-org
roster: `_seat_admin_for_deployment`'s `SELECT role, status FROM org_membership …
WHERE organization_id = :org AND user_identity_id = :i` (`main.py`, one member by
identity) and `_resolve_for_operator`'s identical single-member read; `store.
deployment_visible_orgs` (`store.py:697-755`) joins membership but keys on an
EMAIL and returns *orgs*, not a roster; `store.org_owned_by_other`
(`store.py:626-665`) is an `EXISTS` owner check. So the customer's own member
grid (SC-1a/SC-2) had no data source until this. *(All anchors re-measured
2026-08-21 on `ws-31-members-read`; the §0 drift caveat applies — match by
handler/symbol name, not by the line a stale citation carries.)* Decided with the
same agent-proposed defaults items (f)/(g) used (D16/D17 — the owner may overrule;
the rejected alternative and the residual are recorded):

- **Route:** a dedicated `GET /me/members`, the **exact sibling of `GET /me/seats`
  (item (g)) and `GET /billing/catalog` (item (f))** — a single `PayingCaller`
  org-key read, not a `members` block bolted onto `GET /me/billing` or `GET /me`.
  A new `PayingCaller` read is the same move items (f)/(g) made and the SAME seam,
  not a parallel one.
- **Auth:** the **`can_pay` door** (`organization_for_payment` / `PayingCaller`,
  `auth.py`) — NOT the Operator door, NOT `KeyCaller`, and NOT the
  deployment-key/`seat_admin` door (that is item (h)'s WRITE door). A
  **suspended** organization may read its members (it is the org deciding whom to
  seat as it decides whether to pay — the §9.3(5) reasoning item (g) records one
  route along), and a **deleted** one 403s. The organization is a property of the
  credential (`caller.organization_id`), so no `org_slug` reaches the wire and org
  A can never name org B — the same construction `my_seats`/`my_billing` rely on
  (R11), not a `WHERE` a reader has to remember.
- **Payload:** for each membership row in the calling org, the triple
  `email · role · status` — `user_identity.email` (`CITEXT`) joined on
  `user_identity_id`, `org_membership.role ∈ {owner,admin,member}` and
  `status ∈ {invited,active,suspended,removed}` (`001_customer_console.sql:121-131`)
  — and **nothing else**. Shape it with a `MembersView` / `MemberView` pair
  mirroring `SeatsView` / `SeatPlanView` (`main.py`), so the field set is pinned on
  the model as well as on the wire. **No status filter and no per-member policy**
  (the store's "fetches rows, decides nothing" doctrine, `store.py:1-6`): every
  membership row is returned with its `status` on the wire, and the surface
  chooses which statuses to render — the same decision `deployment_visible_orgs`
  records for *not* consulting `status` (`store.py:720-724`). *(The alternative —
  the read itself filters to `{invited,active}` so a `removed` member's email
  never boards — is left as an owner-overrulable refinement, §9's residuals: it
  bakes surface policy into a read and narrows a roster the write path
  (`_seat_admin_target`) does not narrow, so it does not ride this read's PR.)*
- **Reuse, not fork — the ONE membership-list read.** The read composes the
  established `org_membership ⋈ user_identity` join idiom — the **same join**
  `store.org_owned_by_other` (`store.py:654-660`) and `store.deployment_visible_
  orgs` (`store.py:743-749`) already use — into a single new store helper
  (`store.org_members(conn, *, org_id) -> [{email, role, status}]`,
  `SELECT ui.email, m.role, m.status FROM org_membership m JOIN user_identity ui
  ON ui.id = m.user_identity_id WHERE m.organization_id = :org ORDER BY ui.email`).
  It is a **new helper because none of the existing reads returns a per-org
  roster** (they are single-member-by-identity, by-email-across-orgs, or an
  owner-`EXISTS`), not a fork of one — and it becomes the ONE member-list read
  future consumers extend rather than re-query. `ORDER BY ui.email` for a stable
  answer, exactly as `deployment_visible_orgs` orders by `o.slug`. The route folds
  it into `MembersView`; there is no second membership SQL and no recompute.
- **Tenant isolation:** the read is scoped to `caller.organization_id` from the
  org-key door (`WHERE m.organization_id = :org`) — by construction, the same
  property `my_seats` relies on. Org A's read never returns org B's members.
- **Per-member seat summary — DEFERRED, investigated.** A per-member "which seats
  does this member hold" summary is **not** a pure read off `org_membership`: it
  needs a second query over `seat_assignment` grouped by `user_identity_id`
  (`WHERE released_at IS NULL`), i.e. a recompute this read is defined not to
  carry. So the launch payload is the membership triple only; the surface joins
  `GET /me/members` with `GET /me/seats` (item (g)) client-side, or a **later
  slice** adds a per-member seat column when a done-when needs it. Recorded here so
  the builder does not fold a second seat computation into a membership read.
- **⚠️ PII/gating consideration (surfaced, with a recommendation).**
  `GET /me/members` exposes member **EMAILS** (PII) to any signed-in org member —
  materially more sensitive than the seats read's bare counts. It rides the **same
  session-gated-org-reads "any signed-in member" hardening posture item (g)
  already flags** (`workbench/control_plane/src/app/api/billing/seats/route.ts:26-28`,
  *"known-open 'any signed-in member' posture … its own ticket to fix"*, the B7
  block's foot): the Next BFF gates the read on *signed-in*, not on a
  billing-admin capability. This read does **not make that posture worse in kind**
  (it is one more org-scoped read on the same door) but it does **widen what a
  non-admin member can see** from counts to a roster of emails, so it is called
  out explicitly and carried on that existing hardening ticket rather than left
  implicit. **The ALTERNATIVE, recorded and NOT chosen (owner-overrulable):**
  admin-gate the member read — either at the surface on the tenant billing-admin
  capability (as item (h)'s WRITE surface is), or console-side behind the
  deployment-key door like the write — so only an admin sees the roster. It is
  rejected as the default for two reasons, both matching item (g): (a) the read
  **moves no value and mints nothing**, and item (g)'s precedent puts mint-nothing
  customer reads on the org-key `can_pay` door, not the write's `seat_admin` door;
  and (b) admin-gating a READ would fork it off the seats-read sibling shape onto
  the write's transport for no write. **Recommendation:** ship on `can_pay` as the
  exact sibling of the seats read, carry the email-PII flag on the existing
  "any signed-in member" hardening ticket, and leave admin-gating the roster as an
  owner decision. *(If the owner judges the email exposure unacceptable on a
  session-only gate, the admin-gate alternative is the pre-recorded overrule — but
  that is an owner call, not an agent default.)*
- **Fences (R7), in `tests/unit/test_customer_console_payments.py`** — beside
  `TestTheSeatsRead`, and **already on §7's command list and `pr-check.yml`'s
  skip-guard, so there is nothing new to register** (the read rides item (f)/(g)'s
  suite). Both shown red first, both **R8** against a real Postgres 16 on the
  Console ladder:
  - `test_the_members_read_is_scoped_to_the_key` — provision two orgs, give each a
    distinct extra member, and assert org A's `GET /me/members` never carries a
    member (email/role/status) belonging to org B; A presents its own key AND
    (deliberately) B's slug on the query string, and the slug is inert. The
    two-org isolation precedent is `test_the_seats_read_is_scoped_to_the_key`.
    Mutation evidence to run and revert: driving the read from an `org_slug` query
    param instead of `caller.organization_id` returns org B's members — red.
  - `test_the_members_read_carries_the_membership_triple_and_nothing_else` — the
    field set is **exactly** `{email, role, status}` on the `MemberView` model AND
    on the wire (no `organization_id`, no `identity_id`, no seat count), pinned for
    `SeatPlanView`'s reason (a field nothing reads is a field somebody eventually
    reads); the roster equals `store.org_members` for the org fed the same fixture
    (e.g. the provisioned founder as `owner/active` plus an added `member/invited`),
    never a second SQL; a **suspended** org reads it, a **deleted** org 403s.
    Mutation evidence: adding an `organization_id`, an `identity_id` or a `seats`
    field to `MemberView` fails the field-set equality; a second query that
    recomputes the roster diverges from `store.org_members`.
- **Gate label:** the READ is 🟢 **AGENT-SAFE** (fixtures / real Postgres). It
  moves no value and mints nothing. Deploying the Console and flipping any flag
  stay 🔴 **OWNER-GATE**, as does admin-gating the roster if the owner overrules
  the door above.
- **No migration:** a read over `org_membership` + `user_identity` in `001`
  already ships — no new table, no new column. *(R1 note for completeness: were a
  migration ever needed, the next free Customer-Console number is taken at build
  time by listing `infra/customer_console/` — never written ahead; this item needs
  none.)*
- **The SC-2b consumer.** The manage-seats UI (SC-2b, `subscription_console.md`
  SC-2, WS-30's) renders this member list **beside** the seats (item (g)) and the
  assign/release controls (item (h)) — the admin picks a member from the roster
  and assigns them an available seat. The read's **surface transport mirrors the
  seats read's** (`seats/route.ts` / `catalog/route.ts`): a Next BFF hop
  presenting this deployment's own `cc_live_…` org key, NOT the gateway
  `seat_admin` proxy (that proxy is the WRITE's transport, §9 residual 7). The
  surface slice is WS-30's; this item is the backend read + its data source.

**(j) The MANUAL / bank-transfer activation — the OFFLINE twin of
`payments.fulfil`** — ✅ **BUILT 2026-08-21 (WS-31, `cc-manual-activate`)**: one
route, one request model, one new ledger reason, no migration. Platform staff
activate a PAID plan for a customer that paid OUT OF BAND (bank transfer, cash,
cheque) — there is no Razorpay order, and `payments.fulfil` (the checkout writer)
is fenced to exactly its two capture/redemption call sites (done-when 9), so the
offline path is a **sibling** that reuses the same store seams, never a third
`fulfil` call site. `org_subscription.provider` is `CHECK (razorpay | manual)`
(`001_customer_console.sql:163`) and `'manual'` had no writer until this — the
schema pre-provisioned exactly this door.

- **Route:** `POST /billing/subscriptions/activate`
  (`main.py::activate_subscription_manual`).
- **Auth:** the **`Operator`** door — a cross-org staff act that NAMES the org by
  `org_slug`, exactly as `POST /billing/seats` does. **R11 does not bind a
  named-org staff route** (the operator credential carries no tenant), but the org
  is still resolved from the validated slug (`_org_id`, 404 if unknown), never
  taken as a body identity. A customer `cc_live_` org key, a `cc_depl_`
  deployment key and no-auth are each **401** — the same refusal every other
  Operator route gives, and it is NOT reachable by any customer or deployment
  credential.
- **Behaviour (one transaction, the same seams `fulfil` uses, minus the order):**
  `store.activate_subscription(provider='manual', …NULL provider ids…)` writes
  `status='active'` with a real period (term from the request, default
  `payments.SUBSCRIPTION_TERM_MONTHS`); `store.grant_seats(reason='manual')`
  grants the plan's PAID seats; `store.add_credit(reason=LEDGER_REASON_MANUAL,
  ref=<reference>)` runs only when the request carries `credits`. It does **not**
  touch `organization.status` — same as `fulfil` (done-when 16). The response
  surfaces the activated subscription plus the `_seat_grid` and credit balance the
  reads already emit — never a recompute.
- **Idempotency (the double-grant guard):** activating grants paid capacity and
  `grant_seats`/`add_credit` are append-only INSERTs, so an org already holding an
  **active** subscription is refused **409** (`already_active`) — the simpler safe
  rule the ticket named. An operator adjusts a live term through the seat/credit
  routes, never by re-activating; a `trial` (or any non-active) subscription, and
  an org with no subscription row, activate. Because the 409 is a check-then-grant,
  it is **serialised by an org-keyed advisory lock** —
  `store.lock_org_activation(conn, org_id=…)`, the `lock_seat_capacity` /
  `lock_discount_capacity` idiom (`pg_advisory_xact_lock(hashtext('activation:<org_id>'))`),
  taken as the first statement of the transaction, before the status read, and
  auto-released at txn end. Per-org key, so activations of different orgs never
  serialise. `FOR UPDATE` alone would not do (a fresh org has no `org_subscription`
  row to lock). So the guarantee — **exactly one seat grant, one ledger row, one
  subscription row** — holds under **concurrency** (two simultaneous activations of
  one fresh org), not merely a sequential repeat.
- **Audit:** `_audit(action="subscription.activate_manual", actor="operator")`
  with `reason='manual'`, the operator's free-text `reference`, the plan, seats
  and term in the JSONB `detail` (`control_audit` has no `reason` column, so the
  structured facts ride there, as every other audit does).
- **The ledger reason:** `credits.LEDGER_REASONS` grew one member,
  `LEDGER_REASON_MANUAL = "manual"` — the offline twin of `purchase`, its own word
  so a term settled by bank transfer stays distinguishable from a processor
  capture, and the same word `seat_grant.reason`, the audit row and
  `org_subscription.provider` all carry for this activation. `credit_ledger.reason`
  is bare TEXT with no CHECK (R6, `credits.py`), so the addition needs no
  migration; the value-level fence is the vocabulary set and the `add_credit`
  call-site scan, both green with `'manual'` in them.
- **Fences (R7/R8), in
  `tests/unit/test_customer_console_payments.py::TestTheManualActivation`**
  (already on §7's list and pr-check's skip-guard), all against a real Postgres:
  the happy path; credits-optional; `provider='manual'` not `'none'`; a custom
  term; idempotency (409, no double-grant); **the concurrency proof** (two threads
  activating one fresh org, released on a `threading.Barrier`, land exactly one
  grant / credit / subscription and results `[200, 409]` — red-first without
  `lock_org_activation`, ten iterations, the `test_a_concurrent_double_redeem`
  idiom); unknown/inactive plan → 400 writing
  nothing; unknown org → 404; **authz mutation-proved** (org key / deployment key
  / no-auth → 401 and write nothing, the operator admitted; the 401 shown red
  first by neutralising the guard via FastAPI `dependency_overrides` and
  restoring it — no source edit); the provider CHECK and the ledger vocabulary
  unweakened.
- **Owner-gated, unchanged:** the Console deploys nowhere (ships DARK, no flag,
  following the reads), the operator token is owner-held, and issuing a
  `cc_depl_`/operator credential is §8's. This item is the route existing, not
  running. *(R1: no migration is needed here; were one ever required, the next
  free Customer-Console number is taken at build time by listing
  `infra/customer_console/`, never written ahead.)*

## 7. Verification

```bash
# Customer Console. ⚠️ These need a REAL Postgres or they SKIP THEMSELVES (R8) —
# a skipped R8 test proves nothing:
#   export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
uv run pytest tests/unit/test_customer_console_seats.py tests/unit/test_customer_console_credits.py \
              tests/unit/test_customer_console_keys.py tests/unit/test_customer_console_sql.py \
              tests/unit/test_customer_console_api.py tests/unit/test_customer_console_key_auth.py \
              tests/unit/test_customer_console_router.py tests/unit/test_customer_console_lifecycle.py \
              tests/unit/test_customer_console_resolve.py \
              tests/unit/test_customer_console_payments.py \
              tests/unit/test_customer_console_operator_list.py
# ⚠️ The `_payments.py` line is CP-9 + SC-4g's suite, added 2026-08-18 IN THE PR
# THAT CREATED IT, together with `pr-check.yml`'s skip-guard entry (the Console
# hand-list went 6 -> 7). It needs no Razorpay account: the seam runs against
# `payments.FakeProvider`, which signs with the REAL HMAC-SHA256 algorithm, so
# only the network is fake. It reads this file and the workflow and fails if
# its own name is dropped from either.
# ⚠️ The `_operator_list.py` line is CP-8 slice 1's suite (the cross-org
# customer list, `GET /orgs`), added 2026-08-22 IN THE PR THAT CREATED IT, with
# `pr-check.yml`'s skip-guard entry (the hand-list went 7 -> 8). It proves the
# read is Operator-only (a cc_live_ customer key is 401), that its seat grid is
# byte-identical to `GET /billing/summary`'s (the ONE seat vocabulary), and that
# MRR is paise and zero unless the subscription is active. It reads this file
# and the workflow and fails if its own name is dropped from either.

# CP-2b DEPLOYMENT half (added 2026-08-18 with the B3 answer, §6(i);
# the VARIABLE NAME corrected the same day with the B-a answer).
# ⚠️ A SECOND, DIFFERENT database: the tenant one, on an image that HAS pgvector
# (infra/postgres/01_schema.sql:5-6 needs uuid-ossp AND vector). The suite builds
# its schema from infra/postgres/ via tests/unit/_tenant_ladder.py and gates on
# TENANT_LADDER_DATABASE_URL *as pytest was launched with it* (the snapshot line
# beside tests/conftest.py:16).
#
# ⚠️⚠️ THIS EXPORT IS NOT `DATABASE_URL`, AND THE TWO MUST NOT BE MERGED.
# Setting DATABASE_URL for a `tests/unit/` run arms test_tenant_coverage.py's two
# DB-gated tests (:208-238, :241-258), which fail by construction against a
# freshly-replayed ladder: one demands FORCE-RLS policies that live only in
# infra/postgres/generated/04_policies.sql (never replayed — that file's own
# header, :19-22), the other demands a non-superuser app role. Both are
# WS-29 MT-1b/MT-1c's gates, not CP-2b's. See §6(i)(2).
#   export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1/acb_tenant
uv run pytest tests/unit/test_deployment_resolve_cache.py

# CP-2c slice 2 — the self-serve signup route + the Console-provision client
# (added 2026-08-20 IN THE PR THAT CREATED IT, together with pr-check.yml's
# skip-guard entry). ⚠️ Needs BOTH databases: the tenant ladder (the two-org
# classification + the step-1 backstop writes-nothing) and the Console ladder
# (GST lands on the org row; the deployment-key arm is idempotent on the slug).
# The route unit tests take neither (fakes) and always run; a DB-gated class
# skips with the standard message, which pr-check's two greps catch.
#   export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
#   export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1/acb_tenant
uv run pytest tests/unit/test_signup_provision_route.py -rs

# CP-2b's structural fences. Deliberately NEED NO DATABASE, so they must never
# move into the suite above — folded in, they would skip with it, which is the
# CP-3 disarmed-gate failure this ticket is built to avoid.
# test_signin_resolve_route.py is the ROUTE's own suite (finding F5's fence, a
# P0): it opens no session on purpose, and it is the only thing in the tree that
# tests POST /signin/resolve at all:
uv run pytest tests/unit/test_console_dependency_boundary.py \
              tests/unit/test_signin_resolve_route.py

# The seam and tenancy ratchets this must not regress. ⚠️ test_tenant_coverage.py
# keeps its OWN DSN discipline: its source-level tests always run, and its two
# DB-gated tests answer to DATABASE_URL and SKIP without one — which is what they
# do in CI today and what this ticket leaves untouched. Do not export
# TENANT_LADDER_DATABASE_URL as DATABASE_URL to "make them run": they need a
# database with 04_policies.sql promoted and a non-superuser role, i.e. MT-1b/1c.
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py

# The LLM choke point (existing suites that MUST stay green through the rework)
uv run pytest tests/unit/test_v1_compat_telemetry.py tests/unit/test_v1_compat_max_tokens.py \
              tests/unit/test_llm_usage_telemetry.py tests/unit/test_model_limits.py \
              tests/unit/test_byok_default.py

# Frontend — carries CP-2b's signIn-callback and refusal-copy fences
# (workbench/control_plane/src/app/signin/signin.test.ts)
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

**R8 binds CP-2, CP-2b, CP-3, CP-4b, CP-6 and CP-9 specifically** — their subject is
queries, migrations and predicates, so they are run against a real Postgres before
they are believed. *(CP-9 added 2026-08-18 with the payment seam: its subject is a
CHECK-constrained state machine, a `UNIQUE` idempotency key and a fulfilment
**transaction** — the exact class a hermetic fake agrees with. Its suite
`tests/unit/test_customer_console_payments.py` joins the command block above and
`pr-check.yml`'s hand-maintained skip-guard list **in the PR that creates it**,
as CP-2b's did; that list discovers nothing.)* *(CP-2b and CP-4b added 2026-08-18: both of their done-when
lists already mandate R8 in their own words — CP-2b clause 10 and CP-4b's
metering clause — so this line was simply behind them.)* CP-2b's new suite
`tests/unit/test_customer_console_resolve.py` joined the command block **in the
PR that created it**, together with `pr-check.yml`'s skip-guard entry.
⚠️ **CP-2b binds R8 against TWO databases, and the second one does not exist in
the `test` job yet** *(recorded 2026-08-18 with the B3 answer; the variable name
and the image corrected the same day with B-a/B-b)*. The Console half runs on
`CUSTOMER_CONSOLE_DATABASE_URL`; the deployment half's projection, `resolved_at`
clock, `registry_status` and `registry_capabilities` columns live in the
**tenant** database and answer to **`TENANT_LADDER_DATABASE_URL`** —
deliberately **not** `DATABASE_URL`, because `pr-check.yml:157` runs the whole
`tests/unit/` directory and `DATABASE_URL` there would arm
`test_tenant_coverage.py`'s two DB-gated tests, which fail by construction on a
freshly-replayed ladder (§6(i)(2) names both and why each is WS-29's problem).
`pr-check.yml`'s `test` job provisions one Postgres service (`:113`, image
`postgres:16`) and exports one DSN (`:127`). A tenant-side R8 suite merged
before the second service exists would skip in CI and report green. The build
slice adds the service — on **`pgvector/pgvector:pg16`**, because
`infra/postgres/01_schema.sql:5-6` needs the `vector` extension and the
`migrations` job (`:235-291`) already uses that image for the same file — the
DSN, the reachability assertion and the skip-guard entry **in the same PR as the
suite**; clause 10 mandates it.
**R1: migration numbers are taken at build time** — list the owning directory
(`infra/customer_console/` for a Customer Console migration, `infra/postgres/`
for a tenant one) and re-check at merge. *(The absolute that stood here, "highest
on disk at authoring was 170", was stale within days and is exactly the citation
class R1 exists to stop; corrected to the procedure 2026-08-18.)*

## 8. Gate labels

**🟢 AGENT-SAFE — all of §6.** Building every table, endpoint, fence and console
surface, against fixtures.

**🔴 OWNER-GATE — refuse by name, build the thing and hand it over:**
1. **Splitting `GATEWAY_INTERNAL_TOKEN` from `LITELLM_MASTER_KEY`** (§4.3) — a
   credential rotation via redeploy. Existing gate, work_plan §6.
2. **Deploying the Customer Console anywhere**, and any VPS reach.
3. **Live Razorpay credentials**, and any real payment configuration — **CP-9**
   *(re-pointed 2026-08-18: this gate said CP-8, which is the Operator Console)*.
   ⚠️ **Extended the same day: CREATING the Razorpay account is owner-side even
   in TEST mode**, and so is writing test-mode keys into CI or any deployment's
   env. Any external commercial account is the owner's act
   (`customer_console_infrastructure.md` §5 item 5, §7). The consequence is
   stated in CP-9 §9.7 rather than left to be discovered: an agent builds the
   whole seam against the fake provider and **cannot** run WS-30 SC-4g clause
   2's test-mode capture rehearsal — it is scripted and handed over.
4. **Editing any live organization's entitlements, seats or credit balance** — same
   gate the Subscription Console's fulfilment already carries.
5. **Flipping CP-4's Router flag ON for a real customer**, and the §5.1 pooled cutover.
   ⚠️ *That flag does not exist in code (CP-4's 2026-08-18 amendment); the gate is
   correct and currently ungrounded — it binds the first caller ticket.*
6. **Issuing a production `cc_live_` key to a real organization.**
7. **Issuing a `cc_depl_` deployment key and setting it in a live deployment's
   env** (CP-2b, added 2026-08-18) — a credential issuance *and* a deployment env
   write. Minting keys against fixtures is AGENT-SAFE; a real one is not.
   **And, extended 2026-08-18 by the F1 repair: setting
   `CUSTOMER_CONSOLE_RESOLVE_ENABLED=true` on a live deployment.** It is the
   Next-side switch that arms the sign-in hop, and turning it on converts every
   Console/gateway outage into a sign-in refusal (by design — §6(g)), so it is
   a deliberate availability trade nobody but the owner may take. *Declaring
   the flag, defaulting it OFF and testing both positions is AGENT-SAFE;
   writing it into a running box is not.*
8. **Setting `SELF_SERVE_SIGNUP_ENABLED=true` on a live deployment, and
   expanding a real deployment key's capability set beyond `{resolve}`**
   (CP-2c, added 2026-08-19 by D46). The flag opens organization creation to
   the public internet; the capability growth is gate 7's issuance class
   applied to a wider credential. Building the flow against fixtures, minting
   `{resolve, provision}` keys in tests, and fencing both flag positions is
   AGENT-SAFE.

*All eight are registered in `work_plan.md` §6 (seven as of 2026-08-18, the
eighth as §6(h) on 2026-08-19) — a gate that lives only in a spec is a gate
the dispatch board cannot enforce.*

## 9. Open owner inputs

Everything needed to build §6 is decided. Most of these are commercial and none
blocks a §6 build (6 is an authorization ratification; **7 — the item-(h)
surface transport — is RESOLVED 2026-08-21 to the gateway tier**, below, and now
gates only how WS-30 wires it, not whether):

1. **Seat price for the AI-only shape** — if an organization may buy AI credits
   without Center packages, that SKU is not in D23/D24's ladder. Assumed **no**
   until stated: credits are an add-on to a subscription, not a standalone product.
2. **Trial credits** — how many credits a trial organization starts with, and
   whether they survive conversion to paid.
3. **Auto-top-up default** — §3.3 says it is the default for paid plans; the trigger
   threshold and the top-up amount are unset.
4. **The credit-pack ladder** *(added 2026-08-18 with CP-9)* — WS-30 SC-4a
   assumes "a short ladder of pre-priced packs" and **no such ladder exists
   anywhere**: `plan_catalog.kind` admits only `core|center|addon|bundle`
   (`001_customer_console.sql:144`) and `002_seed_catalog.sql` seeds no pack.
   D19.2 fixes the ₹10 credit **unit**, not a pack price. Pricing the ladder is
   an owner act (§8 gate 4). **This one BLOCKS the pack half of SC-4a** — which
   is why CP-9 §9.1 scopes the launch checkout to the seeded subscription ladder
   and SC-4a's pack clauses are deferred rather than built against a guess.
5. **The Razorpay account itself, test AND live** *(added 2026-08-18)* — §8 gate
   3. Not commercial-only: **it blocks the capture rehearsal**, so it is the one
   item on this list that gates an acceptance clause (SC-4g clause 2) rather
   than a price.
6. **Ratify or overrule the `METERING_EXEMPTION`** *(added 2026-08-18 with
   CP-9's finding F4 — the one item here that is not commercial)*. §9.3(4) says
   **exactly one** edge may be allow-listed; the build needed a **second**, for
   CP-6's pre-existing metering draw on the organization-key Router route
   (`chat_completions → store.record_usage → store.add_credit`, shipped
   2026-08-12), and exempted it **by name** in its own constant with its own
   count-and-contents fence rather than narrowing the fence or growing the list
   the ticket counts. Independent verification judged the deviation **sound and
   minimal** and still recorded it here, because a deviation an agent declares
   is not a rule an agent may change: the owner either ratifies it (and §9.3(4)
   is amended to read "one carve-out plus one named pre-existing exemption") or
   overrules it, in which case the draw moves behind the internal token — which
   is what `/usage/record` had to become after verification minted 100,000
   credits through it. `test_the_metering_exemption_is_still_needed_and_still_that_shape`
   goes red the day the draw moves, so the exemption is deleted rather than
   inherited either way.
7. ✅ **RESOLVED 2026-08-21 (owner call) — the GATEWAY TIER carries the customer
   seat WRITE; the deployment key stays gateway-side.** *(Opened 2026-08-21 with
   WS-31 `ws-31-seat-assign`; not commercial, and it gates the WS-30 "manage
   seats" surface, not the WS-31 backend door — which shipped dark and fully
   fenced regardless.)* Item (h) sites the write on a **deployment-key
   `seat_admin` door** because that is the only customer credential from which the
   Console can itself authorise "admin, not any member" (the org key is *the org*,
   with no member — §6(h) crux). But the deployment key is **fenced out of the
   Next/browser tier by name** (`gateway.test.ts:232` / §6(f): *"read on the
   GATEWAY and never in Next"*), and "manage seats" is a Next billing-settings
   surface — so a browser action cannot present the key.

   **The resolution (option (a), chosen):** route the browser action through the
   **gateway tier**, where the deployment key already lives and already speaks to
   the Console at sign-in resolve. The shape, mirroring the CP-2c signup hop
   (`routes/signup.py`, which already reaches `console_resolve` with a
   session-only identity):
   - the seats panel calls a **Next hop** (`app/api/**`) that forwards the
     assign/release with the **SESSION identity only** — the browser names no org
     and no actor (R11); the hop reaches the gateway through the existing
     BFF-internal bearer seam (`lib/gateway.ts` — `gatewayHeaders` /
     `requireIdentity`, so the internal bearer never travels without an identity),
     and holds **no** deployment key, so `gateway.test.ts:232` stays green;
   - a **gateway seat-admin proxy route** (posture copied from `routes/signup.py`:
     BFF-internal bearer, NOT in `PUBLIC_ROUTES`, session email via
     `get_current_user` which labels and never binds) attaches the **deployment
     key** (which must carry `seat_admin`) and the **acting `actor_email` = the
     authenticated session email**, then calls the Console `POST /registry/seats`
     (+ `/registry/seats/release`) through the **one** Console client
     (`acb_auth/console_resolve.py` — no second gateway→Console client, root
     `CLAUDE.md` §5);
   - the **admin gate stays CONSOLE-side**: `_seat_admin_for_deployment`
     (`main.py:1509`) derives the org from `deployment_visible_orgs(deployment_id,
     actor_email)` and refuses any `actor_email` that is not an **active
     owner/admin** in that org's `org_membership` — so the gateway is a thin
     authenticated proxy that supplies the credential and the acting identity, and
     the money-adjacent authorisation lands where it always did.

   **The rejected alternative — option (b), a dedicated member-authenticated
   Console credential** (a real new auth primitive, entangled with the
   still-unbuilt invite/sign-in leg, so a browser→Next→Console write could carry a
   trusted member identity). Rejected as the default because it funds a **new
   credential and its issuance/rotation surface** to obtain an actor identity the
   gateway tier already holds for free at sign-in resolve — the deployment key is
   already the credential that resolves a *member*, and reusing it costs no new
   auth primitive. It remains the owner's fallback if the gateway is ever to be
   removed from the seat-write path. The earlier **third** option — an org-key
   `POST /me/seats` write with the admin gate pushed entirely into Next — stays
   recorded in §6(h) and rejected there: it needs a fourth carve-out to CP-3's
   org-key entitlement fence (the `METERING_EXEMPTION` shape of ratification, §9
   residual 6) and it cannot enforce the admin axis console-side at all.

   **Inherited residual, stated not hidden** (the same one `routes/signin.py` and
   `routes/signup.py` carry): a holder of the **internal token alone** can assert
   any `X-User-Email`, so it can drive a seat write for an arbitrary `actor_email`
   using this box's deployment key. It is **narrower** here than at sign-in,
   because the Console's own admin gate admits only an `actor_email` that is an
   active owner/admin of a placed org — the attacker can act as a real admin, not
   mint one — and the write is capped and idempotent. Narrowed further by §4.3's
   `GATEWAY_INTERNAL_TOKEN` / `LITELLM_MASTER_KEY` split (§8 gate 1). Accepted and
   recorded, unchanged by this resolution.

   The transport clause, its two-slice split (slice 1 = the gateway proxy + Next
   hop; slice 2 = the assign/release UI), the concrete transport done-whens and
   the named R7 fences live in **`subscription_console.md` SC-2**. **What stays
   OWNER-GATE and is NOT unlocked by this call:** granting `seat_admin` to the
   REAL gateway deployment key (§8 gate 8's capability-growth class), setting the
   deployment key/URL in a live env (§8 gate 7), the live seat write against a
   real customer org (§8 gate 4), and deploying the Console (§8 gate 2). BUILDING
   the transport against a **test** `seat_admin` key / fixtures, dark by
   construction, is AGENT-SAFE.

## 10. References

**Binding:** `saas_multitenancy.md` (§0.9.2 the plane definitions · §3 metering,
now amended by D32.1 · §4 billing · §5.1 silo phasing · §6 blockers) ·
`work_plan.md` §1 (R1–R8) and §3 (D15, D19, D22, D23, D24, **D32**) ·
`subscription_console.md` (WS-30 — the customer-facing client of this service) ·
`department_centers.md` Phase E (WS-16 — per-member caps, same mechanism) ·
`engineering_practice.md` (R6/R7/R8) · `user_management_contract.md` R11/R3.

**Code anchors, verified 2026-08-12** — re-verify at dispatch, never trust from
authoring time: `apps/services/gateway/gateway/routes/v1_compat.py:417`
(`_handle_chat_completions`), `:168` (`_fit_context_window`), `:56`
(`_clamp_max_tokens`) · `packages/acb_llm/acb_llm/client.py:86` (`_TIER_ALIAS_MAP`),
`:517` (`_compute_cost`), `:552` (`_emit_usage`) ·
`apps/services/orchestrator/orchestrator/_model_resolution.py:35`
(`_byok_default_model`) · `packages/acb_llm/acb_llm/key_store.py` (`ProviderKeyStore`,
already `(organization_id, provider)`) · `packages/acb_auth/acb_auth/deps.py`
(`require_llm_api_auth` — the box-wide token CP-3 replaces) ·
`infra/postgres/159_control_plane.sql` (the projection target).
