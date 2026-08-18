# SaaS operations doctrine — how a platform is run, and what a personal brain never needed

**Status:** DOCTRINE — binding on new and changed work; no board row of its own ·
**Date:** 2026-08-12 · **verified against code 2026-08-12** (every finding in §4
carries the file it was measured in; re-verify anchors at dispatch, never trust
them from authoring time) · **Owner:** this spec · **Decisions:** **D33**
(work_plan.md §3) · **Companion:** `customer_console.md` (WS-31) builds the
engine; this document says *what a platform has to be able to do* and *which of our
existing assumptions stop being true*.

> ### `Metorite was designed as one company's brain. Every assumption that made that cheap is now a defect.`
> ### `The question is never "does it work" — it is "does it work for a customer we have never met."`

---

## 1. Why this document exists

Metorite was built as an **AI company brain for one company**: colleagues you
trust, one Microsoft directory, one set of provider keys, one deployment, an
operator who was also the user. Those assumptions were not mistakes — they were
correct, and they made the product cheap to build.

They are now **defects**, and the dangerous ones are not the visible architecture
decisions (those are tracked as D15/D32) but the **quiet conveniences**: a default
that fails open, a literal org slug, a query missing a `WHERE`, an auth provider
pinned to your own directory. None of them look like architecture. All of them stop
a customer from onboarding, or let one customer see another.

This document does three things: **§2** states what a managed SaaS platform must be
able to do, **§3** states the Indian commercial and legal reality of charging for it,
and **§4** is the measured audit of what Metorite currently assumes. §5 maps
gaps to tickets.

## 2. The eight capability domains

A SaaS platform is not "the app, plus billing." It is the app plus eight operational
capabilities, and a platform missing any one of them fails in a way its customers see.

### 2.1 Tenant lifecycle

The full path, each state with an explicit owner and an explicit exit:

```
visitor → signup → provisioning → trial → active
                                     ↓
                              past_due (grace, still working)
                                     ↓
                              suspended (login works · features locked · DATA RETAINED)
                                     ↓
                              cancelled (export window) → deleted (certified)
```

The rules that matter, and why:

- **Provisioning must be idempotent and resumable.** It is a multi-step distributed
  action (create org → seed roles → allocate placement → issue keys → send invite)
  and it *will* fail halfway. A half-provisioned tenant that cannot be re-run is a
  manual database repair on your first busy day.
- **Suspension is not deletion, and login must survive it.** A suspended customer
  who cannot log in cannot pay you. Lock the features, keep the door open.
- **Never delete customer data on non-payment without an export window.** It is a
  trust matter, a DPDP matter (§3.3), and the difference between a churned customer
  who might return and one who warns people away.
- **Deletion must be certifiable.** "We deleted it" is a claim; a deletion record
  naming what was removed and when is evidence. You will be asked for the second.

### 2.2 Identity and access

Four things a personal app collapses into one, which a platform must separate:

| Concern | Question | Must not be conflated with |
|---|---|---|
| **Authentication** | Is this person who they claim? | Anything below |
| **Identity** | Which human is this, globally? | Their membership |
| **Membership** | Which org(s) do they belong to, in what role? | Their identity |
| **Entitlement** | What did their org *buy*? | Their permissions (§2.3) |

The platform requirements that follow:

- **Users arrive from directories you do not control.** A customer's staff are in
  *their* Google Workspace or *their* Entra tenant, or in no directory at all. An
  IdP pinned to your own organization can onboard exactly one customer: you.
- **One human, many orgs.** Your own support staff need this on day two;
  contractors and partners need it by day thirty.
- **Invitation, not provisioning.** An admin invites; the invitee accepts; the seat
  is consumed on acceptance. Hand-provisioning is not a flow, it is a favour.
- **Support impersonation must exist and must be auditable.** Support will need to
  see what the customer sees. Built deliberately it is a logged, time-boxed,
  consent-bounded act; left unbuilt it happens anyway, through a database console,
  invisibly.

### 2.3 Entitlements are not permissions

The single most common architectural error in SaaS, and worth stating flatly:

- **Entitlement** = what the organization *bought*. Answer to "not paid for" is
  **402 + an upsell**, and the customer should *see* the locked thing.
- **Permission** = what this person may *do* with what was bought. Answer to "not
  allowed" is **403**, and the thing should usually be invisible.

Collapse them and you get the two classic failures: an admin who cannot buy their
way out of a 403, and a locked feature that is invisible so nobody ever upgrades.
The intersect is one-directional — **entitlement gates first, permission second** —
and it belongs in one seam, never in route handlers.

### 2.4 Metering and rating

Four separable stages. Merging any two is a bug you find at invoice time:

1. **Measure** — count the raw units (tokens, minutes, rows) at the choke point.
2. **Meter** — attribute them to (org, user, feature) and write an event.
   **Idempotent on a request id**, always: retries and stream reconnects are not
   edge cases, they are Tuesday.
3. **Rate** — apply a *versioned* rate card with an `effective_from`. Never rate at
   read time; a re-priced past is an unauditable past.
4. **Invoice** — aggregate rated usage into a billable document.

Non-negotiables: the ledger is **append-only** and balance is `SUM(delta)`; raw
events are retained ~90 days and **rollups forever**; and the meter runs where the
metered party cannot host it (see `customer_console.md` §4.1).

### 2.5 Billing and revenue operations

- **Your database is the source of truth for entitlements and usage. The processor
  is the source of truth for money.** Never call the processor on the request path;
  never recompute an invoice it has issued.
- **Webhooks get lost.** Every processor integration needs a **reconciliation loop**
  that compares your subscription state to theirs nightly and alerts on drift. This
  is the part that always bites, and this repo already has a `reconciler` service
  pattern to copy.
- **Dunning is a product surface, not an email.** Grace period, in-app warnings,
  degradation order, and a recovery path — decided in advance, not at 2 a.m.
- **Proration and mid-cycle change** are where seat models leak revenue. Decide once
  whether a seat added on day 20 bills now, next cycle, or prorated.

### 2.6 Support and operations

- **Per-tenant observability.** "Is the platform up" is not the question customers
  ask. Every log line, trace and metric needs a tenant dimension or you cannot
  answer "is it slow *for them*."
- **Per-tenant backup and restore.** ⚠️ **We fail this today** (§4, finding 9). A
  whole-cluster restore means serving one customer's recovery rolls every other
  customer back. That is a cross-tenant defect from customer #2.
- **A tenant-scoped kill switch.** One customer's runaway agent loop must be
  stoppable without stopping everyone.
- **Version and drift visibility.** With silo deployments you must know what SHA
  each box runs, or you are debugging a customer against source they are not running.

### 2.7 Trust and compliance

Data residency · encryption at rest with per-tenant key separation for the sensitive
columns · a subprocessor list (your LLM providers are subprocessors, and customers
will ask) · retention and deletion policy · breach notification path · audit log the
*customer* can read, not just you. See §3.3 for the Indian statutory layer.

### 2.8 Change management

- **Deploy ≠ release.** Ship dark behind flags; flipping is a separate, owned act.
- **Release rings.** Internal → early customers → everyone. With silos this is free;
  pooled, it needs a per-org release channel.
- **Expand/contract migrations, forward-only.** Already binding here as R6.
- **One deploy pipeline parameterised by target — never a per-customer script.** The
  moment two boxes deploy differently, version skew has arrived.

## 3. The Indian commercial and legal reality

Charging Indian businesses has specifics that change product design, not just
paperwork. All verified 2026-08-12; sources in §9.

### 3.1 GST

SaaS is a **supply of services**, taxed at **18%**, never goods. B2B invoices must
carry the customer's **GSTIN** and the **place of supply** — which determines
CGST+SGST (intrastate) versus IGST (inter-state). **E-invoicing (IRN) is mandatory
once aggregate turnover crosses ₹5 crore** in any year from 2017-18 onward.

**What this means for the build:** `organization` needs GSTIN and a registered state
**captured at signup, not at first invoice** — chasing a GSTIN after the fact is how
invoices go out wrong and input credit is denied. The invoice mirror must store place
of supply and the tax split, not just a total.

### 3.2 Recurring payments — the RBI e-mandate framework

This is the one that most directly shapes the pricing surface. Under the **Digital
Payments – E-mandate Framework, 2026** (issued 21 April 2026, effective immediately),
recurring card/PPI/UPI debits run **without** additional factor authentication only up
to **₹15,000 per transaction**; above that, each debit needs AFA (an OTP the customer
must action). Registering, modifying or withdrawing a mandate always requires AFA.
Issuers must send a **pre-debit notification at least 24 hours** ahead.

**What this means for the build — three concrete consequences:**

1. **₹15,000/month is a real product boundary.** At the D23/D24 ladder, an org runs
   under it until roughly 25 seats at ₹600, or 5 seats at ₹3,000. Past that, silent
   monthly renewal stops working and every renewal needs customer action.
2. **Annual billing is not merely a discount lever — it is a friction escape**, and
   it inverts the AFA problem (one large debit needs AFA once, not monthly).
3. **⚠️ Auto-top-up of AI credits is a recurring debit and inherits all of this.**
   `saas_multitenancy.md` §3.3 makes auto-top-up the default for paid plans; under
   this framework an auto-top-up above ₹15,000 will fail without customer action,
   *precisely when the customer is out of credits and mid-workflow*. Design the
   top-up amount under the cap, and treat a failed top-up as the soft-block path
   (§3.3's 402 + overdraft), never as a hard stop.

### 3.3 DPDP

The Digital Personal Data Protection Act and its Rules are in force with a phased
runway: the Data Protection Board is constituted, **Consent Manager obligations land
November 2026**, and **full compliance is May 2027**. 2026 is widely described as a
soft-enforcement year — which is runway, not exemption. Penalties reach **₹250 crore**.

The obligations that touch this codebase directly:

- **Plain-language notice before collection**, with purpose, categories and retention.
- **Separate consent per secondary purpose** — and *"model training, product
  improvement and benchmarking" are named secondary purposes.* For an AI product this
  is the sharp edge: **using customer data to improve the product requires its own
  consent**, distinct from consent to run the product.
- **Breach notification** to the Board and to affected principals, on a clock.
- **A processor's contract does not transfer liability.** Our customers remain
  fiduciaries; we are their processor; but our own LLM providers are *our*
  sub-processors and must be disclosed.
- **Deletion on termination** must actually happen, and be evidenced.

**What this means for the build:** consent is a **data model**, not a checkbox — per
purpose, versioned, timestamped, withdrawable, and queryable at export time. It is
cheaper now than retrofitted onto populated tables, for the same reason envelope
encryption is (`saas_multitenancy.md` MT-5).

## 4. The audit — what Metorite currently assumes

Measured 2026-08-12 against the working tree. **Every row is a personal-brain
assumption that was correct then and is a defect now.** Verdict column: what replaces
it. D33 records the overrides.

| # | Finding | Measured at | Why it was fine | Why it is a defect | Verdict |
|---|---|---|---|---|---|
| 1 | **Sign-in is pinned to ONE Microsoft Entra directory.** The provider comment states it outright: *"The tenant-level app registration ensures only users in the Fracktal Microsoft 365 directory can sign in — no domain check needed."* | `workbench/control_plane/src/auth.ts` | Every user was a Fracktal employee | **A customer's staff are not in your directory. This onboards exactly one customer: you.** Nothing in WS-29/WS-31 ticketed it | 🔴 **CP-0** — multi-directory auth. **Blocks customer #1**, not customer #2 |
| 2 | **Auth fails OPEN when unconfigured.** `hasProvider = Boolean(env.AUTH_MICROSOFT_ENTRA_ID_ID)`; with it unset, *"middleware allows all traffic"* | same file | Laptop dev convenience | A mis-provisioned production box is **wide open**, and it reads as "working" | 🔴 **CP-0** — fail-closed in any non-dev environment; fence it with a test |
| 3 | **The auth contract doc describes a different system than the code.** `deps.py` documents *"X-User-Email — the Google-verified email (fracktal.in domain)"*; the code is Entra, and there is no domain check | `packages/acb_auth/acb_auth/deps.py` header | Both were "our staff" | A security contract that misnames the mechanism is worse than none — R7's point exactly | Correct the docstring in the CP-0 slice |
| 4 | **There is no signup.** No `signup`/`register`/`onboard` route exists in the app tree; the only way in is `ensure_owner_bootstrap()` promoting an `EXECUTIVE_EMAILS` address | `workbench/control_plane/src/app/`, `acb_auth/access.py:581` | The operator was the owner | You cannot sell what nobody can join. Invite-only is a *feature*; **no signup at all is a missing product** | **CP-2a** — signup + provisioning (idempotent, resumable, §2.1) |
| 5 | **The bootstrap org slug is a literal:** `_BOOTSTRAP_ORG_SLUG = "default"` | `acb_auth/access.py:541` | One org existed | Provisioning customer orgs cannot route through a constant | Control Plane provisioning owns org creation; the literal stays only as the first-run path for a fresh box |
| 6 | **The `org` subject means "every active user on the box."** `_ORG_MEMBER_SQL` selects active `app_user` rows with **no org filter** | `acb_auth/access.py:~400` | One org on the box | Under pooled tenancy the `org` audience spans customers | Already tracked (§6.4, MT-1i). **Re-verify, do not assume RLS covers it** |
| 7 | **Deployment-singleton credentials.** `mcp_servers`, `plugins`, `model_config` carry no owner or org column | `saas_multitenancy.md` §6.3 | One key set per box | Tenant config bleeds across customers | MT-1 scope; `provider_keys` already fixed to `(organization_id, provider)` by MT-0d |
| 8 | **`ProviderKeyStore` resolves "the sole organization"** and raises once a second exists | `acb_llm/key_store.py:124` | — | *Not a defect — it is the correct pattern:* **fails closed and names its successor.** Listed so it is not "fixed" into something worse | Keep. Call sites pass an explicit org as they convert |
| 9 | **No per-tenant restore — only whole-cluster.** | `saas_multitenancy.md` §6.6, D31 | One tenant | Restoring one customer **rolls every other customer back** | Cross-tenant defect from customer #2. Needs a ticket; its filtered export must use the same `discover_tables()` set the RLS policies use |
| 10 | **Customer-facing provider/model/tier picker** | `settings/models/page.tsx` | The operator *was* the user | Fixes your provider cost to the customer's choice; gives away the margin lever | Already overridden — **D32.7** |
| 11 | **Onboarding doctrine is about colleagues, not customers** | `specs/colleague_onboarding.md` | Literally true | A customer is not a colleague: no shared directory, no trust, no shared threat model | Keep for internal staff; customer onboarding is WS-31's, and the two must not be merged |
| 12 | **The "trusted colleagues" threat model** underpins parked hardening (D10) | `work_plan.md` D10, MT-0c | True internally | **Selling externally replaces the threat model.** Already stated in §0.9.3 | Unchanged and still 🔴 OWNER-GATE — restated here because §4 is where someone will look for it |

**The two findings that are new, and that no existing ticket covers, are #1 and #2.**
Both are in one file. Together they mean: **today, a paying customer cannot sign in,
and a misconfigured deploy lets anyone in.** Everything else in this table was already
known and tracked.

## 5. Gap table — domain → state → owner

| Domain (§2) | What exists | What is missing | Owner |
|---|---|---|---|
| Tenant lifecycle | `organization` rows; silo deploy pipeline | Signup, idempotent provisioning, the state machine, export window, certified deletion | WS-31 CP-2a |
| Identity & access | Entra SSO (one directory), `app_user`, roles, groups, migration 159's inert split | Multi-directory auth, invitations, one-human-many-orgs, auditable impersonation | **CP-0** + WS-31 CP-2 |
| Entitlements | The `intersect()` seam; permissions model | `module_catalog`, `org_module_entitlement`, `user_module_seat`, `ModuleGate`, 402-vs-403 | WS-31 CP-2 (MT-2 absorbed) |
| Metering & rating | Token+cost computed per call at one choke point (`_emit_usage`) | Persistence, attribution keys, idempotency, rate card, ledger | WS-31 CP-3/4/6 |
| Billing & revenue | — | Everything: subscriptions, invoices, dunning, reconciliation, GST fields | WS-31 CP-2/CP-8 |
| Support & ops | Observability (WS-6), activity bus, `reconciler` pattern | Tenant dimension end-to-end, per-tenant restore, tenant kill switch, version/drift view | WS-31 CP-8 + a restore ticket |
| Trust & compliance | Encrypted provider keys; audit rows | Consent model, subprocessor disclosure, retention/deletion policy, breach path, customer-readable audit | **New — unowned** |
| Change management | Flags, rings doctrine, R6 migrations, one pipeline | Per-org release channel | WS-31 CP-2 |

**Two rows have no owner and should get one before customer #1:** compliance (§3.3 has
a November 2026 date on it) and per-tenant restore (finding 9).

## 6. What this changes about build order

1. **CP-0 comes before everything.** Multi-directory auth and fail-closed defaults are
   not hardening — without them there is no customer #1. This is *new* and it
   re-orders `customer_console.md` §6.
2. **Capture GST fields at signup** (§3.1), in the same slice that creates the org.
   Retrofitting a GSTIN onto invoiced orgs is a customer conversation, not a migration.
3. **Cap default auto-top-up below ₹15,000** (§3.2) and treat a failed mandate as the
   soft-block path. This is a config default with a legal reason — write the reason down.
4. **Model the consent record now** (§3.3), while the tables are empty.
5. **Per-tenant restore needs a ticket** before customer #2, per D31.

## 7. Verification

This document is doctrine; its fences live in the slices it directs. The two it
demands immediately:

```bash
# CP-0 — auth must fail CLOSED outside dev, and must accept a non-Fracktal directory
cd workbench/control_plane && npx vitest run   # + the CP-0 auth suite, created with the slice

# The tenancy ratchets nothing here may regress
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py
```

**R7 applies to this document itself:** every rule above either names the slice that
fences it (§5, §6) or is explicitly advisory. §2 is advisory doctrine; §4's verdicts
and §6's ordering are binding.

## 8. Gate labels

**🟢 AGENT-SAFE:** writing every ticket this document implies; building CP-0's
fail-closed default and multi-directory support against fixtures; the consent data model.

**🔴 OWNER-GATE:** registering any real IdP application · GST registration and any
tax configuration · Razorpay/mandate configuration · publishing a subprocessor list or
privacy notice (**legal review, not an agent's call**) · anything touching a live
customer's data, entitlements or credits · the D10/MT-0c threat-model un-parking.

## 9. References

**Internal (binding):** `customer_console.md` (WS-31 — the engine) ·
`saas_multitenancy.md` §0.9.2/§5.1/§6 · `work_plan.md` §1 R1–R8, §3 D15/D32/**D33**,
§6 gates · `engineering_practice.md` · `user_management_contract.md` R11/R3 ·
`org_access_control.md` · `colleague_onboarding.md` (**internal staff only** — not the
customer path).

**External, retrieved 2026-08-12** — re-check before relying on a date or a threshold:
- GST on software/SaaS, rate and SAC classification — [Tax Garden](https://taxgarden.in/blog/gst-on-it-software-services-india-rates-sac-codes-2026), [RegisterKaro](https://www.registerkaro.in/post/gst-registration-for-software-it-services)
- Place of supply and B2B invoicing — [Perfect Accounting](https://perfectaccounting.in/gst-for-service-businesses-india-2026/), [GST for SaaS companies](https://www.kanakkupillai.com/learn/gst-for-saas-companies-in-india/)
- E-invoicing threshold — [LogicERP 2026 guide](https://www.logicerp.com/blog/how-to-generate-e-invoice-under-gst-in-india-2026-complete-guide-with-process-format-software/)
- **RBI Digital Payments – E-mandate Framework, 2026** (21 Apr 2026; ₹15,000 AFA-free cap; 24h pre-debit notice) — [SCC Times](https://www.scconline.com/blog/post/2026/04/24/rbi-issues-digital-payments-e-mandate-framework-2026/), [Medianama](https://www.medianama.com/2026/04/223-rbi-additional-factor-authentication-e-mandates/), [The420](https://the420.in/rbi-2026-e-mandate-framework-recurring-payments-upi-cards-india/)
- **DPDP Act/Rules timeline** (Consent Manager Nov 2026; full compliance May 2027; ₹250 cr penalties; separate consent for model training) — [Digital Personal Data Protection Rules, 2025 (Wikipedia)](https://en.wikipedia.org/wiki/Digital_Personal_Data_Protection_Rules,_2025), [Fisher Phillips](https://www.fisherphillips.com/en/insights/insights/indias-new-data-privacy-rules-are-here), [DPDPA for IT and SaaS](https://www.cybernx.com/dpdpa-for-it-and-saas-companies/)
