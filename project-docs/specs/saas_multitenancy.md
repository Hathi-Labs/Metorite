# SaaS multi-tenancy — selling Metorite to other companies

**Status:** Architecture of record (owner-requested 2026-08-08) · **Board row: `work_plan.md` §2 → WS-29 · Decision: D15** · **§11 is the dispatchable ticket list — start there; [`saas_multitenancy_implementation.md`](saas_multitenancy_implementation.md) is its child and holds the build shapes** · **Owner:** vjvarada ·
**Supersedes:** `tenancy_and_visibility.md` §1 and §6 · **Verified against code:** 2026-08-08,
working tree at `b09093a`; **§11's MT-1 anchors re-verified 2026-08-19** ·
⚠️ **Updated 2026-08-19: `MT-1j · Tenant-side organization provisioning` MINTED,
slices 6 + 1 + 2 + 3 + 4 (OPERATOR ARM) are BUILT, and slice 5 is on its RATCHET
(rounds 1 + 2 landed — the ratchet now stands at `4 + 1`, exactly the owner-gated
floor of the set it measures)** — the
ticket four specs and one migration were
already disclaiming work to
(`_implementation.md` §7.1/§8 trap 5 · `customer_console.md` CP-2b §6(k) ·
`subscription_console.md` · `user_management_contract.md` §3 · `178:39-48`) and which
**did not exist**, while D36.1 asserts customer zero onboards through `/orgs/provision`.
Six slices, three agent-proposed defaults (**D43**), execution gated on H3. Three stale
anchors corrected in the same pass: `_HAS_OWNER_SQL` `:522`→**`:572`**, the second
`app_user` upsert `:509`→**`:550`**, and MT-0d's *"~20 call sites"*→**38** (measured).
**Slices 1+2+3 landed together as one branch** (migration `179_org_provisioning.sql`,
three plpgsql callables; new `tests/unit/test_org_provisioning.py`, 34 R8 tests, 0
skips) — a callable that nothing calls yet, by the auditor's own sequencing: slice 4
brings the box-side caller. **Slice 2 carries a recorded, argued deviation** from its own
"green without edit" clause — see its box. **Slice 4's OPERATOR ARM is BUILT
2026-08-19 (the deployment-key arm is CP-2c's, D46.6 item 2, §6-gated — see the
slice-4 build box); this banner briefly said "remains NOT BUILT" on the same
branch that built it, caught by the independent verifier as the CLAUDE.md §5
stale-mirror defect. Slice 5 is on
its ratchet: rounds 1 and 2 have landed (`11 + 10` → `9 + 1` → `4 + 1`), and every
**walker-visible** untenanted credential call is now an OWNER-GATED one named in the
H4 tables — slice 5 is at the floor of the set the ratchet measures absent an owner
act. ⚠️ Not of the tree: `key_store.py`'s own three `self.` calls (`:420`/`:433`/`:472`)
are uncounted and travel with the same owner act — MT-1j slice 5's round-2 box.** ·
⚠️ **Updated 2026-08-12 (D32 pass): §3.1 is REVERSED and
MT-3 is ABSORBED.** AI metering, per-org keys, the rate card and the credit ledger now
live in a **central Control Plane service** — owning spec
[`customer_console.md`](customer_console.md) (**WS-31**). §3.2–§3.5's design
is unchanged and still binding; only its placement moved. **D15 is untouched** — tenancy
is still a ROW, the deployment still a placement. Read §3's banner before citing §3.
MT-1's tenancy retrofit (H2–H6) is **unaffected and still the long pole**. ·
**Updated 2026-08-10 (D23 pass): pricing is
CENTER-SHAPED — §2.4b is the customer-facing shape of record** (Center packages
₹600/₹300 + all-Centers seat ₹1,800 + add-ons Builder ₹500/Workflows ₹300 + Complete ₹3,000 (D24); modules demoted
to internal billing atoms; D20's Team/Business retired; ₹10 credit model and D19.3
seat rules carry over; §8 item 5 holds the OPEN customer-framing questions) ·
*Prior update 2026-08-09* (consolidation pass): §8 items 1–2 first answered (D18,
since superseded), the Mem0 path-8 decision taken (D17, Option A), §5.1's cutover
trigger ADOPTED, MT-1a's stale `members.py` anchor corrected, and H1
scratch-verified; **migrations 157–159 CONFIRMED on prod 2026-08-09** (PR #404
merged, box self-applied via pull timer)

> **This document re-takes a decision that was deliberately taken the other way.**
> `tenancy_and_visibility.md` §1 (owner-answered 2026-08-03) set the tenant boundary at
> **the deployment** — one VM, one database, one credential set per customer — and §6 put
> row-level multi-tenancy, an org switcher, and multi-org users explicitly out of scope.
> That document's own §6 states the procedure: *"If any of these is ever wanted, the
> correct move is to re-take the §1 decision first, in this document, with a date and a
> reason — not to build one of them as a side effect of an app ticket."*
>
> **The reason: the business model changed.** Metorite is being sold to external
> customers, priced **per module, per user, per month**, plus metered AI. That price point
> and one-VM-per-customer are arithmetically incompatible (§1.4). This document is the
> re-take. `tenancy_and_visibility.md` §1/§6 are amended to point here; **everything else
> in that document — the visibility ladder in §3, the project-grant decision in §4, the
> gap table in §5 — survives unchanged and is still binding.** Tenancy and visibility are
> different axes: tenancy is *which company*, visibility is *who inside that company*.

**Purpose.** Answer four owner questions with decisions, not options:

1. What is the tenant boundary, and how does the system scale? (§1)
2. How are modules sold and enforced per company? (§2)
3. How is LLM access resold and metered? (§3)
4. How are accounts, subscriptions and billing managed? (§4)

§5 is the phased plan and §6 is what must be fixed **before the second tenant exists at
all** — those are correctness blockers, not features.

---

## 0. What already exists (measured 2026-08-08)

Read this first. Three of these findings are what make the recommendation below cheap
rather than a rewrite, and one is what makes it currently unsafe.

| Fact | Anchor | Why it matters |
|---|---|---|
| **One engine, one session factory, one `get_db()`** on the gateway **request path**, plus **six enumerable non-request paths** (§0.1) | `packages/acb_common/acb_common/db.py:107-136`; `tests/unit/test_db_engine_seam.py` fails the build if a new `create_async_engine` appears outside its allow-list | **The single most important finding.** Tenant scoping installs at a *named, bounded* set of connection sites, not at 3,000 query sites. ⚠️ **Not literally one — read §0.1 before quoting "one seam".** §1.3 |
| **`EffectiveAccess.intersect()` already exists** and is already used to narrow an agent's access to its member's | `packages/acb_auth/acb_auth/permissions.py:366-374` | Module entitlements are an intersection with a mask. The mechanism is already written and already tested. §2.4 |
| **`/v1/chat/completions` is the single LLM choke point**, and `_emit_usage()` already computes tokens + cache stats + USD cost per call, including for streamed responses | `apps/services/gateway/gateway/routes/v1_compat.py`; `packages/acb_llm/acb_llm/client.py:552-612`, rebuilt-from-chunks path at `v1_compat.py:563-573` | Reselling AI is ~4 additions to a seam that already meters. It is not a new subsystem. §3 |
| **`organization_id` is on 3 of 143 tables** and is read by **zero** authorization decisions | `130_org_access_control.sql:56,86`; `138_…sql:42`; `tenancy_and_visibility.md` §1.1 | The retrofit is 140 tables — but see §1.3 for why that is a generated migration, not 140 tickets |
| **`provider_keys` is keyed `provider TEXT PRIMARY KEY`** — one key per provider for the whole box | `infra/postgres/08_provider_keys.sql:6-7` | Must become `(organization_id, provider)` before a second tenant. §6 |
| ⚠️ **Integration credentials reach agents through process-global `os.environ`**, and the code says so itself: *"`os.environ` is process-global, so under concurrent [runs]…"* | `apps/services/orchestrator/orchestrator/executor.py:4335-4411` (write at `:4388`, restore at `:4409`) | **This is the one hard blocker.** In a pooled process, tenant A's Zoho token is visible to tenant B's concurrently-running agent. §6.1 |
| **`require_llm_api_auth` accepts one shared box-wide token** (`LITELLM_MASTER_KEY` or the internal token) | `packages/acb_auth/acb_auth/deps.py:448-472` | There is no per-customer attribution at the LLM layer today. §3.2 |
| **Roles, permissions, per-user overrides, feature catalog, groups, invites, audit — all shipped** | `130_org_access_control.sql`, `packages/acb_auth/`, `routes/admin/` | The *intra*-company model is done and good. This document does not touch it. |

Scale of the tree, for cost estimates below: **156 migrations · 143 tables · 209 gateway
Python files · ~142k Python LOC · ~149k TypeScript LOC.**

### 0.1 The connection inventory — correction, 2026-08-08

> ⚠️ **The first draft of this document said "one engine, one `get_db()`" without
> qualification. That was overstated and is corrected here.** It is true of the gateway
> request path and false of the process as a whole. An implementer who took the
> unqualified claim at face value would bind the tenant in `get_db()`, see the request
> path work, and ship six unbound connection paths.

Every path that opens a database connection, measured repo-wide.

> ⚠️ **Corrected 2026-08-08, and the correction is the lesson.** This table
> originally listed **eight** paths and said "measured repo-wide". It was measured
> across `apps/` and `packages/` — which is where the seam ratchets scan, and
> therefore exactly where a blind spot cannot hide. **Two more live in
> `scripts/`**, and one of them writes tenant data. The inventory was wrong in the
> same shape as the thing it was documenting: a scan whose roots decide its
> answer. Rows 9 and 10 were found by *building* the ratchet, not by reading.

| # | Path | Driver | Carries tenant data? | Tenant binding needed |
|---|---|---|---|---|
| 1 | `acb_common/db.py` — the shared async seam | SQLAlchemy/asyncpg | **Yes** — the whole request path | `SET LOCAL app.tenant_id` from the session |
| 2 | `email_ingestion/scheduler.py:160,545,578` | SQLAlchemy | **Yes** | Per-run binding from the job's org. Allow-listed in the seam test as *"separate process; per-run engines"* |
| 3 | `email_ingestion/inbound.py:271` | SQLAlchemy | **Yes** | Per-call binding. Same allow-list entry |
| 4 | `acb_graph/db.py:32` — entity graph | SQLAlchemy **sync** `create_engine` | **Yes** | Binding required. ⚠️ **The seam test only inspects `create_async_engine`, so this file is unguarded by it** |
| 5 | `acb_llm/key_store.py:83-108` | raw `psycopg` | Provider keys | Becomes per-org (§6.3) |
| 6 | `acb_llm/model_config.py:52-76` | raw `psycopg` | Model config | Becomes per-org (§6.3) |
| 7 | `acb_common/org_settings.py:55-81` | raw `psycopg` | Org settings | Already org-shaped; must bind |
| 8 | `acb_memory/mem0_client.py:99` | hands a conninfo to **Mem0's own** pgvector client | **Yes** — all memory | Binding must reach Mem0's connections, or memory is scoped by the scope string alone |
| 9 | `scripts/import_hr_people.py:177` | SQLAlchemy `create_async_engine` | **Yes — it UPSERTs people rows** | ⚠️ **Found 2026-08-08 while building MT-1c's ratchet, after this table claimed to be "measured repo-wide".** An operator script, outside `apps/` and `packages/`, so neither ratchet's scan roots saw it. Once phase-4 policies are on it will either fail or write **unowned rows**. Must bind a tenant from argv |
| 10 | `scripts/check_infra.py:40` | raw `psycopg.connect` | **No** — reads `pg_extension` only | Same blind spot, benign content. Disposition: healthcheck, no tenant needed — but it must be *recorded* as a decision, not left undiscovered |

**This makes RLS more important, not less — and it is the reason to prefer RLS over
application-level filtering or `search_path`.** A policy is enforced by the *server*,
so it covers paths 4–8 no matter which driver opens them and no matter what any
future package forgets. And it **fails closed**: with `app.tenant_id` unset,
`current_setting('app.tenant_id', true)` is NULL, `organization_id = NULL` is NULL, and
the query returns **zero rows**. An unconverted path breaks loudly in testing instead of
silently serving another tenant's data in production.

**Consequences for Phase 1 (§5), which are now explicit acceptance criteria:**

1. All **ten** paths bind a tenant. Paths 2–4 bind from the **job's** org, not a session; path 9 from argv; path 10 is exempt-with-a-reason (no tenant data).
2. **Extend `test_db_engine_seam.py` to `create_engine` as well as
   `create_async_engine`** — path 4 exists today precisely because the ratchet does not
   cover the sync call.
3. Add a companion ratchet for **`psycopg.connect`**, with the same allow-list-with-a-reason
   discipline. Paths 5–7 were invisible to the existing test.
4. **Mem0 (path 8) is the genuinely awkward one** — the connection is opened by a
   third-party library from a conninfo string. **DECIDED 2026-08-09 (D17,
   `agent-proposed, owner may overrule` — `work_plan.md` §3): Option A, bind via
   connection options** (`options=-c app.tenant_id=<uuid>` on the conninfo Mem0
   receives; shapes in `saas_multitenancy_implementation.md` §2.4). The alternatives,
   kept for the record: bind via connection options in the
   conninfo, or give Mem0 its own tenant-scoped database role per tenant, or accept that
   memory isolation rests on the scope string and pin that decision here. **Do not leave
   it undecided.**

---

## 0.9 THE TARGET, stated without reference to what exists

Owner question, 2026-08-08: *disregarding the cost of migrating the current database — we
can start a new one — what is the right multi-tenant architecture for Metorite?*

Answered here **before** §1, because §1 onward reasons from the existing tree and a reader
should be able to see the destination without the retrofit argument attached to it.

### 0.9.1 The thesis — the interesting boundary is not the database

Almost every multi-tenancy discussion is a database discussion. **For Metorite that
is the wrong emphasis, and it is wrong for a reason specific to this product:**

| Ordinary SaaS (Slack, Notion, a CRM) | Metorite |
|---|---|
| Code paths are written by your engineers | **Agents execute model-generated tool calls** |
| Input is typed by authenticated users | **Input arrives from email and WhatsApp** — adversarial by default, prompt injection is a routine event, not an exotic one |
| The app reads and writes rows | Agents **write and run code** (App Workshop, self-mutation) |
| A breach exposes one product's data | A breach exposes **the company** — mail, CRM, finance, HR, meetings, all of it |

The database can be defended by a mechanism that cannot be forgotten: a server-enforced
RLS policy. **The agent runtime has no equivalent.** No policy engine constrains what a
model decides to do with the tools it holds.

> **Therefore: spend the isolation budget on the execution plane, and let the data plane be
> pooled behind a policy.** The instinct that led to "a container per customer" is sound —
> it is simply pointed at the wrong layer. Put the container around **the agent run**,
> not around the database.

This is also the honest answer to why the pooled-vs-silo argument has felt unsatisfying
throughout this document: **both options isolate the layer that was already the easier one
to isolate.**

### 0.9.2 Three planes, three different tenancy models

| Plane | Holds | Tenancy model | Why |
|---|---|---|---|
| **Control** | organizations, identities, placement, entitlements, subscriptions, usage, credit ledger | **Shared, cross-tenant by design.** No RLS — it must read across tenants | It is the operator's view. Never holds tenant business data, so a compromise exposes contracts, not customers' mail |
| **Data** | email, CRM, tasks, projects, people, meetings, memory, apps | **Pooled Postgres, `organization_id` in every PK, FORCE RLS, per-tenant envelope encryption for sensitive columns** | §1 and §1.1a. The mechanism cannot be forgotten and fails closed |
| **Execution** | agent runs, ingestion jobs, app runtimes, mutation, meeting bots | **Ephemeral per-run sandbox, tenant-affine worker pools** | §0.9.3. This is where the money goes |

### 0.9.3 The execution plane — the part worth building properly

**The contract, and it is the whole design:**

> **An agent run receives (a) one tenant binding, (b) only the credentials that run needs,
> issued for that run and expiring with it, and (c) no database connection at all. It
> reaches data exclusively through a tenant-bound API. Its egress is allowlisted. The
> sandbox is destroyed when the run ends.**

Each clause closes a specific hole:

- **No ambient credentials** — kills the class where a compromised agent reads secrets
  belonging to work it was not doing. Today `executor.py:4388` writes them into
  process-global `os.environ` (§6.1), which is the exact opposite of this clause.
- **No database connection** — an agent that cannot open a connection cannot escape RLS,
  cannot set `app.tenant_id`, and cannot be SQL-injected into another tenant. This is what
  makes a pooled data plane defensible *given* model-generated tool calls (§1.8a).
- **Allowlisted egress** — a successfully injected agent that can reach any URL can
  exfiltrate whatever it legitimately holds. Isolation without egress control is theatre.
- **Ephemeral** — no state carries from one tenant's run to the next.

**Implementation, in ascending order of strength:** container per run with seccomp and
no-network-by-default → gVisor → Firecracker microVMs. **Clean slate, start at the first
and design so the third is a swap, not a rewrite.** Keep per-tenant warm pools for
start-up latency; the pool is an optimisation and must never become the isolation
boundary.

> ⚠️ **This inverts a live owner decision and the inversion is the point.** WS-3's T2 tier
> is **parked** (D10, 2026-08-03) on the explicit ground that *"the ladder must hold
> against trusted colleagues, not hostile users."* **Selling to external customers
> replaces that threat model.** Un-parking T2 is not an optional hardening item under this
> architecture — it *is* the architecture. P5-a (per-run credential scoping) already
> shipped, which means the hardest conceptual piece exists; what is parked is the
> enforcement tier above it.

### 0.9.4 Fewer datastores — a clean-slate simplification worth taking

Today: Postgres + pgvector, Redis, Neo4j, Langfuse, plus filesystem workspaces. **Every
additional datastore is another place tenancy must be enforced and another place it can
be forgotten** (§1.9's table is that cost, itemised).

Clean slate:

- **Drop Neo4j.** Neo4j Community offers one database and no real multi-tenancy, so the
  graph becomes a tenancy problem with no good answer. An edge table in Postgres with
  recursive CTEs covers the entity/memory graph at this scale, and inherits RLS for free.
  **Removing a datastore removes a boundary** — that is a security improvement, not just
  an ops one.
- **Blobs to object storage**, `<org_id>/…` prefixed, per-tenant keys — never `BYTEA`
  (§1.6).
- **Redis stays, but tenant prefixing is enforced by a wrapper client**, not by
  convention. A convention is a thing people forget; a client that cannot construct an
  unprefixed key is not.
- **Vectors get per-tenant namespaces** (partition or separate index per large tenant).
  HNSW is the memory-hungry structure and the one place per-tenant physical separation
  earns its cost on merit (§1.6).

### 0.9.5 Identity, resolution and placement

- **Global `user_identity` (email unique) + `org_membership`** from day one. Multi-org is
  not a future feature: your own support staff need it on day two, and partners and
  consultants on day thirty (§1.5).
- **Tenant from the authenticated session or a tenant-scoped API key. Never a header,
  query parameter or body field** (§1.5's binding rule).
- **Subdomain per tenant** for the workbench.
- **A `tenant_placement` indirection from day one**, even when every tenant resolves to
  the same target. It costs one table and one lookup, and it is what turns "move this
  customer to their own database" from an architecture change into a data move — which is
  what makes the silo tier, the competitor objection (§1.8a) and version pinning (§1.4b)
  all answerable with the same mechanism.
- **Evaluate an external IdP** (WorkOS, Clerk, Keycloak) rather than growing this
  yourself — SAML and SCIM arrive with the first enterprise deal (§1.8a).

### 0.9.6 The three invariants

Everything above collapses to three lines. If a design question is ever unclear, resolve
it against these:

> 1. **The tenant is derived from the authenticated principal — never from input.**
> 2. **No code path reaches tenant data without a tenant bound, and the *database*
>    enforces that, not the developer.**
> 3. **Agents hold no ambient authority** — no ambient credentials, no database
>    connection, no unrestricted egress. Everything per-run, scoped, and expiring.

Invariants 1 and 2 are ordinary good multi-tenancy. **Invariant 3 is the one this product
lives or dies on**, and it is the one an ordinary SaaS architecture would not tell you to
write down.

### 0.9.7 Build order, clean slate — and what NOT to build

1. Control plane + identity + tenant resolution, **with placement indirection from day one**
2. Data plane: RLS, `organization_id` in every PK, partitioning on the heavy tables,
   object storage for blobs, envelope encryption for secrets and sensitive columns
3. **Execution plane sandbox contract** (§0.9.3) — before the first external tenant, not after
4. Entitlements + feature flags (§2, §1.4b)
5. Metering + credits (§3)
6. Billing automation (§4)

**Do not build, clean slate or otherwise:** Kubernetes (Docker Compose on a few VMs until
it genuinely hurts — a small team's scheduler is a distraction, not a capability), Citus or
any sharding layer (adopt the distribution-key *discipline*, not the technology, §1.8a),
a service mesh, or a microservice split. **The monolith is correct here**; what needs
splitting is the **three planes' data and trust boundaries**, not the deployment topology.

### 0.9.8 How much of this the phased plan already reaches

Stated so the clean-slate answer and §5 are not read as two different plans:

| Clean-slate element | In §5? |
|---|---|
| Control plane separate from tenant data | ✅ Phase 1 (§1.5) |
| Pooled + RLS + org_id in PKs | ✅ Phase 1 |
| Partitioning, object storage for blobs | ✅ Phase 1 (§1.6) |
| Envelope encryption | ◐ Phase 5, pull into Phase 1 if touching those columns (§1.1a) |
| Identity/membership split, placement, subdomain | ✅ Phase 1 |
| Feature flags + release channel | ✅ Phase 2 (§1.4b) |
| **Execution-plane sandbox contract** | ⚠️ **Phase 0 covers only the credential half (§6.1). The sandbox tier is WS-3 T2 and is currently PARKED.** This is the single largest gap between the phased plan and the target |
| Drop Neo4j, wrapper-enforced Redis prefixes, per-tenant vector namespaces | ❌ Not in §5. Cheap now, expensive later — **fold into Phase 1** |

**The honest summary:** the phased plan converges on the target for the data plane and
diverges from it on the execution plane. Given a genuinely clean slate, **build §0.9.3
first and the database question mostly stops being interesting.**

---

### 0.9.9 Prior art — Odoo, Salesforce, SAP, and the one rule that separates them

*(owner-raised 2026-08-11: "Odoo is also a large complex app with multiple sub-apps for
one company — study its multi-tenancy and tell me whether shared-database is still ideal."
Reviewed against Odoo, then Salesforce, then SAP. Recorded here so the comparison is not
re-run and D15 is not re-litigated from the same starting point.)*

**Outcome: D15 stands, unchanged.** The review changed nothing about the boundary and
produced exactly one genuine finding — §6.6 below.

#### The rule the comparison yields

The question "does per-customer customization force a database per customer?" has a
clean answer, and it is not the obvious one:

> **What forces a silo is not customization. It is customization implemented as DDL.**

Odoo adds a custom field by issuing real DDL — a physical `x_…` column on the shared
table, registered in `ir.model.fields` — and stores installed modules, views and record
rules as rows *in the same database*. Two Odoo tenants sharing a database would therefore
have to share their customizations and their version. That is the forcing function.
Notably it is **not** a verdict on row-level security: Odoo already ships row-level
filtering (`ir.rule` record rules) and trusts it to separate legal entities' books inside
one database. It cannot share the metadata layer, which is a different problem.

Metorite is the inverse and already demonstrates it. `155_projects_custom_fields.sql`
ships ClickUp-style custom fields with **definitions as rows** (`pm_custom_fields`) and
**values as JSONB** on `pm_tasks` behind a `jsonb_path_ops` GIN index. Seven field types,
per-project, and the schema never moves. Custom apps are the same shape — rows in
`apps`/`app_versions` (migration 114), executed in the sandboxed execution plane (§0.9.3),
never as tenant code in the database.

| | Customization expressed as | Tenancy | Reads as evidence for |
|---|---|---|---|
| **Odoo** | DDL + metadata rows in the tenant DB | Database per tenant | Its own implementation choice, not RLS's limits |
| **Salesforce / ServiceNow / NetSuite** | Metadata rows + generic value storage | **Pooled, shared schema** | Deep customization is compatible with pooling — at the cost of a large metadata engine |
| **SAP (BTP/CAP)** | Key-user extensions + side-by-side apps on stable APIs | Shared app, **HDI container (schema) per tenant** | Constrain *how* extension is expressed, not where data sits |
| **Metorite** | Rows + JSONB (`pm_custom_fields`), apps as rows | **Pooled + FORCE RLS** (D15) | — |

#### What each one actually contributes

- **Odoo** is the outlier, and for a historical reason: it grew up as on-prem ERP where
  DDL-per-customer was free. Its capacity figures nonetheless argue *for* pooling at our
  price point — 300–400 MB RAM per tenant for isolated instances, 8–10 tenants per 16 GB
  box, against Center packages at ₹600/₹300 per seat (§2.4b).
- **Salesforce** is the strongest counter-example to "customization needs silos": the
  deepest per-tenant extensibility in the industry, pooled. **The caution it carries is
  the cost** — that flexibility is paid for with an enormous metadata engine. Keep custom
  fields typed, defined and narrow, as migration 155 does. A general EAV platform is a
  priced feature with its own spec, never a pattern to spread table by table.
- **SAP** is the most useful comparison because it is the closest to us *and* it partly
  disagrees. Its CAP model — one shared application, one HDI container per tenant,
  provisioned on subscribe via `cds-mtx` + Service Manager — is §1.8's schema-per-tenant,
  working, in production, at scale. **Recorded honestly: this is real evidence against
  §7 item 1c, and the reason it survives is ours, not a flaw in theirs.** CAP automates
  schema deployment across containers and SAP ships on a quarterly cadence; we ship
  continuously against a 160+ file hand-written ladder, applying migrations before
  restart, with **no rollback** (R6). Schema-per-tenant would make every deploy a fleet
  operation with partial-failure states. If our cadence or our ladder ever changes
  character, this rejection is the one worth re-taking.
- **SAP also re-invented pooling.** CAP provides an explicit *shared container* so the
  provider can hold master data centrally and update it once for all tenants, built on
  HANA cross-container access. That is our control plane (§0.9.2) arrived at from the
  opposite direction: both architectures need a per-tenant space **and** a shared
  cross-tenant space. Starting pooled and carving out the control plane is the cheaper
  direction when most data is tenant-scoped.
- **One nuance worth not overstating:** SAP's own guidance says HANA MDC suits a *trusted
  environment* and does **not** recommend running different customers in one MDC system
  without raised isolation. "SAP uses tenant databases" is therefore not the same claim as
  "SAP treats a tenant database as a hard customer boundary."

#### What the review does **not** dissolve

Two properties that database-per-tenant gets free and we must engineer, both already
recorded rather than newly discovered:

1. **Blast radius is literal.** One pooled database is one failure domain — not
   theoretical for us: on 2026-08-06 a leaked idle-in-transaction session parked DDL
   behind it and email send died. Pooled, that incident is fleet-wide.
2. **RLS fails open if a fence breaks; separate databases fail closed.** This is why
   FORCE RLS at the seam, the tenancy-boundary test and the generated-set fence all
   matter, and why **MT-1d must land before the MT-1b phase-4 promotion** (D27's second
   finding: `run_lifecycle_sweep` sweeps every tenant with no predicate).

---

## 1. DECISION — the tenant boundary is a ROW, and the deployment is a placement

> ### `Tenant = organization_id, enforced by Postgres RLS at the connection seam.`
> ### `Deployment = a placement decision (region / tier), not a tenant boundary.`
> *(owner-requested 2026-08-08)*
>
> Standard customers are **pooled**: one app fleet, one database, isolation enforced by
> the database itself. A dedicated database or a dedicated stack is a **priced tier** for
> customers who ask for it — the same code path, a different row in the tenant catalog.

### 1.1 The question the owner asked, answered directly

> *"Should we spin up new containers with a completely different database for each
> customer so that everything is isolated and separate?"*

**No — not as the default.** Do it for the handful of customers who pay for it.

The instinct is right about the *goal* (a customer must never see another customer's
data) and wrong about the *mechanism*. Container-per-customer buys isolation against a
threat that is not the real one, at a cost that breaks the price point.

**The real leak vector in this system is the application, not the database engine.**
Metorite's dangerous surfaces are an agent with broad tool access, a missing
predicate in one of 209 gateway files, a prompt injection arriving through an ingested
email, and process-global credentials (§0). A separate Postgres container stops none of
those. A tenant-scoped connection that the *database* refuses to widen stops the first
two, and per-run credential scoping (§6.1) stops the fourth. Spend the isolation budget
where the leaks actually are.

### 1.1a "One database for everyone" — where pooled systems actually get their safety

Owner question, 2026-08-08: *isn't it dangerous that Google Workspace keeps every
organization in one database?* Recorded because the premise contains a category error
that is worth fixing permanently, and because the correction produces a Phase-5 item this
document was missing.

**First, the premise.** Workspace is not "one database" in any physical sense. It is **one
logical namespace, physically sharded by customer across thousands of machines** —
Spanner and Colossus, with customer/domain as the partition key. *"Pooled" is a statement
about the schema, not about the hardware.* One customer's data occupies its own contiguous
key range on its own machines; it is simply addressed through one logical system rather
than N administratively separate ones. That is exactly what §1.8a's distribution-key
discipline buys, in miniature.

**Second, the honest part: yes, pooling concentrates consequence.** A single
authorization bug in a pooled system is potentially every customer, where in a silo it is
one. That is real, and no amount of architecture argument makes it not real.

**Third — and this is the load-bearing observation — Google's safety does not come from
its storage topology. It comes from two layers deliberately built because the storage is
pooled:**

1. **One central authorization service that every product must ask.** Zanzibar stores
   ACLs as `user U has relation R to object O` tuples and answers permission checks for
   Drive, Docs, Calendar, Photos, Maps, YouTube and Cloud — **trillions of ACLs, millions
   of checks per second, sub-10 ms p95, >99.999% availability**, published in Google's
   2019 paper. No product re-implements access control; there is exactly one place to get
   it right, and it cannot be forgotten because there is no other way to answer the
   question.
2. **Per-customer encryption keys underneath.** Google's storage layer splits data into
   chunks and encrypts each with keys **separate from those used for other customers** —
   and separate even from other chunks of the same customer's data. Pooled storage is
   therefore not pooled *plaintext*: a compromise at the storage layer does not yield
   readable cross-tenant data.

> **The transferable rule: safety in a multi-tenant system comes from a single
> un-forgettable enforcement point plus a layer beneath it that fails safe — not from how
> many database processes are running.** Silo is one way to buy a weak version of that
> guarantee; a policy the database enforces is a stronger version, and it is the version
> that survives a developer forgetting.

**What this changes in this document.** RLS (§1.3) is Metorite's Zanzibar-analogue at
its scale: one enforcement point, on the server, that no route can forget. **The second
layer is missing and is now a Phase 5 item:**

> **Per-tenant envelope encryption for the sensitive columns** — integration credentials,
> provider keys, message bodies, transcripts — with a per-tenant DEK wrapped by a master
> KEK. It makes a raw storage or backup compromise tenant-scoped rather than global, which
> is the specific residual risk pooling introduces and the only one silo genuinely
> answered. **Retrofitting encryption to populated columns is materially harder than
> adding it at rest-write time**, so if any of these columns are being touched during
> Phase 1, do it then instead.

**What it does not change.** The comparison in §1.4 stands: silo shrinks one category of
bug, does nothing about the categories that cause most real breaches (session and
credential handling, SSRF, dependency compromise, a phished admin, an exposed backup), and
adds one of its own — **wrong-database routing, plus N versions of the access-control code
in production** (§1.4). Concentrated consequence is a real cost, paid for with a
lower probability of the bug occurring at all.

### 1.2 How the companies you named actually do it

The pattern is consistent across all of them, and it is the opposite of
container-per-customer:

| Company | Tenant boundary | Deployment boundary |
|---|---|---|
| **Salesforce** | `OrgId` column on shared tables; one shared, metadata-driven schema serving 150k+ tenants. The canonical proof that pooled scales. | Regional "instances"/pods. A customer is *placed* on a pod; they do not get one. |
| **Microsoft 365 / Entra** | Entra **tenant ID**. Users, licences and policy all key off it in a shared directory service. | Regional scale units and forests. Dedicated stacks exist only as **sovereign/government clouds** — top of the price list, not the default. |
| **Google Workspace** | Customer ID / verified domain. Gmail, Drive and Calendar are massively pooled systems; your company's data is a partition key, not a server. | Data-residency *policy* on a pooled fleet (Assured Controls), not a per-customer deployment. |
| **Zoho** | Pooled per data centre. The customer's choice is *which DC* — US, EU, IN, AU. | The DC is the placement. Zoho One's per-module licensing (§2) rides on top of that pooled base. |

**The rule they all follow:** *the tenant is a row-level concept; the deployment is a
region/tier concept.* Nobody at scale gives a 10-seat customer their own database,
because the marginal cost of a small customer must be near zero or the SMB tier cannot
exist.

The industry names these shapes **pool / bridge / silo** (AWS's SaaS terminology). The
2026 consensus for B2B SaaS is: **pool for the standard tier, bridge or silo for
enterprise customers who pay for it.**

### 1.3 Why this is affordable HERE — the seam that changes the arithmetic

`tenancy_and_visibility.md` §1.2 rejected row-level tenancy on the grounds that it would
*"put a `WHERE organization_id = ?` on 111 tables and every query in the gateway."*
**That objection is wrong, and the reason is `acb_common/db.py`.**

Because the connection sites are a **bounded, named set of eight** (§0.1) rather than
3,000 query sites, tenancy installs at those eight plus three structural changes — and
**zero existing `SELECT`/`INSERT` statements are rewritten**:

**(a) One migration, generated — not 140 hand-written ones.**
```sql
ALTER TABLE <t> ADD COLUMN organization_id UUID
    NOT NULL DEFAULT current_setting('app.tenant_id', true)::uuid
    REFERENCES organization(id) ON DELETE CASCADE;
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;   -- ← without this the owner bypasses it
CREATE POLICY tenant_isolation ON <t> USING (
    organization_id = current_setting('app.tenant_id', true)::uuid
);
CREATE INDEX <t>_org_idx ON <t> (organization_id);
```
The column **default** is what means `INSERT` statements do not change either. The
existing single org backfills every row.

**(b) One seam edit.** `get_db()` binds the tenant onto the session:
```python
async def get_db(tenant_id: str | None = None) -> AsyncSession:
    s = get_session_factory()()
    await s.execute(text("SET LOCAL app.tenant_id = :t"), {"t": tenant_id or _ctx_tenant()})
    return s
```
> ⚠️ **Correction (2026-08-10, found by the first live H2 run):** the literal
> `SET LOCAL app.tenant_id = :t` is a Postgres **syntax error** through the extended
> protocol — `SET` cannot take a bind parameter. Every hermetic test was green with it;
> real Postgres refused it on the first converted handler. The shipped encoding in
> `acb_common.db.tenant_session()` is `SELECT set_config('app.tenant_id', :tenant, true)`
> — identical transaction-local semantics (`is_local = true` IS `SET LOCAL`), but
> parameterizable. The warning below still binds; only the spelling changed.
> `test_tenant_session.py` pins the `set_config` form and refuses both the literal and
> an `is_local = false` variant.
> ⚠️ **`SET LOCAL`, never `SET`.** The pool recycles connections across requests
> (`pool_size` + `max_overflow`, `db.py:114-120`). A session-scoped `SET` survives the
> connection's return to the pool and becomes a cross-tenant read on the next borrower.
> `SET LOCAL` is transaction-scoped and resets on commit/rollback. This is the single
> highest-consequence line in the whole migration and it needs its own test.

**(c) One role change.** The app must connect as a **non-owner, non-superuser** role.
Postgres RLS is bypassed by superusers, by `BYPASSRLS`, and by the table owner unless
`FORCE ROW LEVEL SECURITY` is set. Migrations keep running as the owner; the gateway
gets `acb_app`.

**(d) One build-failing test**, in the spirit of `test_db_engine_seam.py`: enumerate
`pg_tables`, assert every application table has `organization_id`, `FORCE` RLS, and a
policy. A table added tomorrow is covered without anyone remembering — the same
by-construction discipline root `AGENTS.md` constraint 10 already applies to auth.

That converts a 6-month rewrite into roughly **3–4 weeks**. It is still the largest
single piece of work in this document, and §1.5 lists what it does *not* cover.

### 1.4 Why container-per-customer fails this business — priced at 10–50 seats

> **Owner input, 2026-08-08: every customer is a company of 10–50 users**, not an
> individual. This section was first written against a 10-seat single-module customer and
> **its lead argument does not survive that input.** Corrected here rather than quietly
> left standing, because the corrected version is what makes the recommendation honest.

**What no longer holds — the infrastructure-cost argument.** A 25-user company of
desk workers on D23 pricing (₹600 Core + ₹600 Personal + one department Center
₹600 ≈ ₹1,800/user for a typical seat) is ₹45,000/month (≈$540); even all-Core-only
it is ₹15,000 *(earlier drafts computed ₹37,500 from module-first pricing — same
order of magnitude, same conclusion)*. A VPS able to run a full stack is
~$30–40/month — roughly **8% of revenue**. That is affordable. The original claim that
*"the SMB tier does not exist under this model"* was priced for a customer a quarter this
size and **is withdrawn.** At this ACV, dedicated infrastructure is not what breaks.

**What holds, and holds harder — the cost that scales in people, not servers.**

- **156 migrations × N customers × every deploy.** This is the binding constraint and it
  gets *worse* as the customer base grows, because it is linear in N and paid by a small
  team every week. At 20 customers, deploy babysitting, per-box backup verification and
  N incident surfaces realistically consume **half an engineer** — permanently.
- **N boxes means N versions of your access-control code in production.** A migration that
  fails on customer 14 leaves customer 14 running the old permission check. With
  `130_org_access_control.sql` and its successors defining who can see what, **version
  skew is a security defect, not an ops annoyance** — and it is a defect that pooled
  cannot have.
- **Self-service signup is impossible.** Onboarding becomes DNS, TLS, systemd units and
  credential sets — `tenancy_and_visibility.md` §1.2 priced it at *"roughly a day of
  owner-gated work and a permanent second thing to patch."*
- **Cross-tenant product features need fan-out across N databases** — the Operator Console
  (§4.1), aggregate usage, benchmarks, a shared agent marketplace.

**The crossover, stated as a number so the decision is checkable.** Silo's cost is
**linear in customers**; pooled's is a **one-time 4–5 weeks** (§5 Phase 1). They cross at
roughly **8–12 customers**. Below that, silo is genuinely cheaper *and* faster to revenue.
Above it, silo compounds. See §5.1 for what to do with that.

**Where silo is still right:** a customer with a genuine regulatory or contractual
requirement, paying for it, onboarded by hand — and the first handful of customers, as a
deliberate bridge (§5.1). Price it. Don't build the product on it.

### 1.4b Customers on different versions — the one requirement that could overturn §1

Owner question, 2026-08-08. **This is the strongest argument for silo raised so far**, and
it is strong because it attacks §1.4a's own test: *who controls the upgrade cadence?* If
the answer becomes "the customer", the test points at silo and this document must follow
its own reasoning rather than defend its conclusion.

**The phrase covers four different requirements with four different answers. Establish
which one is meant before designing anything.**

| What "different versions" means | Answer |
|---|---|
| **A. Staged rollout / canary** — A gets v2.1 this week, B next week, everyone converges | **Pooled, unchanged.** Standard practice; needs expand/contract migrations (below) |
| **B. Release channels** — a customer chooses "give me changes two weeks late" | **Pooled, unchanged.** This is what Google Workspace ships as Rapid vs Scheduled Release: same code, same storage, admin picks the channel |
| **C. Per-customer configuration/features** — A has modules, fields, workflows or agents B does not | **Pooled, and already solved** — see below. This is the case owners usually mean |
| **D. Genuine version pinning** — A stays on v1.8 for a year because it was validated and must not move | **Silo tier. Pooled cannot do this**, and no amount of engineering makes it |

**Why pooled handles A, B and C — and exactly where it stops.**

> **Multiple *code* versions against one database: fine.** Every blue/green deploy, canary
> and rolling update already runs N code versions against one schema simultaneously.
> **Multiple *schema* versions in one database: impossible.** One database has one schema.

The discipline that makes A and B safe is **expand/contract** (parallel change), and it is
non-negotiable once two versions run at once: add the column nullable → deploy code that
writes both old and new → backfill → deploy code that reads new → drop the old **only
after every running version has passed the read step**. Additive-only, never rename in
place. Where two versions genuinely need different shapes of the same data, a **view per
version** over one physical table buys more room. That comfortably supports **two or three
adjacent versions over a window of weeks**. It does not support eighteen months of drift —
that is case D.

**Case C is already built, and this is the finding that matters most.** Metorite's
per-customer variation is **data, not code**, across the board:

- **Custom Apps** — `114_custom_apps.sql` + `app_files`: apps are DB rows, not deployed code
- **Workflows** — root `AGENTS.md` is explicit that they are *"DB-persisted configuration
  orchestrating code-authored agents"* (ADR-028), the sanctioned exception to no-in-app-authoring
- **Dynamic agents** — `15_dynamic_agents.sql`: registered and persisted, not compiled in
- **Custom fields** — `pm_custom_fields` + `custom_fields JSONB` (`155_…sql:28,80`)
- **Org settings** — `organization.settings JSONB` (`130_…sql:42`), plus `config JSONB`
  on workflows, plugins, projects and agents

> **The platform's whole design premise is that customers extend it with data rather than
> with forks.** Per-tenant data is exactly what a pooled database is good at. **Do not
> reach for per-customer code versions to deliver something the configuration layer
> already delivers** — that trades a solved problem for an unsolved one.

**What customers actually want when they ask for "our own version".** Almost always:
*"don't change things under me without warning."* That is a **release channel plus feature
flags**, not a code fork — and it is why no major SaaS offers version pinning while all of
them offer rollout control. **Add a feature-flag layer** (per-org, per-feature, evaluated
beside the entitlement mask in §2.3, since it is the same shape of lookup) and cases A–C
are covered without touching tenancy. **This is now a Phase 2 item.**

**If the requirement is genuinely D.** Then it is real and it is expensive, and both facts
should reach the customer:

1. **Version-pinned customers go on the silo tier** (§1.5). This is that tier's **second
   independent reason to exist**, alongside compliance and the competitor objection
   (§1.8a) — three unrelated demands, one mechanism, which is a good sign the tier is
   correctly drawn.
2. **Price it at what it costs.** Version pinning means a supported branch, backported
   security fixes, and a separate test matrix — the cost structure that turns enterprise
   software vendors into maintenance organisations.
3. **Cap it contractually**: current version plus one prior; older than that is upgrade or
   lose support. **A cap written after the first pinned customer is a negotiation; written
   before, it is a policy.**

**When this overturns §1.** If D stops being the exception and becomes what most customers
buy, the §1.4a test has genuinely flipped — the customer controls the cadence, and
Metorite is on Hostinger's side of the line rather than WordPress.com's. **Re-take §1
at that point.** Nothing in the phased plan (§5) is wasted if that happens: the silo
customers of §5.1 are already the mechanism, and every silo running the pooled schema is
what keeps both doors open.

### 1.4a The WordPress analogy — why hosting and SaaS answer this differently

Raised by the owner 2026-08-08, and worth recording because the intuition is common,
reasonable, and points the opposite way once followed through.

Hostinger gives every WordPress install its own database. **That is correct for
Hostinger and irrelevant to Metorite, because Hostinger is a host, not a SaaS.**
The determining question is:

> **Who controls the schema and the upgrade cadence — you, or the customer?**

| | Customer controls the app | **You** control the app |
|---|---|---|
| Examples | WordPress on shared hosting · self-hosted Odoo · Jira Data Center | Salesforce · Google Workspace · Slack · Zoho · **Metorite** |
| Consequence | The host cannot know or migrate the schema; customer A may run WP 5.8 while B runs 6.4; the customer installs arbitrary plugins that alter tables | You ship one version to everyone; customers cannot fork the schema or install plugins into your Postgres |
| Correct model | **Database per install — mandatory** | **Pooled — the norm** |

**WordPress's own answer, when WordPress is the SaaS, is not database-per-customer.**
WordPress Multisite puts every site in **one database**, adding a per-site *table prefix*
(`wp_2_`, `wp_3_`, …) over a set of shared network-wide tables — users among them. And at
WordPress.com scale the fix was **hash-based sharding into 16 / 256 / 4096 shards**, not a
database per site.

Two things follow, and both support this document's decisions:

1. **Same software, different business model, different answer.** Hostinger silos because
   the customer owns the install. WordPress.com pools because WordPress.com owns it. You
   own Metorite. You are on the WordPress.com side of that line, not Hostinger's.
2. **Multisite's per-site table prefix is schema-per-tenant in a different costume — and
   it hits exactly the failure §1.8 predicts.** Per-site table sets multiply the catalog
   (a 1,000-site network is tens of thousands of tables), which is *why* large networks
   shard. That is independent real-world confirmation of §1.8's catalog-pressure argument,
   arriving from the very example that seemed to argue the other way.

### 1.5 The target architecture, concretely

**Three tiers, one codebase.** The tenant resolver returns `(organization_id,
connection_target)`; everything downstream is identical.

| Tier | Data | Compute | Onboarding | Who |
|---|---|---|---|---|
| **Standard (pool)** | Shared Postgres, RLS | Shared fleet | Self-service, seconds | ~95% of customers |
| **Dedicated data (bridge)** | Own Postgres DB (or own schema) | Shared fleet | Semi-automated, hours | Compliance-sensitive mid-market |
| **Dedicated stack (silo)** | Own everything | Own VM/namespace | Manual, days | Enterprise, regulated, data residency |

**The tenant catalog.** A small **control-plane database, separate from tenant data**,
holding: `organization`, `tenant_placement` (which shard/DB/region), billing, entitlements
and usage. It must be readable *across* tenants — which is exactly what RLS is designed to
prevent — so it does not belong in the pooled tenant DB. It also has a different backup
and retention profile, and keeping revenue data out of the tenant DB means a tenant-side
compromise does not expose every customer's contract. (Microsoft's sharded-multitenant
reference architecture calls this the catalog database; it is a standard component, not an
invention.)

**Tenant resolution — subdomain, bound to the session.**
`acme.metorite.app` → workbench middleware resolves the slug → the **session** carries
the tenant claim → the gateway reads it from the authenticated identity.

> **Binding rule, extending `user_management_contract.md` rule 10** (*"never take the
> acting identity from a query parameter or request body"*): **never take the acting
> tenant from a header, query parameter or request body either.** The tenant is derived
> from the authenticated session or from a tenant-scoped API key (§3.2), and from nowhere
> else. An `X-Organization-Id` header that the client can set is a one-line
> cross-tenant read. (`multi_user_organization_research.md` §17.3 proposes exactly that
> header — **that proposal is rejected here.**)

**Multi-org users become supported.** `tenancy_and_visibility.md` §6.3 ruled them out
because `app_user.email` is globally unique. For SaaS this must change: partners,
consultants, and *your own support staff* need to be in more than one tenant. The standard
shape (Clerk, Auth0, Slack, Google Workspace all converge on it):

```
user_identity(id, email UNIQUE, name, …)        -- global, one row per human
org_membership(user_id, org_id, status, …)      -- the tenant-scoped membership
```
Today's `app_user` becomes `org_membership`; the email-keyed columns across the schema
(`app_grants.subject`, `apps.owner_email`, `gtd_items.user_id`, `meeting.owner_email`, …)
stay email-keyed and become correct automatically, because RLS already constrains the row
set to one tenant. **That is a second reason to do RLS first** — it makes the identity
split cheap instead of a re-key of 31 columns.

**What is NOT the tenant boundary.** Centers/departments are *inside* a tenant and are
already answered by `tenancy_and_visibility.md` §3 — `private → Center → org`, expressed
as `email | group:<slug> | org`. **That ladder is unchanged and still binding.** A tenant
is not a Center; a Center is never a deployment. Do not introduce a third scoping doctrine
(§3.2's standing rule).

### 1.6 Physical layout at multi-GB per tenant *(added 2026-08-08, owner question)*

Pooling is a **logical** isolation decision. It says nothing about physical layout, and at
several GB per customer the physical layout is a separate design problem that must be
answered whichever tenancy model wins. Answered here so "pooled" is not mistaken for "one
undifferentiated heap".

**Where the gigabytes actually are, measured:**

| Store | Shape | Weight |
|---|---|---|
| **pgvector embeddings** | `email_embeddings.embedding vector(1536)` (`73_…sql:29`), `whatsapp_embeddings vector(1536)` (`111_…sql:31`), `transcript_segment.embedding vector(1024)` (`95_…sql:79`), `entity.embedding vector(1024)` (`01_schema.sql:86`), plus Mem0's own | **Dominant term.** A 1536-dim float32 vector is ~6 KB; with an HNSW index the on-disk cost is roughly double. 100k embedded emails ≈ **1–1.5 GB for one tenant's email index alone** |
| **`agent_blob.content BYTEA`** | Blobs stored **inside Postgres** (`71_agent_blob_store.sql:30`), plus a versioned history table | Grows without bound; the natural first candidate to evict |
| **Email bodies + FTS** | `email_messages` + GIN `to_tsvector` indexes (`72_email_search_fts.sql:31`) | Large, but ordinary relational data |
| **Meeting media** | `meeting_media.artifact_path TEXT` → filesystem (`NOTES_MEDIA_DIR`, `95_…sql:56`) | ✅ **Already outside Postgres.** Good — keep it that way |

> **The reframe that matters:** for most tenants the "multiple GB" is **embeddings and
> blobs, not rows.** Move `agent_blob` to object storage keyed by `<org_id>/…` and the
> relational working set per tenant drops to the hundreds of MB. **Do that regardless of
> tenancy model** — a BYTEA column is the wrong home for file content in any topology.

**What actually constrains a single Postgres — and it is not total size.** Postgres runs
multi-TB routinely; 100 tenants × 5 GB is 500 GB, which is unremarkable. The three real
constraints are:

1. **Working set vs RAM.** One instance with a large `shared_buffers` serves the union of
   all tenants' hot pages better than N instances that each reserve their own and cannot
   lend. This is the single strongest efficiency argument for pooling and it is the one
   that container-per-tenant-on-one-VPS gets exactly backwards (§1.7).
2. **HNSW index memory.** The vector indexes are the memory-hungry part, and a pooled
   index means every tenant's search shares one structure. **This is the one place where
   per-tenant physical separation is worth considering on merit rather than on fear** —
   see the partitioning rule below.
3. **Restore time (RTO).** A multi-TB `pg_restore` is measured in hours. This is a real
   argument for keeping the pooled instance from growing unboundedly, and it is
   independent of isolation.

**Three rules that make pooled work at this data size:**

- **Partition the heavy tables by tenant.** Declarative partitioning on `organization_id`
  for `email_messages`, `email_embeddings`, `chat_message`, `audit_event` and the vector
  tables. Partition pruning means a query for tenant A never touches tenant B's pages —
  most of the locality and noisy-neighbour benefit of separate databases, inside one
  instance. **Use LIST partitions for the few largest tenants and a HASH/default partition
  for the long tail**; one partition per tenant across all tenants recreates the catalog
  pressure that sinks schema-per-tenant (§1.8).
- **Per-tenant logical backup is a required capability, not a tenancy-model side effect.**
  "Restore this one customer to yesterday" must be answerable, and in a pooled instance
  `pg_restore` cannot answer it. Build a per-tenant logical export/import job in Phase 1.
  Note this is the one genuine capability that database-per-tenant gives for free — and
  buying it costs one job, not N databases.
- **Keep an eviction path.** `tenant_placement` (§1.5) is what makes a large tenant
  movable: export, load into its own database, flip the row. **A tenancy model you cannot
  reverse is the actual risk**, and this is the cheapest insurance against picking wrong.

### 1.7 Rejected — one container per organization on the same VPS

Considered explicitly (owner question, 2026-08-08) because it is a different proposal from
one VPS per customer and deserves its own answer. **It is the worst of the three options**,
and this is not a close call:

1. **It fragments the one resource that matters.** N Postgres containers each hold their
   own `shared_buffers`, WAL, autovacuum workers and connection slots, and **cannot lend
   memory to each other**. Twenty containers on a 16 GB box get well under 1 GB of cache
   each; one pooled instance gives the *union* of hot working sets the whole cache. At
   multi-GB tenants with HNSW indexes (§1.6), this is decisive.
2. **It does not deliver the isolation it appears to.** Same kernel, same page cache
   pressure, same disk queue. A tenant running a heavy import still starves the others on
   IOPS. Container boundaries do not partition a shared spindle.
3. **It keeps the entire operational cost of database-per-tenant.** N migration runs, N
   backup jobs, N restore procedures, N monitoring targets, N connection pools — all
   unaffected by whether the containers share a VPS.
4. **It adds a failure mode neither other option has:** one box's resource exhaustion or
   reboot takes down *every* tenant, so the blast radius is silo's ops cost with pool's
   blast radius.

**The honest summary:** dedicated containers only buy something when they are on
**dedicated hardware** — which is the silo tier in §1.5, priced accordingly. On shared
hardware they are ceremony.

### 1.8 Rejected — schema-per-tenant *(the closest alternative; recorded properly)*

This is the strongest option **not** chosen, and it was under-weighted in the first draft.
It deserves a real entry rather than a dismissal.

**What is genuinely good about it:** one Postgres instance, so §1.7's memory-pooling
argument is preserved; `pg_dump -n <schema>` gives per-tenant backup for free; moving a
tenant out later is mechanical; and the isolation story is easier to explain to a
procurement team than an RLS policy.

**Why it still loses, on one decisive property:**

> **RLS fails closed. `search_path` fails open.**
>
> With RLS, an unset or wrong `app.tenant_id` yields **zero rows** — a loud, obvious,
> immediate failure that surfaces in the first test. With schema-per-tenant, a wrong
> `search_path` yields **a complete, valid-looking result set belonging to another
> tenant** — silently, with no error, indistinguishable from correct behaviour until a
> customer reports seeing someone else's data.

Both models concentrate the trust in one per-request binding. They differ entirely in what
happens when that binding is wrong, and for a system where §0.1 shows eight distinct
connection paths, the failure mode is the whole argument.

Two secondary costs: **catalog pressure** — 143 tables × N schemas, where the practical
ceiling is in the low hundreds to low thousands of tenants before `pg_dump`, autovacuum
and query planning degrade — and **migrations run N times** (better than N instances, but
still N, against 156 files today).

**Where it would win, stated so the call can be re-taken:** if the target is a few dozen
large customers rather than many small ones, catalog pressure never arrives, per-tenant
backup matters more than onboarding speed, and the procurement conversation is easier.

> **Tested against the owner's answer, 2026-08-08 — the condition is NOT met.**
> 10–50 users per customer is **mid-market, not enterprise**: it is Slack's, Notion's,
> HubSpot's, Freshworks' and Zoho's core segment, and every one of them is pooled. The
> flip condition needs *few customers*, and 10–50 seats implies the opposite — a customer
> base counted in dozens-to-hundreds, where catalog pressure (143 tables × N schemas) does
> arrive and onboarding speed does matter. **Pooled stands.** Re-take this only if the
> plan changes to topping out at ~20–30 accounts at high ACV, which is a different
> business, not a bigger version of this one.

### 1.8a Greenfield check — which arguments here are design, and which are retrofit

Owner question, 2026-08-08: *would this still be the recommendation if it were not
anchored to what Metorite already is?* Recorded because a reader two years from now
must be able to tell **"we chose this"** from **"we inherited this"**, and because the
audit produced two changes to Phase 1.

**Arguments that are pure design — they hold for any greenfield system with this customer
profile, and nothing in them depends on this tree:**

- Pooled over silo for 10–50-seat B2B customers (§1.4). The comparison set — Slack,
  Notion, HubSpot, Freshworks, Zoho — did not inherit anything from us.
- RLS over application filtering, on **fails-closed vs fails-open** (§0.1, §1.8). That is
  a property of the mechanisms.
- Container-per-org on shared hardware being the worst option (§1.7) — resource arithmetic.
- Entitlements ≠ permissions (§2.1), credits not tokens (§3.2), assigned seats not active
  users (§2.2). Three business principles with no code dependency.

**Arguments that are retrofit reasoning, and must not be mistaken for design:**

- *"The seam already exists"* — `get_db()`, `EffectiveAccess.intersect()`, `_emit_usage()`.
  These make the migration cheap. **Greenfield they carry zero weight**, because greenfield
  you simply write the tenant column into the first migration and the whole question
  evaporates.
- **The 4–5 week Phase 1 estimate is entirely a retrofit number.** Greenfield, multi-tenancy
  is roughly three days of schema discipline. ⚠️ **This is the largest single distortion in
  the document:** the pooled-vs-silo debate is expensive *here* only because 143 tables were
  built without a tenant column. It is not evidence that the decision is hard in general.
- **§3.1's "don't add a proxy" is ~60% retrofit.** Greenfield, buying an AI gateway
  (LiteLLM, Portkey, Helicone) versus building metering into the app is close to a coin
  flip. The one argument that survives greenfield is that a separate proxy must **re-resolve
  the tenant**, creating a second boundary to get right, and that it lacks the app context
  (which module, which agent) that per-module margin analysis needs. Buy it if the routing
  and dashboards are worth more than that. **The conclusion is unchanged; the confidence
  should be lower than §3.1 implies.**

**Two things a greenfield design would include that this document did not — both cheap
now, both expensive later, and both therefore added to Phase 1:**

1. **Treat `organization_id` as a distribution key, not just a filter column.** Put it in
   every primary key and every index prefix, and colocate related tables on it. Costs
   nothing today and is the precondition for sharding — Citus and every distributed
   Postgres take tenant-id colocation as their flagship multi-tenant pattern. Retrofitting
   a distribution key after the fact means rewriting every primary key. **Adopt the
   discipline; do not adopt Citus, which is unnecessary complexity at this scale.**
2. **Evaluate an external identity provider for organizations, memberships and SSO**
   (WorkOS, Clerk, Keycloak) rather than growing `app_user` into it. Enterprise B2B
   eventually demands SAML and SCIM, and building those is a tar pit. This is a genuine
   *"greenfield I would not build this myself"* — but note the honest counterweight: the
   shipped RBAC (`org_access_control.md`) is good, and the migration cost may already
   exceed the benefit. **Decide deliberately rather than by default.**

**The argument this document under-weighted, and it is independent of the codebase:**
Metorite's agents execute model-generated tool calls over content ingested from
untrusted sources (email, WhatsApp). That is a **materially higher risk profile than
ordinary SaaS**, and it is a real point in silo's favour that §1.1 waved past. It does not
flip the decision — an injected agent already holds its own tenant's data, and RLS blocks
the incremental "read *other* tenants" step at the server — but it raises the bar on two
things that are now non-negotiable rather than merely advisable:

> **No agent ever gets a raw-SQL tool, and no agent-reachable code path can set
> `app.tenant_id`.** The agent must inherit a session already bound by the request or job
> and must never open a connection of its own. If either of those is violated, pooled
> tenancy is not defensible and §1 should be re-taken.

**The one go-to-market risk that no architecture answers:** if two customers are
competitors — plausible when selling manufacturing software from a manufacturer — *"is my
data in the same database as theirs?"* is a procurement question, and *"no, separate
database"* is a far easier answer than explaining a row-level policy. That is a **sales**
argument for the dedicated-data tier (§1.5), not a technical one, and it is the reason
`tenant_placement` and the eviction path (§1.6) earn their keep on day one.

### 1.9 The surfaces RLS does NOT cover — decide each, or they leak

Postgres RLS protects Postgres. These do not run on Postgres:

| Surface | Today | Required |
|---|---|---|
| **Redis** | `cc:*` keys carry no tenant (`cc:activity`, `cc:room`, `cc:cost`, `cc:presence`, …) | Prefix every key `cc:<org_id>:…`; separate consumer groups per tenant on the Streams bus |
| **Background jobs** | Ingestion scheduler, reconciler, orchestrator runs — no request, so no session tenant | Every job carries an explicit `organization_id` and binds it before `get_db()`. **This is where pooled systems actually leak.** A job that forgets is unbounded, not one row wide. |
| **Neo4j / Graphiti** | Single Community instance, one database | Tenant property + mandatory filter, or (better) accept that Neo4j Community allows one DB and move the graph behind a tenant-aware service |
| **Agent workspaces / blobs** | Filesystem paths, `agent_blob.instance ∈ ''\|u:<email>\|t:<team>` | Tenant becomes the outermost path/prefix segment: `<org_id>/<agent>/…`; object storage (S3/MinIO) rather than VM disk |
| **Mem0 memory scopes** | `<email>` · `prefs:` · `room:` · `agent:` · `org:global` | `org:global` is currently *deployment*-global. Must become tenant-scoped. Coordinate with WS-10 S1 — do not add a sixth scope shape independently. |
| **Langfuse / observability** | One project | Tenant tag on every trace, or a project per tenant |
| **Self-mutation** | Native-MAF agents open PRs against **this monorepo** (root `AGENTS.md` constraint 3) | **Hard-blocked for third-party tenants.** See §6.2. |

---

## 2. DECISION — modules are ENTITLEMENTS, and entitlements are not permissions

> ### `access = entitled(org, module) AND permitted(user, feature)`
> Two layers, two owners, evaluated in that order. Never conflated.

### 2.1 Why the distinction is load-bearing

Metorite already has a permission layer: `feature:whatsapp`, roles, per-user
overrides with deny-wins-by-specificity (`permissions.py`). That answers **"is this user
allowed?"** and its owner is the *customer's* admin.

Entitlement answers **"did this company buy it?"** and its owner is **you**.

Collapse them and two things break immediately: a customer's admin can grant themselves a
module they never paid for (they control the role table), and a downgrade at renewal has
to rewrite everyone's roles — losing the customer's own access configuration in the
process. Every mature per-module product (Microsoft 365 licences, Zoho One, Atlassian,
Salesforce feature licences) keeps these separate for exactly these two reasons.

### 2.2 Schema — in the control-plane DB, not the tenant DB

```sql
-- What you sell. A SKU, product-facing.
module_catalog(
    slug            TEXT PRIMARY KEY,     -- 'crm', 'email', 'whatsapp', 'finance'
    display_name    TEXT NOT NULL,
    feature_slugs   TEXT[] NOT NULL,      -- which feature_catalog rows it unlocks
    requires        TEXT[] NOT NULL DEFAULT '{}',   -- e.g. finance requires core
    is_core         BOOLEAN NOT NULL DEFAULT false, -- always on, never sold separately
    list_price_per_seat_month NUMERIC(12,2),
    currency        TEXT NOT NULL DEFAULT 'INR'
);

-- What a company currently owns. The CACHE OF BILLING TRUTH, written by webhooks.
org_module_entitlement(
    organization_id UUID NOT NULL,
    module_slug     TEXT NOT NULL REFERENCES module_catalog(slug),
    state           TEXT NOT NULL CHECK (state IN
                      ('trial','active','past_due','suspended','cancelled')),
    seats_purchased INT  NOT NULL DEFAULT 0,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until TIMESTAMPTZ,
    source          TEXT NOT NULL,         -- 'stripe' | 'razorpay' | 'manual'
    PRIMARY KEY (organization_id, module_slug)
);

-- Which named user holds a seat. This is what "per module per user" means.
user_module_seat(
    organization_id UUID NOT NULL,
    user_id         UUID NOT NULL,
    module_slug     TEXT NOT NULL,
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by     TEXT,
    PRIMARY KEY (organization_id, user_id, module_slug)
);
```

**Why an explicit seat assignment rather than counting active users.** This is the
Microsoft 365 model and it is the right one for a per-user-per-module price:

- The invoice is **explainable and predictable** — "you assigned 12 CRM seats" beats "13
  people opened CRM in June, one of them once."
- **Unassigned seats are visible**, to you and to the customer. "You are paying for 3
  unassigned CRM seats" is a retention conversation; "you have 4 users without WhatsApp"
  is an upsell. Active-user billing surfaces neither.
- It is **auditable**. Assignment is an act with an actor and a timestamp; usage is a
  side effect.

**Never bill on active users.** Customers cannot forecast it, so they distrust it, and
every quiet month becomes a support ticket. Put predictability in seats and variability in
metered AI (§3) — that is the hybrid model the market has settled on.

### 2.3 Enforcement — one seam, zero route edits

`EffectiveAccess.intersect()` **already exists** (`permissions.py:366-374`) and already
does exactly this job for agents ("an agent acts on behalf of a member and must never
exceed them"). Entitlements are the same operation with a different mask:

```python
effective = role_and_override_access.intersect(entitlement_mask(org_id))
```

Compute `entitlement_mask` once per request from `org_module_entitlement` (cached in
Redis, invalidated by the billing webhook — never a Stripe call on the request path), and
**every existing `require_permission("feature:crm")` call site and the entire nav gating
inherit entitlement enforcement with no route changes.** Same trick as §1.3: find the one
seam.

**Distinguish the two failures on the wire:**
- **403 Forbidden** — you are signed in, your org owns this module, your admin has not
  granted it to you. *Action: ask your admin.*
- **402 Payment Required** — your org does not own this module. *Action: upgrade.*

`/auth/me` returns **both** `features` (what you may use) and `modules` (what the org
owns, with state and trial expiry) so the frontend can tell these apart.

### 2.4 "Comprehensive even with a fraction of the modules" — the degradation contract

This is the owner's real requirement and it is a **design rule**, not a feature. A locked
module must be **absent-but-legible**, never broken:

1. **A locked module shows an upsell, not a 404.** `<ModuleGate module="crm"
   fallback={<Upsell/>}>` in the workbench. A module the customer cannot see, they cannot
   buy. This is a revenue lever, not a courtesy — it is how Zoho One and Atlassian
   cross-sell.
2. **Cross-module surfaces degrade, never error.** The Company Center rolls up every
   Center; with Finance unowned, the Finance tile renders empty-with-CTA. A CRM deal
   linked to an email thread renders as plain text when Email is unowned.
3. **A `core` module is always on** — auth, chat, admin, dashboard shell, memory. The
   product is never empty, and there is always a surface on which to sell the rest.
4. **Modules declare dependencies** (`module_catalog.requires`). Buying Finance without
   Core is rejected at checkout, not discovered at runtime.
5. **Gate the non-HTTP surfaces too — this is the one people forget.** An unowned module
   must not: register its agents, run its ingestion schedulers, consume its Redis streams,
   or fire its workflow triggers. Otherwise the module is dark in the UI while its email
   sync still polls every five minutes and **still costs you provider spend for a customer
   who is not paying for it.**

**Mapping today's features to modules — THE INTERNAL ATOM LEDGER (D19 prices,
demoted from "SKU list" by D23, 2026-08-10).** Modules are **billing atoms that
Center packages compose from — customers never buy them individually**; the
customer-facing list of record is **§2.4b**. The ₹ column is each atom's internal
cost weight (used to price packages), not a purchasable price. Reachability:
`email`/`whatsapp`/`meetings` only via the **Personal Center ₹600**; `crm` only
via **Sales ₹600**; `finance` only via **Finance ₹600**; `projects` (plus
Knowledge Base and Dashboards when built) ride inside **every** Center package as
slices, never alone. Only `builder` (₹500) and `workflows` (₹300) are directly
buyable, as org-wide add-ons. (`FEATURES` at
`packages/acb_auth/acb_auth/permissions.py:73`, `feature_catalog` seeded by
migrations 130 and 140):

| Module (atom) | ₹ weight | Features it unlocks | Note |
|---|---|---|---|
| `core` | **600** (every member) | chat, memory, dashboard, artifacts, settings, **tasks (personal lens)**, **calendar**, **people directory** (`people`, `center.people`), **approvals**, **observability** | Always on; every org member consumes a Core seat (D19.3). ⚠️ no `calendar` feature slug exists yet — mint one with MT-2 |
| `email` | 300 | email + `center.marketing` mail surfaces | Heaviest ingestion cost; embeddings absorbed in price (D19.2) |
| `whatsapp` | 300 | whatsapp | Per-number provider cost **absorbed in price** (D19.2) |
| `crm` | 300 | crm, `center.sales` | |
| `projects` | 300 | projects, `center.*` project surfaces | **One task store with Core** (D-PM-6): this SKU gates portfolios, project boards, dependencies, ClickUp import/sync, org-wide views — personal task management stays Core |
| `finance` | 300 | `center.finance` | Not yet built — the catalog row can exist before the module does |
| `meetings` *(was `notes`)* | 300 | notes, meeting bot | Per-minute STT **metered as credits** (D19.2). Customer-facing name: **Meetings** |
| `workflows` *(was `automation`)* | 300 | workflows | Customer-facing name: **Workflows**. Approvals + observability moved to `core` (D19.1) |
| `builder` | **500** | build.apps, build.agents | Highest-risk module; gate hardest; priced above the standard add-on for that reason |

*(The former standalone `people` module row folded into `core` — D19.1.)*

### 2.4b Center packages — the pricing shape of record (D23, 2026-08-10; supersedes 2.4a's Team/Business tiers)

**The sales object is the Center; the module stays the billing atom.** Full
statement in `work_plan.md` §3 D23; the schema/enforcement consequences here:

- **Layers:** Core ₹600/member mandatory (basic AI chat, tasks, calendar,
  directory, personal dashboard, approvals, admin plane) · **Center packages**
  per user per Center — app-bearing ₹600 (Personal = Email+WhatsApp+Meetings,
  optional per user; Sales = CRM incl. CPQ; Marketing; Finance; Support),
  slices-only ₹300 (R&D, Operations, People pre-HR) — each package bundling the
  Center's modules **plus its slice of Projects, Knowledge Base and Dashboards**
  · **org-wide add-ons** Builder ₹500, Workflows ₹300 · **all-Centers seat
  ₹1,800/user (D24.3)** = every Center package, no add-ons — the multi-hat
  relief · Company Center free for
  leadership · **Complete ₹3,000/user (D24.4; was ₹3,600)** = Core + all
  Centers + both add-ons, keeping rule 5 below
  (all-GA, price-protected, wildcard). **D24 customer-framing calls:** the ₹600
  Core headline stays; slices-only Centers pitch as "team workspace"; role
  presets in SC-2. Ladder of record: 600 · 1,200 · 1,800 · 2,400 · 3,000.
- **Schema (MT-2):** `center_package(center_slug TEXT PRIMARY KEY, module_slugs
  TEXT[], price_per_seat_month NUMERIC, currency TEXT DEFAULT 'INR')` beside
  `plan_catalog`; `user_module_seat.source` gains `'center'`. **Assigning a
  Center package is ONE act** that creates the billing seat, the `org_group`
  membership, the module entitlements and the D12 slice grants — and
  unassignment reverses all four. The entitlement seam (§2.3), degradation
  contract (§2.4), 402-vs-403, hard cap and proration (§4.2) are all unchanged;
  a user's module set is the **union** across their packages (never billed
  twice for a module; billed per Center because each package is a new team-data
  slice).
- The tier machinery below (2.4a) survives only for `complete`; its Team and
  Business rows are **retired, never seeded**.

### 2.4a Plan tiers — ⚠️ Team/Business SUPERSEDED by 2.4b (D23); Complete survives recast (D20, 2026-08-09)

*(D23 body correction, 2026-08-10: the paragraph and table this section shipped
with described Team/Business/Complete-₹2,400 over a purchasable a-la-carte list —
**both retired**. Nothing on §2.4's atom table is customer-purchasable; the
customer objects are §2.4b's Center packages, the two org-wide add-ons, and
Complete at ₹3,600. The original table is kept below, struck, as the decision
record only — never seed `plan_catalog` from it.)*

| ~~Tier~~ | ~~₹/user/mo~~ | ~~Modules~~ | Status under D23 |
|---|---|---|---|
| Core | 600 | the base | **Survives** — the mandatory member layer (D19.3) |
| ~~Team~~ | ~~1,200~~ | ~~Core + Projects + Meetings + Workflows~~ | **RETIRED, never seeded** |
| ~~Business~~ | ~~1,800~~ | ~~Team + CRM + Email + WhatsApp~~ | **RETIRED, never seeded** |
| Complete | ~~2,400~~ → ~~3,600~~ → **3,000 (D24.4)** | everything (wildcard) | **Survives recast** — all Centers + both add-ons + Core (§2.4b) |

Rules (as amended by D23):

1. **Schema (MT-2):** `plan_catalog(slug TEXT PRIMARY KEY, module_slugs TEXT[],
   price_per_seat_month NUMERIC, currency TEXT DEFAULT 'INR')` beside
   `module_catalog` — under D23 it holds **only the `complete` wildcard row** —
   plus `center_package` (§2.4b); `user_module_seat` gains
   `source ∈ ('center','plan','alacarte')` so a package or plan change can
   recompute exactly the seats it granted and unbundling stays computable. No
   org-level plan column — mixed levels inside one org are legal (three Complete
   power users, twenty Core members).
2. **Pricing floor:** Complete is always ≤ the sum of all Center packages plus
   add-ons, and upgrading a user from stacked Center packages to Complete must
   never cost more — the console surfaces that swap as a savings prompt
   (upsell lever).
3. **Included monthly credits per package/bundle: deliberately NOT decided.**
   Launch default is none — credits sell separately (§3.2). Bundling credits is
   an owner knob left open; do not invent values for it.
4. The ladder of record is D23.5's worked seats as amended by D24 (600 · 1,200 ·
   1,800 · 2,400 · 3,000) — driven by package count, not tier steps.
5. **Complete is defined contractually (owner, 2026-08-09 review round):
   Complete = every generally-available module, always.** New modules (the D21
   roster as they ship) appear for Complete subscribers automatically; the list
   price may rise at a module launch, but **existing subscribers keep their price
   for their current contract term** (price protection). A frozen-2026
   composition and float-for-everyone repricing were both rejected. Put this
   sentence in the customer contract template verbatim, and encode it in MT-2 as
   `plan_catalog.module_slugs = ['*']` for the `complete` row (a literal
   wildcard, expanded at entitlement time) rather than a hand-maintained list
   that silently goes stale at each module launch.

**Adding a module must stay a data change.** A new atom is a `module_catalog` row plus a
`feature_catalog` row plus a `FEATURES` tuple entry; a new **customer-visible**
offering is additionally a `center_package` row (or a module folded into an
existing package) — never a code path per customer. Note
today's trap, documented in the `FEATURES` docstring at `permissions.py:65-72`: a slug
seeded in SQL but missing from
the `FEATURES` tuple is **invisible even to an owner holding `*`**. Keep the pinning test.

---

## 3. DECISION — resell AI through the existing `/v1` choke point, priced in CREDITS

> ⚠️ **§3.1 IS REVERSED — D32.1 (owner, 2026-08-12).** Owning spec for the
> replacement: **`specs/customer_console.md` (WS-31)**. AI is now metered and
> routed by a **central Control Plane service**; Metorite's `/v1` becomes a
> forwarder. **Do not build §3.1's "keep it all in the gateway" shape.**
>
> **What §3.1 got right and still binds:** Metorite must not grow a *second
> tenancy boundary*. It does not — the Router sits OUTSIDE CC as a supplier, never
> sees a CC session and never resolves a tenant from request input.
>
> **What it did not weigh:** **§5.1's silo rollout.** §3.1 reasoned about one pooled
> box, where the tenant is already resolved in-process. With one deployment per
> customer, "keep metering in your own code" means the rate card, the margin and the
> credit balance sit on the **customer's own box**, N times over — and a provider
> swap becomes N deploys, which is the drift §5.1 condition 3 warns about.
>
> **What survives unchanged and is NOT re-litigated by WS-31:** everything below
> about *what* is metered and *how it is priced* — §3.2's four additions (per-org
> virtual keys, pre-flight Redis gate, post-flight metering, the rate card), the
> `usage_event`/`request_id` idempotency contract, D19.2's credit unit, §3.3's
> soft-block and overdraft, and §3.4's BYOK-as-a-tier. Only the **placement** of the
> mechanism moved. Read §3.2–§3.5 as still-binding design; read §3.1 as history.

> ### ~~`Do not reintroduce a separate proxy. The gateway's /v1 already IS the proxy.`~~ *(reversed — D32.1)*
> ### `Sell internal credits, not provider tokens.` *(stands)*

### 3.1 Why not a separate LLM proxy process — ⚠️ **REVERSED by D32.1; kept as the record of what the reversal had to answer**

The obvious move is to put LiteLLM Proxy (or similar) in front of everything and use its
virtual keys, team budgets and spend tracking — which are genuinely good features.
**Don't**, and the reason is in this repo: the proxy process was already removed
(`infra/litellm/config.yaml`: *"The gateway uses the litellm Python SDK directly (no proxy
process)"*), and `/v1/chat/completions` in `v1_compat.py` is now the documented choke point
*"every agent runtime POSTs through"* — already authenticated, already computing
per-call cost, already handling the streaming case.

Adding a proxy back would create a **second** key store, a **second** database, and a
**second** place where tenant identity must be enforced correctly. You would be buying 80%
of something you have already built, at the cost of a second tenancy boundary to get
right. Keep metering in your own code, where the tenant is already resolved.

*(If you would rather buy than build, LiteLLM's virtual-keys/team-budget model is the
right thing to buy and the design below maps onto it one-for-one — key → org, team budget
→ credit balance. Decide once; do not run both.)*

### 3.2 The four additions

**(1) Per-organization virtual keys — the load-bearing change.**
Today `require_llm_api_auth` accepts a single box-wide token (`deps.py:448-472`), so there
is **no per-customer attribution at the LLM layer at all**. Replace with:

```sql
llm_api_key(
    id UUID PK, organization_id UUID NOT NULL, prefix TEXT NOT NULL,  -- 'cc_live_a8f3…'
    key_hash TEXT NOT NULL, label TEXT, scopes TEXT[], 
    created_by TEXT, revoked_at TIMESTAMPTZ
);
```
Match on `prefix`, verify the hash. **The key resolves the tenant**, and everything
downstream — budget gate, metering, model policy, rate limits — hangs off that one
resolution. Nothing else in §3 works without it.

**(2) Pre-flight budget gate — in Redis, not Postgres.**
Before the provider call, check the org's balance against a Redis counter and reject with
**402** if exhausted. This is on the hot path of every token; Postgres is the ledger,
Redis is the gate. Include a **per-run spend circuit breaker**: an agent in a tool loop can
burn a large amount in minutes, and this codebase has retry loops and a 32k default output
ceiling (`v1_compat.py:_DEFAULT_MAX_OUTPUT_TOKENS`).

**(3) Post-flight metering — `_emit_usage` already has the numbers.**
`client.py:552-612` already computes prompt/completion/cached tokens and USD cost, and
`v1_compat.py:563-573` already rebuilds usage from streamed chunks. Add: write a
`usage_event` row and decrement the Redis counter.

```sql
usage_event(
    id UUID PK, organization_id UUID NOT NULL, user_email TEXT, agent TEXT,
    module_slug TEXT,                       -- which module drove the spend → per-module margin
    model TEXT, tier TEXT,
    prompt_tokens INT, completion_tokens INT, cached_tokens INT,
    provider_cost_usd NUMERIC(14,8),        -- what it cost YOU
    billed_credits NUMERIC(14,4),           -- what you charge THEM
    request_id TEXT UNIQUE NOT NULL,        -- ← idempotency; retries must not double-bill
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
`request_id UNIQUE` is not decoration. Retries, reconnects and the streaming rebuild path
all create double-write opportunities, and a customer billed twice for one call is a
credibility event.

**(4) A rate card — and sell credits, not tokens.**

```sql
model_rate_card(model TEXT, input_credits_per_1k NUMERIC, output_credits_per_1k NUMERIC,
                cached_input_credits_per_1k NUMERIC, effective_from TIMESTAMPTZ,
                PRIMARY KEY (model, effective_from));
```

**This is the most important commercial decision in §3.** Do not bill customers in raw
provider tokens:

- Tokens are **provider-specific and model-specific**. Bill in tokens and you have
  promised a price on DeepSeek that you cannot honour on Anthropic.
- **Providers reprice under you.** With a rate card, your margin is a table edit; without
  one, it is a code change and a customer conversation.
- **You can price cache hits lower.** `prompt_cache.py` already ships. "Cached context is
  billed at 25%" is a real, differentiated selling point — and it costs you almost nothing
  because it reflects your actual cost.
- Customers **cannot reason about tokens** but can reason about "10,000 credits ≈ a month
  of normal email triage."

This is what OpenRouter, Cursor and Vercel's AI Gateway all do, and for these reasons.

**The credit unit, defined (owner, 2026-08-09, D19.2 — this resolves the "flat ₹10
vs cost × 2" ambiguity D18 carried):**

- A **credit is the ₹10 purchase and display unit**. It is what customers buy and
  what the balance shows. "AI action" is marketing shorthand for the credit — it is
  **not** a flat per-call or per-gesture price.
- Each model call draws credits **fractionally** at **provider cost × 2** through
  the rate card: a cheap classification call might burn 0.2 credits, a long agent
  run 15. The ~50% gross margin is a property of the multiplier, not of any flat
  price.
- The rate card is **denominated natively in INR** — hand-maintained per-model
  prices, updated when providers reprice. There is **no FX machinery**;
  `usage_event.provider_cost_usd` stays as a bookkeeping column, but nothing on the
  billing path converts currency at runtime.
- **Scope of metering:** LLM calls and **per-minute STT** (the Meetings module)
  draw from the same credit balance via the same rate card. **Embeddings and
  WhatsApp per-number provider fees are NOT metered** — they are absorbed into the
  Center package prices (D23; the Personal Center ₹600 is where email/WhatsApp
  live — roughly flat per-seat costs; metering background ingest the customer
  never asked for reads badly).

### 3.3 Failure semantics — decide now, not at 2 a.m.

**Soft-block with a grace overdraft.** At zero balance, LLM calls return a specific 402
that the UI renders as "out of credits — top up", the **non-AI parts of every module keep
working**, and a ~10% overdraft prevents a hard stop mid-sentence. Auto-top-up is the
default for paid plans; alert at 80%.

A hard cut-off mid-workflow generates a support ticket and a refund request that together
cost more than the overdraft. This is a business decision encoded in a config value —
write it down.

### 3.4 BYOK is a tier, not an exception

Some customers will insist on their own Anthropic/OpenAI key (data policy, existing
committed spend). Support it: `provider_keys` becomes `(organization_id, provider)` (§6),
and a BYOK org is **metered but not charged for tokens** — you charge the platform fee
only. This also caps *your* financial exposure on your largest accounts, which is why
nearly everyone in this space offers both.

### 3.5 "Will an LLM be able to do that?"

**No LLM is involved, and none should be.** Metering is deterministic bookkeeping: count
tokens, multiply by a rate, decrement a balance, write a row. The only judgement call is
the rate card, and that is a business decision made once by a human. Never let a model
decide what to bill.

---

## 4. DECISION — billing architecture

> ### `Your database is the source of truth for entitlements and usage.`
> ### `The payment processor is the source of truth for money.`
> Never call the processor on the request path. Never recompute an invoice it has issued.

### 4.1 Components

**(a) The Operator Console — build this early.** A separate surface (`/operator`) that
**only your staff** can reach, never bundled into the tenant UI, showing per company:
plan and MRR · seats purchased vs **assigned** per module · credit balance and burn rate ·
last invoice status · trial expiry · activity (last login, 7/30-day actives).

This answers the owner's question directly — *"depending on what modules, how many users
are using in that particular company"* — and it is simultaneously your revenue instrument,
your churn radar and your support tool. Unassigned seats and unowned-but-viewed modules
are your upsell queue.

**(b) Billing tables** (control-plane DB, alongside §2's):
```sql
org_subscription(organization_id PK, provider, provider_customer_id,
                 provider_subscription_id, plan, status, trial_ends_at,
                 current_period_start, current_period_end);

credit_ledger(id, organization_id, delta NUMERIC, reason, ref, balance_after,
              created_at);        -- APPEND-ONLY. Balance is SUM(delta), cached in Redis.
                                  -- Never UPDATE a balance column: you lose the audit
                                  -- trail exactly when a customer disputes a charge.

usage_rollup(organization_id, period DATE, dimension, quantity,
             PRIMARY KEY (organization_id, period, dimension));
             -- nightly from usage_event; raw kept ~90d, rollups forever

invoice(id, organization_id, provider, provider_invoice_id, period,
        amount, currency, status, hosted_url);   -- mirror, so the customer sees
                                                 -- invoices without a provider round-trip
```

**(c) The reconciliation loop — the part that always bites.** Webhooks get lost, cards
fail, admins downgrade mid-cycle. A nightly job compares your `org_module_entitlement` and
seat counts against the processor's subscription items and **alerts on drift**. This repo
already has a `reconciler` service — same pattern, new subject.

**(d) Lifecycle state machine**, written once, read by every module via
`entitlement.state`:
```
trial → active → past_due (grace: warnings, still working)
      → suspended (login works · modules locked · DATA RETAINED)
      → cancelled (export window) → deleted
```
> **Never delete customer data on non-payment without an export window.** It is a trust
> matter, a DPDP/GDPR matter, and the difference between a churned customer who might come
> back and one who tells people not to buy from you.

### 4.2 How the seat charge is actually computed

**Billing line items are per CENTER PACKAGE (D23), not per module**: each package
(and each org-wide add-on, and Complete) is its own processor subscription item,
`quantity = COUNT(*)` of seats holding it, pushed on assignment/unassignment with
proration; module counts remain the internal expansion (union semantics — a
shared module is never billed twice). **The three rules are decided (owner,
2026-08-09, D19.3):**

1. **Hard cap.** Assigning beyond `seats_purchased` is **blocked** with a buy-more
   prompt — a purchase is always an explicit act, never a side effect of assignment.
   `seats_purchased` is therefore a real cap, not advisory.
2. **Every member consumes a Core seat.** Membership IS the Core billing event:
   inviting a member bills a Core seat, removing them frees it, and there is no such
   thing as a zero-seat member. The invoice reads (D23): members × ₹600 Core +
   Σ(Center package seats × ₹600/₹300) + add-on seats (Builder ₹500, Workflows
   ₹300) + all-Centers seats × ₹1,800 + Complete seats × ₹3,000 (D24).
3. **Mid-cycle changes use the processor's standard prorated behaviour** (a day-20
   assignment bills ~⅓ of the month; unassignment credits the remainder). "Peak
   assigned seats in the period" was considered and rejected as punitive for brief
   experiments. State the proration rule in the customer contract verbatim —
   ambiguity here is the single most common source of B2B billing disputes.

### 4.3 Payment processor — and the India question

Stripe supports this shape natively: **Billing Meters + meter events** for usage,
subscription items for seats. Note the current API reality: the legacy usage-records API
was removed in API version `2025-03-31.basil`, so **every metered price now requires a
backing Meter**, and the v2 Meter Event Stream handles high-volume ingestion (~10k
events/sec) if you ever meter per-call rather than per-rollup.

Two models, and the recommendation is to run both:

| Model | Mechanism | Use for |
|---|---|---|
| **Prepaid credits** *(default)* | Customer buys a credit pack; you decrement the ledger. Processor sells a one-off/top-up product. | **Recommended default.** No bill shock, no collections risk, best fit for SMB and for India. |
| **Postpaid metered** | Report meter events; processor invoices at cycle end with graduated tiers. | Enterprise on invoice terms. |

> ⚠️ **India-specific, and it matters because you are billing from India.** For domestic
> INR recurring collection, RBI's e-mandate rules make recurring card auto-debit
> genuinely painful above the additional-factor threshold, and Stripe's India coverage is
> narrower than its international coverage. **Razorpay/Cashfree** handle UPI Autopay and
> e-NACH properly. **Recommendation: a `payment_provider` seam** — Stripe for
> international, Razorpay for India — with **both writing the same
> `org_subscription` / `org_module_entitlement` / `credit_ledger` tables.**
>
> **Do not let the processor's data model become your data model.** Entitlements are yours;
> the processor is a device for collecting money. That indirection is also what makes
> prepaid credits and manual/enterprise invoicing work without a second code path.

**Accounting.** Invoices, GST/VAT/sales tax and dunning belong to the processor (Stripe
Tax or the Razorpay equivalent), not to your app. Export to books (Zoho Books is the
natural choice — you already integrate Zoho CRM) **nightly, not per transaction**.
Deferred-revenue recognition on annual prepay lives in the accounting system, never in
Metorite.

---

## 5. Phasing — what to build, in order

Each phase is independently shippable and each one is sellable before the next exists.

| Phase | Work | Est. | Gate |
|---|---|---|---|
| **0 — Blockers** | §6: per-run credential scoping ✅ · per-org provider keys ✅ · self-mutation containment ✅ · **no-raw-SQL agent tools ✅ (MT-0c-1)**. ⚠️ **MT-0c-2 (the container tier) is deliberately NOT here** — D16 moves it to a precondition of the §5.1 pooled cutover, because with one tenant per box an escaped agent reaches only its own data | 1–2 wk · **DONE** | **Nothing ships to a second customer before this** |
| **1 — Tenancy** | org_id + FORCE RLS on all tables (generated), **org_id in every PK and index prefix** (§1.8a), tenant binding at **all eight connection paths** (§0.1), `acb_app` role, `create_engine` + `psycopg.connect` ratchets, Mem0 decision, **no raw-SQL tool for agents** (§1.8a), Redis prefixing, subdomain resolution, identity/membership split (+ the external-IdP call, §1.8a), per-tenant logical backup job, partitioning for the heavy tables, build-failing coverage test | 4–5 wk | The big one. §0.1, §1.3, §1.6, §1.8a, §1.9 |
| **2 — Entitlements** | module catalog, entitlement + seat tables, `intersect()` mask, 402 vs 403, `ModuleGate` + upsell, non-HTTP gating, **per-org feature flags + release channel** (§1.4b — same lookup shape as the entitlement mask) | 2–3 wk | **Sell here.** Invoice by hand while proving the model. |
| **3 — AI credits** | per-org virtual keys, Redis budget gate, rate card, `usage_event`, credit ledger, top-up | 2–3 wk | §3 |
| **4 — Billing automation** | Stripe + Razorpay seam, webhooks → entitlements, dunning, Operator Console, reconciler | 3–4 wk | §4 |
| **5 — Tiers & compliance** | Dedicated-DB tier, **per-tenant envelope encryption for sensitive columns** (§1.1a — pull into Phase 1 if those columns are being touched anyway), residency, SOC 2 groundwork, DPA/DPDP | ongoing | Sell before you build this |

**Do not reorder 1 before 0, or 3 before 1** — metering without tenant resolution meters
nothing, and entitlements over unisolated data are a UI convention rather than a control.

**You can sell during Phase 2.** Manual invoicing for the first ten customers is normal
and is how you learn whether the module split and the price points are right, before
automating them.

### 5.1 Start siloed, cut over at the crossover *(owner input 2026-08-08: 10–50 seats)*

The phases above describe the destination. They do **not** require waiting 4–5 weeks
before the first customer, and at 10–50 seats per company they should not.

> **Customers 1–5: run them as silos. Build Phase 1 in parallel. Cut over at 8–12.**

**Why this is right rather than a compromise.** §1.4's crossover is ~8–12 customers, so
below it silo is genuinely cheaper *and* reaches revenue sooner. Five hand-run
deployments teach you which modules customers actually buy and what they pay — the two
inputs §8 says are still open — and that learning is worth more than a month of
architecture built against guesses.

**The four conditions that make it a bridge rather than a trap.** Without these it is
not a staged rollout, it is silo-by-default arrived at by drift:

1. **Phase 0 is non-negotiable even for silos.** Process-global credentials (§6.1) leak
   *between concurrent runs* — the second tenant needn't be on the same database for
   that to matter, only in the same process. Self-mutation containment (§6.2) likewise.
2. **Every silo runs the pooled schema**, with `organization_id` populated and RLS
   enabled from day one, even though the database holds one tenant. A silo is then a
   pooled deployment with N=1, and cutover is a data move rather than a migration.
   **Skipping this is what turns the bridge into a rewrite.**
3. **One deploy pipeline, parameterised by target** — never a per-customer script. The
   moment two boxes deploy differently, §1.4's version-skew defect has arrived.
4. **A written cutover trigger**, checked monthly: customer count ≥ 8, *or* deploy
   overhead exceeding roughly a day a month, *or* the first version-skew incident —
   whichever comes first. **A bridge with no trigger is a destination.**
   **ADOPTED 2026-08-09** (recorded on the board — `work_plan.md` §2 WS-29 row — per
   §11.2 item 4; the monthly check is the owner's).

---

## 6. Blockers — fix before a second tenant exists

These are not features. Each is a live cross-tenant defect the moment a second company's
data is on the box, and each is cheap now and expensive later.

### 6.1 Process-global credential injection ⚠️ **HARD BLOCKER**

`orchestrator/executor.py:4335-4411` writes every run's resolved integration credentials
into `os.environ` (`:4388`) and restores afterwards (`:4409`). **The code already documents
the flaw** at `:4364`: *"`os.environ` is process-global, so under concurrent [runs]…"*.

Under one tenant this is a within-org concern that `tenancy_and_visibility.md` §1.1
correctly deferred. **Under two tenants it is a credential leak**: tenant A's Zoho/Gmail
token is readable by tenant B's concurrently-executing agent — and agents run
model-generated tool calls, which is precisely the code you must assume is hostile.

**Fix:** pass credentials through the run context / a per-run scoped environment
(subprocess env, contextvar-backed resolver), never the process environment. This is
BO-7-adjacent and is the prerequisite for every other item here.

### 6.2 Self-mutation writes to the shared monorepo ⚠️ **HARD BLOCKER**

Root `AGENTS.md` non-negotiable #3 already flags this: native-MAF agents land approved
self-mutations by opening a PR **against this monorepo**, and the constraint says it
*"MUST be swapped for a tenant-isolated mechanism before any multi-tenant/customer
deployment — third parties must never push to the shared monorepo."*
See `docs/DESIGN_LIMITATION_native_maf_mutation.md`.

**Fix, in ascending order of effort:** (a) disable self-mutation for non-first-party
tenants — a config gate, days; (b) per-tenant agent repositories; (c) a mutation sandbox
whose output is a tenant-scoped artifact, never a push. **(a) is sufficient to unblock
Phase 1 and should be taken first.**

### 6.3 Deployment-singleton credentials

`provider_keys` is `provider TEXT PRIMARY KEY` (`08_provider_keys.sql:6-7`);
`mcp_servers`, `plugins` and `model_config` have no owner or org column. All must become
tenant-keyed. `tenancy_and_visibility.md` §1.1 called deployment-wide credentials *"exactly
the right shape"* — **true under its §1 decision, false under this one.**

### 6.4 The `org` subject means "every active user on the box"

`packages/acb_auth/acb_auth/access.py:400-402`: `_ORG_MEMBER_SQL` is `SELECT email FROM
app_user WHERE status = 'active'` — **no org filter**. Under pooled tenancy the `org`
subject would expand across every customer. Leak sites 1–10 in
`tenancy_and_visibility.md` §1.1 were *"moot by definition"* under deployment-per-tenant;
**this decision un-moots all of them.** RLS makes most of them correct automatically (the
row set is already tenant-constrained) — but each must be *verified*, not assumed, and
site 9 (`_HAS_OWNER_SQL`, `:522`, with no org filter, which makes
`ensure_owner_bootstrap()` a permanent no-op once any owner exists anywhere) is a
**lockout that RLS does not fix** and must be repaired by hand.

> ⚠️ **Re-derive these anchors before editing.** `tenancy_and_visibility.md` §1.1
> publishes `access.py:338-340` and `:460-464` for these two constants; measured
> 2026-08-08 they are at `:400` and `:522`. That document has already been through two
> anchor-correction passes for the same reason — use the §9 commands, not the numbers any
> document quotes.

### 6.5 TV-1 still applies, and now leaks for real

The three `org_group` slug-only joins (`tenancy_and_visibility.md` §2 / board **WS-14a**)
are cross-organization matches by construction. That document rated them "wrong within one
org too, nothing leaks today." **Under this decision they leak.** WS-14a's priority rises
from cleanup to prerequisite.

### 6.6 There is no per-tenant restore — only a whole-cluster one

*(the single genuine finding of the §0.9.9 prior-art review, 2026-08-11.)*

BO-23 backs up and restores **the cluster**. Under one tenant that is exactly right and
nothing here applies. **The moment a second company's data is on the box it becomes a
cross-tenant defect in this section's own terms:** the only way to answer "restore us to
3pm yesterday, we bulk-deleted a pipeline" is to roll everyone back. Serving one
customer's recovery by destroying another's day is not a recovery procedure.

This is the one capability database-per-tenant gets free, and every comparison in §0.9.9
has it — HANA MDC backs up and recovers each tenant database independently; CAP inherits
the same per-container granularity. Pooled, it must be built: a **tenant-scoped logical
export and re-import** (`organization_id`-filtered dump across the tenant-scoped table
set, restored into the live cluster without touching other tenants' rows).

**Fix, and the order it wants:** (a) a filtered logical export driven by the *same*
generated table set the RLS policies use — `scripts/gen_tenant_migration.py`'s
`discover_tables()`, so a table can never be in one list and missing from the other;
(b) re-import into a staging schema, then a reviewed swap; (c) rehearse it in CI the way
`rehearse_restore.sh` already rehearses the cluster path, because an untested restore
should not first run during an incident (BO-23's own lesson).

**When it is due:** *not* a pre-customer-#1 gate — with one tenant the cluster restore is
the per-tenant restore. It is due **before customer #2**, which places it alongside the
rest of this section rather than in MT-5's compliance basket.

**Not yet dispatchable:** no ticket contract, and (a) depends on the tenant-scoped set
being complete, i.e. after MT-1b's promotion. Recorded on the board's WS-29 row.

---

## 7. Explicitly rejected

Recorded so they are not re-proposed, and so the reasoning survives:

1. **Container/database per customer as the default tier.** §1.4. Kept as a priced
   enterprise tier only.
1b. **One container per organization on the same VPS.** §1.7 — it fragments the memory
   that pooling exists to share, delivers no real isolation on shared hardware, keeps
   every operational cost of database-per-tenant, and makes one box's failure everyone's.
   Dedicated containers only buy something on dedicated hardware.
1c. **Schema-per-tenant.** §1.8 — the strongest rejected alternative, and rejected on one
   property: **RLS fails closed (zero rows), `search_path` fails open (another tenant's
   rows, silently).** Re-take it if the market turns out to be a few dozen large accounts.
   ⚠️ **Contrary evidence, recorded rather than argued away (§0.9.9):** SAP's CAP ships
   precisely this — shared app, one HDI container per tenant — in production at scale. It
   survives rejection here for a reason that is ours and not theirs: CAP automates schema
   deploy across containers on a quarterly cadence, while we ship continuously against a
   160+ file ladder applied before restart with no rollback. **If our release cadence or
   our migration mechanism changes character, this is the first rejection to re-open.**
2. **`X-Organization-Id` as the tenant source** (proposed in
   `multi_user_organization_research.md` §17.3). Client-settable tenancy is a one-line
   cross-tenant read. The tenant comes from the authenticated session or a tenant-scoped
   API key. §1.5.
3. **A separate LLM proxy process.** §3.1 — the gateway `/v1` already is one.
4. **Billing customers in provider tokens.** §3.2(4) — sell credits.
5. **Billing on active users.** §2.2 — sell assigned seats; put the variability in AI
   credits.
6. **Entitlements expressed as roles/permissions.** §2.1 — the customer's admin owns roles;
   you own entitlements.
7. **Per-query `WHERE organization_id = ?` as the isolation mechanism.** RLS at the
   connection seam. A predicate you must remember is a predicate someone will forget across
   209 files; a database policy is not forgettable. Hand-written predicates are permitted
   as an *optimisation* (index selectivity), never as the control.
8. **A second scoping doctrine.** `tenancy_and_visibility.md` §3.2's standing rule is
   unchanged and extends here: tenant isolation is `organization_id` + RLS, visibility
   inside a tenant is `email | group:<slug> | org`. Two mechanisms, two axes, no third.

---

## 8. Open — owner decisions still needed

1. ~~**Price points and module boundaries.**~~ **ANSWERED 2026-08-09 (owner, D18):
   Core ₹600 per user per month** (Tasks, Calendar, Chat, People directory) **+ ₹300
   per user per month per add-on module** (CRM, Projects, Email, Meetings, WhatsApp,
   Workflows). Selected from agent-drafted options anchored on Zoho India pricing; the
   owner expects to revise against the first five silo customers (§11.2 item 3 said
   drafting-now-revising-later is correct). §2.4's module split becomes the SKU list's
   starting shape — MT-2's entitlement catalog seeds from this. **⚠️ Refined 2026-08-09
   by D19.1:** where this answer and §2.4's old table disagreed (Tasks, People,
   Calendar, Builder/Finance pricing, Meetings/Workflows naming), D19's reconciled
   table governed. **⚠️ Superseded again 2026-08-10 by D23:** the customer-facing
   shape of record is **§2.4b's Center packages**; §2.4's table is demoted to the
   internal atom ledger. Cite D23 for anything a customer buys.
2. ~~**Credit-to-rupee conversion and target gross margin on AI.**~~ **ANSWERED
   2026-08-09 (owner, D18): the credit unit is a ₹10 "AI action" at ~50% gross margin**
   — the `model_rate_card` prices each model call at provider cost × 2, denominated in
   credits, so buyers see actions, never tokens (§3's rate-card rule unchanged: sell
   credits via the rate card, never provider tokens). **⚠️ Refined 2026-08-09 by
   D19.2 — the flat-₹10-vs-cost×2 ambiguity is resolved:** the credit is the ₹10
   purchase/display unit, calls draw fractionally at cost × 2, the rate card is
   INR-native, STT is metered, embeddings/WhatsApp fees are absorbed. Full statement
   in §3.2.
3. ~~**Payment provider split.**~~ **ANSWERED 2026-08-09 (owner, D19.5): Razorpay
   only at launch**, behind the `payment_provider` seam (§4.3's seam design stands);
   Stripe lands as a second implementation of the same seam when the first
   international customer appears. This was the last input blocking MT-4 — **MT-4 is
   now unblocked for spec detailing.**
4. ~~**Data residency commitments.**~~ **ANSWERED 2026-08-09 (owner, D19.6):
   promise India-only at launch.** All customer data on India-region infrastructure;
   a second residency tier is a deliberate priced-placement decision later (D15's
   placement model). Constrains hosting choices to India regions from customer #1.
5. ~~**Customer-facing framing of D23 pricing.**~~ **ANSWERED 2026-08-10 (owner,
   D24 — all four ruled):** **(a)** the ₹600 Core headline STAYS (the
   "Workspace ₹1,200 default" reframing was rejected); **(b)** slices-only
   Centers stay ₹300, pitched as "a full team workspace", never a filter;
   **(c)** the **all-Centers seat exists at ₹1,800** (every Center, no add-ons)
   — and it forced **Complete down to ₹3,000** (D24.4, arithmetic in the D24
   record); **(d)** role presets ship in SC-2's launch scope. Still standing
   from the review: the typical-month credit anchor on the pricing page, and
   zero internal vocabulary customer-facing. Purchase-flow copy is now
   buildable.
6. ~~**Whether first customers get the pooled tier or hand-run silos.**~~
   **ANSWERED 2026-08-08** by the owner's seat-count input (10–50 users per customer):
   **silo customers 1–5, build Phase 1 in parallel, cut over at 8–12.** The reasoning,
   the crossover arithmetic and the four conditions that keep it a bridge rather than a
   drift are in **§5.1**.

---

## 9. Verification

```bash
# §0 — one engine, one session seam
grep -n "create_async_engine\|def get_db\|async_sessionmaker" packages/acb_common/acb_common/db.py

# §0 — the intersect() seam entitlements will reuse
grep -n "def intersect" -A 12 packages/acb_auth/acb_auth/permissions.py

# §0/§3 — the LLM choke point and the existing per-call cost computation
grep -n "def _emit_usage" -A 30 packages/acb_llm/acb_llm/client.py
grep -n "_emit_usage\|require_llm_api_auth" apps/services/gateway/gateway/routes/v1_compat.py

# §0/§6.3 — deployment-singleton credentials
grep -n "PRIMARY KEY" infra/postgres/08_provider_keys.sql

# §6.1 — process-global credential injection, and its own admission
sed -n '4335,4415p' apps/services/orchestrator/orchestrator/executor.py

# §6.4 — the org subject with no org filter
grep -n "_ORG_MEMBER_SQL\|_HAS_OWNER_SQL" -A 6 packages/acb_auth/acb_auth/access.py

# §1 — the retrofit surface
grep -rn "organization_id" infra/postgres/*.sql | grep -ci "add column\|organization_id UUID"
ls infra/postgres/[0-9]*_*.sql | wc -l
```

---

## 11. THE WORK PLAN — dispatchable tickets

**Board rows:** `work_plan.md` §2 *Multi-tenancy* → **WS-29**. Ticket IDs are `MT-n`
(R2: no phase-ID reuse). This section owns *what to build and how*; the board owns
*order and ownership*.

**Honesty about dispatchability.** MT-0 and MT-1 carry the full seven-point contract
(`work_plan.md` §1) and are dispatchable today. **MT-2 through MT-5 are scoped, not
dispatchable** — each names what must be answered to make it so. Writing testable
acceptance for Phase 4 today would be inventing it, and §1's contract point 3 forbids
"done when: owner call" dressed up as a criterion.

**Standing rules for every ticket below.** R1 — migration numbers are *"next free at build
time"*, never written here. Anchors are re-verified at dispatch, never trusted from
authoring time (§0.1 exists because that rule was broken). R4 — a PR that ships a ticket
updates this spec's status header in the same PR.

---

### MT-0 — Blockers · *nothing ships to a second tenant until all four are in*

> These are not features and they do not gate on the tenancy decision. **MT-0a and MT-0c
> are live defects the day two companies share a process — not a database, a *process*.**

#### MT-0a · Per-run credential scoping — kill process-global `os.environ` · ✅ **BUILT 2026-08-08, pending review**
**Owner:** §6.1 · **Anchor:** `orchestrator/executor.py:4335-4411` (write `:4388`, restore
`:4409`; the flaw is documented in-code at `:4364`)

**Done when:**
1. A run's resolved integration credentials reach the agent through the **run context or a
   per-run scoped subprocess environment** — never `os.environ` of the gateway process.
2. A hermetic test proves two concurrent runs with different credential sets **cannot
   observe each other's values.** Verified **red** against today's code first, and the
   failure quoted in the PR.
3. `os.environ` is not written by `executor.py` for integration credentials at all — a
   grep assertion in the test, so a later PR cannot reintroduce it.
4. `uv run ruff check` clean on the touched files only (never `ruff check .` — ~1983
   pre-existing errors on this tree, not a signal).

**Verify:** `uv run pytest tests/unit/test_integration_env_scoping.py -v -rs` (12 passed)
· `uv run pytest tests/unit/test_code_tools.py tests/unit/test_web_tools_fallback.py
tests/unit/test_acb_skills.py tests/unit/test_agent_paths.py -q` (72 passed across the fence)

**As built.** The bridge is a **`ContextVar` in `acb_skills.integrations`**
(`bind_run_credentials` / `release_run_credentials` / `run_credentials` /
`credential`), not a scoped `os.environ` write. Consumers: `code_tools._script_env`
for subprocess scripts, and `integrations.credential()` for the **three in-process
reader lines** that existed — `skill-clickup-sync/core.py` ×2 and `web_tools.py` ×1.
The ~20 `os.getenv` calls in `integrations.py` itself are **resolvers reading the
operator's `.env`** and correctly still do.

**Verified red first**, and quoted: replaying the interleaving against the previous
implementation printed `run B saw run A's : clk-secret-123` → `FAIL (RED) —
AssertionError: run B could read run A's credential`. *(Only one direction
reproduced; the other was masked because run B's teardown completed before run A
resumed — itself a demonstration of how timing-dependent the old scoping was.)*

⚠️ **Two limits, deliberately not closed by this ticket.** (1) An **operator-provided**
env value still wins and is still process-global — that is unchanged precedence, and
making the operator's own store per-tenant is **MT-0d**. (2) The declared-*list*
(`_WRITE_ARTIFACT_CONTEXT`) is still a process-global dict despite a docstring calling
itself coroutine-local; a concurrent run can still widen *which names* are looked up,
but a widened name now yields nothing unless this run also holds that credential.

#### MT-0b · Self-mutation containment for non-first-party tenants · ✅ **BUILT 2026-08-08, pending review**
**Owner:** §6.2 · root `AGENTS.md` non-negotiable 3 · `docs/DESIGN_LIMITATION_native_maf_mutation.md`

**Done when:** a config gate disables native-MAF self-mutation for any tenant not flagged
first-party; it **defaults to disabled**; a test asserts a non-first-party tenant's failure
event produces no PR attempt. ⚠️ **`work_plan.md` WS-3 records that no `first_party` field
exists on any manifest, config or column** — this ticket creates it. Do not assume it.

**As built.** Migration **157** adds `organization.first_party BOOLEAN NOT NULL DEFAULT
false`, backfilling `slug='default'` to true — so today's behaviour is unchanged and every
organization created afterwards is contained *by construction*.
`mutation._self_mutation_permitted()` is called **first** in `attempt_self_mutation`,
before the attempt tally, the sandbox, git or the network, and returns
`MutationResult(attempted=False, skipped_reason=…)`.

**It fails closed on every path** — unreachable DB, missing column, and (the multi-tenant
case) no *sole* organization, because the untenanted query requires `count(*) = 1` rather
than falling back to the default org. A default-org fallback would keep answering `true`
after tenant #2 arrived, which is the leak. `SELF_MUTATION_DISABLED=1` is an operator
hard-off that short-circuits before any query.

**Verify:** `uv run pytest tests/unit/test_mt0b_self_mutation_containment.py -v -rs`
(8 passed) — verified **red** first: 6 of the 8 failed against the pre-gate source.
One test deliberately guards the *other* direction — that a first-party org still reaches
the tally — because "refuse everything" would pass every other assertion here while
silently switching the feature off for Fracktal too.

#### MT-0c · The execution-plane sandbox — **SPLIT 2026-08-08 (D16)**

> **`DECISION (agent-proposed, owner may overrule)` — 2026-08-08.** The owner delegated
> this call. Recorded under the same label as D13 so it stays overrulable.
>
> **MT-0c as one ticket was the wrong shape.** Its four clauses have wildly different
> costs and wildly different *urgency*, and bundling them meant the cheapest, most
> valuable one waited behind the most expensive one.

**MT-0c-1 · No agent tool accepts SQL · ✅ BUILT 2026-08-08, pending review**

**Why now, not at cutover.** §0.9.3 already names this a *condition on the pooled
decision*. It was **already violated** — and not only in a multi-tenant sense.
`query_history` took a **model-generated SQL string** and executed it through
`acb_graph.get_session()` (connection path 4 in §0.1 — the sync `create_engine` the seam
ratchet never inspected). It was registered in `agent-orchestrator/config.json`, injected
at `_tool_injection.py:623`, and advertised to the model as *"Run a SELECT-only SQL
query"*.

**Its guard was wrong in both directions, measured 2026-08-08:**

| | Result |
|---|---|
| `SELECT role, content, created_at FROM chat_message` — *the tool's own documented example* | **Rejected** — `CREATED_AT` contains the substring `CREATE` |
| `SELECT * FROM provider_keys` | **Allowed.** So were `email_messages`, `app_user`, everything. The guard policed *verbs*; nothing policed *tables* |

So this was a live within-org read primitive **today**, and a cross-tenant one the day
MT-1 lands. **As built:** `query_history` now takes search criteria — the model supplies
*values*, never syntax; the SQL is a fixed string with bound parameters over exactly the
two tables it always documented. Results narrow to the acting member's own sessions when
the run context names one (the old tool could read any member's conversations — its own
docstring example did). A **build-failing ratchet** (`test_no_agent_tool_accepts_a_sql_parameter`)
stops the shape returning.

**Verify:** `uv run pytest tests/unit/test_mt0c1_no_raw_sql_agent_tools.py -v -rs`
(9 passed; verified **red** — 6 of 9 failed against the SQL version). ⚠️ The pinned call
contract in `tests/unit/test_tool_schema_diet.py` was updated **deliberately** — that
ratchet exists to stop a contract changing by accident, and it caught this correctly.

**MT-0c-2 · The container/microVM tier (WS-3 T2) · 🔴 STAYS OWNER-GATE, STAYS PARKED**

**Why parked is still right, and this is the substance of the decision.** D10 parked T2
because *"the ladder must hold against trusted colleagues, not hostile users."* That
reasoning **still holds for the silo phase** (§5.1): with one tenant per box, an escaped
agent reaches only its own tenant's data, which is the blast radius it already had.

T2 becomes load-bearing at the **pooled cutover** (customer 8–12), not at customer #1.
Building Firecracker-grade isolation before the first customer exists is speculative
infrastructure — weeks of work whose value arrives months later, paid for out of the
runway that should be buying customers.

> **The trigger:** MT-0c-2 must land **before** the first pooled tenant, i.e. it is a
> precondition of the §5.1 cutover, not of Phase 0. Un-parking is still **OWNER-GATE**
> and `work_plan.md` §6 keeps its entry.

**What this split does NOT do.** It does not weaken §0.9.3. The *condition* on the pooled
decision was "no raw-SQL tool **and** no agent-reachable path can set `app.tenant_id`" —
MT-0c-1 satisfies the first half now, and the second half cannot be violated before
`app.tenant_id` exists (MT-1b). The container tier is defence in depth on top of both,
which is why it can wait; the two conditions themselves cannot.

#### MT-0d · Per-organization provider keys · ✅ **BUILT 2026-08-08, pending review**
**Owner:** §6.3 · **Anchor:** `08_provider_keys.sql:6-7` (`provider TEXT PRIMARY KEY`)

**Done when:** `provider_keys` is keyed `(organization_id, provider)`; `mcp_servers`,
`plugins` and `model_config` carry an org column; `acb_llm/key_store.py` and
`model_config.py` resolve by tenant; the single existing org backfills; a test asserts a
lookup without a tenant returns nothing rather than another tenant's key.

**As built.** Migration **158** re-keys all four: `provider_keys` →
`PRIMARY KEY (organization_id, provider)`, `model_config` → `(organization_id, key)`,
`mcp_servers` → `(organization_id, name)`, and `plugins` keeps its UUID pk while its
deployment-wide `name UNIQUE` becomes `(organization_id, name)`. Every existing row
backfills to the operator's org, and a `DO` block **refuses to re-key** if any row is left
ownerless rather than proceeding.

**The untenanted resolution is the design decision worth reviewing.**
`key_store._resolve_org(None)` resolves to *the sole organization* — literally
`WHERE (SELECT count(*) FROM organization) = 1`. So the existing call sites keep
working unchanged today, and **every one of them fails closed the moment a second
organization exists**, which is exactly when MT-1 must supply a real tenant. A
"default org" fallback would keep answering after tenant #2 and serve the operator's keys
to a customer. Reads return `""`; **writes raise**, because a credential written with no
owner is how a key ends up readable by the wrong tenant.

> ⚠️ **The size of that surface, corrected 2026-08-19: it is 38 call sites, not "~20"** —
> `grep -rn "get_key_store()" --include=*.py apps packages | grep -v tests/ | grep -v "def
> get_key_store" | wc -l` → **38**, across 20 files, plus **10** `model_config`
> `load_blob`/`save_blob` sites. Threading a tenant through them is **MT-1j slice 5**, and
> that slice must not "fix" `_resolve_org` — `work_plan.md:844` records it as the one old
> convenience that must NOT be repaired.

⚠️ **The in-memory cache was the other half, and correct SQL does not protect it.**
`ProviderKeyStore._cache` was keyed by `provider` alone — the second tenant asking for
`openai` would have been served the first tenant's **decrypted** key straight from memory,
with no query issued at all. It is now keyed `(organization_id, provider)`, pinned by its
own test.

**Verify:** `uv run pytest tests/unit/test_mt0d_per_org_credentials.py -v -rs` (8 passed)
— verified **red** first: 7 of 8 failed against the pre-fix source.

⚠️ **Not verified against a live database.** No Docker daemon was available in the build
environment, so migrations 157 and 158 were **statically** checked only: all four
auto-generated constraint names confirmed against `schema.generated.sql`, and no foreign
key anywhere references the primary keys being re-pointed (so the drop-and-re-add is
safe). **Run both against a scratch Postgres before deploying** — `apply_migrations.sh`
replays from `02_` upward, and a failure there fails the deploy.

---

### MT-1 — Tenancy foundation · *the big one · 4–5 weeks*

#### MT-1a · Control plane, identity split, placement · ◐ **PARTIAL 2026-08-08**

> **Built:** migration 159 (`tenant_placement`, `user_identity`, `org_membership`, seeded from
> `app_user`) + `acb_common/placement.py`. **Additive and inert** — `app_user` is untouched and
> still authoritative.
> **NOT built — MT-1a-2:** cutting the auth path over. The live path still upserts on
> `app_user.email`, and a half-migrated identity is worse than an unmigrated one.
> ⚠️ **Anchors re-measured 2026-08-19 and they had drifted twice.** The two `ON CONFLICT
> (email)` upserts are **`acb_auth/access.py:550`** (`_BOOTSTRAP_OWNER_SQL`) and
> **`gateway/routes/admin/_common.py:599`** (`_PROVISION_MEMBER_SQL`) — *not* `:509`, and
> **not `access.py:205`**, which is `access_request`'s upsert and already uses the correct
> `ON CONFLICT (lower(email))` idiom. The spec before that cited them in `members.py`,
> where they have never been. ~~**Both are also broken against today's schema**~~ —
> **REPAIRED 2026-08-19 by MT-1j slice 6**: migration 162 had dropped `app_user_email_key`
> and both statements raised **42P10 at plan time**, so neither the conflict path nor the
> fresh-insert path worked at all. Both now say `ON CONFLICT (lower(email))`; fence
> `tests/unit/test_app_user_upserts.py`. **MT-1a-2 still rewrites these two statements**
> onto `org_membership` — slice 6 made them work against today's schema, it did not move
> identity.
**Owner:** §1.5, §0.9.5

**Done when:**
1. A **separate control-plane database** (or at minimum a separate schema with its own
   role) holds `organization`, `tenant_placement`, and the billing/entitlement/usage
   tables. It carries **no** tenant business data and is **not** under RLS.
2. `user_identity(id, email UNIQUE, …)` + `org_membership(user_id, org_id, status, …)`
   replace `app_user`'s dual role. ⚠️ **`app_user.email` global uniqueness is depended on
   by two `ON CONFLICT (email)` upserts — re-measured 2026-08-19 at
   `acb_auth/access.py:550` and `gateway/routes/admin/_common.py:599`.** This line has now
   published three different anchor pairs (`members.py:173`/`access.py:447`, then
   `:205`/`:509`); **re-derive with grep at build time and trust nothing here.** Both are
   rewritten in this ticket, and a test pins that the same email can hold membership in two
   orgs. ⚠️ ~~**They are additionally invalid today**~~ — **repaired 2026-08-19**: both now
   use the `lower(email)` idiom (**MT-1j slice 6**, which landed first, as planned). They
   are still `app_user` upserts, which is what this line is about.
3. `tenant_placement(organization_id, target, region)` exists and is consulted, **even
   though every row resolves to the same target on day one.** A test asserts the resolver
   reads it rather than a constant.

#### MT-1b · `organization_id` + FORCE RLS on every table · ◐ **GENERATED, NOT APPLIED**

> **Built:** `scripts/gen_tenant_migration.py` + `tests/unit/test_tenant_coverage.py`.
> 146 tables discovered, **135 tenant-scoped**, 11 exempt-with-a-reason.
> ⚠️ **Output goes to `infra/postgres/generated/` — OUTSIDE the sequence the deploy replays**,
> in four separately-appliable phases. `apply_migrations.sh` carries a lock-timeout design
> written after a **14h44m outage** of exactly this shape, and there is no database in the
> build environment to try any of it against. Promoting these is a human act in a window.
> **Phase 4 requires MT-1c deployed and verified first**, or every unbound connection reads
> zero rows and the product goes dark.
**Owner:** §1.3 · **the generated migration, not 143 hand-written ones**

**Done when:**
1. Every application table carries `organization_id UUID NOT NULL DEFAULT
   current_setting('app.tenant_id', true)::uuid` with an FK, an index, **and**
   `ENABLE` + `FORCE ROW LEVEL SECURITY` with a `tenant_isolation` policy.
2. **`organization_id` is in every primary key and every index prefix** (§1.8a — the
   distribution-key discipline; retrofitting it later means rewriting every PK).
3. The gateway connects as a **non-owner, non-superuser role** (`acb_app`). Migrations
   still run as the owner. A test asserts the app role has neither `BYPASSRLS` nor
   ownership.
4. **A build-failing coverage test** enumerates `pg_tables` and fails if any application
   table lacks the column, FORCE RLS, or a policy — the same ratchet discipline as
   `tests/unit/test_db_engine_seam.py`, so a table added tomorrow is covered.
5. **Zero `SELECT`/`INSERT` statements in the gateway are rewritten by this ticket.** If a
   query needed changing, the column default or the policy is wrong.

#### MT-1c · Tenant binding at all **ten** connection paths + two new ratchets · ◐ **SEAM + RATCHETS BUILT**

> **Built:** `acb_common.db.tenant_session()` (SET LOCAL inside an explicit transaction, fails
> closed when unbound) · the `create_engine` ratchet · the new `psycopg.connect` ratchet.
> **NOT built:** the call-site conversion, and the Mem0 decision (§0.1 path 8).
>
> ⚠️ **The conversion surface is 561 sites across 138 files, not the "~200" this spec said
> until 2026-08-08.** The undercount happened because the dominant idiom is the *aliased*
> import — `from gateway.db import get_db as _get_db`, then `await _get_db()` (441 of the
> 561) — so a grep for `get_db()` alone misses four fifths of it. Measured:
> `grep -rhoE "await _?get_db\(\)" --include=*.py apps packages | wc -l`. **This makes the
> conversion, not the RLS migration, the long pole of MT-1** — see the handover runbook §2.
**Owner:** §0.1 — **read its table before starting; the inventory is the ticket**

**Done when:**
1. All eight paths bind a tenant. **`SET LOCAL`, never `SET`** (§1.3) — a dedicated test
   proves a pooled connection returned and re-borrowed carries **no** tenant setting.
2. `test_db_engine_seam.py` is extended to **`create_engine`** as well as
   `create_async_engine` — path 4 (`acb_graph/db.py:32`) exists today precisely because
   the ratchet only inspected the async name.
3. A **new ratchet for `psycopg.connect`**, allow-list-with-a-reason, covering paths 5–7.
4. **The Mem0 decision (path 8) is taken and written into this spec** — **taken
   2026-08-09: D17, Option A (conninfo options); the build must implement it or
   escalate why not** — conninfo options,
   a per-tenant role, or scope-string-only isolation. **Leaving it undecided fails this
   ticket.**
5. A test asserts an **unbound** connection returns **zero rows** rather than another
   tenant's (the fail-closed property §0.1 rests on).

#### MT-1d · Background-job tenant binding · 🟢 AGENT-SAFE
**Owner:** §1.9 — *"a job that forgets doesn't leak one row; it leaks unbounded"*

**Done when:** every scheduled/queued unit of work (ingestion scheduler, reconciler,
orchestrator runs, broker handlers, the Redis Streams consumer) carries an explicit
`organization_id` on its job record and binds it before any DB access; a test asserts a job
constructed without one **refuses to run** rather than defaulting.

> ✅ ~~**Named site, found by the WS-27 alignment audit 2026-08-10 — H2 will NOT
> reach it.** `automation.run_lifecycle_sweep` … opens the **un-bound**
> `get_db()` and starts with `SELECT * FROM pm_projects WHERE parent_project_id
> IS NULL` — every tenant's roots, no predicate.~~ **CLOSED 2026-08-10 by
> WS-27aa** (`project_management_app.md` §9.2). The sweep now takes a required
> `organization_id`, refuses with `TenantUnbound` without one before issuing a
> statement, and filters roots on `AND organization_id = CAST(:org AS uuid)`;
> `workflows/service._pm_lifecycle_sweeper` resolves that tenant from the
> workflow **owner** through `app_user` (the `workflows` table has no
> `organization_id` column until H3 phase 1 lands) and binds it with
> `tenant_session(org)`.
>
> ⚠️ **This entry's own prescription was wrong and is struck: it does NOT need a
> per-tenant loop.** A loop inside the sweep would be one tenant's scheduled
> workflow acting for every other tenant — the unbounded-job shape this section
> exists to forbid. The loop is over *workflows*: each tenant schedules its own
> and each sweeps exactly its own. Proven two-org against a real Postgres by
> `tests/live/live_ws27aa.py`.
>
> **`_pm_task_updater` beside it is still open** — it patches ONE task by id on
> the unbound seam, so it wants the task's own organization threaded from the
> node config, not a loop. It belongs to WS-29's H4 slice, not to Projects.

#### MT-1e · Redis: prefixes enforced by the client · ◐ **WRAPPER BUILT, CALL SITES NOT CONVERTED**

> **Built:** `acb_common/tenant_redis.py` — a client that *cannot* express an unprefixed key,
> plus two AST ratchets (direct `redis` import; hand-written `cc:` literals).
> **NOT converted:** ~58 key sites across 10 clients. Deliberately separate — the docstring
> carries the migration path, including *not* writing a dual-read shim (every key is cache,
> presence or a bounded stream, so conversion is a cache-cold event, not a data migration).
>
> ⚠️ **Three things no ratchet can catch, all verified against the tree:**
> 1. `routes/chat.py:707` — `SCAN match="cc:active:*"` **enumerates every tenant's sessions**
>    the moment a second exists. Highest severity in the inventory.
> 2. `ingestion/consumer.py:95` — `_GROUP = "cc-ingest"` is **one consumer group shared by all
>    tenants**; §1.9 requires one per tenant.
> 3. **Untenanted non-`cc:` namespaces** invisible to the `cc:` ratchet:
>    `ingestion:{clickup,zoho,gmail,dlq}`, `session_mem:`, `email:att:cache:` — plus
>    `orchestrator/agents.py:436`, which hands `redis_url` to `agent_framework`'s
>    `RedisHistoryProvider`, keying chat history **outside this wrapper entirely**. That one
>    needs its own decision, not a conversion.
**Owner:** §0.9.4, §1.9 · **Anchor:** today's untenanted `cc:activity`, `cc:room`,
`cc:cost`, `cc:presence`, `cc:runactor`, …

**Done when:** a wrapper client is the only way the codebase reaches Redis and it **cannot
construct an unprefixed key**; consumer groups are per-tenant; a grep-assertion test fails
the build on a direct `redis.asyncio` client outside the wrapper. *A convention is a thing
people forget; a client that cannot express the wrong thing is not.*

#### MT-1f · Subdomain tenant resolution · 🟢 AGENT-SAFE
**Owner:** §1.5's binding rule

**Done when:** the workbench resolves `<slug>.<domain>` and the tenant claim rides the
**authenticated session**; the gateway derives the tenant from the session or a
tenant-scoped API key **only**; a test asserts an `X-Organization-Id` header, query
parameter or body field is **ignored**, not honoured. *(This extends
`user_management_contract.md` rule 10 — that spec gains the eleventh rule in this PR.)*

#### MT-1g · Blobs out of Postgres · 🟢 AGENT-SAFE
**Owner:** §1.6 · **Anchor:** `71_agent_blob_store.sql:30` (`content BYTEA`)

**Done when:** `agent_blob` content lives in object storage keyed `<org_id>/…`; the table
keeps metadata and a pointer; meeting media (already filesystem-backed at
`95_note_taker.sql:56`) moves to the same store. *Worth doing in any tenancy model — a
BYTEA column is the wrong home for file content in any topology.*

#### MT-1h · Partitioning + per-tenant logical backup · 🟢 AGENT-SAFE
**Owner:** §1.6

**Done when:** the heavy tables (`email_messages`, the `*_embeddings` vector tables,
`chat_message`, `audit_event`) are partitioned on `organization_id` — **LIST for the
largest tenants, HASH/default for the tail** (one partition per tenant across all tenants
recreates the catalog pressure §1.8 rejects); **and** a per-tenant logical export/import
job exists and has been **run end-to-end at least once**, quoted in the PR. *"Restore this
one customer to yesterday" is the one capability database-per-tenant gives free, and it
costs one job here, not N databases.*

#### MT-1i · The leak sites this decision un-mooted · ✅ **BUILT 2026-08-08** (one criterion open)

> All five predicates derived, verified red first. ⚠️ `tenancy_and_visibility.md` §2 **done-when 3**
> (the DB-backed two-org behavioural fixture) is **NOT discharged** — it lives in files that skip
> entirely without Postgres. It needs a live database.
**Owner:** §6.4, §6.5 · absorbs board **WS-14a**

**Done when:** the three `org_group` slug-only joins carry a derived org predicate
(`tenancy_and_visibility.md` §2's done-when 1–5 apply **verbatim** and are not restated
here — that spec owns them); `_ORG_MEMBER_SQL` (`access.py:400`) is org-filtered; and
`_HAS_OWNER_SQL` (**`access.py:572`** — re-measured 2026-08-19; this line read `:522`
from authoring time) is org-filtered — **that last one is a lockout RLS does not fix**
and must be repaired by hand.

#### MT-1j · Tenant-side organization provisioning · ◐ **SLICES 1 + 2 + 3 + 4 (OPERATOR ARM) + 6 BUILT 2026-08-19 · slice 5 ◐ RATCHET ROUNDS 1 + 2 — AT THE OWNER-GATED FLOOR OF THE WALKER-VISIBLE SET (`4 + 1`; three uncounted `self.` calls inside `key_store.py` remain — round-2 box)** — minted 2026-08-19, every anchor below verified against code that day
**Gate:** 🟢 **AGENT-SAFE to build and to R8-test against scratch databases** ·
🔴 **OWNER-GATE to EXECUTE against a real second organization** (Decision C below;
registered in `work_plan.md` §6).
**Owner:** this section. **No other document owns any part of this** — the four specs and
one migration that disclaim it link here and add nothing (contract point 6).

> **Why this ticket exists: four specs disclaimed the same work to a fifth that did
> not exist.** Measured 2026-08-19:
>
> | Site | What it hands off |
> |---|---|
> | `saas_multitenancy_implementation.md` §7.1 step 3 · §8 trap 5 | *"that seeding must become a parameterised function, or org #2 has no roles and no owner"* |
> | `customer_console.md` CP-2b §6(k) (`:3081-3089`) | creating the local `organization` row → *"CP-2a's lifecycle path and WS-29's tenant bootstrap"* |
> | `subscription_console.md` (`:196`, `:987`) | `billing:purchase` born unheld in every org → *"the org-provisioning ticket that parameterises role seeding — not here"* |
> | `user_management_contract.md` §3 (`:139`) | the same capability, *"which the org-provisioning ticket owns"* |
> | `178_billing_purchase_permission.sql:39-48` (in-code) | *"owned by the org-provisioning ticket that parameterises role seeding"* |
>
> Each refusal is individually **correct** — none of those slices should have invented
> per-org provisioning on the way past. The defect is that the referent was never minted.
> Meanwhile **D36.1 is already asserted**: *"Fracktal signs up through the same
> provisioning flow as any customer — same `/orgs/provision`, same trial, same seat cap."*
> A disclaimer chain with no terminus is exactly how customer zero arrives at an
> organization with **no roles, no owner and no placement**, and discovers it at the
> checkout button.

**Two planes, and the ticket spans both — say which one you mean at every line.**
The **Customer Console** database (`infra/customer_console/`) has its own `organization`,
`org_placement`, `user_identity`, `org_membership`. The **tenant** database
(`infra/postgres/`) has its own `organization`, `tenant_placement`, `org_role`,
`app_user`. They are different tables with the same names in different databases, and
migration 159's comment says so (*"mirrors migration 159 in the tenant plane, which
becomes the local projection of this table (D32.4)"*). Measured 2026-08-19: the tenant
plane has **no production writer for either of its two rows** — the only
`INSERT INTO organization` outside test fixtures is `130_org_access_control.sql:49`'s
`default` seed, and the only `INSERT INTO tenant_placement` is `159`'s seed for that same
org.

**Scope — six slices, positively stated.**

1. Extract migration 130's role seed into a **callable** so any organization can be
   seeded with the system roles and their grants.
   ✅ **BUILT 2026-08-19** — `provision_org_roles(org_id)`, migration 179.
2. Give the ownership bootstrap a path that is not `_BOOTSTRAP_ORG_SLUG`, **without**
   changing what that constant does for a fresh box (D36.3).
   ✅ **BUILT 2026-08-19** — `provision_org_owner(org_id, email)`; the constant is
   untouched, and a fence now asserts that in the negative.
3. Create the tenant-plane `organization` + `tenant_placement` pair as **one idempotent
   act**.
   ✅ **BUILT 2026-08-19** — `provision_organization(slug, …)`.
4. Wire the Console↔tenant seam so a provisioned org **resolves** — including the
   Console-side half: `POST /orgs/provision` never writes `org_placement`.
   ✅ **OPERATOR ARM BUILT 2026-08-19** — `deployment_label` (required at the
   model as slice 4 shipped it; `str | None` since CP-2c slice 1, required in
   the handler for this arm), the placement write and its two refusals, and the
   tenant seam function `provision_local_organization`. The **deployment-key arm
   shipped in CP-2c slice 1 (2026-08-19)** — the model/handler split, the
   `provision` capability, and its create-only refusal; **issuing or widening a
   real key stays behind §6 (f)/(h)** (D46.6 item 2, that gate unchanged — only
   the code arm is built).
5. Thread the tenant through the `key_store` / `model_config` call sites so a second
   org's credentials resolve — **without** weakening any fail-closed contract.
   ◐ **RATCHET ROUNDS 1 + 2 landed 2026-08-19** — round 1 the ratchet itself plus the
   `model_config` subgraph on the LLM/model surface; round 2 the Integration Registry
   credential surface (`routes/integrations.py`). Banked **11 + 10 → 9 + 1 → 4 + 1**,
   which now EQUALS the H4 tables: every **walker-visible** untenanted credential call in
   `apps/` + `packages/` is an owner-gated one, so the ratchet is at the **FLOOR of the
   set it measures** absent the credential-scope act. ⚠️ Not a floor of the tree —
   `key_store.py` makes three untenanted calls on `self` (`:420`, `:433`, `:472`) that
   the ratchet's walker cannot see; same class, same owner act, itemised in the round-2
   box below. The LIVE completion path turned out to be H4 and is marked, not
   threaded (slice 5's box below).
6. Repair the two `ON CONFLICT (email)` upserts that migration 162 invalidated.
   ✅ **BUILT 2026-08-19** — the predicted 42P10 reproduced red on a real ladder first, and
   it took out the fresh-insert path too, not just the conflict one (slice 6 below).

**Non-goals — named so a later agent does not widen this.**

- **The commercial one-assignment act is MT-2's** (seat + membership + entitlements +
  grants, D23.2). MT-1j is the substrate *beneath* it: an organization that exists, has
  roles, has an owner, has a placement and can read its own keys. It sells nothing and
  grants no entitlement.
- **The self-serve signup form is CP-2c's** (`customer_console.md`; minted
  2026-08-19 by D46 — CP-2a is the API beneath it; this line said "CP-2a's"
  while that form had no ticket of its own). This ticket consumes
  `POST /orgs/provision`; it does not build a UI. ⚠️ CP-2c names **slice 4 of
  this ticket** as its hard dependency — the Console↔tenant seam including the
  `org_placement` hole is what its orchestration calls.
- **The identity cutover is MT-1a-2 / H6.** MT-1j fixes two upserts that are *broken
  against today's schema*; it does not move sign-in onto `user_identity`.
- **Not the RLS promotion (MT-1b / H3).** See Decision C: H3 gates *executing* this
  against a real second org, and gates nothing about building or testing it.
- **No second scoping doctrine, no second seeding doctrine** — 178's comment refused a
  fourth looping seed for exactly this reason, and that refusal is honoured by Decision A,
  not evaded.

---

**Slice 1 · Parameterised role seed. · ✅ BUILT 2026-08-19.**
**Anchors:** `130_org_access_control.sql:175-260` (the `DO $$` block: `owner`, `admin`,
`manager`, `member`, `guest`, `agent_service` — **six** `org_role` rows, the five
assignable ones plus the service principal) · its `slug='default'` lookup at `:180` ·
the three later grant-adding seeds that replay the same shape:
`131_integration_memory_permissions.sql:36`, `133_workflows_publish_permission.sql:34`,
`178_billing_purchase_permission.sql:60`.

Extract those four seeds' **data** — role rows and their permission arrays — into one
callable keyed on `organization_id` (Decision A: a SQL function). The migration that
introduces it takes **the next free number at build time** (R1; the ladder tops at **178**
today — a measurement, not an instruction) and re-points `default`'s existing seed at the
same callable so there is one statement of the grant set, not two.

**Done when:** a two-org R8 test seeds a second organization through the callable and
asserts org #2's `admin` holds `billing:purchase` and org #2's `owner` holds `*`; and
`default`'s permission set is **byte-identical before and after** the re-point (the
regression the extraction can actually cause).

> ✅ **BUILT 2026-08-19 — `infra/postgres/179_org_provisioning.sql`** (number taken at
> build time; the ladder topped at 178 on `main` **and on every origin branch**, R1).
> `provision_org_roles(p_org_id UUID)` replays all four seeds' data — six roles,
> `20 / 19 / 11 / 2 / 7 / 1` permissions for admin / manager / member / guest /
> agent_service / owner, measured off the live `default` org before extraction.
>
> ⚠️ **The re-point did NOT happen, and that is deliberate.** 130/131/133/178 are
> HISTORY (R6 expand-only; root `CLAUDE.md` §5), and re-pointing `default` at the
> callable would have required either editing four shipped migrations or adding a fifth
> `default`-scoped predicate to 179 — **growing the very ratchet this slice installs**.
> Every organization that exists today is already seeded, so 179 seeds nothing on its
> own. The done-when's *byte-identical* clause is therefore met the stronger way:
> `test_the_callable_reproduces_the_default_orgs_grant_set_exactly` asserts the
> callable's output for a fresh organization **equals `default`'s live set, role for
> role and permission for permission**, on the same replayed ladder. A text diff could
> not have made that claim, and it fails if *either* statement drifts.
>
> **Evidence.** Red first: 25 failed / 9 passed, every subject
> `function provision_organization(unknown, unknown, unknown) does not exist`.
> Green: **34 passed, 0 skips** (`test_org_provisioning.py`, both shells).
> Mutation-proved — widen `manager` with `billing:purchase` → **2 red** (this suite's
> parity test *and* `test_billing_purchase_capability.py`'s role set); drop
> `memory:write_org` from `admin` → **1 red**.
>
> **The `slug = 'default'` ratchet is installed** as `TestTheDefaultSlugRatchet`,
> whitespace-proof (`slug\s*=\s*'default'`), ladder-scoped, baseline **31** — the
> spec's quoted **29** is the *spaced* measuring command; the two extra are 161's
> unspaced comment hits, which is exactly why the fence uses the wider regex. It
> **did not grow**, and it caught the first draft of 179 red for quoting the predicate
> in a comment. It also ratchets DOWN: `test_the_baseline_is_not_stale` fails if a seed
> is legitimately retired without lowering the number in the same commit.
>
> ⚠️ **One existing suite needed an argued edit**: `test_billing_purchase_capability.py`
> asserted *exactly one* migration names `'billing:purchase'`, and there are now
> deliberately two — 178 (the `default` seed, history) and 179 (the callable). The
> helper is split into `_seed_migration()` / `_per_org_callable()`, told apart by the
> `default`-slug predicate the callable cannot have **because the ratchet forbids it**,
> and two tests were ADDED so the second statement is fenced rather than merely
> tolerated (10 → 12 tests, still 0 skips).

**Slice 2 · `_BOOTSTRAP_ORG_SLUG` stops being the only path to an owner. · ✅ BUILT
2026-08-19.**
**Anchors:** `packages/acb_auth/acb_auth/access.py:540` (`_BOOTSTRAP_ORG_SLUG =
"default"`) · `:542-561` `_BOOTSTRAP_OWNER_SQL` · `:572-577` `_HAS_OWNER_SQL` ·
`:580-642` `ensure_owner_bootstrap()`, which binds the literal into **both** queries at
`:614` and `:630`.

⚠️ **MT-1i's fix is why this is now a hard wall rather than a soft one.** Before it,
`_HAS_OWNER_SQL` was unscoped — a lockout. MT-1i correctly scoped it *to a literal*, so
the guard and the insert now agree about which organization they mean and that
organization is always `default`. **Org #2 cannot bootstrap an owner at all.**

⚠️ **Do not "fix" this by making the constant configurable.** **D36.3** is explicit: the
`default` bootstrap org stays as the **fresh-box first-run path** and *"provisioning a
customer organization never routes through that constant."* Pointing it at a customer is
the first-party-bypass shape D36.2 forbids. The provisioning act names its organization
and its owner **explicitly**; `ensure_owner_bootstrap()` keeps its existing job unchanged.

**Done when:** provisioning organization #2 with a named owner leaves that address holding
`owner` **in org #2**; `default`'s owner set is untouched by the operation; and
`ensure_owner_bootstrap()`'s behaviour on a fresh box is unchanged (its existing tests in
`tests/unit/test_owner_bootstrap.py` stay green without edit).

⚠️ **That last clause cannot be satisfied as written, and slice 6 measured why
(2026-08-19).** `test_owner_bootstrap.py` gates on `DATABASE_URL` reachability, which no
CI job sets — **it skips everywhere**, which is exactly how slice 6's 42P10 reached main.
Pointed at a ladder-replayed database it is **4 passed, 1 failed**: its own fixture
executes `INSERT INTO app_user … ON CONFLICT (email) DO NOTHING`, the same idiom migration
162 invalidated, so `test_an_existing_owner_makes_it_a_noop` raises 42P10 in setup. Slice
6 deliberately did **not** touch it (the clause above says *without edit*, and the ticket's
anchors name two production sites); **slice 2 owns the call**: repair the fixture's
conflict target and re-word this clause, or move the suite onto
`TENANT_LADDER_DATABASE_URL` so it stops being a fence that never runs. Evidence the
production half is now right: with slice 6's fix,
`test_empty_deployment_bootstraps_the_owner` **passes** against the real ladder and fails
without it (`ownership_bootstrap_failed`, same error). The `tests/` tree is for that
reason **not** scanned by slice 6's ratchet — see its `_SCANNED_TREES` comment.

> ✅ **BUILT 2026-08-19 — `provision_org_owner(p_org_id, p_email, p_display_name)` in
> migration 179.** Named address, explicit organization, `ON CONFLICT (lower(email))`,
> then the `owner` grant. `_BOOTSTRAP_ORG_SLUG` is **byte-unchanged** and
> `ensure_owner_bootstrap()` is **not touched at all** — no production Python changed in
> this slice. The two paths differ in *where the owner's address comes from*, which is
> why they are two paths and not one parameterised one: the bootstrap reads
> `EXECUTIVE_EMAILS` from the environment, which SQL cannot do; a provisioned
> organization is TOLD its owner, which is a parameter. Neither is a special case of the
> other, so D36.3 is honoured by construction rather than by discipline.
>
> **Fail-closed, and it is the interesting half.** `app_user` is unique on
> `lower(email)` **globally**, so one address cannot be a member of two organizations.
> Adopting a taken address would MOVE a person between tenants (S1-1's write leak) and
> attaching org #2's `owner` role to a row that stays in org #1 would be a cross-tenant
> grant. The function **RAISES**, and because a plpgsql body runs inside the calling
> statement the whole provisioning act rolls back — asserted by
> `test_a_refused_owner_rolls_the_whole_act_back`, because an organization that exists
> with no owner is the 2026-07-30 lockout shape (no owner ⇒ no inviter).
>
> **Done-when, met:** provisioning org #2 with a named owner leaves that address holding
> `owner` **in org #2** (`test_a_named_owner_holds_owner_in_the_provisioned_organization`);
> `default`'s owner set is untouched — asserted against a `default` that *has* an owner,
> because on a fresh ladder `app_user` is empty and "unchanged" would otherwise be
> trivially true.
>
> ---
>
> ### ⚠️ RECORDED DEVIATION (2026-08-19): the "green without edit" clause was overruled, and here is the argument
>
> **What the clause said:** *"its existing tests in `tests/unit/test_owner_bootstrap.py`
> stay green without edit."* **What was done:** the suite was **edited on two axes** —
> the fixture's `ON CONFLICT (email)` repaired to `(lower(email))`, and the whole suite
> re-gated from `DATABASE_URL` onto `TENANT_LADDER_DATABASE_URL`.
>
> **Why, in order of weight:**
>
> 1. **The clause was unsatisfiable as written and this section already said so.** The
>    paragraph above it records the measurement: pointed at a real ladder the suite is
>    4 passed / 1 failed, because its own fixture names an index migration 162 dropped.
>    Reproduced again at the start of this slice, verbatim: `1 failed, 4 passed`,
>    `psycopg.errors.InvalidColumnReference … 42P10`. A clause nothing can satisfy is
>    not a constraint, it is a stop.
> 2. **The clause protects production semantics; the edit touches neither.** "Without
>    edit" exists so nobody quietly re-writes the bootstrap's meaning to make a new
>    feature fit. Not one production line changed in this slice — `access.py` is
>    byte-identical. What changed is a **test fixture's own INSERT** and a **skip
>    condition**. A fixture that raises in setup pins nothing at all, so leaving it
>    broken would have preserved the letter of the clause and none of its purpose.
> 3. **The re-gate is the whole reason slice 6's defect shipped.** `DATABASE_URL` is
>    set by no CI job — pr-check.yml refuses to set it, deliberately and with a comment,
>    because it would arm `test_tenant_coverage.py`'s two DB-gated tests. So these five
>    tests skipped in **every run ever made**, while the statement beneath them raised
>    42P10 on every call and `ensure_owner_bootstrap()`'s catch-all swallowed it.
>    Repairing the fixture without re-gating would have produced a suite that is correct
>    and still never runs.
>
> **The deviation is proved, not asserted.** Mutation: re-arm slice 6's defect on
> `_BOOTSTRAP_OWNER_SQL` alone → this suite goes **2 failed / 6 passed**. Before the
> re-gate the same mutation produced **5 skipped, 0 failed**. That difference is the
> entire value of the edit.
>
> **Scope held.** `ensure_owner_bootstrap()` untouched; `_BOOTSTRAP_ORG_SLUG` untouched
> and now *fenced* by `TestD363TheBootstrapConstantIsNotRepointed` (mutation: re-point
> it at `os.environ.get("BOOTSTRAP_ORG", "acme")` → **6 red** across the two suites).
> The suite is added to `pr-check.yml`'s R8 skip guard on the existing
> `TENANT_LADDER_DATABASE_URL unset` grep line, and `test_this_suite_is_named_in_the_ci_skip_guard`
> makes its own removal fail. Result: **8 passed, 0 skips**, from a suite that had never
> once executed.
>
> **Two incidental findings, both surfaced only because it now runs:**
> * `access.py:283`'s `_tables_missing` is a **process-global latch** — the first
>   resolve that fails with *"does not exist"* sets it permanently and every later call
>   short-circuits to `_degraded()` without touching a database. Any earlier module in a
>   directory run arms it, and this suite then reports a successfully-bootstrapped owner
>   as `is_active=False`. Reset in the fixture (same class as the old
>   `acb_common.db._ENGINE` reset). **It is a production latch with no reset path
>   either — a transient "relation does not exist" during a migration window disables
>   access resolution for the life of the process. Flagged for the board; NOT fixed
>   here.**
> * On Windows, psycopg's async mode refuses `ProactorEventLoop`, the loop
>   `pytest-asyncio` hands every test there. The suite builds its async engine on
>   `postgresql+asyncpg`, mirroring `acb_common.db.async_database_url():59-60`, which is
>   what production already does — fidelity, not a workaround.

**Slice 3 · `organization` + `tenant_placement`, one act, one transaction. · ✅ BUILT
2026-08-19.**
**Anchors:** `130_org_access_control.sql:36-52` (`organization` DDL + the `default` seed) ·
`159_control_plane.sql:40-65` (`tenant_placement` DDL + its seed) ·
`packages/acb_common/acb_common/placement.py` (the resolver; `_PLACEMENT_SQL:36-40`,
`_SOLE_PLACEMENT_SQL:46-50`).

Idempotent **on the slug**, for the reason `customer_console/main.py:588-591` already
argues: the natural key is what a retrying signup form resends. Both rows in one
transaction — an `organization` without a `tenant_placement` is a tenant whose data plane
is unresolvable, and `placement.resolve_placement` refuses rather than guessing.

**Done when:** re-running provisioning for the same slug yields **one** organization and
**one** placement; and the set-difference *organizations without a placement* is **empty**
in the tenant plane (fence:
`test_every_organization_has_a_placement`).

> ✅ **BUILT 2026-08-19 — `provision_organization(p_slug, p_display_name, p_owner_email,
> p_domain, p_tier, p_target, p_region)` in migration 179.** One statement:
> `organization` ON CONFLICT (slug) DO NOTHING → `tenant_placement` ON CONFLICT
> (organization_id) DO NOTHING → `provision_org_roles` → optionally
> `provision_org_owner`. **Atomicity comes from the shape, not from a convention**: a
> plpgsql body executes inside the calling statement, so there is no reachable state in
> which the organization exists and the placement does not.
>
> **Where it lives, and why not Python.** In the migration, beside the role seed —
> D43-A already ruled that the seeding doctrine is *one SQL function*, and a Python
> re-statement of the same act would be the second doctrine 178's comment refuses. It
> also adds **no** database-connection site (R5) and needs no new engine. **Slice 4
> brings the caller**: a seam function invoking `SELECT provision_organization(…)`
> through the existing `get_db()` idiom, which is Decision B's pull direction arriving
> from the box side. **Until then this is a callable nothing calls** — acceptable
> within one branch of its caller by the auditor's own sequencing, and stated here so
> nobody reads it as an oversight.
>
> **Done-when, met:** re-running for the same slug returns the same id and leaves
> **one** organization and **one** placement; `test_every_organization_has_a_placement`
> is a genuine set-difference over the whole tenant plane, not a read-back of the row
> just written — so a future provisioning path that skips the placement fails there
> even though its own tests pass. `test_a_freshly_provisioned_org_has_the_five_system_roles_and_an_owner`
> is kept at the spec's own name and asserts the **SET** of six slugs, per the fences
> table's warning about the name.
>
> **Mutation-proved:** delete the `tenant_placement` insert → **7 red**; drop the
> cross-tenant refusal → **2 red**; name the bare `email` column in the owner upsert →
> **13 red** (42P10 at PLAN time, exactly as slice 6 measured).
>
> Also fenced: an explicit `bridge`/`eu-west-1` placement is honoured (§1.5's priced
> tiers stay expressible), an unknown tier is refused by 159's own CHECK rather than
> defaulted, a blank slug is refused, and an organization may be provisioned **without**
> an owner — roles and placement still land, because an org with roles and no owner is
> recoverable and one with neither is not.

**Slice 4 · The Console↔tenant seam — and the Console-side half that was missing.
· ✅ OPERATOR ARM BUILT 2026-08-19** (the deployment-key arm is CP-2c's, D46.6
item 2, still §6-gated).
**Anchors** *(as measured before the build)*: `customer_console/main.py:584-664`
(`POST /orgs/provision`: writes
`organization`, `user_identity`, seats, `org_membership`, `org_subscription`,
`provisioning_run`, an audit row — and **no `org_placement` row**) ·
`customer_console/store.py:618-632`, whose resolve query **inner-joins**
`org_placement` at `:625` with `WHERE … p.deployment_id = :dep` ·
`infra/customer_console/001_customer_console.sql:96-106` (`org_placement` DDL) ·
`customer_console/store.py:511-542` `issue_deployment_key` (capabilities default
`'{resolve}'`; `006_deployment_key.sql:56`).

⚠️ **The defect this slice closed, kept in the past tense.** Measured 2026-08-19 before
the build: `org_placement` had **no production writer anywhere** — the only `INSERT` was
a test fixture (`tests/unit/test_customer_console_resolve.py`), whose docstring named the
gap and correctly declared fixing it a non-goal *of CP-2b*. The consequence composed with
the inner join: **an organization created through `/orgs/provision` could never be
resolved by any deployment key** — CP-2b's resolve arm returned nothing for it, forever,
and failed closed in a way that reads as "the Console is down". ✅ The route now writes
the row; the end-to-end clause is fenced by
`test_customer_console_resolve.py::TestProvisioningIsWhatPlacesAnOrg`.

⚠️ **Flag the capability-set question; do not widen it silently.** If Decision B's pull
direction needs the box to ask the Console for anything beyond `resolve`, the
`{resolve}`-only capability set (`006_deployment_key.sql:53-56`, argued as *"a capability
set of one is the only one that…"*) gains a sibling — which is a **credential-scope
change** and belongs to the owner (§6 gate (f)), not to this ticket's implementer.

⚠️ **AUDITED NO-GO 2026-08-19 (seven blockers, all documentation) → REMEDIATED the same
day.** The decisive finding: 4a demanded an `org_placement` row while nothing said where
`deployment_id` comes from — `ProvisionRequest` (`main.py:120-129`) carries no deployment
field, the route's `Operator` credential has no deployment identity, **and the Console
ladder seeds no `deployment` row at all** — so the clause was unconstructible without an
unmade design decision; 4b was self-declaredly vacuous; the tenant-side caller slice 3's
box promises had no clause; and CP-2c (minted the same day) proposed the exact auth
answer the blockquote above forbids. The remediation below adjudicates all of it as
**D46.6** (`agent-proposed, owner may overrule`).

**The adjudication (D46.6) — who names the deployment, in two arms so neither ticket
waits on the other:**
1. **Slice 4 ships the OPERATOR arm, and the operator names the deployment explicitly**:
   `ProvisionRequest` gains a `deployment_label` resolving to
   `deployment.label` (`001_customer_console.sql:82-94`, `UNIQUE`) — *required at
   the model as slice 4 shipped it; since CP-2c slice 1 (2026-08-19, item 2's
   amendment landed) the model is `str | None` and the operator arm REQUIRES it
   in the handler*. Missing field →
   ~~422~~ **400 since CP-2c slice 1**; unknown label → **404 naming the label, per the operator idiom `_org_id`
   already ships** (`main.py:449-455` — was `:436-442` pre-build; this PR's own
   `ProvisionRequest` insert shifted it +13, caught at review; the operator credential is cross-org by design,
   so naming what it asked about is not an existence oracle — `customer_console.md`
   CP-9 clause 7 is the authority, and it calls this "the contrast, not the
   precedent"; *ruled at the 2026-08-19 re-audit, which found the earlier "collapsed
   refusal" wording untestable and contradicting that clause*). This is a
   breaking change to `POST /orgs/provision` and is FREE today: the Console is deployed
   nowhere and the route has no production caller — **the six CP-2a-era suites that
   POST `/orgs/provision` (12 call sites, auditor-measured) are updated in the same
   PR**: `test_customer_console_api.py` · `test_customer_console_key_auth.py` ·
   `test_customer_console_lifecycle.py` · `test_customer_console_payments.py` ·
   `test_customer_console_resolve.py` · `test_customer_console_router.py`. **No
   capability change, no §6 act — agent-safe end to end.**
2. **The DEPLOYMENT-KEY arm (the key names itself) is CP-2c's**, arrives with CP-2c, and
   stays gated on §6 (f)/(h) — the `{resolve, provision}` growth remains CP-2c's
   *proposal*, decided by the owner at issuance, never by an implementer. Slice 4 is
   decoupled from that open question by construction.
   ⚠️ **AMENDED IN WRITING 2026-08-19 (CP-2c's audit, blocker B1) — the model
   consequence:** when CP-2c slice 1 lands, `ProvisionRequest.deployment_label`
   becomes `str | None = None` on the `ResolveRequest.org_slug` precedent (both
   arms' rules move to the handler) and **the operator arm's missing-label answer
   moves 422 → 400**. Slice 4's mutation-proved no-inference fence
   (`test_customer_console_lifecycle.py:332-374`) is amended IN THAT PR, red-first —
   it pins 400 and must re-prove that the `count(*)=1` mutation still goes red
   through the handler path. ✅ **THAT PR LANDED 2026-08-19 (CP-2c slice 1,
   branch `ws-31-cp2c-slice1`)**: the model is `str | None`, the operator
   missing-label answer is **400**, the fence was amended red-first
   (`assert 422 == 400` shown failing), and the handler-side sole-deployment
   mutation was added and killed alongside the lookup-side one — the two cover
   disjoint paths (verifier-measured). Recorded here so slice 4's box and CP-2c
   cannot disagree about who changed the fence and when — and now, that it
   happened.
3. **The sole-deployment heuristic is FORBIDDEN by name**: no arm may infer the
   deployment from `count(deployment)=1` — that would be a fourth copy of the sole-org
   guess this same ticket retires (`key_store.py:114-139` / `model_config.py:40-48` /
   `placement.py:46-50`), and it silently mis-places every org the day a second box
   exists. Fence: with exactly one deployment seeded, a request WITHOUT
   `deployment_label` is still refused — **400 since CP-2c slice 1** (422 as
   slice 4 shipped it; the fence moved with the item-2 amendment, red-first).
4. **Provisioning never MOVES a placement.** The write is
   `ON CONFLICT (organization_id) DO NOTHING`; re-run with the SAME label → no-op (one
   row, `moved_at` untouched); re-run naming a DIFFERENT label → **409 refusal** — a
   move is a separate operator act with the move-on-conflict semantics the CP-2b fixture
   sketches (`test_customer_console_resolve.py` — the `_place` helper, `:249-271` post-build;
   was `:232-242` when ruled), owned by a future placement
   ticket, not by provisioning. `database_target` is left NULL by slice 4 — stated, not
   forgotten; a field arrives when a real need names it.

**Done when** *(split 2026-08-19 at dispatch confirmation — "both real databases" named
a two-ladder harness that does not exist; no module loads both ladders, and
`test_deployment_resolve_cache.py:25-30`'s fixture sets `DATABASE_URL` in-process, which
is exactly the collision that makes a two-engine harness go green against the wrong
database; re-split at remediation into three clauses)*, **single-DB halves plus a
reserved smoke:**
- **(4a, Console DB):** against the Console ladder alone, with a `deployment` row seeded
  by the fixture (the ladder seeds none — zero-deployment behaviour IS one of the
  cases): `POST /orgs/provision` with `deployment_label` writes **exactly one**
  `org_placement` row for the named deployment with `database_target` NULL; re-run same
  label → still one row, `moved_at` untouched; different label → 409; missing field →
  ~~422~~ **400 since CP-2c slice 1** even with exactly one deployment seeded
  (adjudication item 3's fence, which moved with the item-2 amendment); unknown
  label → **404 naming the label** (adjudication item 1 — the operator idiom); and
  `POST /registry/resolve`'s deployment arm, called
  with that deployment's key, **returns the provisioned org** (red today: the fixture
  at `test_customer_console_resolve.py:232` is the only writer). Every clause R8,
  red-first.
- **(4b, tenant DB) — the seam caller slice 3 promised:** a seam function
  `provision_local_organization(slug, display_name, owner_email=None, …)` (proposed
  home: beside the ONE engine in `acb_common`, final path at build time; **no new
  engine site, R5(b)**) invokes `SELECT provision_organization(…)` through the shared
  engine seam via **`get_session_factory()`** *(amended at verification — this clause
  was authored as "`get_db()`" while `test_db_engine_seam.py`'s
  `H2_BASELINE_ELSEWHERE = 111` ratchet may only go DOWN and `get_db`'s own docstring
  refuses new sites; the shipped idiom is the same engine and pool, the one
  `console_resolve`/`access` use for pre-tenant acts, and `tenant_session()` is
  unavailable by construction — this function CREATES the tenant. The build box below
  carries the full argument.)*. R8 on the tenant ladder **through its own call path** (delegating
  assertions to 179's suite would be the vacuity the audit killed): twice-called → one
  org, one placement, six roles, one owner; blank slug surfaces 179's refusal. Its
  first production caller is CP-2c's route — until then the "callable nothing calls"
  note graduates from 179 to this function, honestly, in this header.
- **(4c, tenant DB) — the projection now has a PROVISIONED row to land on:** extend
  `test_deployment_resolve_cache.py` with the provisioned-slug case — after
  `provision_local_organization('x')`, a fresh resolve answer for x's member takes the
  WRITE path: projection rows written AND the `console_resolve.unprovisioned_org`
  skip-warn (`console_resolve.py:606-626`) does NOT fire. ⚠️ **There is no projection
  bug to hunt** (re-audit finding): the write path is already fenced green — the
  suite's own `_provision_org` (`test_deployment_resolve_cache.py:271-284`) hand-seeds
  a local row, and the skip-warn has a passing negative fence at `:1311`. 4c's
  non-vacuous claim is that **the seam function's row is discoverable via the slug
  join key** — it fails today only because `provision_local_organization` does not
  exist, and that is the clause.
- **True end-to-end (both databases, live HTTP)** is reserved for the deployment smoke at
  execution time (🔴 the §6 gate). If an implementer wants the in-process bridge anyway,
  the harness is `httpx.ASGITransport` onto `customer_console.main.app` in a NEW module
  that loads both ladders explicitly — named here so nobody invents it as an acceptance
  criterion; it is optional hardening, not the done-when.

> ✅ **OPERATOR ARM BUILT 2026-08-19** — D46.6 arm 1, agent-safe end to end. The
> deployment-key arm (D46.6 arm 2) is CP-2c's and remains §6 (f)/(h)-gated; nothing here
> touched a capability set, a `database_target` value, a placement *move*, the two-ladder
> bridge, or any live execution.
>
> **What shipped.**
> - **4a, Console DB.** `ProvisionRequest` gains a **required** `deployment_label`
>   (`customer_console/main.py`, `ProvisionRequest`), and `POST /orgs/provision` resolves
>   it FIRST — before anything is written — then writes exactly one `org_placement` row
>   with `database_target` NULL. Three new SQL statements in `customer_console/store.py`,
>   separate because the decision between them is a refusal: `deployment_by_label`
>   (`None` ⇒ the caller's 404), `current_placement` (read-before-write, which is what
>   makes 409 possible against a `DO NOTHING` write) and `place_organization`
>   (`ON CONFLICT (organization_id) DO NOTHING` — provisioning places, it never moves).
>   The audit row and `provisioning_run.steps_done` carry the new step **for symmetry
>   with CP-2a's array — and nothing more: the adversarial review measured that NOTHING
>   in the tree reads `steps_done` (the only occurrences are the one INSERT and two test
>   assertions), the row is written unconditionally at the END of an already-successful
>   transaction, so a partial array is unreachable by construction and no resume
>   mechanism exists.** The resume path is CP-2a's owed step-kill clause, not anything
>   this slice shipped — an earlier draft of this box claimed otherwise and the claim
>   was struck at review.
> - **4b, tenant DB.** `packages/acb_common/acb_common/provisioning.py` ·
>   `provision_local_organization(slug, display_name=None, owner_email=None, *, domain,
>   tier, target, region)` — one statement, `SELECT provision_organization(…)` with every
>   argument explicitly CAST, beside the ONE engine and next to `placement.py`, which
>   READS the row it writes. No client-side validation: 179's refusals reach the caller
>   unchanged.
> - **4c, tenant DB.** `test_deployment_resolve_cache.py::
>   test_a_slug_PROVISIONED_BY_THE_SEAM_takes_the_write_path` — the seam's row is
>   discoverable through §6(k)'s slug join key, so the projection takes the WRITE path and
>   `console_resolve.unprovisioned_org` does not fire.
> - **The six CP-2a-era suites** were updated for the required field in the same PR
>   (12 call sites). The `deployment` row every one of them now needs is seeded by one
>   shared helper, `tests/unit/_customer_console_ladder.py::ensure_deployment` — six
>   copies of that INSERT is the exact failure that module exists to prevent.
>
> ⚠️ **One deviation from the dispatch contract, argued rather than smuggled.** The
> contract named `get_db()` as the acquisition idiom (`acb_common/db.py:171`). Built that
> way, `tests/unit/test_db_engine_seam.py::test_get_db_sites_elsewhere_only_ratchet_down`
> went **red at 112 against a frozen baseline of 111** — that fence exists precisely to
> stop new unbound sites, and `get_db`'s own docstring says it "is not a second sanctioned
> way in". The seam therefore takes `get_session_factory()`, which is the SAME shared
> engine and pool (R5(b) satisfied, no new engine site) and the SAME idiom
> `acb_auth.console_resolve` and `acb_auth.access` already use for the one other act that
> runs before any tenant can be bound. `tenant_session()` is not available to this
> function by construction: it *creates* the tenant, so there is nothing to bind until
> after the statement it would have wrapped.
>
> **Evidence.**
> - **Red first, 4a:** with the pre-change `main.py`/`store.py` and the new tests,
>   **8 failed / 79 passed** (`test_customer_console_lifecycle.py` +
>   `test_customer_console_resolve.py`) — including `200 == 404`, `200 == 422`, and
>   `assert [] == ['born-…']` for the resolve clause.
> - **Red first, 4b/4c:** with `acb_common/provisioning.py` removed, **5 failed / 84
>   passed**, every subject `ModuleNotFoundError: No module named
>   'acb_common.provisioning'` — the honest form of "it fails today only because the
>   function does not exist".
> - **Green:** `398 passed, 0 skipped` across the eight suites the done-when names, both
>   DSNs, real Postgres 16 (baseline before the change: 386).
> - **Mutation-proved**, one revert at a time: remove the placement write → **5 red**;
>   remove the 409 refusal → **1 red** (`test_re_provisioning_a_different_label_…`);
>   `DO NOTHING` → `DO UPDATE` (provisioning moves) → **1 red**
>   (`test_re_provisioning_the_same_label_leaves_the_row_untouched`, on `moved_at`);
>   `deployment_label` made optional → **1 red**; the seam stops committing → **4 red**;
>   the seam drops the owner argument → **2 red**.
> - ⚠️ **Mutation found a real hole in the first version of the item-3 fence, and it is
>   the reason that test now has two clauses.** Substituting the FORBIDDEN
>   `count(deployment) = 1` fallback for the 404 left **every test green**: the 422 case
>   never reaches the handler, and every other fence runs against a database holding many
>   deployments, where the heuristic refuses anyway.
>   `test_nothing_infers_the_deployment_from_there_being_exactly_one` now asserts BOTH
>   arms in the emptied, one-deployment world — missing field ⇒ 422, unknown label ⇒ 404 —
>   and the same mutation dies there. *(Historical record of what slice 4 shipped;
>   the missing-field arm reads **400** since CP-2c slice 1's item-2 amendment,
>   2026-08-19, amended red-first in that PR.)*

**Slice 5 · `key_store` / `model_config` tenant threading. · ◐ RATCHET ROUNDS 1 + 2 BUILT
2026-08-19 — the ratchet stands at `4 + 1`, the owner-gated FLOOR of the set it measures
(walker-visible call sites; `key_store.py`'s own three `self.` calls are an uncounted
remainder of the same class — round-2 box).**
**Anchors + measurement (2026-08-19):**

```bash
grep -rn "get_key_store()" --include=*.py apps packages \
  | grep -v "tests/" | grep -v "def get_key_store" | wc -l      # 38  (across 20 files)
grep -rnE "\b(load_blob|save_blob)\(" --include=*.py apps packages \
  | grep -v "tests/" | grep -v "acb_llm/model_config.py" | wc -l # 10
```

Three copies of the same sole-org heuristic: `acb_llm/key_store.py:114-139`
(`_resolve_org`, the `count(*) = 1` query at `:136-139`) · `acb_llm/model_config.py:40-48`
· `acb_common/placement.py:46-50` — **the last has zero production callers** (measured:
nothing in `apps/` or `packages/` imports the module), so it is a shape to keep consistent,
not a live path to convert.

🚫 **The fix is NEVER weakening `_resolve_org`'s fail-closed contract.** `work_plan.md` §3
records it by name, in **D33's finding 3** (`:880` on 2026-08-19 — the *finding number*
travels, the line does not): *"Not every old convenience is a defect, and one must NOT be
'fixed'… Recorded so a future agent does not helpfully replace it with a silent default."* The two
tests that pin it are `tests/unit/test_mt0d_per_org_credentials.py:163`
(`test_untenanted_read_returns_nothing_once_a_second_org_exists`) and `:176`
(`test_untenanted_write_raises_once_a_second_org_exists`). **The work is threading the
tenant through the 38 + 10 call sites**, so that the moment a second org exists the sites
are already supplying one and the fail-closed arm is never reached in production.

**Done when:** those two pinned tests are green **unedited**, and a second organization's
provider key and model config read correctly through the threaded sites while the first
organization's reads are unchanged. **And a completion RATCHET holds (added 2026-08-19 at
dispatch confirmation — without it this clause is satisfiable by threading three sites):**
the pattern is `tests/unit/test_db_engine_seam.py:310`'s H2 bank-your-progress mechanism
(`_GET_DB_CALL` + `H2_EXEMPT_FILES` + `H2_CONVERTED_PACKAGES`) — pin the starting counts
**38 (`get_key_store()`) + 10 (`load_blob`/`save_blob`)** and assert they only go DOWN;
a converted package is pinned at zero. Slice 5 may land across several PRs on this
ratchet's cadence; each PR banks its progress.

> ### ◐ RATCHET ROUND 1 — BUILT 2026-08-19 · `tests/unit/test_credential_tenant_threading.py`
>
> **Banked: `11 + 10` → `9 + 1`.** Not `38 + 10` → see the correction below, which is the
> most important thing this round produced.
>
> ⚠️ **CORRECTION — "38 call sites fail closed" over-states the blast radius by ~3.5x,
> and the ratchet makes that auditable.** The spec's grep counts `get_key_store()`
> *lines*; the fail-closed contract lives in `_resolve_org`, which only the
> **tenant-scoped methods** reach. Parsed (AST, not grep) across `apps/` + `packages/`
> minus `tests/`, the 38 lines yield **48** store-method calls:
>
> | Method | Calls | Resolves a tenant? |
> |---|---|---|
> | `decrypt` | 23 | **No** — Fernet over the deployment's single `ACB_MASTER_KEY` |
> | `encrypt` | 10 | **No** — same |
> | `put` | 6 | Yes |
> | `get_all` / `get_by_type` | 2 / 2 | Yes |
> | `delete` | 1 | Yes |
> | `configure_litellm` / `configure_integrations` | 2 / 1 | process-global startup path |
> | `_execute` | 1 | raw SQL; the statement carries its own scope |
>
> The 33 `encrypt`/`decrypt` calls seal `email_accounts.credentials_encrypted` and the
> WhatsApp bridge's credentials — a second organization does not perturb them at all.
> **The real surface is 11 `key_store` + 10 `model_config` = 21 sites**, and *that* is
> what the ratchet counts. The spec's two greps are kept as **ceilings** (38 / 10), since
> a correctly-threaded site still contains both strings and could never make them fall —
> a fence that can only stay flat is not a ratchet, so the file pins both kinds and says
> which is which.
>
> **Converted this round — the `model_config` subgraph on the LLM/model surface**
> (9 of the 10 blob sites, plus the provider-key write on the same request):
>
> * `gateway/routes/settings.py` → **0** untenanted (was 6 blob + 2 store):
>   `_load_tier_overrides`, `_save_tier_override`, `_load_catalogue`, `_save_catalogue`
>   (`tier_overrides` + `enabled_models`), and `_sync_key_to_store` behind
>   `POST /settings/llm/key`. ⚠️ **That last one was a live loss, not a latent one**:
>   `put()` raises once org #2 exists and the raise is swallowed by the surrounding
>   `except Exception: pass`, so org #2's admin saving a provider key lost it silently.
> * `gateway/routes/agent.py` → **0** untenanted (was 3 blob): `_load_agent_aliases` /
>   `_set_agent_alias`, read by the Agents page and by `observability.roster`.
>
> The tenant comes from `acb_common.db.current_tenant()` — the ContextVar
> `acb_auth.deps._with_resolved_access` fills from the authenticated session — reached
> through the gateway's existing `gateway.db` re-export, so no second seam. Every route
> in both modules is under the app-wide `require_authenticated`, whose own dependency is
> `get_current_user`, so the binding is in context. `None` outside a request is
> **deliberate and not a fallback**: it hands `_resolve_org` exactly today's argument, so
> the sole-org resolution and its fail-closed arm are byte-unchanged.
>
> ### 🔴 THE FINDING: the LIVE completion path is H4, and this round did NOT thread it
>
> The dispatch asked for the live LLM path first. Traced, it cannot be threaded without an
> owner act, and guessing would have been the R5/R11 violation:
>
> | Site | Why no tenant is reachable |
> |---|---|
> | `acb_llm/client.py::_ensure_keys_loaded` (2 store calls) | a **once-per-process latch** whose whole output is process-global: `configure_litellm()` assigns `litellm.<provider>_api_key` and `os.environ`. Calling it per org would make the LAST org's key the one every caller sends. |
> | `routes/v1_compat.py::_handle_chat_completions` (its caller) | authed by `require_llm_api_auth` — the deployment-wide `LITELLM_MASTER_KEY` **every agent holds**. That Bearer resolves through `get_current_user`'s branch 1b to `system:internal`, which never reaches `_with_resolved_access`, so `current_tenant()` is `None`. `X-CC-Agent`/the body are request input — R11 forbids both. |
> | `acb_llm/client.py::_init_tier_models` (1 blob call) | runs at **import** time and fills the process-global `_TIER_MODEL`. |
> | `gateway/main.py` lifespan (2 store calls) | startup: no request, no session, no tenant. |
> | `routes/settings.py::_inject_env_into_litellm`'s `os.environ` half | same class, and it is a **cross-tenant write**: org #2 saving its key overwrites the process-wide env var for everyone. Marked, not fixed. |
>
> **What a real fix needs:** a **tenant-scoped API key** (`user_management_contract.md`
> R11's second admitted identity source) plus per-request provider credentials instead of
> per-process ones. Widening `LITELLM_MASTER_KEY`'s scope is a **credential-scope change —
> `work_plan.md` §6 gate (f), the owner's act**, and it is the same shape slice 4 flags for
> the Console's `{resolve}` capability set. Every site above carries an `H4:` comment in
> place naming its reason, and each is pinned at its **EXACT** remaining count so the next
> agent cannot make it "converted" by threading a guessed org either.
>
> **Fences (R7), all in `tests/unit/test_credential_tenant_threading.py` — 22 tests,
> 0 skips.** `TestTheCredentialSurfaceDoesNotGrow` (the spec's two greps as ceilings) ·
> `TestTheUntenantedRatchet` (the AST counts, `<=` plus a `test_the_baselines_are_not_stale`
> that forces the number DOWN in the same commit) · `test_converted_files_stay_converted`
> (parametric, zero) · `test_h4_*_sites_hold_their_exact_count` (exact in **both**
> directions) · `test_every_h4_site_carries_its_marker_in_place` ·
> `TestTheFailClosedContractIsNotRepaired` (pins the `count(*) = 1` predicate in **both**
> copies by content and refuses a `slug = 'default'` fallback by name — D33 finding 3) ·
> the R8 two-org half · `test_this_suite_is_named_in_the_ci_skip_guard`. Named on
> `pr-check.yml`'s existing `TENANT_LADDER_DATABASE_URL unset` grep line.
>
> **Mutation-proved (each reverted after measuring):**
>
> | Mutation | Red |
> |---|---|
> | un-thread one banked site (`_load_catalogue`) | **3** |
> | add a new untenanted `load_blob` call | **5** at the implementer's site; the count is SITE-DEPENDENT (verifier measured **4** planting in `agent.py`) — the property, not the number, is the fence |
> | thread a *guessed* org at an H4 site | **5** at `_init_tier_models` (incl. the exact-count fence); site-dependent (verifier: **2** at `client.py`'s blob site) — every site tried went red |
> | replace `count(*) = 1` with `WHERE slug = 'default'` in both copies | **7** |
> | `key_store._resolve_org` ignores the org it is given | **6** (2 here + 4 in the mt0d suite) |
> | `model_config._resolve_org` ignores the org it is given | **2** |
> | delete the pytest argument from `pr-check.yml` | **1** |
>
> ⚠️ **Two of this suite's own tests were found vacuous by mutation and strengthened.**
> The `slug = 'default'` mutation left both untenanted-read assertions GREEN, because the
> fallback resolved to an organization that happened to hold nothing. They now seed the
> **operator's** organization first, which is MT-0d's stated leak in one line ("serve the
> operator's keys to a customer"); with that, the same mutation turns them red. The
> CI-guard fence had the same shape — a substring search for the filename matched the
> *comment* above the step, so deleting the pytest argument stayed green; it now matches
> the argument line.
>
> **R8, and the transport is the only thing faked.** The two-org half seeds both
> organizations through **migration 179's `provision_organization`** (slice 3 — the way
> the product creates them, not a hand-written INSERT that could disagree), gives each a
> key through the store's **own `put()`**, and asserts A reads A's and B reads B's while an
> untenanted read returns `""` and an untenanted write raises. `key_store._execute` and
> `model_config`'s `psycopg.connect` are redirected onto the test's connection; **the SQL
> is the production module's, byte for byte**, so Postgres parses and plans the
> `count(*) = 1` sub-select and the `(organization_id, provider)` conflict arbiter — the
> class of thing slice 6 proved a fake cannot answer for. Rows live inside the test's
> transaction and are rolled back.
>
> **The pinned pair is green and UNEDITED** (`test_mt0d_per_org_credentials.py` — 8
> passed). ⚠️ Worth recording: that suite's `_FakeStore` intercepts the organization query
> **by prefix**, so it is *blind* to the `slug = 'default'` softening — it stayed green
> under that mutation. The tripwire is necessary and not sufficient, which is why this
> round added the source-shape fence and the live two-org read.
>
> **Remaining for round 2+ (9 + 1):** `routes/integrations.py` (5 — `get_by_type` ×2,
> `put` ×2, `delete` ×1; request-scoped and convertible exactly like `settings.py`, simply
> a different subgraph and deliberately out of this round's scope) and the 4 + 1 H4 sites
> above, which are blocked on the owner act, not on effort. **Round 2 took the first half
> — see the box below.**

> ### ◐ RATCHET ROUND 2 — BUILT 2026-08-19 · the ratchet reaches the floor of the set it measures
>
> **Banked: `9 + 1` → `4 + 1`,** which now EQUALS `sum(H4_KEY_STORE_SITES)` +
> `sum(H4_BLOB_SITES)`. **Every *walker-visible* untenanted credential call in `apps/` +
> `packages/` is an owner-gated one named in the H4 tables with its reason**, so slice 5
> cannot move further on that set without the credential-scope act (`work_plan.md` §6
> gate (f)).
>
> ⚠️ **Scope of that claim, corrected in repair round 3 — it is the floor of the
> WALKER-VISIBLE set, not of the tree.** The ratchet counts a call whose receiver is
> `get_key_store()` or a name bound from it (`_is_store_expr`,
> `test_credential_tenant_threading.py`). Three real untenanted tenant-scoped calls sit
> **inside `packages/acb_llm/acb_llm/key_store.py` itself**, on `self`, and are invisible
> to it — verified against the file 2026-08-19:
>
> | Site | Call | Consequence once org #2 exists |
> |---|---|---|
> | `key_store.py:420` (`configure_integrations`) | `self.get(provider)` | `_resolve_org` returns `""` → the read yields `""`, so no stored integration credential reaches `os.environ` |
> | `key_store.py:433` (`configure_integrations`) | `self.put(...)` — the env→store auto-migration | **RAISES** (`put` fails closed on a write), aborting the loop mid-way; its one caller wraps the whole block in `try/except` → `gateway.key_store_skipped`, so every integration after the raising one is silently left unconfigured rather than the process failing |
> | `key_store.py:472` (`configure_litellm`) | `self.get_all()` | returns `{}` → no provider key is loaded into `litellm`'s module config |
>
> **Same class as H4, not a separate ticket.** The two methods holding them are called
> from exactly the places the H4 tables already name: `configure_integrations` only from
> `gateway/main.py`'s lifespan (`:222`, in the same `try` block as the H4-counted
> `get_all` at `:209` and `put` at `:220`), `configure_litellm` from that same lifespan
> (`:221`) and from `acb_llm.client._ensure_keys_loaded` (`:329`) — and both destinations
> (`litellm.<provider>_api_key`, `os.environ`) are process-global, so threading these
> three travels with the same owner-gated credential-scope act (§6 gate (f)) rather than
> being separately dispatchable. They are recorded here and in the terminal fence's
> docstring; they are **not** in the H4 tables, because those tables are keyed by what
> the walker counts and adding an uncountable entry would make the exact-count fences
> unsatisfiable.
>
> ⚠️ **Second known limitation of the same walker, named not fixed: an accessor
> WRAPPER.** `routes/tasks/core.py:326` defines `_key_store()` (`return get_key_store()`);
> six modules import it and call it 15 times. Every one is `encrypt`/`decrypt` today —
> master-key Fernet with no tenant dimension, so nothing is being missed now — but a
> credential *read* grown behind that wrapper would not be seen by any count in the
> suite, because the receiver is a call to `_key_store`, not to `get_key_store`.
>
> **Converted this round — the Integration Registry credential surface.**
> `gateway/routes/integrations.py` → **0** untenanted (was 5), pinned in
> `CONVERTED_FILES`:
>
> | Route | Call | Was |
> |---|---|---|
> | `GET /integrations/status` | `get_by_type("integration")` | rendered *"no keys stored"* for org #2 — the untenanted read returns `{}` |
> | `GET /integrations/keys` | `get_by_type("integration")` | same: `{"services": {}, "total_keys": 0}` |
> | `POST /integrations/configure` | `put(...)` | the raise was **swallowed** into a `integrations.db_write_failed` warning, and the route still returned its `written` list — a silent loss, the same shape as round 1's `_sync_key_to_store` |
> | `PUT /integrations/keys` | `put(...)` | the raise surfaced as an unhandled **500** |
> | `DELETE /integrations/keys` | `delete(provider)` | ⚠️ the worst of the five: `delete` **logs `key_store.delete_no_tenant` and returns**, so the route answered `{"ok": true, "deleted": true}` having deleted nothing |
>
> Idiom identical to round 1 — `from gateway.db import current_tenant`,
> `organization_id=current_tenant()`, read on the request's own context, never from
> `req.service` or any other request input (R11). All five routes are under the app-wide
> `require_authenticated`; this module's only `PUBLIC_ROUTES` entry is the OAuth callback,
> which touches none of them.
>
> 🚫 **Refused and marked in place, not repaired: the `os.environ[...]` /
> `_upsert_env_var(...)` halves** of `configure` / `put` / `delete`. A process env var and
> a single `.env` file hold one value for the whole deployment, so org #2 saving
> `ZOHO_CLIENT_ID` overwrites org #1's for every caller and every agent reading it through
> `get_settings()`; the delete's `os.environ.pop` unsets it for every organization while
> removing only the caller's row. Same class as
> `settings.py::_inject_env_into_litellm` — it needs per-request provider credentials, the
> same owner act the H4 sites wait on. Consequence worth stating: `/integrations/status`'s
> `configured` / `missing_keys` / `env-file` columns stay **deployment-wide**; only the
> `db_keys` / `encrypted-db` half is per organization.
>
> **New fences (R7), both red-first:** `test_every_remaining_untenanted_site_is_an_owner_gated_one`
> — set equality (not just a total, since two totals can agree while naming different
> sites) between the measured untenanted files and the H4 tables · and the R8
> `test_integration_credentials_are_org_scoped_on_listing_and_delete`, which exercises the
> two store methods the `get`/`get_all` pair did not: `get_by_type` (a `credential_type`
> predicate stacked on `organization_id`) and `delete` (**the only destructive method in
> the set, and the one whose untenanted arm returns silently**). Suite: **25 tests, 0
> skips** (was 22).
>
> **Mutation-proved (each performed, measured, reverted):**
>
> | Mutation | Red |
> |---|---|
> | un-thread one newly banked site (`delete` in `integrations.py`) | **4** — incl. the new equality fence |
> | add a new untenanted `get_by_type` in `integrations.py` | **4** — site-dependent, the property is the fence |
> | thread a *guessed* org at an H4 site (`main.py:209 get_all`) | **3** — incl. the exact-count fence going red for *shrinking* |
> | `key_store.delete` drops `organization_id` from its `WHERE` | **1** — and it is the new R8 test; nothing else in the suite saw it |
> | `key_store.get_by_type` drops `organization_id` from its `WHERE` | **1** — same, same test |
>
> ⚠️ **The last two are why the R8 half was extended.** Both mutations are cross-tenant
> credential bugs that the whole structural half — every count, every ceiling, every
> exact-count pin — reports as GREEN, because the call sites are correctly threaded and
> only the SQL underneath is wrong. That is the R8 case in one line.

**Slice 6 · The `ON CONFLICT (email)` repair. · ✅ BUILT 2026-08-19 — the prediction held,
and it was WORSE than predicted.**
**Anchors (as dispatched):** `packages/acb_auth/acb_auth/access.py:550` (inside
`_BOOTSTRAP_OWNER_SQL`) · `apps/services/gateway/gateway/routes/admin/_common.py:599`
(inside `_PROVISION_MEMBER_SQL`) — both said `ON CONFLICT (email)`, while
`162_app_user_email_case.sql` creates `app_user_email_lower_key ON app_user (lower(email))`
and then **drops `app_user_email_key`**, the `UNIQUE` constraint `09_app_user.sql:15`
created (`email TEXT UNIQUE NOT NULL` → the auto-generated name 162 drops). The correct
idiom already existed twice in the tree — `acb_auth/access.py:205` and
`acb_auth/console_resolve.py:440`, both `ON CONFLICT (lower(email))`. Both statements now
name `(lower(email))`; nothing else in either SQL string changed.

**The repro, measured before the fix** (fresh database on the tenant-scratch container,
schema built by `tests/unit/_tenant_ladder.py`'s replayer; the live catalog showed
`app_user_email_lower_key` + `app_user_email_idx` and **no** unique index on the bare
column). Verbatim:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.InvalidColumnReference)
there is no unique or exclusion constraint matching the ON CONFLICT specification
                                                              -- sqlstate 42P10
```

⚠️ **It was not a conflict-path bug. Postgres resolves the `ON CONFLICT` arbiter at PLAN
time, so all six calls failed — the three fresh-insert ones included** (`_BOOTSTRAP_OWNER_SQL`
× {fresh, repeat, mixed-case} and `_PROVISION_MEMBER_SQL` × the same three); `app_user`
ended the red run **empty**. What that cost while it shipped: `ensure_owner_bootstrap()`
catches every exception, so an ownerless box logged `ownership_bootstrap_failed` and served
on with no owner and no inviter — the 2026-07-30 lockout re-armed — and every
`POST /admin/members` invite and every sign-in approval raised. After the fix the same six
calls pass and a mixed-case second insert **updates** the existing row: two addresses, two
rows, zero duplicates.

**Done when** *(met)*: both upserts exercised against the real ladder on **both** paths —
fresh insert *and* conflict — and the `DO UPDATE` arms behave identically to today.
⚠️ `_PROVISION_MEMBER_SQL`'s trailing tenant fence
(`WHERE app_user.organization_id IS NULL OR … = EXCLUDED.organization_id`) is S1-1's
write-leak repair and **must not change meaning** — its own comment block above the
statement explains why it lives on the `DO UPDATE` arm rather than in Python. It is
unchanged, and `test_the_tenant_fence_still_refuses_another_organization` now drives it
with a **differently-cased** address too, which is an input that could not reach it before
(no conflict ever happened, so nothing was refused — it wrote a second row instead).

**Fences (R7), all in `tests/unit/test_app_user_upserts.py` — 20 tests, 0 skips:**
`test_no_un_allow_listed_site_names_the_bare_email_column` (whitespace-proof
`ON\s+CONFLICT\s*\(\s*email\s*\)` ratchet over `packages/` + `apps/`; allow-list is **one
entry with a reason** — `customer_console/store.py`, the *Console* plane, where
`user_identity.email` is `CITEXT NOT NULL UNIQUE` and the bare target is correct — and
`test_the_allow_listed_site_really_has_a_bare_email_constraint` checks that reason against
the Console DDL rather than believing the comment, while
`test_the_allow_list_carries_no_dead_entry` stops it accumulating) ·
`test_the_old_conflict_target_still_raises_42P10` (the red preserved, so the suite cannot
go vacuous if somebody "fixes" a future violation by re-adding the byte-exact constraint —
the move 162's own comment refuses) · `test_app_user_carries_no_unique_index_on_the_bare_column`
(asserted from `pg_indexes`, since that is what an arbiter resolves against) ·
`test_the_functional_index_and_the_drop_ship_in_one_migration` (finds 162 **by content**,
R1) · plus both statements × {fresh, conflict, mixed-case} and the `DO UPDATE` arms
(`invited → active`, `removed` never reactivated, `display_name` never blanked, the owner
grant landing exactly once). Mutation-proved: reverting the `access.py` conflict target
alone turns **6** of the 20 red. The suite is named in `pr-check.yml`'s R8 skip guard and
reuses the existing `TENANT_LADDER_DATABASE_URL unset` grep line.

---

**Three decisions — `DECISION (agent-proposed, owner may overrule)`, 2026-08-19.**
Recorded under the same label as **D16/D17** so they stay overrulable; carried on the
board as **D43**.

**A · One SQL function, not a role-template table.** Seed per-org at provision time via a
single `provision_org_roles(org_id)` SQL function replaying 130/131/133/178's grants as
data. *Why:* one seeding doctrine — 178's own comment rejects *"a second seeding doctrine"*
and assumes the successor parameterises the existing one. A `role_template` table is the
more general answer and buys nothing until customers edit system roles, which no ticket
asks for. **Rejected alternative recorded so it is not re-proposed.**

**B · Pull, not push — the box asks the Console which organizations it should host.**
*Why:* symmetry with CP-2b's fail-closed resolve, which is already pull-shaped and already
carries the deployment key. Push would make the Console hold a write credential into every
tenant database — the largest new blast radius available. ⚠️ **The open sub-question is the
capability set** (slice 4): if pull needs more than `{resolve}`, that is an owner act, not
a widening.

**C · H3 (RLS promotion) is a hard prerequisite for EXECUTING MT-1j against a real second
organization — and blocks neither building nor R8-testing it.** *Why:* provisioning org #2
before promotion produces a second tenant with **no database-level isolation** —
`infra/postgres/generated/04_policies.sql` has never been replayed, and
`tests/unit/test_tenancy_boundary.py:126`'s `BASELINE_UNSCOPED` names **114** tables that
carry no `organization_id` at all (measured 2026-08-19). A scratch database can apply the
generated phases inside the fixture, so the build and its fences are unblocked today.
**Stated in scope AND in non-goals above; registered as an execution gate in
`work_plan.md` §6.**

---

**Fences (R7).** A rule here names the test that makes breaking it fail, or it is advisory:

| Fence | Binds |
|---|---|
| `test_every_organization_has_a_placement` | slice 3 — set-difference over the tenant plane, so a future provisioning path cannot skip the placement. ✅ **BUILT** in `tests/unit/test_org_provisioning.py` (not `test_tenant_placement.py`, which is hermetic by design and has no ladder harness) |
| `test_a_freshly_provisioned_org_has_the_five_system_roles_and_an_owner` | slices 1+2. ⚠️ Assert the **set** of role slugs, not a count: 130 seeds **six** rows — the five assignable roles plus `agent_service`. ✅ **BUILT**, name kept verbatim, set asserted |
| `tests/unit/test_mt0d_per_org_credentials.py:163` + `:176` | slice 5's standing tripwire — green **unedited**, or the fail-closed contract moved. ✅ Still green and still unedited after slices 1+2+3 and slice 5's ratchet rounds 1+2 (8 passed). ⚠️ **Necessary, not sufficient**: its `_FakeStore` matches the organization query by PREFIX, so it stays green when `count(*) = 1` is replaced by a `slug = 'default'` fallback (measured 2026-08-19). Slice 5 round 1 added `TestTheFailClosedContractIsNotRepaired` (source shape, both copies) and a live two-org read that seeds the operator's key, which do catch it |
| `tests/unit/test_credential_tenant_threading.py` | slice 5's completion ratchet — the untenanted **call** counts (parsed, not grepped) only go DOWN, converted files pin at zero, H4 leftovers pin at their EXACT count, and the spec's two greps stay as ceilings. ✅ **BUILT** rounds 1 + 2, **25** tests / 0 skips, banked **11 + 10 → 9 + 1 → 4 + 1** — its FLOOR, since `4 + 1` equals `sum(H4_KEY_STORE_SITES)` + `sum(H4_BLOB_SITES)` and every remaining untenanted call is owner-gated |
| a grep-ratchet on `WHERE slug = 'default'` under `infra/postgres/` | slice 1 — allow-list-with-a-reason, same discipline as `_SYNC_ENGINE_ALLOWED`. Baseline measured 2026-08-19: **29 lines across 8 ladder files** (`grep -rn "slug = 'default'" infra/postgres --include=*.sql \| grep -v /generated/`), of which 130/131/133/178 are the four this ticket retires. ⚠️ The ratchet's own regex must be whitespace-proof: `slug\s*=\s*'default'` (the spaced literal above is the measuring command, not the fence — a `slug='default'` evades it; 2 such hits exist today, both in 161's comments). ⚠️ Scope the ratchet to the **ladder**: `generated/02_backfill.sql` carries **140** more by construction (one per scoped table) and is regenerated, not edited. ✅ **BUILT** as `TestTheDefaultSlugRatchet`, pinned at **31** — the whitespace-proof count, which is 29 + 161's two unspaced comment hits. Ratchets both ways: `test_the_baseline_is_not_stale` forces the number DOWN in the same commit as any retirement. ⚠️ **The four seeds were NOT retired** (R6: shipped migrations are history), so the baseline does not fall on this branch — it must simply never rise |

**Rule exposure.** **R1** — the migration number is taken at build time (the ladder topped
at 178 when this ticket was minted; slices 1+2+3 took **179** after re-deriving it across
`main` *and* every origin branch, and `_tenant_ladder.ladder()` fails the whole suite on a
duplicate prefix). **R5** — no new persisted table is introduced by any
slice; if one appears it faces `test_tenant_coverage.py`'s source gate, and no slice adds a
DB-connection or Redis site outside the seam. *(Slices 1+2+3 added none: the three
callables are SQL, and their only Python is test code.)* **R6** — the role-seed extraction is
expand/contract: add the callable and re-point, never rename in place; the deploy applies
migrations *before* restarting services, so the old code must still meet the new schema.
*(As built, there is no re-point at all — see slice 1's box. `CREATE OR REPLACE FUNCTION`
plus a migration that writes no data is as expand-only as it gets: the ladder can replay it
against a database serving the old code with no effect whatsoever.)*
⚠️ **One deferred security decision, recorded in 179's header rather than taken here:**
the three callables are **SECURITY INVOKER** (the default). Under H3's FORCE-RLS promotion
a caller bound to tenant A cannot insert tenant B's rows — correct for the app, wrong for a
path that by definition creates a tenant nobody is bound to yet. `SECURITY DEFINER` is a
privilege decision that belongs *with* the policy it answers to, not months before it.
**H3 must resolve this; it is not resolved.**
**R8 — mandatory and not satisfiable hermetically**: every slice's subject is a query, a
migration, a constraint or a predicate, and §7.1's traps are precisely the hermetic-fake
class.

**Verification — both databases, both DSNs.** They are deliberately different names
(`test_billing_purchase_capability.py` records why `DATABASE_URL` must not be used):

```bash
# Any ports work — the invariant is that the two DSNs must not name the same
# database (5443 is the session's tenant-scratch; the Console scratch has run
# on 5442 — reuse it, the 5444 here is illustrative, not documented anywhere):
export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform

# slices 1+2 — the new fences, plus the seeds they generalise
uv run pytest tests/unit/test_org_provisioning.py \
              tests/unit/test_billing_purchase_capability.py -v -rs
# slice 2 — the fresh-box path must be untouched
uv run pytest tests/unit/test_owner_bootstrap.py tests/unit/test_org_access_control.py -v -rs
# slice 3 — placement. `test_tenant_placement.py` is MT-1a's hermetic resolver
# suite and stays that way; slice 3's own R8 fences (the one idempotent act,
# and `test_every_organization_has_a_placement`) live in test_org_provisioning.py
uv run pytest tests/unit/test_tenant_placement.py tests/unit/test_org_provisioning.py -v -rs
# slice 4 — the three single-DB clauses (4a Console · 4b seam caller · 4c projection).
# "End to end" was struck at remediation: the split above de-scopes it to the
# execution-time smoke, and this line must not promise what the clauses do not.
# The last four suites are here because the required deployment_label is a
# breaking change and they POST /orgs/provision (re-audit measure: 6 suites,
# 12 call sites) — a green run on the first four alone proves nothing about them.
uv run pytest tests/unit/test_customer_console_resolve.py \
              tests/unit/test_customer_console_lifecycle.py \
              tests/unit/test_org_provisioning.py \
              tests/unit/test_deployment_resolve_cache.py \
              tests/unit/test_customer_console_api.py \
              tests/unit/test_customer_console_key_auth.py \
              tests/unit/test_customer_console_payments.py \
              tests/unit/test_customer_console_router.py -v -rs
# slice 5 — the completion ratchet, plus the standing tripwire it must leave
# UNEDITED. Run them together: the ratchet is only meaningful while the pair it
# refuses to "repair" is still green.
uv run pytest tests/unit/test_credential_tenant_threading.py \
              tests/unit/test_mt0d_per_org_credentials.py -v -rs
# slice 6 — both upsert paths against the real ladder
uv run pytest tests/unit/test_app_user_upserts.py -v -rs
```

⚠️ **A skip is not a pass** — read the `-rs` block. The R8 suites above skip loudly
without their DSN, and a green run that skipped them proves the SQL was written, not that
it works.

**Suite state, 2026-08-19 after slices 1+2+3 and slice 5's ratchet rounds 1+2 (all on
the tenant DSN, 0 skips):**

| Suite | Count | Note |
|---|---|---|
| `tests/unit/test_org_provisioning.py` | **34** | NEW — slices 1+2+3's fences. Was *"does not exist yet"*; the name was reserved here and is the name that shipped |
| `tests/unit/test_owner_bootstrap.py` | **8** | RE-GATED onto `TENANT_LADDER_DATABASE_URL` by slice 2 (was 5 tests skipping in every run ever made) |
| `tests/unit/test_billing_purchase_capability.py` | **12** | was 10; +2 for the second statement of the grant (see slice 1's box) |
| `tests/unit/test_app_user_upserts.py` | **20** | slice 6, unedited |
| `tests/unit/test_org_access_control.py` | 67 | unedited *(row corrected 2026-08-19 at verification — the first write-up transcribed 74; measured 67)* |
| `tests/unit/test_tenant_placement.py` | 7 | unedited, still hermetic by design *(corrected from a transcribed 8; measured 7)* |
| `tests/unit/test_mt0d_per_org_credentials.py` | 8 | the standing tripwire, unedited |
| `tests/unit/test_credential_tenant_threading.py` | **25** | slice 5's completion ratchet. 22 at round 1; round 2 adds the H4-equality fence, the R8 integration-credential case and one `CONVERTED_FILES` parameter. 0 skips on the tenant DSN |

Whole-block run: **181 passed, 0 skipped** *(was 156; +25 when slice 5's ratchet joined
the block at round 2 — measured, not summed)*. Directory sweep
(`pytest tests/unit -k "not calendar and not memory_integration"`, **both DSNs
exported**): **7150 passed, 10 failed, 44 skipped** at slice 5 round 2, measured against
a same-session clean-tree run of **7147 passed, 10 failed, 44 skipped** — exactly the +3
tests round 2 adds and not one collateral change. The ten failures are the SAME ten in
both runs and are pre-existing (`test_chat_message_upsert`, `test_code_tools`,
`test_observability_access`, `test_workflows_engine`, `test_workflows_modules`), none
touching MT-1j's surface. ⚠️ Export **both** DSNs before comparing this number: with
`CUSTOMER_CONSOLE_DATABASE_URL` unset the same tree reports **6830 passed / 364
skipped** — a ~320-test swing that is skip posture, not regression, and the kind of
difference a transcribed number hides.
The first four suites above, plus `test_credential_tenant_threading.py`, are named in
`pr-check.yml`'s R8 skip guard and share the one `TENANT_LADDER_DATABASE_URL unset` grep
line. That suite asserts its own membership
(`test_this_suite_is_named_in_the_ci_skip_guard`), matching the pytest **argument** rather
than a mention — slice 2's box records why the weaker check is not a check.

---

### MT-2 … MT-5 — scoped, not yet dispatchable

Each names the one thing that would make it so. **Do not hand these to an agent as written.**

| Ticket | Scope | Owning § | To become dispatchable |
|---|---|---|---|
| **MT-2** Entitlements | `module_catalog` · **`center_package` + `plan_catalog` (D23/D20 — the sales objects)** · `org_module_entitlement` · `user_module_seat` (**with `source ∈ center/plan/alacarte`**) · the `intersect()` mask · 402-vs-403 · `ModuleGate` + upsell · non-HTTP gating · per-org feature flags + release channel · **the one-assignment act (seat + membership + entitlements + grants, D23.2)** | §2, §2.4b, §1.4b | ~~The SKU list and price points~~ **INPUT ANSWERED — final shape D23 2026-08-10 (§2.4b; D18/D19's module-first answers superseded).** Remaining to dispatch: write the seven-point ticket contract onto §2/§2.4b (per-item done-whens + verification) — the input is no longer the blocker |
| ~~**MT-3** AI credits~~ **→ ABSORBED by WS-31 (D32.2, 2026-08-12)** | Per-org virtual keys · Redis budget gate + per-run circuit breaker · `usage_event` (idempotent on `request_id`) · `model_rate_card` · `credit_ledger` · BYOK tier | §3 (design) — **placement now `specs/customer_console.md`** | **DO NOT DISPATCH FROM HERE.** Every item listed moved to the central Control Plane as CP-3/CP-4/CP-6, with the seven-point contract written there. The *design* in §3.2–§3.5 is unchanged and still binding; only where it runs moved (see the §3 banner) |
| **MT-4** Billing | `payment_provider` seam · Stripe + Razorpay · webhooks → entitlements · dunning state machine · Operator Console · reconciler | §4 | **The provider split decision** (§8 item 3) and MT-2 shipped |
| **MT-5** Tiers & compliance | Per-tenant envelope encryption · dedicated-DB tier activation · **drop Neo4j / graph into Postgres** · residency · SOC 2 groundwork | §1.1a, §0.9.4 | Nothing blocking. ⚠️ **Envelope encryption should be pulled into MT-1 if MT-0d or MT-1g touch those columns anyway** — retrofitting encryption onto populated columns is materially harder |

---

### 11.1 Sequencing, and the one thing that is not sequential

```
MT-0a ──► MT-0c (owner-gate)          MT-0b, MT-0d  ─┐
                                                      ├─► MT-1a ─► MT-1b ─► MT-1c ─► MT-1d
                                                      │              └─► MT-1e, MT-1f, MT-1g, MT-1h, MT-1i (parallel)
                                                      │
Customers 1–5 shipped as silos (§5.1) ────────────────┘   ──► MT-2 ──► MT-3 ──► MT-4 ──► MT-5
```

- **MT-1b before MT-1c.** Binding a tenant against tables with no policy proves nothing.
- **MT-1a before MT-1b.** The org rows the FK points at must exist first.
- **MT-1e–MT-1i are parallel** once MT-1c lands — five independent PRs.
- **MT-1j is buildable now and executable only after H3** *(added 2026-08-19 with the
  mint; Decision C)*. Nothing sequences before it — its six slices are repairs and
  extractions against code that already ships — but **executing it against a real second
  organization waits on the RLS promotion**, or that organization exists with no
  database-level isolation. It is the substrate **MT-2's** one-assignment act assumes:
  MT-2 assigns seats and entitlements *in* an organization that MT-1j is what creates,
  with roles, an owner and a placement. Slice 6 also lands **before** MT-1a-2/H6, which
  rewrites the same two upserts. **Built order, 2026-08-19: slice 6 first (it edits the
  same SQL string slice 2 would have), then 1+2+3 as one branch — splitting those three
  would have shipped a callable that nothing calls and an owner path with no organization
  to own. Slice 4 is next and brings the first caller; slice 5 rides its own ratchet.**
- **MT-0 does not block selling.** §5.1's first five customers ship as silos *while* MT-1
  is built — but **MT-0a/b/d must be in before customer #2**, silo or not, because they
  are process-level not database-level defects.

### 11.2 Week one — what to actually do on Monday

1. **Take the MT-0c decision** (un-park T2, or record why not). It is the only owner-gate
   in MT-0 and everything in §0.9.3 waits behind it. *Owner, ~1 hour.*
2. **Dispatch MT-0a.** Largest live defect, self-contained, no dependencies.
3. ~~**Answer §8 items 1–2**~~ **DONE — final shape D23 2026-08-10 (§2.4b Center packages; the first D18 answer — Core ₹600 + ₹300/module — is superseded)**;
   ₹10 AI-action credit at ~50% margin. Revision against the first silo customers is
   expected and fine.
4. ~~**Write the cutover trigger down**~~ **DONE 2026-08-09** — adopted in §5.1
   condition 4 and carried on the board's WS-29 row.

### 11.3 What this plan deliberately does not do

- **No Kubernetes, no Citus, no service mesh, no microservice split** (§0.9.7). The
  monolith is correct; what needs splitting is the three planes' trust boundaries.
- **No `organization_id` threaded into query bodies by hand** (§7 item 7). RLS is the
  control; a hand-written predicate is an optimisation only.
- **No second scoping doctrine.** `tenancy_and_visibility.md` §3.2's standing rule is
  unchanged: tenant isolation is `organization_id` + RLS; visibility *inside* a tenant
  stays `email | group:<slug> | org`.
- **No acceptance written for MT-2…MT-5 until their named input exists.** A criterion an
  implementer cannot test is worse than an empty ticket.

---

## 10. References

**Internal (binding):** `tenancy_and_visibility.md` (visibility ladder §3, project grants
§4, gap table §5 — all still current; §1 and §6 superseded here) ·
`user_management_contract.md` (the ten rules; §1.5 adds an eleventh for tenant
resolution) · `org_access_control.md` (the shipped RBAC model) ·
`multi_user_organization_research.md` §17 (prior research — §17.2's pooled-first
recommendation is adopted; §17.3's header-based tenant resolution is rejected) ·
`department_centers.md` · root `AGENTS.md` non-negotiables 2, 3 and 10 ·
`docs/DESIGN_LIMITATION_native_maf_mutation.md`

**External:** [AWS — SaaS tenant isolation strategies: the bridge
model](https://docs.aws.amazon.com/whitepapers/latest/saas-tenant-isolation-strategies/the-bridge-model.html) ·
[AWS Database Blog — multi-tenant data isolation with PostgreSQL row-level
security](https://aws.amazon.com/blogs/database/multi-tenant-data-isolation-with-postgresql-row-level-security) ·
[AWS SaaS multi-tenant architecture guide — pool/silo, onboarding,
metering](https://hidekazu-konishi.com/entry/aws_saas_multi_tenant_architecture_guide.html) ·
[Multi-tenant SaaS architecture patterns
(2026)](https://architecturediagram.ai/blog/multi-tenant-architecture) ·
[Stripe — analyze and query meter usage](https://docs.stripe.com/billing/subscriptions/usage-based/analytics) ·
[Stripe — usage metering guide](https://stripe.com/resources/more/usage-metering) ·
[Stripe — Langfuse: subscription + metered hybrid at billions of
events](https://stripe.com/customers/langfuse) ·
[LiteLLM — multi-tenant architecture](https://docs.litellm.ai/docs/proxy/multi_tenant_architecture) ·
[LiteLLM — virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys) ·
[LiteLLM — budgets and rate limits](https://docs.litellm.ai/docs/proxy/users) ·
[Revenera — SaaS licensing models](https://www.revenera.com/blog/software-monetization/saas-licensing-models-guide/) ·
[Nalpeiron — SaaS licensing and entitlement management](https://docs.nalpeiron.com/education-and-training/licensing-education/learn-about-software-licensing-models/saas-licensing-and-entitlement-management)

**External — the §0.9.9 prior-art review (2026-08-11).** ⚠️ The two Odoo sources are an
agency guide and a single forum respondent; their RAM figures are directional capacity
planning, not measurements, and are cited as such:
[Odoo multi-tenant architecture guide](https://oec.sh/blog/odoo-multi-tenant-architecture) ·
[Odoo forum — multi-tenant Community with white-label](https://www.odoo.com/forum/help-1/what-s-the-best-architecture-to-manage-multi-tenant-odoo-community-setup-with-white-label-support-289650) ·
[SAP CAP — deploy multitenant SaaS applications](https://cap.cloud.sap/docs/guides/multitenancy/) ·
[SAP samples — the CAP shared container (cross-tenant master data)](https://github.com/SAP-samples/btp-cap-multitenant-saas/blob/main/docu/2-basic/7-explore-the-components/components/SharedContainer.md) ·
[SAP KBA 2101244 — HANA MDC FAQ (incl. the trusted-environment caveat)](https://userapps.support.sap.com/sap/support/knowledge/en/2101244) ·
[SAP HANA MDC reference (PDF)](https://help.sap.com/doc/0987e3b51fb74e5a8631385fe4599c97/1.0.12/en-US/SAP_HANA_Multitenant_Database_Containers_en.pdf)

## Board record (2026-08-09) — moved from work_plan.md §2

> Moved here in the 2026-08-09 consolidation (work_plan.md D18): board rows now
> carry state + gates only. The narrative below is preserved verbatim from the
> final long-form row; the dated corrections after it win where they conflict.

### WS-29 — Multi-tenancy — turning Metorite into a product sold to other companies
**State cell (as of the move):** ✅ **Phase 0 DONE** (MT-0a/0b/0c-1/0d) · ◐ **MT-1 partial** (1a schema · 1b generated-not-applied · 1c seam · 1e wrapper · 1i done) · 🔴 MT-0c-2 OWNER-GATE (D16) · ◐ MT-2…MT-5 blocked on owner inputs
**Narrative (verbatim):** **Re-takes D11.** `tenancy_and_visibility.md` §1 set the tenant boundary at THE DEPLOYMENT and §6 put row-level tenancy, an org switcher and multi-org users out of scope. The business model changed — per module, per user, per month, plus metered AI — so §1/§6 are **superseded** by that spec's own re-take procedure. **§2–§5 of `tenancy_and_visibility.md` (the visibility ladder, the `group:` project grant, the gap table) are UNCHANGED and still binding**; tenancy is *which company*, visibility is *who inside it*. **The decision: tenant = `organization_id` enforced by Postgres RLS at the connection seam; the deployment is a placement, not a boundary.** Pooled standard tier, dedicated DB/stack as priced tiers. ⚠️ **The thesis is not a database thesis** (§0.9): agents execute model-generated tool calls over adversarial input, and the database can be defended by a policy that cannot be forgotten while the agent runtime cannot — so **the isolation budget belongs on the execution plane**, and MT-0c is the load-bearing ticket, not MT-1b. **Three findings that changed the plan:** (1) *"one engine, one `get_db()`"* was **wrong** — true of the request path, false of the process; §0.1 enumerates **eight** connection paths, two of which the seam ratchet never inspected (`acb_graph`'s sync `create_engine`; three raw `psycopg.connect` callers), which is why MT-1c also extends the ratchets. (2) **RLS fails closed** (unset `app.tenant_id` → NULL → zero rows) where `search_path` fails open — that property, not topology, is why schema-per-tenant was rejected (§1.8). (3) The customization layer that makes per-customer code forks unnecessary **already ships** — Custom Apps, Workflows (ADR-028), `dynamic_agents`, `pm_custom_fields`, `settings JSONB` (§1.4b). **Blockers before ANY second tenant, silo or pooled — process-level, not database-level:** MT-0a (integration credentials reach agents via process-global `os.environ`, `executor.py:4388`, flaw documented in-code at `:4364`) and MT-0b (self-mutation opens PRs against this monorepo — root `AGENTS.md` non-negotiable 3). **MT-0c is OWNER-GATE and inverts D10:** T2 is parked because *"the ladder must hold against trusted colleagues, not hostile users"* — selling externally replaces that threat model, so un-parking is the architecture, not optional hardening. **Rollout (§5.1): silo customers 1–5, build MT-1 in parallel, cut over at 8–12** — crossover is where silo's linear cost meets MT-1's one-time 4–5 weeks; every silo runs the pooled schema with `organization_id` + RLS from day one, or the bridge becomes a rewrite. **Absorbs WS-14a** as MT-1i: the three `org_group` slug-only joins were "wrong within one org, leaking in none" under D11 — **under D15 they leak**, and `_HAS_OWNER_SQL` (`access.py:522`, no org filter) is a **lockout RLS does not fix**. §11.2 is the week-one list.

**Corrections applied 2026-08-09:** H1 is now SCRATCH-VERIFIED (157/158/159 applied +
idempotent on a full-ladder replica; baseline 213 passed / 2 skipped; prod apply = the
owner's merge of PR #404 — see the handover's H1 result block). MT-2/MT-3's owner
inputs are ANSWERED (D18 — §8 items 1–2). The §5.1 cutover trigger is ADOPTED. The
Mem0 path-8 decision is taken (D17, Option A). "MT-2…MT-5 blocked on owner inputs" is
therefore stale for 2 of 4: MT-2/MT-3 now lack only their seven-point ticket
contracts; MT-4 still needs §8 item 3 (payment-provider split).
