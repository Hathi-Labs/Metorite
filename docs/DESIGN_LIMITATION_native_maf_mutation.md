# ⚠️ DESIGN LIMITATION — native-MAF self-mutation via monorepo PR is DEV-ONLY

**Status:** interim mechanism, in use while Metorite is a work in progress.
**Must be replaced before:** any production / multi-tenant deployment where agents
are run on behalf of third parties or customers. *(ticketed: saas_multitenancy.md
MT-0b — built 2026-08-08, pending review; root AGENTS.md constraint 3 tracks it)*
**Owner decision:** flagged 2026-07-15 — "we have to figure this out later."

---

## What we do today

A **native MAF agent** (runtime `maf`, registered by `local_path` only — e.g.
`email-assistant`, `task-manager`) runs from an isolated local-only git clone with
no remote of its own. When such an agent proposes a fix to its own code and an
operator **approves** it in the Agents inbox, the gateway opens a **pull request
against the Metorite monorepo** that edits the agent's source at
`apps/agents/agent-<name>/` in place. Merging that PR makes the fix durable source
that ships on the next deploy.

Code: `apps/services/gateway/gateway/routes/monorepo_pr.py`, wired into
`approve_pending_commit` in `apps/services/gateway/gateway/routes/agent.py`
(the `has_remote == False` branch). Gated by `mutation_monorepo_repo` +
`mutation_pr_token` settings; disabled (falls back to keep-local) when unset.

This is correct and fine **right now**, because every native MAF agent is a
first-party agent that lives inside our own monorepo, and we are the only ones
approving mutations.

## Why this CANNOT go to production as-is

The mechanism routes an agent's self-improvement into **the shared Metorite
monorepo**. That is unacceptable the moment Metorite is multi-tenant:

- **Third parties / customers would be pushing to our monorepo.** A customer-run
  MAF agent mutating itself must NOT open PRs against — let alone land code in —
  the repository that is the source of truth for *everyone's* Metorite.
- **Blast radius.** A monorepo merge ships to every deploy. One tenant's agent fix
  cannot be allowed to alter the platform all tenants run on.
- **Trust boundary.** Approval today is a single first-party operator. In
  production, the approver and the code owner are different parties per tenant.

## What has to be figured out (the real production design)

We need a mechanism where a native MAF agent's approved mutation is durable and
reviewable **without touching the shared platform monorepo**. Open options to
evaluate when we get there (not yet decided):

1. **Per-tenant / per-agent repo.** Each customer's agent gets its own git repo
   (their org, or a tenant-scoped repo we manage); mutations PR there, never the
   platform monorepo. Closest to how GitHub-Copilot agents already work.
2. **Tenant-scoped store, not git.** Approved agent code lives in a per-tenant
   store (DB/object storage) that the loader reads at runtime, so "the platform"
   and "a tenant's agent code" are physically separate. Pairs naturally with the
   Part 2 blob-store work (files/memory → Postgres, store-authoritative).
3. **Fork/branch isolation.** Platform monorepo stays first-party-only; tenant
   agents are provisioned into isolated forks with their own approval + deploy
   lane.

The tenancy model **was settled on 2026-08-08** (D15: organization_id + RLS,
deployment = placement — `project-docs/specs/saas_multitenancy.md` §1), and
this limitation is ticketed as **MT-0b (WS-29)**: a config gate defaulting to
disabled, **BUILT 2026-08-08 pending review** (migration 157 adds
`organization.first_party`; mutation refuses non-first-party targets). This note
stays until MT-0b is merged and verified.

## Guardrail until then

- Keep `mutation_monorepo_repo` **pointed only at our own first-party monorepo**
  and only in first-party/dev environments.
- Do **not** enable the monorepo-PR path for any agent that is not first-party.
- Before shipping multi-tenant Metorite, replace this path per the design
  chosen above and delete this limitation once resolved. *(= merging MT-0b; see
  above)*

See also: the `mutation_monorepo_repo` / `mutation_pr_token` settings docstrings in
`packages/acb_common/acb_common/settings.py`, and the header of
`infra/postgres/70_pending_commit_pr_mode.sql`.
