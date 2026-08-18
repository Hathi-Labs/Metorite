# Customer Console — the subscription, seat and AI-metering engine (WS-31)

*(Authored 2026-08-12 as the "Platform Control Plane"; renamed **Customer
Console** by **D41**, 2026-08-18 — file was `platform_control_plane.md`. The
path/env/package mapping is in D41.1.)*

**Status:** ◐ **CP-0 · CP-1 · CP-2 · CP-2a · CP-3 · CP-4 BUILT · CP-6 mechanism
BUILT (refusals ship OFF) · CP-2b CONSOLE HALF BUILT 2026-08-18, deployment half
OPEN** — 260 Customer Console tests against a real Postgres 16 per R8 (219 + the
41 of `test_customer_console_resolve.py`) · CP-3 was **rejected by independent
verification once** and rebuilt (see its ticket) · CP-5 · CP-7 · CP-8 spec only ·
**CP-4b (streaming pass-through) MINTED 2026-08-18, spec only** — it carries the
half of CP-4's done-when that was never met (`stream: true` returns 501) and
CP-4's ✅ is amended accordingly ·
⚠️ **CP-2b is HALF built and the split is the important part.** The **Customer
Console side is BUILT** — the fourth auth scheme (`cc_depl_…`, capability set
exactly `{resolve}`, `auth.py`), migration `006_deployment_key.sql`, and
`POST /registry/resolve` answering **two schemes with two shapes** chosen by the
credential (`main.py`), fenced by `tests/unit/test_customer_console_resolve.py`
against a real Postgres 16 (clauses 1–5, 8's Console half, 9's Console half, 10,
12, plus the two R7 fences the implementation itself owes). The **deployment
side is UNBUILT and still has no caller in the tree**: the `acb_auth` entry
point, the resolve cache with its TTL/`MAX_STALENESS` pair, the two projection
columns and the multi-org refusal copy — i.e. **clauses 6, 7, 11, clause 8's
`resolved_at` half and clause 9's deployment-side refusal** — remain open, so
*the seat cap is enforced by the Console and still not consulted by the product*.
Issuing a real `cc_depl_` key into a live deployment stays OWNER-GATE (§8 gate
7). Its earlier re-audit history, kept because it is what made the ticket
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
`infra/customer_console/001_customer_console.sql`; the ladder is 001–**006**
(`006_deployment_key.sql` taken by CP-2b's Console half) and the next free
number is **007** — R1 says list the directory at build time and re-check at
merge rather than trusting this sentence. ⚠️ **`main.py` line anchors below
moved again with CP-2b** (`resolve` gained a second scheme and two helper
functions); re-derive them at dispatch. *(The previous header's "repo-wide grep: zero
hits … none of the commercial substrate exists" was true at authoring on
2026-08-12 and false from CP-1 onward; likewise "CP-2 … spec only" — CP-2 is
**✅ BUILT**: `main.py:603` `GET /billing/summary`, `:635` `POST /billing/seats`,
`:675` release, pinned by `test_customer_console_seats.py:59`
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

**Non-goals.**
- Payment processing itself — Razorpay integration is CP-8, deliberately last, and
  D19.5 already fixed the provider choice. Nothing here re-opens it.
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

# Deployment scheme — CP-2b. The CONSOLE side is BUILT and answers this shape
# (main.py `_resolve_for_deployment`); the CALLER — the box that presents the
# key — is the deployment half and is still unbuilt, so nothing sends it yet.
# A box asking about a person it has just authenticated. It names no org: the
# org is the ANSWER, not the assertion (R11).
POST /registry/resolve      Authorization: Bearer cc_depl_<prefix>_<secret>
     { email, display_name? }              # an org_slug field here is 400
  → 200 { identity_id, organizations: [ { organization_id, slug, placement,
                                          status, seat } ] }   # never `role`
  | 200 { "organizations": [] }            # invisible — exactly this body
  | 409 { reason: "seat_cap_exceeded", buy_more: {...} }
  | 403 "organization is <state>"
```

Metorite caches the answer into migration 159's `user_identity` /
`org_membership` projection — **that projection is the cache of record** — and
proceeds. **This is what makes the seat cap real**: a person cannot become a user
of an organization without the Customer Console allocating them a seat, because
the box asks before admitting them.

Honest cost: while the Customer Console is **reachable**, deprovisioning is as
fast as `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`; while it is **not**, a cached
person proceeds up to `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS` unless the
cached answer already carries a state that refuses (CP-2b clause 6). Neither is an
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
last-login/actives) plus the nightly drift job.

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

**CP-2b · Sign-in resolve: the product calls the registry.** ◐ **CONSOLE HALF
BUILT 2026-08-18 · DEPLOYMENT HALF OPEN.** §5.2's wiring — the half that has
existed as prose since 2026-08-12 and as code in nobody.

**What is built (this side of the wire).** The fourth auth scheme
`cc_depl_<prefix>_<secret>` beside the three that existed, with a capability set
of exactly `{resolve}` enforced as a dependency
(`customer_console/auth.py` — `DeploymentCaller`, `deployment_or_operator`,
`AUTHENTICATING_DEPENDENCIES`); the sibling table
`infra/customer_console/006_deployment_key.sql`; and `POST /registry/resolve`
answering **two schemes with two response shapes chosen by the credential**
(`customer_console/main.py` — `_resolve_for_operator`,
`_resolve_for_deployment`, `_allocate_core_seat`), with the visibility predicate
in `store.deployment_visible_orgs` and the key lookup in
`store.resolve_deployment_key`. Fenced by
`tests/unit/test_customer_console_resolve.py` (41 tests) against a real Postgres
16, every fence below shown **red first** by reverting the behaviour it pins.
Clauses met: **1, 2, 3, 4, 5, 8 (Console half), 9 (Console half), 10, 12**, plus
the two R7 fences this implementation itself owes.

**What is NOT built, and it is the half that makes the cap real.** There is
still **no caller anywhere in the tree** — no `acb_auth` entry point, no resolve
cache, no `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS` /
`…_MAX_STALENESS_SECONDS` settings, no `org_membership.resolved_at` /
`organization.registry_status` projection columns, and no multi-org refusal
copy. **Clauses 6, 7, 11, clause 8's `resolved_at` half and clause 9's
deployment-side refusal are open.** Until they land, **the seat cap is a number
the Customer Console keeps, can now answer, and the product still never
consults** — a person becomes a user of a Metorite deployment without any seat
being allocated, which is exactly the claim §5.2 rests on (*"the box asks before
admitting them"*), still unbuilt.

**Two findings recorded rather than fixed** (neither is decided by any clause,
so neither was decided in the build): `org_membership.status` is **not**
consulted by the visibility predicate — clause 4 states the criterion as *holds
a membership* and clause 5 enumerates three invisible cases, none of which is
"membership was removed", so a `removed` member still resolves; and
`deployment.status` (`active|draining|retired`) does not affect its keys.

The 2026-08-18 audit found **three undecided questions** blocking dispatch. Each
is answered below as an **agent-proposed default, owner may overrule** — the
D16/D17 convention (`work_plan.md` §3: *"proposed defaults, adopted unless the
owner objects"*). They are named as proposals because each has a defensible
alternative; none of them is a commercial call, so none needs to wait.

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
rather than a per-route `if`. What it can learn about an organization is bounded
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
(`main.py:635`) and `POST /credits/grant` (`main.py:690`) already do. Under a
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
  lifecycle state is `suspended`, `cancelled` or `deleted` is applied **at once,
  without re-consulting and without any freshness grace**. Staleness may only
  ever make the cache *more* restrictive — a state can be relaxed by a
  successful re-consult and never by expiry.
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
  - Fences: `test_a_cached_dead_org_refuses_without_asking_the_console`
    (`deleted`, Console unreachable, inside *and* outside the TTL — refused in
    both), `test_a_cached_suspended_org_locks_features_without_asking_the_console`
    (login still works, `can_use_ai` false), and
    `test_staleness_never_relaxes_a_cached_state`.
- **So the honest bound is a pair, not a number.** **Console reachable →
  `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`.** **Console unreachable →
  `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`**, with the dead-state rule
  above cutting the `deleted` case to *immediate* even then. §5.2 states the
  same pair; the two must be edited together.
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

**Where the cache lives — migration 159's projection tables are the CACHE OF
RECORD.** *(Named explicitly 2026-08-18.)* Every bound above — `resolved_at`,
the TTL, the staleness ceiling, the last-seen lifecycle state — is evaluated
against **rows**, because a 24-hour ceiling is unenforceable in a per-process
dict: `acb_auth`'s `_cache` is an in-process dictionary with a **60-second** TTL
(`access.py:37`, `CACHE_TTL_SECONDS = 60.0`), per **worker**, wiped on every
restart. That dict stays exactly what it is — a **read-through** in front of the
projection, not a second store — and the resolve cache extends it rather than
duplicating it (`access.py:73-98`). Consequences to build to: two workers may
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
| org slug | `organization.slug` (`130_org_access_control.sql:39`) | already exists |
| **freshness** | *— none —* | **new nullable column** `org_membership.resolved_at TIMESTAMPTZ`, the per-person TTL clock |
| **org lifecycle** | *— none —* | **new nullable column** `organization.registry_status TEXT`, last-seen `trial\|active\|past_due\|suspended\|cancelled\|deleted` |

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

**Both new columns sit on tables in `gen_tenant_migration.EXEMPT`, and that is
correct for them.** `organization` ("*the tenant list itself*",
`scripts/gen_tenant_migration.py:72`) and `org_membership` ("*control plane — the
tenant-scoped half; org_id is its PK*", `:75`) are already exempt from the RLS
generator, alongside `tenant_placement` (`:73`) and `user_identity` (`:74`). A
column added to an exempt table needs **no** new exemption entry and must not add
one: it inherits the table's reason, and the reason still holds — a policy that
hid the tenant list from the connection resolving which tenant this is would make
the box unable to answer its own first question. This ticket therefore adds
**zero** rows to `EXEMPT` and leaves `test_tenant_coverage.py`'s map untouched —
the same finding that reworded CP-1 clause 3.

Both new columns are **nullable with NO default and nothing renamed**. That is
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
entry point whose only job is *"a sign-in just completed for this address"*, and
it is the only thing in the tree that holds the `cc_depl_` key or calls the
resolve client. The BFF reaches it from the sign-in callback and nothing else
does; `_with_resolved_access` keeps doing exactly what it does today and gains
nothing. A **second** caller is what clause 11's fence exists to fail.

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
   existing 403-on-`deleted` (`main.py:542-547`) is the right shape and stays.
   Fence: `test_a_dead_org_placed_here_is_named_not_hidden`.
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
   ⚠️ **And a cached dead state overrides all three** *(added 2026-08-18)*: an
   answer already carrying `deleted` refuses sign-in **immediately**, Console
   reachable or not, inside the TTL or outside it — a dead state in the cache is
   a fact, not a stale hint, and staleness may only make the cache more
   restrictive, never less. `suspended`/`cancelled` land their **feature** locks
   the same way while login stays open, because `lifecycle.capabilities_of()` is
   the one state machine and this ticket does not mint a second copy of it.
   Fences: `test_a_cached_dead_org_refuses_without_asking_the_console`,
   `test_a_cached_suspended_org_locks_features_without_asking_the_console`,
   `test_staleness_never_relaxes_a_cached_state`.
7. **A lifecycle change lands within the stated bound — and the bound is a
   pair.** *(Corrected 2026-08-18: this clause read "lands within the TTL",
   which contradicted clause 6 and overstated what a partitioned box can
   promise.)* **Console reachable** → an organization moved to `suspended` locks
   features on the deployment no later than `CUSTOMER_CONSOLE_RESOLVE_TTL_SECONDS`,
   with login still working (§4.1d, `lifecycle.py:72-75`). **Console
   unreachable** → the change lands no later than
   `CUSTOMER_CONSOLE_RESOLVE_MAX_STALENESS_SECONDS`, because a box that cannot
   ask cannot learn — *unless* it already holds the dead state, in which case
   clause 6's rule fires at once. `deleted` refuses sign-in (`capabilities_of`
   is the only place that decides which states do). Fences:
   `test_a_suspension_reaches_the_deployment_within_the_ttl`,
   `test_a_partitioned_deployment_refuses_a_cached_person_at_the_ceiling`.
8. **Idempotent projection upsert.** Resolving the same person five times leaves
   exactly **one** `user_identity` row (matched on `lower(email)`) and **one**
   `org_membership` row, with `resolved_at` moved and nothing else rewritten.
   ◐ **Console half MET, projection half OPEN.** The two halves live in two
   databases and only one exists: the Console's own `user_identity` is `CITEXT
   UNIQUE` (`001:110`) and `store.ensure_identity` is `ON CONFLICT … DO UPDATE`,
   so five resolves leave one row and one membership — fence
   `test_resolving_five_times_writes_one_console_identity_row` *(renamed from
   `…_one_projection_row`, which named the tenant plane's table and would have
   read as green for a thing nobody built)*. The `resolved_at` clock and the
   `lower(email)` match belong to migration 159's projection and land with the
   deployment half.
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
   Choosing among them is the **chooser**, which is a non-goal below and stays
   one. Fences: `test_a_multi_org_resolve_allocates_no_seat`,
   `test_a_multi_org_person_is_refused_sign_in_and_told_why`.
10. **R8 — proven against a real Postgres.** Every clause in this list that reads
    or writes runs against a real Postgres 16 via
    `tests/unit/_customer_console_ladder.py`; the new suite
    (`tests/unit/test_customer_console_resolve.py`) is added to §7's command
    list **and** to `pr-check.yml`'s skip-guard, because CP-3's finding was that
    CI skipped every DB-gated fence while reporting green.
11. **Resolve is called from the sign-in path and from nowhere else, with a
    session-derived email.** *(Added 2026-08-18 — without it the seat cap is
    farmable, see the ⚠️ above.)* The email passed to resolve comes from the
    **completed** sign-in (the NextAuth callback that fires with an `account`
    present, i.e. a fresh sign-in rather than a token refresh —
    `workbench/control_plane/src/auth.ts:85-93`), forwarded once to the single
    gateway-side caller. It is **never** taken from `X-User-Email` on an
    ordinary request, and resolve is **never** hung off
    `_with_resolved_access`, which runs per request (`deps.py:391`, `:435`).
    Fence: `test_resolve_is_reachable_only_from_the_signin_path` — a
    **structural** fence is acceptable and preferred here (R7 prefers structural
    to example): assert over the tree that the resolve client has exactly one
    caller and name it, so a second call site added later fails rather than
    quietly doubling the seat surface.
    🔓 **Named accepted risk, pre-existing and NOT inherited silently:** the
    gateway trusts `X-User-Email` when it arrives with the internal Bearer, by
    design and in the code's own words — *"still trust Next.js"* (`deps.py:393`)
    and *"whoever holds the internal token can already assert any X-User-Email,
    so a narrower set would be theatre"* (`deps.py:402-404`, granting
    `SERVICE_ACCESS`, `access.py:42-45`). So a holder of **both** the internal
    token **and** the deployment key can still drive sign-in-shaped calls for
    addresses of their choosing and burn a customer's cap to `409`. Clause 11
    reduces the surface from *every authenticated request* to *one call site*;
    it does not remove it, and nothing in this ticket does. The residual is
    bounded by seat idempotence (`decide_assignment(already_assigned=True)` is a
    no-op that succeeds, `seats.py:129-130`), so the cost is one seat per
    **distinct** address, which is cheap for an attacker who already holds the
    box's internal token. **This risk predates CP-2b and is a property of the
    internal-token design (§4.3's `GATEWAY_INTERNAL_TOKEN` split is the gate
    that narrows it, work_plan §6).** Recorded here so the next reader does not
    discover it inside a billing incident.
12. **The deployment-key response schema is exactly this, and it carries no
    `role`.** *(Added 2026-08-18 — the shape was unspecified, and §5.2's code
    block still documented the forbidden one until the same change.)*
    - **Success:** `200` with `identity_id` and an `organizations` array; each
      entry carries `organization_id`, `slug`, the **placement target** it
      resolves to, the **lifecycle status**, and the **seat outcome**
      (`allocated` | `already_held` | `not_allocated`). **No `role` field, ever
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
      (`seats.py:135-146`; pinned today at `test_customer_console_api.py:142`).
      Reachable only in the single-visible-org case, since clause 9 allocates
      nothing when there are several.
    - **Dead org placed here:** `403` with the existing shape,
      `{"detail": "organization is <state>"}` (`main.py:542-547`) — named, not
      hidden, per clause 5's ⚠️.
    - **The operator scheme keeps its current shape unchanged**
      (`identity_id`, `organization_id`, `role`, `status`, `seats`,
      `main.py:594-600`). One endpoint, two schemes, two response shapes chosen
      by the credential — and the regression for that is clause 3's.
    Fences: `test_the_deployment_answer_never_carries_a_role`,
    `test_the_empty_answer_carries_nothing_but_an_empty_list`,
    `test_the_deployment_at_cap_returns_the_shipped_buy_more_payload`,
    `test_the_operator_response_shape_is_unchanged`.

**Gate split.** 🟢 **AGENT-SAFE:** the fourth scheme, the migration, the
endpoint change, the projection columns, the gateway-side caller, every fence,
and the whole thing exercised dark against fixtures. 🔴 **OWNER-GATE — refuse by
name:** **issuing a real `cc_depl_` key and setting it in a live deployment's
env** (registered as §8 gate 7). It is a credential issuance plus a deployment
env write, i.e. two existing gate classes at once, and a deployment key is the
credential that lets a box ask about people.

**Non-goals** (each is a later ticket, named so this one does not grow):
placement **heartbeat** on the deployment key — plausibly the second capability
it earns, and explicitly not now, because a capability set of one is the only
one that is obviously right · entitlement/module sync to the deployment ·
**enforcing** seats or lifecycle in product surfaces beyond sign-in (MT-2's
`intersect()` seam stays where it is, §2) · the multi-organization chooser for a
person visible in two orgs on one deployment · retiring the operator-auth shape
· any Router or metering change.

**Sequencing.** **CP-0** → CP-1 → CP-2 → **CP-2a** → **CP-2b** → CP-3 → CP-4 →
CP-5 → CP-6 → CP-7 → CP-8, with **CP-4b** owed out of order: CP-6 shipped before
it, and it must land before the first Router caller, because every agent runtime
streams. CP-4 is
where revenue-relevant data starts existing (real per-org burn, unpriced), and it is
worth reaching before CP-6 sets a rate card, because a rate card set on estimates is
a rate card you change on customers.

*(Sequence line updated 2026-08-18 — CP-2b inserted after CP-2a, CP-4b noted.
The board row in `work_plan.md` §2 carries the same line and was updated in the
same change.)*

## 7. Verification

```bash
# Customer Console. ⚠️ These need a REAL Postgres or they SKIP THEMSELVES (R8) —
# a skipped R8 test proves nothing:
#   export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
uv run pytest tests/unit/test_customer_console_seats.py tests/unit/test_customer_console_credits.py \
              tests/unit/test_customer_console_keys.py tests/unit/test_customer_console_sql.py \
              tests/unit/test_customer_console_api.py tests/unit/test_customer_console_key_auth.py \
              tests/unit/test_customer_console_router.py tests/unit/test_customer_console_lifecycle.py \
              tests/unit/test_customer_console_resolve.py

# The seam and tenancy ratchets this must not regress
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py

# The LLM choke point (existing suites that MUST stay green through the rework)
uv run pytest tests/unit/test_v1_compat_telemetry.py tests/unit/test_v1_compat_max_tokens.py \
              tests/unit/test_llm_usage_telemetry.py tests/unit/test_model_limits.py \
              tests/unit/test_byok_default.py

# Frontend
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

**R8 binds CP-2, CP-2b, CP-3, CP-4b and CP-6 specifically** — their subject is
queries, migrations and predicates, so they are run against a real Postgres before
they are believed. *(CP-2b and CP-4b added 2026-08-18: both of their done-when
lists already mandate R8 in their own words — CP-2b clause 10 and CP-4b's
metering clause — so this line was simply behind them.)* CP-2b's new suite
`tests/unit/test_customer_console_resolve.py` joins the command block **in the PR
that creates it**, together with `pr-check.yml`'s skip-guard entry; it is not
listed above because a verification command that names a file which does not
exist fails for the wrong reason.
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
3. **Live Razorpay credentials**, and any real payment configuration.
4. **Editing any live organization's entitlements, seats or credit balance** — same
   gate the Subscription Console's fulfilment already carries.
5. **Flipping CP-4's Router flag ON for a real customer**, and the §5.1 pooled cutover.
   ⚠️ *That flag does not exist in code (CP-4's 2026-08-18 amendment); the gate is
   correct and currently ungrounded — it binds the first caller ticket.*
6. **Issuing a production `cc_live_` key to a real organization.**
7. **Issuing a `cc_depl_` deployment key and setting it in a live deployment's
   env** (CP-2b, added 2026-08-18) — a credential issuance *and* a deployment env
   write. Minting keys against fixtures is AGENT-SAFE; a real one is not.

*All seven are registered in `work_plan.md` §6 as of 2026-08-18 — a gate that
lives only in a spec is a gate the dispatch board cannot enforce.*

## 9. Open owner inputs

Everything needed to build §6 is decided. These are commercial, not blocking:

1. **Seat price for the AI-only shape** — if an organization may buy AI credits
   without Center packages, that SKU is not in D23/D24's ladder. Assumed **no**
   until stated: credits are an add-on to a subscription, not a standalone product.
2. **Trial credits** — how many credits a trial organization starts with, and
   whether they survive conversion to paid.
3. **Auto-top-up default** — §3.3 says it is the default for paid plans; the trigger
   threshold and the top-up amount are unset.

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
