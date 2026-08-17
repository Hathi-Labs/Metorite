-- Native-MAF mutation → monorepo PR (Part 1).
--
-- ⚠️ DEV-ONLY MECHANISM — MUST BE REPLACED BEFORE PRODUCTION / MULTI-TENANCY.
-- Landing an agent's self-mutation as a PR against the shared Metorite
-- monorepo is acceptable only while every agent is first-party and Command
-- Center is a work in progress. Third parties / customers must NOT push to the
-- shared monorepo. Swap for a tenant-isolated mechanism before production.
-- See docs/DESIGN_LIMITATION_native_maf_mutation.md.
--
-- A native MAF agent (runtime "maf", local_path only) runs from an isolated
-- local-only clone with NO git remote, so approving its self-mutation used to
-- be a no-op ("kept local") and the change was silently clobbered on the next
-- deploy re-seed. Instead, approval now opens a PR against the Metorite
-- monorepo that edits apps/agents/agent-<name>/ in place — so an approved fix
-- becomes durable, reviewable source that ships on the next deploy.
--
-- These columns record the PR outcome. GitHub-Copilot agents (which push to
-- their own repo) are unaffected: mutation_mode stays 'push' for them.

ALTER TABLE pending_commit
    -- 'push'   → agent has its own git remote; approve pushes there (Copilot,
    --            or a GitHub-sourced MAF agent). Existing behaviour.
    -- 'monorepo_pr' → native MAF agent; approve opens a Metorite PR.
    ADD COLUMN IF NOT EXISTS mutation_mode TEXT NOT NULL DEFAULT 'push'
        CHECK (mutation_mode IN ('push', 'monorepo_pr')),
    -- The monorepo path the agent's source lives at, e.g.
    -- 'apps/agents/agent-task-manager' (from the registry local_path). NULL for
    -- push-mode commits.
    ADD COLUMN IF NOT EXISTS target_path TEXT,
    -- The opened pull-request URL (set once the PR is created). NULL until then.
    ADD COLUMN IF NOT EXISTS pr_url TEXT;

-- New terminal-ish status: the PR is open and awaiting merge on GitHub. The
-- commit is "approved" from the operator's side but not yet merged upstream.
ALTER TABLE pending_commit DROP CONSTRAINT IF EXISTS pending_commit_status_check;
ALTER TABLE pending_commit
    ADD CONSTRAINT pending_commit_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'eval_failed', 'pr_open'));
