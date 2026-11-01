# Seat plans — one price per seat, AI included, sold worldwide (WS-PLN)

- **Product:** Metorite · **Feature:** seat tiers, individual mode and global sales · **Updated:** 2026-11-02 · **Owner:** vjvarada
- **Status:** 📐 **SPECIFIED, NOT BUILT.** The owner answered PLN-Q1, Q3, Q4 and Q5 on 2026-11-01. §3 is the proposed offer. The owner confirms it in the pull request that adds this spec (PLN-Q2).
- **Verified against code on 2026-11-02 at `2d15a28a1`.** Each path in §2 was read on that commit. Re-verify each anchor at dispatch.
- **Board:** WS-PLN in `work_plan.md` §2.1. **Decision:** D152. §10 lists each decision that it amends or keeps.
- **Metorite:** project "Billing & Accounts", epic 1273, this spec task 1274. §11 gives the task of each slice.
- **Research first:** §14 here, the survey of 2026-11-02, with its sources.
- **Gate summary:** three owner calls open the money work: PLN-Q2, PLN-Q6 and PLN-Q7. A build slice is AGENT-SAFE. A live charge, a live org's price and the launch flip are OWNER-GATE (§11).

---

## 0. Thesis

The owner does not want to charge for a seat and then charge again for AI compute. A customer buys **one seat tier**. The tier holds a monthly AI allowance and a set of features. An org mixes tiers, one tier for each member, and an admin sets the mix.

**One person is an org of one.** An individual account is an organization with one member and private defaults. It is not a second data model, so the upgrade to a team moves no data.

**Sold worldwide from day 1.** The world pays in USD and India pays in INR. Both go live together.

**Text is cheap, so the tiers do not split on text.** Media, live voice, agents that run at the same time, and deep research hold both the cost and the value. The tiers split there.

---

## 1. Scope and non-goals

**In scope.**

- Seat tiers as catalog data, and mixed tiers in one org.
- A monthly allowance in the Router decision, feature gates, and limits that are not AI cost.
- Individual mode, and the upgrade to a team.
- Prices in two currencies, two payment rails, tax identity, a legal pack, fraud controls and a US or EU AI route.
- The public pricing page.

**Non-goals.**

- **One login in two organizations.** One address holds one organization in the first release (PLN-Q5). A switcher is its own HIGH-tier workstream.
- **Team back to individual.** A member who leaves does not take a personal copy (D63 seals the tree).
- **SSO, SAML, SCIM, audit export, a data region choice and approved AI providers.** These are org-wide, so they belong to a later Enterprise add-on and not to a seat tier.
- **More than one language.** The product stays in English at launch.
- **A free tier.** The first step is a 7-day trial with a card (PLN-44).
- **Automatic upgrades.** D19.3 stands: a person always buys by an explicit act. The system suggests, and the admin decides (PLN-11).

---

## 2. What exists today

Each row is a measured fact and what it means for this work.

| Fact | Where | Consequence |
|---|---|---|
| `core` is the one sellable seat, at ₹500. | `customer_console/seats.py:34`, `infra/customer_console/008_flat_plan_d49.sql` | `core` stays the slug of the lowest tier. |
| The unique index covers `(organization_id, plan_slug, user_identity_id)`. | `infra/customer_console/001_customer_console.sql`, `038_app_seats.sql` | One member can hold two slugs today. Mixed tiers need one new rule: one live tier per member. |
| The entitlement mask of `saas_multitenancy.md` §2.3 is designed and NOT built. | No code names `entitlement_mask` | PLN-9 is its first build, and it joins PKG-7 (task 1245). `EffectiveAccess` is at `acb_auth/permissions.py:375`. |
| The payment seam takes one INR order. | `customer_console/payments.py`, `PaymentProvider`, `create_order(amount_paise=…)`, `"currency": "INR"` | ⚠️ The seam has no currency and no subscription. PLN-12 and PLN-41 widen it before a second rail can exist. |
| `org_subscription` and `activate_subscription` exist, with `provider IN (razorpay, manual)`. | `customer_console/payments.py` | PLN-12 and PLN-41 extend that table by migration. A second subscription table is a defect. |
| A credit is priced in rupees. | `infra/customer_console/017_credit_price.sql`, `inr_per_credit`, `usd_to_inr` | The credit stays an internal unit. A credit pack gets a price per currency (PLN-1). |
| `v1_compat._member_for` prefers a signed proof, and its last line still accepts an unsigned `x-cc-member`. | `gateway/routes/v1_compat.py`, `acb_auth/member_proof.py` | A member can still name another member. PLN-6 closes that fallback and proves each caller before any allowance binds. |
| Three AI paths write no usage row. | H-287, task 517 | An allowance fed by `usage_event` would miss them. PLN-7 waits on task 517. |
| `decide_member_cap` exists and nothing calls it on a proven member. | `customer_console/credits.py:679` | PLN-8 wires it. |
| A Console migration runs with no backup of the Console database. | H-170, task 507 | CLAUDE.md §3a rule 1 asks for a backup first. Task 507 comes before the first Console migration of this workstream reaches production. |
| A new root project starts org-visible. | `gateway/routes/projects/tree.py:1192` | In an individual org this becomes a leak at the upgrade. PLN-23 and PLN-31 close it. |
| Signup asks for a company, a slug and a team size up to 10. It asks for no country. | `gateway/routes/signup.py:154`, `SignUpForm.tsx` | PLN-21 and PLN-42 change it. |
| `/v1/images/generations` exists in the Router. Nothing calls it, and no migration binds `tier-image`. | `customer_console/main.py:9834`, `015_tier_pricing.sql` | ⚠️ Image generation is not a product feature yet. PLN-19 makes it one. |
| `/v1/audio/speech` exists. No migration binds `tier-tts`, and no screen reads aloud. | `customer_console/main.py:9952`, `016_tier_task.sql` | Read aloud is not built. PLN-18 builds it. Check `tier_binding` on production at dispatch. |
| The only microphone code is in Notes and WhatsApp calls. | `app/notes/lib/recorder.ts`, `app/whatsapp/lib/callAudio.ts` | Dictation is not built. PLN-18 builds it. |
| `tier-video` and `tier-music` have catalog rows and no binding. | `infra/customer_console/015_tier_pricing.sql` | PLN-16 serves them. |
| Notes is `preview`, and §2.0 of `work_plan.md` puts it in M4. | `src/lib/nav.ts:297` | Meeting notes stays off the pricing page until Notes is live (§3.2). |
| Deep research is in build. | `specs/web_research.md`, WS-WEB | WS-WEB builds the feature. PLN-9 gates it. |
| File bytes go to Supabase Storage through one seam, and the seam is dark. | D108, `acb_common/object_store.py`, `OBJECT_STORE_ENABLED` | The quotas of §3 need task 701 (MT-1g). Some files still sit on the box disk (`routes/tasks/attachments.py:36`). |
| The artifact quota is 5 GB per member and 5 GB × `seats_purchased` for the org. | D108 item 5, `artifact_management.md` §7.5 | PLN-15 turns these numbers into tier data. Both limits stay. |
| Text tiers call DeepSeek's own API. | `002_seed_catalog.sql`, `035_provider_health.sql` | PLN-45 gives them a US or EU route. |
| 14 screens hard-code ₹, 9 of them in the CRM. 19 use the `en-IN` locale. | `grep` over `workbench/control_plane/src` | PLN-46 replaces both. |
| The default zone is Kolkata when an org names none. | `acb_common/today.py:26`, `ingestion/scheduler.py:85`, `gateway/routes/projects/core.py:3956` | PLN-46 replaces it. The operator range `customer_console/store.py:1645` stays (D94). |
| The public site has terms, privacy and refund pages, and no pricing page. | `site/`, `specs/marketing_site.md` §1 | PLN-43 updates the pages. PLN-47 adds the pricing page and amends that non-goal. |

---

## 3. The offer

### 3.1 The three tiers

**Names:** Standard, Pro, Max. The slug of Standard stays `core`.

| | **Standard** | **Pro** | **Max** | State today |
|---|---|---|---|---|
| Price per seat per month | $20 · ₹1,699 | $100 · ₹8,499 | $200 · ₹16,999 | PLN-1 |
| Who it is for | Every member | Managers, sales, marketing | Founders, builders, content makers | |
| Monthly AI allowance | 1× | 5× | 10× | PLN-7 |
| Chat with Mira, drafts, summaries, triage, reading documents and images | ✓ | ✓ | ✓ | Live |
| All live apps | ✓ | ✓ | ✓ | Live |
| External AI agents over MCP | ✓ | ✓ | ✓ | Live |
| Dictation and read aloud | ✓ | ✓ | ✓ | PLN-18 |
| Meeting notes | ✓ | ✓ | ✓ | Preview, M4 |
| Extra usage, if the admin allows it | ✓ | ✓ | ✓ | PLN-8 |
| Deep research reports | — | ✓ | ✓ | WS-WEB |
| Image generation and editing | — | ✓ | ✓ | PLN-19 |
| Live voice conversation with Mira | — | ✓ | ✓ | PLN-17 |
| Video, music and sound | — | — | ✓ | PLN-16 |
| Early access to new features | — | — | ✓ | PLN-9 |
| Background agents at one time | 1 | 3 | 10 | PLN-14 |
| Scheduled automations | 10 | 100 | 500 | PLN-14 |
| Largest file | 100 MB | 1 GB | 5 GB | PLN-14 |
| AI Studio | Use the team's agents | Build agents and automations | Also build apps with code and publish packages | WS-STU, PLN-9 |
| Storage per seat, with an org total | 30 GB | 250 GB | 1 TB | PLN-15 |

Prices exclude tax. India adds 18% GST. An org seat and an individual plan of one tier cost the same. No minimum seat count exists. Annual billing gives two months free (about 17% off), and the allowance still resets each month.

### 3.2 Two rules for the table

1. **The pricing page names only what works.** A row whose feature is not live on the launch date stays off the page. It joins the page when it goes live. Fence: PLN-47.
2. **Every value in the table is data.** Each one is a row in the Console catalog with `effective_from`. A change needs no code. Fence: PLN-1.

### 3.3 The size of the allowance

**At full use, AI at provider prices stays at or below 30% of the seat price.** That gives about $6, $30 and $60. The real numbers come from the priced rate card (task 524), because the size must follow measured burn.

This table shows the worst case for one seat, before infrastructure and support. It uses the merchant-of-record fee of §9.3 and storage at $0.02 per GB (D108).

| | Standard | Pro | Max |
|---|---|---|---|
| Price | $20.00 | $100.00 | $200.00 |
| AI at full use | $6.00 | $30.00 | $60.00 |
| Storage at full use | $0.60 | $5.00 | $20.00 |
| Payment fee, 5% plus $0.50 | $1.50 | $5.50 | $10.50 |
| **Left at full use** | **$11.90** | **$59.50** | **$109.50** |

Most members use much less than the full allowance. The top tier stays at 10× until measured burn shows room for more (§14).

---

## 4. The allowance

The owner accepted these rules on 2026-11-01 (PLN-Q3).

1. **Per seat, not pooled.** A pooled allowance removes the reason to buy a higher tier.
2. **It resets each month and does not roll over.** It is a usage limit and not money. Bought credits keep D32.6.
3. **One allowance pays for every AI act**, at the tier rate of D67. That covers text, voice, image and video.
4. **Keep the meter apart from `credit_ledger`.** A counter per org, member and period is fed by `usage_event`. An included draw never reads as spend of paid credits.
5. **The order of draw:** first the seat allowance. Then the org's prepaid credits, if the admin allows extra usage for the member. Then degrade to `tier-fast` (D32.8) or block with 402.
6. **Extra usage is OFF by default.** It draws prepaid credits only, so nobody is billed after the fact.
7. **Work for a member draws that member's allowance.** That covers chat, triage of their mailbox, and their agents.
8. **Work with no member draws an org pool.** Each plan includes a pool of 10% of the sum of the seat allowances. Past the pool, the work draws prepaid credits.
9. **Only a proven member draws an allowance** (PLN-6). A call with no proof is system work.
10. **An optional ceiling per AI tier** is data on the tier row. The operator uses it if one feature eats the month.

**What a member sees (D66):** a percent of this month's allowance and the reset date. **What an admin sees:** credits per member and per tier. Nobody sees a model name.

---

## 5. Seat tiers in an org

1. An org buys seats of each tier, in any count. Each member holds exactly one live tier. **The database enforces this.**
2. The three counts of D32.5 and the hard cap of D19.3 apply per tier. An assign past the bought count answers 409 with `buy_more`, and the message names the tier.
3. **Every role starts on Standard.** An admin may set a default tier for a role or a group (D12), and the invite offers a tier.
4. A tier change is one transaction. An upgrade applies now, with proration. A downgrade applies at the end of the period.
5. A monthly job suggests who to move up or down, with a reason. The admin applies each one (PLN-11).
6. The soft cap of ten users per org stays until the owner lifts it (D123 item 3, `MAX_TEAM_SIZE`).
7. **A new price never reprices a live subscription.** A price row binds new sales. Moving an existing org is an owner act (§9.7).

---

## 6. Three mechanisms, and no fourth

| Mechanism | What it decides | Slice | Refusal |
|---|---|---|---|
| **Gate** | A feature is on or off for a tier. These are the ✓ and — cells of §3.1. | PLN-9 | 402 "your seat does not include this". An admin's refusal stays 403. |
| **Allowance** | How much AI a member uses each month. | PLN-7 | Degrade, or 402, by policy. |
| **Limit** | Agents at one time, automations, largest file, storage. | PLN-14, PLN-15 | 409 or 413, with a reason and the next step. |

Each mechanism reads the tier row. The Router refuses an AI tier that the seat lacks. The nav and the API give the same answer. **A seat limit never stops work in progress.** A lower limit applies to the next start.

---

## 7. Individual mode

1. `organization.kind` is `individual` or `organization`, default `organization`, expand-only (R6). The resolve and `/auth/me` carry it.
2. **The server keeps an individual at one member and one seat.** It refuses an invite with 409 and the code `upgrade_to_team`. Hiding a button is not the fence.
3. **The mode is presentation and defaults, never a permission.** This is the rule of `preview` (D49 part 3). One filter acts on the nav, and it moves into the shell manifest with PKG-5.
4. **New records are private.** Home shows the "me" altitude (D87). The sharing, assignee and team controls step aside.
5. **Mira sets up the person.** The setup interview asks about goals, routines, current work and areas of life. It does not ask about a company.
6. **Billing shows one plan card** and no seat grid. A consumer invoice needs no tax ID.

---

## 8. The upgrade to a team

1. The same `organization.id` and the same data stay. Nothing is copied or moved. The person stays `owner`.
2. **Step 1 is the privacy review, and the flow cannot skip it.** It lists every org-visible record by app, and "keep private" is the default for each one. The choice applies as a grant change (D12).
3. The profile, Mira's memory (D132), the private chat summaries (D142) and connected mailboxes stay private. The review does not offer them.
4. **Step 2** takes the company details. **Step 3** sets the seats: the current plan becomes one seat of the same tier. **Step 4** opens the invite.
5. The kind changes once, in one transaction, with a `control_audit` row.
6. The allowance used this period carries over. It does not reset at the upgrade.

---

## 9. Selling worldwide

### 9.1 Prices and currency

USD is the list price for the world, and INR is the price for India. The billing country sets the currency at the first sale. An operator act alone changes it after that. The INR prices sit near the USD prices at about ₹85 to the dollar, because the AI cost is in dollars.

### 9.2 The credit

The credit stays an internal unit, so D19.2 and the rate card do not change. A credit pack gets a price per currency. The Operator Console keeps its rupee vocabulary (D94), and it converts USD revenue with the stored `usd_to_inr`.

### 9.3 The payment rails

D19 item 5 chose one `PaymentProvider` seam, with Stripe as its second implementation "when the first international customer appears". Going global on day 1 brings that second rail into the launch.

| Shape | Seller | Tax work | Fee | Note |
|---|---|---|---|---|
| **Merchant of record** (Paddle or Dodo Payments) | The provider | The provider files it | About 4% to 5%, plus a fixed fee | **Recommended** |
| **Stripe with Stripe Tax** | Hathi Labs | We register and file in each place | About 3%, plus about 0.5% | Stripe accepts Indian businesses by invite only |

India stays on Razorpay in both shapes, with GST invoices. The owner decides in PLN-Q6.

⚠️ **The seam must widen first.** It takes one INR order and it knows no subscription (§2). PLN-12 adds the currency, minor units and the subscription to the one seam. PLN-41 adds the second implementation. A parallel billing path is a defect.

### 9.4 Tax identity

`organization` holds `gstin` only. PLN-42 adds the billing country, the currency, a tax ID with its kind, and a billing address. `gstin` stays, and its value is copied.

### 9.5 Law

PLN-43 updates `site/terms.html`, `site/privacy.html` and `site/refunds.html`, and adds a data processing agreement and a subprocessor list. A lawyer reviews each one. The product adds export and delete for an org and for a member.

D19 item 6 promises India-only data. It stays for storage. The agreement must also say where AI providers process text.

### 9.6 The AI route

Many EU and US buyers refuse a subprocessor that handles their text in China. PLN-45 binds the text tiers to a US or EU host of the same open-weight models. **Recommended: this route is the default for every org, so one subprocessor list exists.** The owner decides in PLN-Q7, because the route changes the cost per call.

### 9.7 Existing customers

The owner accepted this on 2026-11-01 (PLN-Q4). Existing orgs stay on `core` at ₹500 until a date that the owner sets. After that date, `core` is Standard at its new price. During the pilots, the owner may grant Pro or Max to named members by hand, at no charge, to learn real burn per tier.

⚠️ That grant writes a live org's seats, so it is OWNER-GATE (CLAUDE.md §3a rule 3). PLN-5 and PLN-13 build the write door. The owner uses it.

### 9.8 Abuse

Each seat holds AI that costs real money, and a global signup page is a target. PLN-44 requires a verified email and a card before any allowance. One trial binds to one card. A chargeback suspends AI at once and keeps the data.

**The trial:** 7 days, with the features of Pro and the allowance of Standard.

---

## 10. What changes in the decisions

| Decision | Change |
|---|---|
| **D49 part 1** | Superseded when PLN-50 flips. Until then, flat ₹500 stays the live offer. |
| **D123 item 3** | "Flat per user" is superseded. The soft cap of ten users stays. |
| **D19 item 5** | Amended. Two rails at launch. PLN-Q6 picks the second one. |
| **D19 item 6** | Amended. It stays for storage, and the data agreement names where AI providers process text. |
| **D19.2** | Kept. The credit stays an internal unit (§9.2). |
| **D19.3, D32.5** | Kept, now per tier. |
| **D32.6** | Binds bought credits only. The allowance does not roll over. |
| **D32.8** | Kept. Degrade to `tier-fast` is the default past the allowance. |
| **D108 item 5** | Kept. The numbers become tier data, as that item foresaw. |
| **D66, D67, D94** | Kept. |
| **D12, D15** | Kept. An individual is a row, not a tenant model. |
| **D36** | Kept. Customer zero is an existing org, so §9.7 covers it. |
| **`launch_surface.md` §4 and CLAUDE.md §2** | Each one carries a banner now. Their text changes at PLN-50. |

---

## 11. The slices

**Reading the tables.** "Risk" is the review tier of `next-ticket.md` step 1b. A HIGH slice keeps the full loop, with Opus review. "Gate" is AGENT-SAFE unless the row says OWNER-GATE. Each task holds the longer scope. Take each migration number at build time, and re-check it at merge (R1).

### 11.0 Owner calls

| Slice | Task | Question | State |
|---|---|---|---|
| PLN-Q1 | 1275 | Currency and prices | ✅ Answered. Global from day 1. |
| PLN-Q2 | 1276 | The offer of §3 | 🟡 Open. The owner confirms it in the PR of this spec. |
| PLN-Q3 | 1277 | Allowance rules | ✅ Answered. §4. |
| PLN-Q4 | 1278 | Existing orgs | ✅ Answered. The date stays open. §9.7. |
| PLN-Q5 | 1279 | One login in two orgs | ✅ Answered. Not in the first release. |
| PLN-Q6 | 1319 | Seller and global provider | 🔴 Open. It blocks PLN-41 and PLN-43. |
| PLN-Q7 | 1404 | The default AI route | 🔴 Open. It blocks PLN-45 and the rate card numbers. |

### 11.1 Phase 1, foundations

Nothing here charges anybody, and it can start now.

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-6 | 1285 | Every member AI path proves the member | HIGH | AGENT-SAFE | None | A test lists the Router callers and fails on a member path with no proof. A live chat turn shows a proven member in `usage_event`. | `test_member_paths_send_proof.py` |
| PLN-20 | 1293 | The account kind, and one member for an individual | HIGH | AGENT-SAFE | Task 507 for production | The default, the invite refusal and the second-seat refusal pass on a real database. | `test_account_kind_r8.py` |
| PLN-42 | 1321 | Country, currency and tax ID | HIGH | AGENT-SAFE | Task 507 for production | A new Indian org gets INR and Razorpay. A new foreign org gets USD and the global rail. An old org with a GSTIN keeps working. | `test_org_tax_identity_r8.py` |
| PLN-46 | 1407 | Global readiness: no hard-coded ₹, `en-IN` or Kolkata | MEDIUM | AGENT-SAFE | PLN-42 | A US test org sees USD amounts, US dates and its own zone in the CRM, billing and Home. The fence refuses a new ₹ outside the operator console. | `test_no_rupee_literal.py` |
| PLN-1 | 1280 | Catalog: tiers, prices per currency, limits, gates | HIGH | AGENT-SAFE | Task 507 for production | A second live tier for one member fails in the database. A replay changes nothing. Every `core` seat reads as Standard. | `test_customer_console_seat_tiers.py` |

Tasks outside WS-PLN feed this phase: task 507 (Console backup), task 517 (H-287, usage rows) and task 524 (price the rate card).

### 11.2 Phase 2, seat tiers

The mask stays dark behind `SEAT_MASK_ORGS`, default OFF.

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-2 | 1281 | Counts and the hard cap per tier | HIGH | AGENT-SAFE | PLN-1 | A full tier answers 409 with `buy_more`. The first sign-in takes the member's tier and no higher. | The seat tests of `seats.py` |
| PLN-3 | 1282 | Change a tier in one transaction | HIGH | AGENT-SAFE | PLN-2 | No moment holds two live tiers or none. A scheduled downgrade applies once. | `test_customer_console_seat_tier_change.py` |
| PLN-4 | 1283 | Default tier by role and group, and the invite picks a tier | MEDIUM | AGENT-SAFE | PLN-2 | The picker and the full-tier case pass in vitest, with four captures. Mira does the same from chat. | `InviteDialog` tests |
| PLN-5 | 1284 | The seat tab shows tiers, and the operator page matches | MEDIUM | AGENT-SAFE. A grant on a live org is OWNER-GATE. | PLN-3 | Each state passes in vitest, with four captures. | Seat tab tests |
| PLN-9 | 1288 | The seat mask in the one seam, and the Router 402 | HIGH | AGENT-SAFE | PLN-1, PLN-6, PKG-7 | A member's features never exceed the mask. The nav and the API agree. The Router refuses an AI tier the seat lacks. | `test_seat_entitlement_mask.py` |
| PLN-14 | 1325 | Limits: agents at one time, automations, largest file | MEDIUM | AGENT-SAFE | PLN-1, PLN-6 | A Standard member cannot start a second agent. A downgrade lets three running agents finish. | `test_seat_limits.py` |
| PLN-15 | 1326 | Storage numbers per tier, over the one object store | HIGH | AGENT-SAFE | PLN-1, task 701 | The quota of org A does not touch org B. A delete frees space at once. The nightly recount matches. | `test_storage_quota_r8.py` |

### 11.3 Phase 3, the allowance, dark

The flag `SEAT_ALLOWANCE_ORGS` stays default OFF. With it off, nothing changes. Pilots run it first, with the owner's grants, to learn burn per tier.

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-7 | 1286 | The allowance in the Router decision | HIGH | AGENT-SAFE | PLN-1, PLN-6, task 517, task 524 | The order of draw, the reset and the degrade pass on a real database. Included draws plus paid draws equal total usage. | `test_seat_allowance_r8.py` |
| PLN-8 | 1287 | Extra usage, member caps, notices at 80 and 100 percent, through the shell bell (D89) with no app-local copy | HIGH | AGENT-SAFE | PLN-7, task 528 | Each policy passes on a real database. The settings screen has four captures. | `test_extra_usage_r8.py` |
| PLN-10 | 1289 | The member's usage meter. Chat notices use the shell bell and the Ask bar (D89), with no app-local copy. | LOW | AGENT-SAFE | PLN-7 | Each state passes in vitest. No model name shows. Mira answers "how much AI do I have left?" | Meter tests |
| PLN-11 | 1290 | Seat-fit suggestions | MEDIUM | AGENT-SAFE | PLN-3, PLN-7 | The rules for up, down and no change pass. The admin applies a suggestion in one click. | `test_seat_fit.py` |
| PLN-13 | 1292 | Operator: edit tiers, see burn and margin | MEDIUM | AGENT-SAFE. A change on production is OWNER-GATE. | PLN-1, PLN-7 | The write door and its role check pass. A change is a new row. | Console tests |
| PLN-45 | 1324 | The US or EU route for text | HIGH | AGENT-SAFE. The binding on production is OWNER-GATE until PLN-Q7. | PLN-Q7 | A test call lands at the new host, and the usage row matches its price. A failure falls back to an approved region only. | `test_provider_region.py` |

### 11.4 Phase 4, individual mode

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-22 | 1295 | One filter hides the team surfaces | MEDIUM | AGENT-SAFE | PLN-20 | `nav.test.ts` covers both kinds, with four captures of each. | `nav.test.ts` |
| PLN-23 | 1296 | Private defaults | HIGH | AGENT-SAFE | PLN-20 | An individual org makes no org-visible record by default, in each app. | `test_individual_private_default_r8.py` |
| PLN-24 | 1297 | Mira sets up a person | MEDIUM | AGENT-SAFE. An eval over USD 0.50 needs the owner. | PLN-20 | The interview picks the individual script by the kind. Five first-run chats read right. | Interview tests |
| PLN-21 | 1294 | Signup: "Just me" or "My team" | MEDIUM | AGENT-SAFE | PLN-20, PLN-1, PLN-42 | `signup.test.ts` covers both paths and both flag positions, with four captures. | `signup.test.ts` |
| PLN-25 | 1298 | Billing for one person | MEDIUM | AGENT-SAFE | PLN-3, PLN-8, PLN-10, PLN-20 | Each state passes in vitest, with four captures. | Plan card tests |

### 11.5 Phase 5, money

Tests run against provider sandboxes. A live charge is OWNER-GATE (CLAUDE.md §3a rule 3).

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-12 | 1291 | Widen the seam. Checkout, proration, one invoice line per tier | HIGH | AGENT-SAFE | PLN-2, PLN-3, tasks 513, 525, 528 | Each case passes on the Razorpay test account. The five money invariants (task 529) still hold. | `test_customer_console_payments.py` |
| PLN-41 | 1320 | The global rail | HIGH | AGENT-SAFE | PLN-Q6, PLN-1, PLN-12, PLN-42 | Buy, upgrade, downgrade, a failed renewal and a refund each move the ledger once in the sandbox. A replayed webhook changes nothing. | One contract suite for both rails |
| PLN-44 | 1323 | Fraud controls and the trial | HIGH | AGENT-SAFE | PLN-41, PLN-7, PLN-9 | A signup with no card gets no AI. A second trial on one card is refused. A chargeback suspends AI within one minute. | `test_signup_abuse.py` |
| PLN-43 | 1322 | Legal pack, export and delete | HIGH | Part A OWNER-GATE. Part B AGENT-SAFE. | PLN-Q6 | Export of org A holds no row of org B. Delete of org A leaves no row after the grace period, and it writes an audit row. | `test_org_export_delete_r8.py` |
| PLN-47 | 1408 | The pricing page | LOW | AGENT-SAFE | PLN-1, PLN-42, PLN-Q2 | The page shows USD or INR by country and lists only what is live. | `test_marketing_site.py` |

**PLN-43 has two parts.** Part A is the legal text of §9.5. A lawyer reviews it, and the owner publishes it. Part B is the product: export and delete for an org and for a member.

### 11.6 Phase 6, the upgrade

The flag `TEAM_UPGRADE_ENABLED` stays default OFF.

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-31 | 1300 | The privacy review | HIGH | AGENT-SAFE | PLN-20, PLN-23 | After the upgrade, a new member sees nothing until the owner shares it. The test covers each app and store of §8. | `test_team_upgrade_privacy_r8.py` |
| PLN-32 | 1301 | Carry the plan into the org | HIGH | AGENT-SAFE. A live paid customer is OWNER-GATE. | PLN-12, PLN-20, PLN-41 | Each item passes on a provider sandbox, in both currencies. | Payment tests |
| PLN-30 | 1299 | "Turn this into a team" | HIGH | AGENT-SAFE | PLN-4, PLN-31, PLN-32 | The whole flow keeps the org id and the owner's role, and the kind changes once. Mira can start it from chat. | `test_team_upgrade_r8.py` |

### 11.7 Phase 7, what the tiers sell

These slices run beside Phases 4 to 6. Each one stays dark until its gate works.

⚠️ **A live provider call costs money.** Each fence and each "Done when" passes on a stub in CI. A live check over USD 0.50 needs the owner first.

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-18 | 1405 | Dictation and read aloud in the one shell | MEDIUM | AGENT-SAFE. A live call over USD 0.50 needs the owner. | None for the build | A member dictates into the Ask bar and hears an answer. Each act writes a usage row. | `test_voice_shell.py` |
| PLN-19 | 1406 | Image generation and editing in chat | MEDIUM | AGENT-SAFE. A live call over USD 0.50 needs the owner. | PLN-9 for the gate | A Pro member makes and edits an image from chat, and it lands in Artifacts. A Standard member gets the upgrade message. | `test_image_tool.py` |
| PLN-16 | 1327 | Serve video, then music | MEDIUM | AGENT-SAFE. A live call over USD 0.50 needs the owner. | PLN-9 for the gate | A Max member makes an 8-second clip from chat. A failed job costs nothing. | `test_video_job.py` |
| PLN-17 | 1328 | Live voice | HIGH | AGENT-SAFE. M3. A live call over USD 0.50 needs the owner. | PLN-18, PLN-9 | The answer starts to play within 2 seconds. The usage rows add up to the cost. | `test_live_voice.py` |

### 11.8 Phase 8, launch

| Slice | Task | What | Risk | Gate | Blocked by | Done when | Fence |
|---|---|---|---|---|---|---|---|
| PLN-50 | 1409 | Open paid signup worldwide | HIGH | OWNER-GATE | Phases 1 to 6, with PLN-43 Part A | A test buyer in India and one abroad each complete checkout on live rails, with the owner's approval. The owner names the date of §9.7. | The checklist below |

**The checklist before the flip.**

1. Phases 1 to 6 are done, and evidence shows each flag working on production.
2. Task 507 gives a Console backup, and task 701 has moved files to the object store.
3. Task 524 has priced the rate card from pilot burn, and the allowance rows hold the result.
4. A lawyer has reviewed PLN-43 Part A, and the owner has published it.
5. The pricing page lists only what is live (PLN-47).
6. A test buyer in India and one abroad each complete checkout on live rails, with the owner's approval.
7. A refund and a chargeback each work on live rails, at the smallest amount.

---

## 12. The delivery plan

The critical path runs through money:

> PLN-Q2, Q6, Q7 → PLN-1 → PLN-2, PLN-3 → PLN-7 → PLN-12 → PLN-41 → PLN-44 → PLN-50

| Phase | Target | Why this order |
|---|---|---|
| **0. Decide** | By mid-November 2026 | Each owner call blocks money work. |
| **1. Foundations** | November to December 2026 | Identity, usage rows and the catalog come first. Pilots keep running on flat ₹500. |
| **2. Seat tiers** | December 2026 to January 2027 | The owner can grant tiers by hand from here (§9.7). |
| **3. Allowance, dark** | January 2027 | Pilots give real burn per tier, and that sizes the allowance. |
| **4. Individual mode** | January to February 2027 | It needs the account kind and the plan card. |
| **5. Money** | February 2027 | ⚠️ PLN-43 Part A and the provider's approval start by January. Both wait on people outside the team. |
| **6. Upgrade** | February to March 2027 | It needs both rails and the privacy defaults. |
| **7. What the tiers sell** | Beside Phases 4 to 6 | Pro needs image and research. Max needs video. |
| **8. Launch** | By 31 March 2027 (M2) | The owner flips it. |

These targets are a proposal. Metorite holds the status and the order (D118).

**Three things can slip the date.** The provider's approval and the lawyer's review run on their calendars. The rate card needs a month of pilot burn. Task 701 must move files off the box before a storage promise means anything.

---

## 13. Verification

```sh
# Start the real database first. Without it, the R8 tests SKIP and the run reads green.
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"

# Backend: name the files that the slice adds or changes.
uv run pytest tests/unit/test_customer_console_seat_tiers.py -q
uv run python scripts/affected_tests.py --run

# Frontend, from workbench/control_plane.
npx tsc --noEmit && npx vitest run src/lib/nav.test.ts

# The whole pr-check suite, on the commit, before a push.
bash scripts/ci_local.sh

# This spec and its index.
uv run pytest tests/unit/test_index_completeness.py tests/unit/test_doc_names_resolve.py tests/unit/test_board_index.py -q
node .claude/hooks/ste-lint.mjs project-docs/specs/seat_plans.md --warnings
```

A slice with screens also needs four captures and a `Visual review:` line. Check delivery by the migration ledger, the deployed SHA and a log line (CLAUDE.md §3 rule 8).

---

## 14. What other products do

Survey of 2026-11-02. The notes are filed in full on epic 1273.

- **Anthropic: Pro $20, Max $100 at 5× and Max $200 at 20×.** Source type: third-party guides, which agree on these numbers. The Max plans share Pro's features, and they differ in capacity and priority. **We take:** three price points and extra usage. **We do not take:** equal features, because the owner wants features to differ. **We do not take 20× yet:** at our 30% rule it would put AI at 60% of the Max price.
- **Merchant of record.** Source type: vendor pages, mostly Dodo Payments' own. Paddle costs about 5% plus $0.50, and Dodo about 4% plus $0.40. **We take:** the shape, for sales outside India. **Not verified:** which provider pays out to an Indian bank account, and payout fees.
- **Stripe in India.** Source type: Stripe's own help page. New Indian businesses join by invite only. **We take:** this fact, as a weight against Stripe at launch.
- **Video prices.** Source type: third-party price guides, which disagree. About $0.05 to $0.15 per second at 720p. **We take:** Max only, and a choice on cost per usable clip. **Not verified:** any provider's own pricing page.

**Sources.**

- [Claude Max guide](https://ai.zenken.co.jp/en/post/claude-max-plan-guide/)
- [Claude Pro vs Max](https://dupple.com/learn/claude-pro-vs-max)
- [Dodo Payments vs Paddle](https://dodopayments.com/it/compare/dodopayments-vs-paddle)
- [Merchant of record for India](https://dodopayments.com/blogs/top-merchant-of-record-for-saas-india)
- [Stripe invite-only in India](https://support.stripe.com/questions/stripe-accounts-are-invite-only-in-india)
- [AI video API pricing](https://apiframe.ai/blog/ai-video-api-pricing-2026)
- [Cheapest video APIs](https://www.atlascloud.ai/blog/cheapest-ai-video-generation-api-2026)

---

## 15. Risks

- **The allowance is a guess until burn is measured.** Task 524 and Phase 3 replace the guess.
- **A heavy Max member can cost more than the seat earns.** The allowance caps our cost, and extra usage is prepaid.
- **An unproven call must never be free.** PLN-6 lands before any allowance binds.
- **Max may launch before video works.** §3.2 keeps the row off the page, so Max then sells on capacity, agents and code.
- **The exchange rate moves.** An INR price is a dated row, so the operator can reprice new sales. Live subscriptions keep their price.
- **A US or EU route can cost more than DeepSeek's own API.** PLN-Q7 weighs this, and the rate card uses the real cost.
- **Merge order.** PLN-9 and PKG-7 share one seam. Whichever lands second extends the first.

---

## 16. Rejected

- **A separate personal tenant, or a data model keyed on the user.** It breaks D15, and it turns the upgrade into a data migration.
- **One allowance pooled across the org.** It removes the reason to buy a higher tier.
- **A separate bucket for each AI feature.** Separate buckets add meters and confuse the customer. One allowance plus gates does the same work.
- **Automatic upgrades when the allowance runs out.** D19.3 says that a person always buys by an explicit act.
- **A deep discount for India.** The AI cost is in dollars, so a discount cuts into the allowance.
- **Priority at peak hours for Max.** No queue exists to deliver it, so the offer does not promise it.
