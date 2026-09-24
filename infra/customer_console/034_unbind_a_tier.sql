-- Let an operator take a tier OFF the air. H-178.
--
-- 🔴 **There was no way to unbind a job, and that made a broken tier
-- permanent.** Owner report, 2026-09-24: `tier-stt` points at a Groq model on
-- a box whose only credential is DeepSeek, so every transcription fails at the
-- provider and bills zero on the way. The advice was "unbind it" and the
-- console could not: `POST /catalog/bindings` refuses an empty chain, and the
-- board's Save greys out with "this job has no model left".
--
-- ⚠️ **Deleting the rows is NOT the fix, and §6A.5 says why.** `tier_binding`
-- is insert-only *"because a past invoice was computed against"* it. Removing
-- the rows would destroy the record of what served a call somebody has already
-- been billed for.
--
-- So an unbound job is an APPEND like every other change to this table: one
-- row at a new `effective_from` whose `model` is NULL. The newest set wins, as
-- it already does, and every earlier set stays readable as history.
--
-- ⚠️ **R6 — this widens and never tightens.** Dropping NOT NULL is the expand
-- direction: old code that only ever inserts a concrete model keeps working
-- against the new schema, which is what lets the migration land before the
-- code that uses it.
--
-- ⚠️ **Every reader must now filter `model IS NOT NULL`.** A reader that
-- forgets would hand the Router a model named NULL and fail at the provider —
-- the same shape as the bug this exists to let an operator fix.
-- `tests/unit/test_unbind_a_tier.py` is the fence, and it scans the tree for
-- a `FROM tier_binding` read that lacks the filter.

ALTER TABLE tier_binding ALTER COLUMN model DROP NOT NULL;

-- A tombstone carries no rank meaning, so pin it to 1. Without this a second
-- tombstone for the same (tier, task, effective_from) could collide on the
-- primary key in a way nobody could read back.
COMMENT ON COLUMN tier_binding.model IS
    'The model this rank serves. NULL is a TOMBSTONE: the job is unbound as of '
    'this effective_from, and every reader must filter it out (H-178).';
