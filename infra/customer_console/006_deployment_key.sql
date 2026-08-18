-- ============================================================================
-- 006_deployment_key.sql — the FOURTH credential: a box asking about a person
-- ============================================================================
-- Spec: customer_console.md §6 CP-2b (a) · §5.2 · D32.4.
--
-- Sign-in resolution needs a credential that none of the three existing schemes
-- can be:
--
--   * the OPERATOR token is cross-organization and staff-held, and the service
--     already says in its own words why a tenant deployment must not hold it
--     ("a tenant deployment holding a cross-organization credential is the whole
--     thing D32/D35 are arranged to avoid");
--   * the INTERNAL token is the Router's, and equally cross-organization;
--   * the ORGANIZATION key is org-scoped, which is right for /me and wrong here
--     — a POOLED deployment must resolve ANY email to ITS org, and the org is
--     not known before the answer.
--
-- So a deployment key: `cc_depl_<prefix>_<secret>`, minted by `keys.mint_key`
-- with env='depl' (already a parameter, not a fork), one row per deployment.
--
-- ⚠️ A SIBLING TABLE, not a column on `llm_api_key`. That table's owner is an
-- `organization` (001:212-214, `organization_id UUID NOT NULL`); this key's
-- owner is a `deployment` (001:82). Hanging it off a nullable organization id
-- would make "which kind of key is this" a property of a NULL, and every query
-- downstream would have to remember to ask.
--
-- ⚠️ `capabilities`, not `scopes` — and the divergence from the neighbour
-- (`llm_api_key.scopes TEXT[]`, 001:220) is deliberate and specified. These are
-- not the same vocabulary: `scopes` there is an unused, empty-defaulted column
-- on the customer's LLM credential; `capabilities` here is a NON-empty,
-- enforced set whose only member today is `resolve`, checked as a dependency
-- before the endpoint runs. A shared name would invite a shared reader, and the
-- two answer different questions. Recorded here so the next person who notices
-- the asymmetry finds the reason rather than "fixing" it.
--
-- R6: additive, `IF NOT EXISTS` throughout, nothing renamed. The suites replay
-- the whole ladder twice (`tests/unit/_customer_console_ladder.py`), so a
-- statement that is not replay-safe fails on a real server, not in review.
-- ============================================================================

CREATE TABLE IF NOT EXISTS deployment_key (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The OWNER of this credential. NOT NULL: a deployment key that belongs to
    -- no deployment could not answer the one question it exists for ("which
    -- organizations are placed on the box that is asking").
    deployment_id   UUID NOT NULL REFERENCES deployment(id),
    -- Looked up by prefix, verified by hash — the same split `llm_api_key`
    -- uses (001:215-218). The prefix is safe to log and to show; the secret is
    -- never stored.
    prefix          TEXT NOT NULL UNIQUE,
    key_hash        TEXT NOT NULL,
    label           TEXT,
    -- Exactly `{resolve}` today. NOT NULL with a default so a hand-inserted row
    -- cannot mint a key whose capability set is NULL — which `= ANY(NULL)`
    -- would quietly answer NULL for, i.e. neither granted nor denied.
    capabilities    TEXT[] NOT NULL DEFAULT '{resolve}',
    created_by      TEXT,
    -- Defaults mirror `llm_api_key` rather than the spec's sketch, which elides
    -- them for every column: a key with no creation time is a key no audit can
    -- order.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

COMMENT ON TABLE deployment_key IS
    'THE KEY RESOLVES THE DEPLOYMENT, and the deployment bounds what may be '
    'seen: a holder learns org id, slug, placement target, lifecycle status and '
    'seat outcome for one presented email, and only for organizations whose '
    'CURRENT org_placement names this deployment. Never a balance, never an '
    'invoice, and never the existence of an organization this deployment does '
    'not serve (customer_console.md §6 CP-2b clauses 4 and 5).';

COMMENT ON COLUMN deployment_key.capabilities IS
    'Enforced as a dependency before the route body runs, never as a per-route '
    'if. Exactly {resolve} today; a capability set of one is the only one that '
    'is obviously right, and placement heartbeat is a NAMED non-goal of CP-2b.';
