# Customer Console — the subscription, seat and AI-metering engine (WS-31)

*(Authored 2026-08-12 as the "Platform Control Plane"; renamed **Customer
Console** by **D41**, 2026-08-18 — file was `platform_control_plane.md`. The
path/env/package mapping is in D41.1.)*

**Status:** ◐ **CP-0 · CP-1 · CP-2a · CP-3 · CP-4 BUILT** (2026-08-12/13) — 172
platform tests against a real Postgres 16 per R8 · CP-3 was
**rejected by independent verification once** and rebuilt (see its ticket) ·
CP-2a ✅ · CP-2 · CP-5…CP-8 spec only · **where it runs is an open owner decision —
[`customer_console_infrastructure.md`](customer_console_infrastructure.md)** ·
**Date:** 2026-08-12 · **verified against code 2026-08-12** (repo-wide grep: zero hits for `usage_event`, `credit_ledger`,
`model_rate_card`, `usage_rollup`, `module_catalog`, `org_module_entitlement`
— none of the commercial substrate exists; `llm_api_key`'s eight hits are all
the *setting* `settings.llm_api_key`, not a table. Highest migration on disk:
`170_projects_search_trgm.sql`). · **Owner:** WS-31 (this spec) ·
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
  which becomes a *client* of this service rather than a reader of CC-local tables.
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

```
POST /registry/resolve { org, email } → { identity_id, role, seats: [...], status }
                                      | 409 seat_cap_exceeded { buy_more: ... }
```

Metorite caches the answer (TTL a config value) into migration 159's
`user_identity` / `org_membership` projection and proceeds. **This is what makes the
seat cap real**: a person cannot become a user of an organization without the
Customer Console allocating them a seat, because the box asks before admitting them.

Honest cost: deprovisioning is as fast as the cache TTL, not instantaneous. That is
a config value, not an architectural limit, and it buys not having to build and
secure an identity provider in the same quarter as everything else here.

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
the *opposite* of every other plane's requirement); `tests/unit/test_tenant_coverage.py`
records the new tables in `gen_tenant_migration.EXEMPT` with the §0.9.2 citation as
the reason — **R5(a) is satisfied by an explicit, reviewable exemption, never by
omission**.

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
Behind a flag, default **OFF** — Metorite can still call providers directly
(CLAUDE.md §4, ship dark). **Done when:** with the flag ON, a completion through
CC `/v1` is byte-identical for the client to one with it OFF (the streaming path
included — this is the choke point every agent runtime streams through); one
`usage_event` row exists per completion; a retried `request_id` writes **one** row,
not two.

**CP-5 · Tier capabilities and the model-access rework.** `GET /v1/tiers`, the CC-side
cache, `_fit_context_window` / `_clamp_max_tokens` driven from it, and removal of the
customer-facing model picker. **Done when:** a tier whose window shrinks in the
Router causes CC to fit to the smaller window without a CC deploy; a bare
(non-tier) model id returns 400 rather than being coerced; the Settings→Models
provider/model/tier tabs are gone from the customer product and
`npx tsc --noEmit && npx vitest run` is green.

**CP-6 · Rate card, ledger and the balance gate.** §3.4 + §4.4. **Done when:**
balance equals `SUM(credit_ledger.delta)` in a fixture and no code path UPDATEs a
balance column (structural fence — grep the tree, per R7's preference for
structural over example tests); a zero-balance org gets **402** with the top-up
payload while a non-AI endpoint on the same org still returns 200; the ~10%
overdraft is a named config value with a test at both edges; the per-run circuit
breaker trips a runaway loop.

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
org, not two, and a test kills it at each step; the lifecycle states of §4.1d are
enforced with `suspended` keeping **login working** while features lock; an org reaching
`cancelled` retains data through a named export window.

**Sequencing.** **CP-0** → CP-1 → CP-2 → **CP-2a** → CP-3 → CP-4 → CP-5 → CP-6 → CP-7 →
CP-8. CP-4 is
where revenue-relevant data starts existing (real per-org burn, unpriced), and it is
worth reaching before CP-6 sets a rate card, because a rate card set on estimates is
a rate card you change on customers.

## 7. Verification

```bash
# Customer Console. ⚠️ These need a REAL Postgres or they SKIP THEMSELVES (R8) —
# a skipped R8 test proves nothing:
#   export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1/cc_platform
uv run pytest tests/unit/test_customer_console_seats.py tests/unit/test_customer_console_credits.py \
              tests/unit/test_customer_console_keys.py tests/unit/test_customer_console_sql.py \
              tests/unit/test_customer_console_api.py tests/unit/test_customer_console_key_auth.py \
              tests/unit/test_customer_console_router.py tests/unit/test_customer_console_lifecycle.py

# The seam and tenancy ratchets this must not regress
uv run pytest tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py

# The LLM choke point (existing suites that MUST stay green through the rework)
uv run pytest tests/unit/test_v1_compat_telemetry.py tests/unit/test_v1_compat_max_tokens.py \
              tests/unit/test_llm_usage_telemetry.py tests/unit/test_model_limits.py \
              tests/unit/test_byok_default.py

# Frontend
cd workbench/control_plane && npx tsc --noEmit && npx vitest run
```

**R8 binds CP-2, CP-3 and CP-6 specifically** — their subject is queries, migrations
and predicates, so they are run against a real Postgres before they are believed.
**R1: migration numbers are taken at build time** (highest on disk at authoring was
170) and re-checked at merge.

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
6. **Issuing a production `cc_live_` key to a real organization.**

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
