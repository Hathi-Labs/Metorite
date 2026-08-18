# Multi-tenancy implementation reference — the shapes, not the reasons

**Status:** 🟢 Binding build reference · **Created:** 2026-08-08 · **Owner:** vjvarada ·
**Board row:** WS-29 · **Parent spec:** [`saas_multitenancy.md`](saas_multitenancy.md) ·
**Verified against code:** 2026-08-08, working tree at `b09093a`; **§7.1 and §8 trap 5
re-verified 2026-08-19** · ⚠️ **Updated 2026-08-19:** §7.1's *"until MT-1i lands"* warning
was **stale in its reason and still true in its conclusion** — MT-1i landed and step 3
still produces an ownerless org. Both §7.1 step 3 and §8 trap 5 now name their owner:
**`saas_multitenancy.md` §11 MT-1j**, minted the same day.

> **This document owns SHAPES, not DECISIONS.** Every decision it implements is owned by
> `saas_multitenancy.md` and is cited, never re-argued here:
>
> | Decision | Owner |
> |---|---|
> | Tenant = row + RLS; deployment = placement (D15) | `saas_multitenancy.md` §1 |
> | The three planes; the agent sandbox contract | §0.9 |
> | The eight connection paths | §0.1 |
> | Entitlements ≠ permissions | §2 |
> | Credits, rate card, metering | §3 |
> | Billing, ledger, reconciliation | §4 |
> | The tickets (MT-0 … MT-5) and their done-when | §11 |
> | Visibility *inside* a tenant (unchanged) | `tenancy_and_visibility.md` §3–§5 |
>
> **If this doc and the parent disagree, the parent is right and this doc is stale — fix
> it here.** What lives here and nowhere else is the *executable shape*: the SQL, the seam
> code, the test ratchets and the runbooks an implementer needs so that MT-n does not have
> to be re-derived per ticket.
>
> ⚠️ **Every SQL block below is a TEMPLATE, not a migration.** R1 binds: migration numbers
> are resolved at build time, never written into a doc. Anchors are re-verified at
> dispatch — §0.1 of the parent exists because that rule was broken once already.

---

## 1. MT-1b — the tenancy migration, generated

### 1.1 The per-table template

Applied to every application table. **Generate it; do not hand-write 143 of these.**

```sql
-- ① the column, with the default that keeps INSERTs unchanged
ALTER TABLE {t}
    ADD COLUMN IF NOT EXISTS organization_id UUID
    DEFAULT current_setting('app.tenant_id', true)::uuid;

-- ② backfill the single existing org, then make it NOT NULL
UPDATE {t} SET organization_id = (SELECT id FROM organization WHERE slug = 'default')
 WHERE organization_id IS NULL;
ALTER TABLE {t} ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE {t} ADD CONSTRAINT {t}_org_fk
    FOREIGN KEY (organization_id) REFERENCES organization(id) ON DELETE CASCADE;

-- ③ the policy. ENABLE alone is not enough — the table OWNER bypasses it.
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE  ROW LEVEL SECURITY;
CREATE POLICY {t}_tenant_isolation ON {t}
    USING       (organization_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK  (organization_id = current_setting('app.tenant_id', true)::uuid);

-- ④ the index. org_id FIRST — it is a distribution key, not a filter column (§1.8a)
CREATE INDEX IF NOT EXISTS {t}_org_idx ON {t} (organization_id);
```

**Four things in that template are load-bearing and each has an incident waiting behind
it if dropped:**

| Clause | Drop it and |
|---|---|
| `DEFAULT current_setting(…)` | every `INSERT` in 209 gateway files needs editing — the whole "zero queries change" property is this one line |
| `FORCE ROW LEVEL SECURITY` | the table owner (and therefore anything connecting as it) reads every tenant, silently |
| `WITH CHECK` | a tenant can **write** a row stamped with another tenant's id. `USING` filters reads; only `WITH CHECK` constrains writes |
| `, true` in `current_setting` | an unset GUC **raises** instead of returning NULL. Raising is *also* fail-closed, but it turns "no rows" into a 500 on every unconverted path and makes MT-1c undiagnosable |

### 1.2 Composite primary keys — the distribution-key discipline

Parent §1.8a. `organization_id` goes **first** in every PK and every composite index:

```sql
-- before                          -- after
PRIMARY KEY (id)                   PRIMARY KEY (organization_id, id)
PRIMARY KEY (app_id, subject)      PRIMARY KEY (organization_id, app_id, subject)
INDEX (user_id, created_at DESC)   INDEX (organization_id, user_id, created_at DESC)
```

**Do this in MT-1b or never.** Changing a PK after data exists means rewriting every
referencing FK; it is the one part of this plan that is genuinely expensive to defer.

> ⚠️ **Watch the FK graph.** Promoting a PK to composite forces every referencing FK to
> become composite too. Generate both sides in one pass; a half-converted FK graph does
> not apply.

### 1.3 The app role

```sql
CREATE ROLE acb_app LOGIN PASSWORD :'pw' NOINHERIT;
-- NOT superuser. NOT the table owner. NOT BYPASSRLS. All three bypass policies.
GRANT CONNECT ON DATABASE :db TO acb_app;
GRANT USAGE ON SCHEMA public TO acb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO acb_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO acb_app;  -- covers future tables
GRANT ALLOW ON ALL SEQUENCES IN SCHEMA public TO acb_app;       -- adjust to usage/select
```

Migrations keep running as the owner (`scripts/apply_migrations.sh` is unchanged).
**Only the gateway, ingestion and orchestrator processes switch to `acb_app`.**

### 1.4 The coverage ratchet (MT-1b done-when 4)

Same discipline as `tests/unit/test_db_engine_seam.py`. **Source-level would not work
here** — the failure mode is a table, not a call site — so this one is DB-backed and must
be run against a migrated database in CI.

```python
_EXEMPT = {
    # Control-plane tables: cross-tenant BY DESIGN (parent §1.5). Each needs a reason.
    "organization", "tenant_placement", "user_identity",
    "org_subscription", "org_module_entitlement", "user_module_seat",
    "module_catalog", "usage_event", "usage_rollup", "credit_ledger", "invoice",
    "feature_catalog",          # a catalog, identical for every tenant
    "schema_migrations",
}

async def test_every_table_is_tenant_scoped(db):
    rows = await db.execute(text("""
        SELECT c.relname,
               EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = c.relname AND column_name = 'organization_id') AS has_col,
               c.relrowsecurity  AS rls_enabled,
               c.relforcerowsecurity AS rls_forced,
               EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname) AS has_policy
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relkind = 'r'
    """))
    bad = [r.relname for r in rows
           if r.relname not in _EXEMPT
           and not (r.has_col and r.rls_enabled and r.rls_forced and r.has_policy)]
    assert not bad, f"tables missing tenant scoping: {bad}"
```

> **The exemption list is the security review.** Adding a name to `_EXEMPT` must require a
> written reason in the same PR — it is the only way a table legitimately escapes, and
> therefore the only way one illegitimately does.

---

## 2. MT-1c — binding the tenant at all eight paths

Parent §0.1 holds the inventory. These are the shapes.

### 2.1 The shared async seam — `acb_common/db.py`

```python
_TENANT: ContextVar[str | None] = ContextVar("acb_tenant", default=None)

@asynccontextmanager
async def tenant_session(tenant_id: str | None = None):
    """The ONLY way to obtain a tenant-bound session.

    ``SET LOCAL`` — never ``SET``. The pool recycles connections across requests
    (``db.py`` pool_size + max_overflow), and a session-scoped ``SET`` survives the
    connection's return to the pool: the next borrower reads the previous tenant.
    ``SET LOCAL`` is transaction-scoped and resets on commit or rollback.
    """
    tid = tenant_id or _TENANT.get()
    if not tid:
        raise TenantUnbound("no tenant in context — a caller outside a request or job "
                            "must pass one explicitly")
    session = get_session_factory()()
    try:
        await session.begin()                       # SET LOCAL needs a transaction
        await session.execute(
            # ⚠️ corrected 2026-08-10: the SET LOCAL literal cannot bind a
            # parameter (extended-protocol syntax error, found live);
            # set_config(..., true) is the same transaction-local semantics.
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
```

> ⚠️ **`SET LOCAL` outside a transaction is a silent no-op** — Postgres warns and moves on,
> the policy then sees an unset GUC, and every query returns zero rows. That presents as
> "the feature is broken", not as "tenancy is broken", which is why the explicit
> `session.begin()` is in the template rather than left to SQLAlchemy's autobegin.

**`get_db()` keeps its name and signature** so the ~200 `db = await get_db()` call sites
do not change; it becomes a thin wrapper that raises when no tenant is bound.

### 2.2 The two new ratchets (MT-1c done-when 2 and 3)

`test_db_engine_seam.py` inspects `create_async_engine` **only**. Extend it and add a
sibling:

```python
_SYNC_ENGINE_ALLOWED = {
    "packages/acb_graph/acb_graph/db.py": "entity graph; sync by design — MUST bind tenant",
}
_PSYCOPG_ALLOWED = {
    "packages/acb_common/acb_common/org_settings.py": "control-plane read, pre-tenant",
    "packages/acb_llm/acb_llm/model_config.py":       "control-plane read, pre-tenant",
    "packages/acb_llm/acb_llm/key_store.py":          "per-org keys after MT-0d — MUST bind",
}
# Same AST walk as the existing test; assert every psycopg.connect / create_engine
# call site is in its allow-list, with a reason string.
```

**The allow-list entry is the design review.** Parent §0.1 exists because two paths had no
ratchet at all and were therefore invisible.

### 2.3 The fail-closed test (MT-1c done-when 5)

The single most important test in the whole workstream:

```python
async def test_unbound_connection_returns_no_rows(raw_session):
    """An unconverted path must fail CLOSED — zero rows, not another tenant's rows.

    This is the property the entire pooled decision rests on (parent §0.1). If this
    test ever goes green for the wrong reason, the architecture is unsound.
    """
    await raw_session.execute(text("RESET app.tenant_id"))
    rows = (await raw_session.execute(text("SELECT * FROM gtd_items LIMIT 5"))).all()
    assert rows == []
```

### 2.4 Mem0 — the decision MT-1c must record

Path 8 hands a conninfo string to a third-party client. **DECIDED 2026-08-09: Option A
(D17, `agent-proposed, owner may overrule` — recorded in `work_plan.md` §3 and the
parent's §0.1 consequence 4).** The three options, kept for the record:

| Option | Shape | Cost |
|---|---|---|
| **A — conninfo options** *(recommended)* | append `options=-c app.tenant_id=<uuid>` to the conninfo Mem0 receives; the GUC rides the startup packet | Mem0 needs a connection per tenant, so pool per tenant or reconnect per scope change |
| **B — role per tenant** | `SET ROLE tenant_<id>`, policies keyed on `current_user` | N roles to provision and drop; onboarding gains a DDL step |
| **C — scope-string only** | accept that Mem0 isolation rests on the existing scope strings, not RLS | Cheapest, weakest. **Only acceptable if written down as an accepted risk with a date** |

---

## 3. MT-1a — the control plane

Separate database (or at minimum a separate schema with its own role). **No RLS here —
it must read across tenants.** Parent §1.5.

```sql
CREATE TABLE organization (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          TEXT UNIQUE NOT NULL,          -- the subdomain
    display_name  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('trial','active','past_due','suspended','cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tenant_placement (
    organization_id UUID PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE,
    tier            TEXT NOT NULL CHECK (tier IN ('pool','bridge','silo')),
    target          TEXT NOT NULL,   -- connection alias, NOT a raw URL with a password
    region          TEXT NOT NULL DEFAULT 'ap-south-1'
);
-- Day one every row is ('pool', 'primary'). The indirection is the point:
-- it turns "move this customer to their own database" into a data move (parent §1.6).

CREATE TABLE user_identity (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,     -- global; one row per human
    display_name  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE org_membership (
    organization_id UUID NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES user_identity(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('invited','active','suspended','removed')),
    invited_by      TEXT, invited_at TIMESTAMPTZ,
    joined_at       TIMESTAMPTZ, last_active_at TIMESTAMPTZ,
    PRIMARY KEY (organization_id, user_id)
);
```

> ⚠️ **`app_user.email` global uniqueness is load-bearing today** — two `ON CONFLICT (email)`
> upserts on the live sign-in path, measured 2026-08-08 at `acb_auth/access.py:205` and `:509`
> *(the `members.py:173`/`access.py:447` pair first published here was stale — corrected
> 2026-08-09; re-derive with grep)*. Both are rewritten by MT-1a.
> The invite path is the dangerous one: today's
> `INSERT … ON CONFLICT (email) DO UPDATE SET organization_id = EXCLUDED.organization_id`
> is, under two orgs, **an account-takeover primitive** (`tenancy_and_visibility.md` §1.1
> site 3). It must become an insert into `org_membership`, never an update of the identity.

**Email-keyed columns across the tenant plane stay email-keyed.** `app_grants.subject`,
`apps.owner_email`, `gtd_items.user_id`, `meeting.owner_email` do **not** get re-keyed to
UUIDs — RLS already constrains the row set to one tenant, so `email` is unambiguous within
it. That is the second reason to do RLS before the identity split.

---

## 4. MT-2 — entitlement shapes

Control-plane tables. Parent §2.2 owns the reasoning; **§2.4b (D23) owns the
sales-object shapes** — modules are internal atoms, customers buy Center
packages, org-wide add-ons, or Complete.

```sql
CREATE TABLE module_catalog (                            -- internal billing ATOM (D23)
    slug          TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    feature_slugs TEXT[] NOT NULL,                      -- unlocks these feature_catalog rows
    requires      TEXT[] NOT NULL DEFAULT '{}',
    is_core       BOOLEAN NOT NULL DEFAULT false,
    list_price_per_seat_month NUMERIC(12,2),            -- atom cost WEIGHT, not a customer price
    currency      TEXT NOT NULL DEFAULT 'INR'
);

CREATE TABLE center_package (                            -- D23: THE customer sales object
    center_slug   TEXT PRIMARY KEY,                     -- matches the Center registry slug
    display_name  TEXT NOT NULL,
    module_slugs  TEXT[] NOT NULL,                      -- atoms this package expands to
    price_per_seat_month NUMERIC(12,2) NOT NULL,        -- ₹600 app-bearing / ₹300 slices-only
    currency      TEXT NOT NULL DEFAULT 'INR'
);

CREATE TABLE plan_catalog (                              -- D20.5 as amended by D23:
    slug          TEXT PRIMARY KEY,                     --   holds ONLY the 'complete' row
    module_slugs  TEXT[] NOT NULL,                      --   ['*'] wildcard, expanded at
    price_per_seat_month NUMERIC(12,2) NOT NULL,        --   entitlement time (₹3,600)
    currency      TEXT NOT NULL DEFAULT 'INR'
);

CREATE TABLE org_module_entitlement (
    organization_id UUID NOT NULL,
    module_slug     TEXT NOT NULL REFERENCES module_catalog(slug),
    state           TEXT NOT NULL CHECK (state IN
                      ('trial','active','past_due','suspended','cancelled')),
    seats_purchased INT NOT NULL DEFAULT 0,
    effective_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until TIMESTAMPTZ,
    source          TEXT NOT NULL,                      -- stripe | razorpay | manual
    PRIMARY KEY (organization_id, module_slug)
);

CREATE TABLE user_module_seat (
    organization_id UUID NOT NULL,
    user_id         UUID NOT NULL,
    module_slug     TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'alacarte'    -- D23: which purchase granted it —
                    CHECK (source IN ('center','plan','alacarte')),
    source_slug     TEXT,                               -- the center_package/plan row, when not alacarte
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by     TEXT,
    PRIMARY KEY (organization_id, user_id, module_slug)
);
-- D23.2 — the ONE-ASSIGNMENT ACT: granting a user a center_package writes, in one
-- transaction: the billing seat rows (source='center'), the org_group membership,
-- and the D12 slice grants. Unassignment reverses all of it. Union semantics: a
-- module reached via two packages exists once (PK above); billing lines are per
-- PACKAGE seat, never per module (parent §4.2).

CREATE TABLE org_feature_flag (          -- §1.4b: release channel + per-org flags
    organization_id UUID NOT NULL,
    flag            TEXT NOT NULL,
    enabled         BOOLEAN NOT NULL,
    set_by          TEXT, set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, flag)
);
```

### 4.1 The enforcement seam — zero route edits

`EffectiveAccess.intersect()` already exists (`packages/acb_auth/acb_auth/permissions.py:366-374`)
and already narrows an agent to its member. Entitlements are the same operation:

```python
def entitlement_mask(org_id: str) -> EffectiveAccess:
    """feature:* grants for the modules this org currently owns. Redis-cached,
    invalidated by the billing webhook. NEVER a payment-provider call on the
    request path (parent §4)."""
    slugs = [f for m in active_modules(org_id) for f in m.feature_slugs]
    return EffectiveAccess(grants=frozenset(f"feature:{s}" for s in slugs) | {"agents:run:*"})

# in acb_auth.deps, where EffectiveAccess is already resolved once per request:
effective = role_and_override_access.intersect(entitlement_mask(user.organization_id))
```

Every existing `require_permission("feature:crm")` and the whole nav then honour
entitlements **with no route changes.**

### 4.2 402 vs 403

```python
class NotEntitled(HTTPException):
    """The ORG does not own this module → 402. Action: upgrade."""
    def __init__(self, module: str):
        super().__init__(status_code=402, detail={
            "error": "module_not_entitled", "module": module,
            "upgrade_url": f"/settings/billing?module={module}"})
```

403 keeps its meaning: signed in, org owns it, **your admin** has not granted it. The
frontend routes the two differently — *upgrade* vs *ask your admin* — and `/auth/me`
returns both `features` and `modules` so it can.

### 4.3 The non-HTTP gate (MT-2, the part everyone forgets)

An unowned module must not run its background work. Otherwise it is dark in the UI while
its email sync still polls every five minutes **on your provider spend, for a customer who
is not paying.** Gate at four places:

```python
# agent registry · ingestion scheduler · Redis stream consumer · workflow triggers
if not is_entitled(org_id, "email"):
    continue          # skip this tenant's slice of the sweep
```

---

## 5. MT-3 — metering shapes

```sql
CREATE TABLE llm_api_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    prefix          TEXT NOT NULL UNIQUE,     -- 'cc_live_a8f3…' — the lookup key
    key_hash        TEXT NOT NULL,            -- argon2/sha256; never the key
    label           TEXT, scopes TEXT[],
    created_by      TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

CREATE TABLE model_rate_card (
    model                        TEXT NOT NULL,
    input_credits_per_1k         NUMERIC(12,4) NOT NULL,
    output_credits_per_1k        NUMERIC(12,4) NOT NULL,
    cached_input_credits_per_1k  NUMERIC(12,4) NOT NULL,
    effective_from               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (model, effective_from)
);

CREATE TABLE usage_event (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   UUID NOT NULL,
    user_email        TEXT, agent TEXT, module_slug TEXT,
    model TEXT, tier TEXT,
    prompt_tokens INT, completion_tokens INT, cached_tokens INT,
    provider_cost_usd NUMERIC(14,8),          -- what it cost YOU
    billed_credits    NUMERIC(14,4),          -- what you charge THEM
    request_id        TEXT NOT NULL UNIQUE,   -- ← idempotency. Not decoration.
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE credit_ledger (               -- APPEND-ONLY. Never UPDATE a balance.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    delta         NUMERIC(14,4) NOT NULL,   -- + top-up, − consumption
    reason        TEXT NOT NULL, ref TEXT,
    balance_after NUMERIC(14,4) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.1 Where the four hooks go

All four attach to the shipped choke point — `gateway/routes/v1_compat.py` and
`acb_llm/client.py::_emit_usage` (`:552`), which already computes tokens, cache stats and
USD cost, streaming included (`v1_compat.py:563-573`).

```
request → require_llm_api_auth   → ① resolve org from the key PREFIX (replaces the
                                     single box-wide LITELLM_MASTER_KEY at deps.py:448-472)
        → pre-flight             → ② Redis DECR against balance; 402 if exhausted
        → provider call          →    (unchanged)
        → _emit_usage            → ③ usage_event INSERT … ON CONFLICT (request_id) DO NOTHING
                                  ④ credits = tokens ÷ 1000 × rate_card
```

> ⚠️ **`request_id` uniqueness is not decoration.** Retries, reconnects and the streaming
> rebuild path all create double-write opportunities, and a customer billed twice for one
> call is a credibility event.
>
> ⚠️ **The gate is Redis, the ledger is Postgres.** The pre-flight check is on the hot path
> of every token; a Postgres round-trip there is a latency regression on every LLM call in
> the product.

---

## 6. MT-0c — the agent sandbox contract

🔴 **OWNER-GATE** — parent §0.9.3 and `work_plan.md` §6. Shape recorded so the ticket is
ready the moment it is un-parked; **do not build it before then.**

```
┌─ agent run ─────────────────────────────────────────────────┐
│  IN:  tenant_id · run_id · scoped credentials (expiring)     │
│       tool manifest · workspace mount                        │
│  OUT: tool calls over a tenant-bound API                     │
│                                                              │
│  ✗ NO database connection, driver or connection string       │
│  ✗ NO ambient env credentials (MT-0a is the prerequisite)    │
│  ✗ NO egress except an allowlist                             │
│  ✗ NO raw-SQL tool, ever, at any tier                        │
│  ✓ destroyed at end of run                                   │
└──────────────────────────────────────────────────────────────┘
```

**Assertion that must exist before any external tenant:**

```python
def test_agent_process_holds_no_database_credentials(agent_env):
    """Parent §1.8a makes this a CONDITION on the pooled decision, not a nicety.
    An agent that cannot open a connection cannot escape RLS, cannot set
    app.tenant_id, and cannot be SQL-injected into another tenant."""
    assert "DATABASE_URL" not in agent_env
    assert not any(k.endswith(("_DSN", "_URL")) and "postgres" in str(v).lower()
                   for k, v in agent_env.items())
```

Implementation ladder: container + seccomp + no-network-by-default → gVisor → Firecracker.
**Start at the first; design so the third is a swap.** Warm pools are an optimisation and
must never become the isolation boundary.

---

## 7. Runbooks

### 7.1 Onboarding a tenant (pooled)

```
1. INSERT organization (slug, display_name)          -- slug = subdomain
2. INSERT tenant_placement (tier='pool', target='primary')
3. Seed org_role + org_role_permission FOR THAT ORG
   ⚠️ 130_org_access_control.sql seeds by `slug='default'` — that seeding must
      become a parameterised function, or org #2 has no roles and no owner.
      OWNED BY: saas_multitenancy.md §11 MT-1j slice 1 (minted 2026-08-19).
      (130 seeds SIX org_role rows — the five assignable roles plus
      `agent_service`; three later migrations, 131/133/178, add grants to the
      same `default`-keyed rows and are part of the same extraction.)
4. INSERT user_identity (if new) + org_membership (owner)
5. Grant the `core` module entitlement + assign the owner a seat
6. Issue an llm_api_key; credit the trial balance in credit_ledger
7. Verify: sign in at <slug>.<domain>, /auth/me returns the expected
   features AND modules
```

> ⚠️ **Step 3 still silently produces an ownerless org — and the reason changed.**
> *(Re-measured 2026-08-19; the previous text said "until MT-1i lands" and MT-1i has
> landed.)* MT-1i **did** fix the unscoped guard: `_HAS_OWNER_SQL` is now at
> **`access.py:572`** and joins `organization o ON … o.slug = :org_slug`. But the fix bound
> that parameter to a **literal** — `_BOOTSTRAP_ORG_SLUG = "default"` (`access.py:540`),
> passed into both the guard (`:614`) and the insert (`:630`) — so
> `ensure_owner_bootstrap()` is no longer a no-op *for `default`* and **can never provision
> an owner into any other organization at all.** The conclusion is unchanged and this is
> still the single most likely way tenant #2 fails; the owner is now
> **`saas_multitenancy.md` §11 MT-1j slice 2**, which must fix it *without* re-pointing
> that constant (**D36.3**: the `default` bootstrap stays the fresh-box path and
> provisioning a customer org never routes through it).

### 7.2 Cutting a silo customer over to pooled — 🔴 OWNER-GATE to execute

Only works because every silo runs the pooled schema from day one (parent §5.1
condition 2). If it did not, this is a rewrite rather than a runbook.

```
1. Freeze writes (maintenance flag)
2. pg_dump --data-only the silo, with its organization_id already populated
3. Load into the pooled cluster; verify row counts per table
4. Copy blobs: <silo>/… → <org_id>/… in object storage
5. Repoint tenant_placement.target → 'primary'
6. Smoke: sign in, /auth/me, one read per owned module
7. Unfreeze. Keep the silo cold for 7 days — do not decommission on the same day
```

### 7.3 Verifying isolation before the first external tenant

```bash
# every table is scoped
uv run pytest tests/unit/test_tenant_coverage.py -v -rs
# unbound access returns nothing, not someone else's rows
uv run pytest tests/unit/test_tenant_binding.py -v -rs
# the ratchets
uv run pytest tests/unit/test_db_engine_seam.py tests/unit/test_psycopg_seam.py -v
# the agent holds no DB credentials  (MT-0c)
uv run pytest tests/unit/test_agent_isolation.py -v
# manual, two-org fixture: A cannot see B on every owned surface
uv run pytest tests/integration/test_cross_tenant.py -v -rs
```

> ⚠️ **Never run these against production.** `test_owner_bootstrap.py` already carries that
> warning (`tenancy_and_visibility.md` §7) and it applies to every test here.

---

## 8. The traps, each with the thing that will find it

| # | Trap | How it presents |
|---|---|---|
| 1 | `SET` instead of `SET LOCAL` | Works in dev (one connection). In prod, a random request reads the previous borrower's tenant. **Load-test with a small pool to reproduce** |
| 2 | `SET LOCAL` outside a transaction | Silent no-op → every query returns zero rows → reads as "the feature is broken" |
| 3 | App connects as table owner | Every test passes; RLS never applies. **`test_tenant_coverage` checks `relforcerowsecurity`, not just `relrowsecurity`, for exactly this** |
| 4 | A background job with no tenant | Not one row — **unbounded**. Job records must carry `organization_id` and refuse to run without it |
| 5 | Role seeding still keyed `slug='default'` | Org #2 gets no roles and no owner. Runbook 7.1 step 3. **Owner: `saas_multitenancy.md` §11 MT-1j slice 1** (2026-08-19) |
| 6 | ~~`_HAS_OWNER_SQL` unfiltered~~ → **filtered to a LITERAL** | ⚠️ *Restated 2026-08-19.* MT-1i filtered it (`access.py:572`) — and bound `:org_slug` to `_BOOTSTRAP_ORG_SLUG = "default"`, so org #2 is **still** ownerless and nobody can grant access back. **A lockout RLS does not fix. Owner: MT-1j slice 2** |
| 6a | An organization row with no `tenant_placement` / `org_placement` | Tenant plane: `placement.resolve_placement` refuses, correctly. Console plane: `store.py:625`'s **inner join** means the org can never be resolved by any deployment key, and it reads as "the Console is down". **Owner: MT-1j slices 3+4** (2026-08-19) |
| 6b | `ON CONFLICT (email)` after migration 162 | 162 dropped `app_user_email_key`, leaving only `app_user_email_lower_key ON (lower(email))`. Two upserts still name the bare column (`access.py:550`, `admin/_common.py:599`) — predicted **42P10** at runtime, invisible to a hermetic fake. **Owner: MT-1j slice 6**, which must reproduce it RED on a real ladder first (2026-08-19) |
| 7 | Redis key without a tenant prefix | Cross-tenant cache/presence bleed, invisible to every DB test. **MT-1e's wrapper is the fix; a convention is not** |
| 8 | `usage_event` without `request_id` unique | Double billing on retry |
| 9 | Entitlement checked in the UI only | Module dark in nav, scheduler still polling, still costing you money |
| 10 | Composite PKs deferred | Cheap in MT-1b, needs a full FK-graph rewrite afterwards |

---

## 9. Verification

```bash
# §2.1 — the seam, and that nothing else creates an engine
grep -n "SET LOCAL\|def tenant_session\|def get_db" packages/acb_common/acb_common/db.py
grep -rn "create_async_engine\|create_engine(\|psycopg.connect" --include=*.py apps packages

# §1.3 — the app role has no bypass
psql -c "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname='acb_app'"

# §1.1 — policies are FORCED, not merely enabled
psql -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
         WHERE relkind='r' AND relnamespace='public'::regnamespace AND NOT relforcerowsecurity"

# §4.1 — the intersect seam entitlements reuse
grep -n "def intersect" -A 12 packages/acb_auth/acb_auth/permissions.py

# §5.1 — the metering choke point
grep -n "def _emit_usage" -A 30 packages/acb_llm/acb_llm/client.py
grep -n "require_llm_api_auth" -A 25 packages/acb_auth/acb_auth/deps.py
```

---

## 10. References

**Parent (owns every decision here):** [`saas_multitenancy.md`](saas_multitenancy.md) —
§0.1 connection inventory · §0.9 the three planes and the sandbox contract · §1 tenancy ·
§2 entitlements · §3 credits · §4 billing · §5.1 rollout · §6 blockers · §11 tickets.

**Binding neighbours:** [`user_management_contract.md`](user_management_contract.md) (R11
is the tenant-resolution rule this doc implements) ·
[`tenancy_and_visibility.md`](tenancy_and_visibility.md) §2–§5 (visibility *inside* a
tenant — unchanged and still binding) ·
[`org_access_control.md`](org_access_control.md) (the shipped RBAC these shapes extend) ·
[`permissions_sandbox_b6.md`](permissions_sandbox_b6.md) (P5-c is MT-0c) ·
`work_plan.md` WS-29 · D15 · §6 owner-gate registry.
