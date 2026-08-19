# Where the Customer Console runs — the infrastructure decision (owner session)

**Status:** ✅ **DECIDED 2026-08-12 — Supabase** (owner, recorded as **D34** in
`work_plan.md` §3): managed Postgres in Mumbai **plus** Supabase Auth as the
authenticator, consumed as one provider inside NextAuth rather than replacing
it. §3's disqualification of Firebase and §4's reasoning stand as the record of
why. **Items 1, 2 and 3 of §5 are answered; item 4 was answered 2026-08-19
(D47 — the SERVICE runs on the production VPS for now, overruling this
document's own default; see §5) and item 5 remains open (H-14).** ·
**Date:** 2026-08-12 · **Owner:** vjvarada (this is an owner call) ·
**Companion:** `customer_console.md` (WS-31 — what runs), this document
(**where** it runs) · **Blocks:** nothing. CP-1 is built and tested against
plain Postgres 16, so every option below is still open.

> ### `The scaffolding was deliberately built on nothing vendor-specific.`
> ### `Read §3 first: one option is disqualified on technical grounds, not preference.`

---

## 1. Read this before comparing vendors

**What was built (CP-1) runs on plain Postgres and standard FastAPI.** No
extensions beyond `pgcrypto` and `citext`, both in contrib everywhere. That was
a deliberate choice so this decision could be made late, on evidence, without
rework. Whatever you pick, the migration and the service move unchanged.

**The one thing that is NOT reversible is the data model's shape**, and it is
already committed: the Customer Console is **relational, transactional and
constraint-heavy**. That is not an aesthetic preference — §3 shows it
disqualifies one of the four options you named.

## 2. What this component actually has to do

Requirements derived from what CP-1 already does, not from a generic checklist:

| # | Requirement | Where it comes from |
|---|---|---|
| R-a | **Multi-row transactions.** Recording usage writes a `usage_event` *and* a `credit_ledger` row, and a retry must write neither | `store.record_usage` — the double-billing guard |
| R-b | **Partial unique indexes.** `seat_assignment` unique on `(org, plan, member) WHERE released_at IS NULL` is what makes double-assignment impossible under concurrency | `001_customer_console.sql` |
| R-c | **Aggregates as truth.** Balance is `SUM(delta)` over an append-only ledger; seat counts are sums over signed grants | D32.6, §3.3 |
| R-d | **Case-insensitive identity.** `CITEXT` on email, or the tenant plane's migration-162 duplicate-identity bug repeats here | `ensure_identity` |
| R-e | **It is the single point of failure for every customer.** Down = nobody signs in (seat resolution) and nothing meters | §5.2, CP-4 |
| R-f | **India data residency**, and it holds GSTIN + billing data | DPDP; `saas_operations_doctrine.md` §3.3 |
| R-g | **Backups you can actually restore**, separate from the tenant boxes | D31 — we currently have no per-tenant restore at all |

R-e is the one that should drive the decision, and it is the argument against
the cheapest option: **the Customer Console must not share a failure domain with a
customer deployment.** A tenant box falling over should inconvenience one
customer. The Customer Console falling over stops sign-in and metering for all of
them.

## 3. The options

### ❌ Firebase — disqualified on technical grounds

Not a preference call. Firestore is a document store, and R-a through R-c are
exactly what it does not give you:

- **No partial unique indexes** (R-b). Uniqueness must be faked with a
  transaction-plus-sentinel-document pattern, which is precisely the "unlikely
  rather than impossible" posture the schema was written to avoid.
- **No server-side `SUM` over a collection** (R-c). Balance becomes either a
  maintained counter — the mutable balance column the ledger design exists to
  refuse — or a full read of every ledger row on the hot path.
- **Aggregation and reporting** for the operator console ("MRR by plan, burn by
  module") are SQL `GROUP BY`s. In Firestore they become an export pipeline.

Firebase is a good product for the shape of problem it is for. This is not that
shape. **Recommend excluding it and not revisiting.**

### ✅ Supabase — Postgres, plus the auth we still owe

*It is Postgres*, so R-a…R-d are satisfied by definition and the CP-1 migration
applies unchanged. Mumbai (`ap-south-1`) covers R-f. Managed backups cover R-g.

The real attraction is **Supabase Auth**, because CP-0 left something unfinished:
the code now accepts multiple directories, but somebody still has to operate
per-customer SSO — Google, Microsoft, and email/OTP for customers with neither.
That is a genuine build, and Supabase Auth is a credible way to not build it.

Watch-outs: Supabase's centre of gravity is client-direct access with RLS, which
is the *tenant* plane's model and explicitly wrong here (§0.9.2 — this plane is
cross-tenant by design). Use it as managed Postgres + Auth, with our FastAPI
service in front; do not let the client talk to it directly.

### ✅ Azure — the Entra argument, and it is stronger than it looks

Azure Database for PostgreSQL Flexible Server (Central India) + Container Apps
satisfies everything. The specific reason to weigh it above the others: **you are
already on Microsoft Entra**, which means Entra External ID can host customer
identities under an account you already administer, and enterprise customers'
procurement asks about Azure with a familiarity they do not extend to Supabase.

Watch-out: the most operationally complex of the three, and the easiest to
over-provision. If you pick Azure, pick exactly two services and stop.

### ◐ The existing Hostinger VPS — cheapest, and the one I would not choose

It works today and costs nothing new. But it fails R-e — the Customer Console would
share a box with a tenant deployment — and it puts payment and GST data on
self-managed infrastructure whose backup story is the one D31 already flags as
broken. **Fine for developing against; wrong for the thing that holds every
customer's billing record.**

## 4. Recommendation

**Managed Postgres in an India region, with the FastAPI service in a container
in front of it.** Vendor second, and deliberately so: because CP-1 is plain
Postgres, the vendor choice is **reversible via `pg_dump`** in a way almost no
other architectural decision is. Do not spend the session optimising it.

If forced to one: **Supabase**, on the strength of Supabase Auth closing the
CP-0 gap — that is real, scoped work you would otherwise write yourself. Choose
**Azure** instead if enterprise procurement or Entra consolidation matters more
than saving that build, which is a business judgement, not a technical one.

**Whichever you pick, treat the auth decision as separate and decide it
explicitly.** It is the one with lock-in: the database is portable, the identity
provider is not, because migrating identities means every customer's users
re-authenticate.

## 5. What to actually decide on the PC

1. **Vendor** for managed Postgres (see §4 — 20 minutes, reversible).
2. **Auth provider** for customer sign-in — Supabase Auth, Entra External ID, or
   build on the CP-0 foundation. **The one with lock-in; give it the time.**
3. **Region**, confirming India for R-f.
4. **Whether the Customer Console gets its own environment** separate from tenant
   deployments (§R-e). My answer is yes; it is worth ten minutes to disagree.
   ✅ **ANSWERED 2026-08-19 — the owner took the ten minutes and disagreed, for
   now (D47, `work_plan.md` §3):** the Console SERVICE runs on the production
   VPS as its **own systemd unit with its own env file**; the data plane stays
   the Supabase Console project, so §R-e's single-point-of-failure cost is
   bounded (a box loss loses no Console data) and CP-2b's fail-soft cache
   covers sign-in through a Console outage ≤24h. Public surface is the
   Razorpay webhook path ONLY — gateway/BFF calls go to `127.0.0.1`. Named
   move trigger: **the day a second deployment exists**, the service leaves
   the box (relocation = re-point `CUSTOMER_CONSOLE_URL`; data unmoved).
   Named obligation on the way in: the deploy pipeline must gain the
   `infra/customer_console/` ladder-apply step — the board's "on the box but
   inert" gap. Deploying it remains 🔴 OWNER-GATE (§7 / `customer_console.md`
   §8 gate 2).
5. **Razorpay account and GST registration status** — both are prerequisites for
   CP-8 and neither is an engineering task. 🔴 OWNER-GATE.

## 6. What is already true regardless

Built and verified 2026-08-12 against Postgres 16, so none of it waits on §5:

- `infra/customer_console/001_customer_console.sql` + `002_seed_catalog.sql` — the schema
  and the D23/D24 catalog. Applies and **replays** cleanly.
- `apps/services/customer_console/` — the service: provisioning, seat resolution, seat
  writes, credit grant/balance, usage recording.
- **79 tests**: 45 pure-domain, 20 SQL against real Postgres (R8), 14 HTTP
  end-to-end.
- The service **fails closed** without `CUSTOMER_CONSOLE_OPERATOR_TOKEN`, and its
  DSN has **no default**, so it cannot silently reach the tenant database.

## 7. Gate labels

**🟢 AGENT-SAFE:** everything in §6; further CP tickets against local Postgres.

**🔴 OWNER-GATE:** creating any cloud account · provisioning any managed
database · registering an IdP application · Razorpay credentials · GST
registration · deploying this service anywhere.
