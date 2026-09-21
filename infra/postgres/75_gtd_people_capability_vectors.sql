-- 75_gtd_people_capability_vectors.sql — semantic capability matching for the
-- HR/assignment layer (spec: task_manager_hr_planning_and_memory.md §5, Phase 2).
--
-- What: a capability_embedding on people so the clarify/assignment engine can
--       rank owners by SEMANTIC fit (task text ⟷ a person's role+skills+résumé),
--       not just keyword overlap. capability_text_hash lets the embedder skip a
--       person whose derived capability text hasn't changed (embeddings cost
--       tokens), mirroring email_embeddings.content_hash (migration 73).
-- Why:  Phase 2 — "sharper assignment". ADDITIVE + flag-gated
--       (task_semantic_match_enabled, default OFF): keyword matching stays the
--       default and the guaranteed fallback, exactly like the email hybrid search
--       (semantic never DROPS a keyword candidate, only re-ranks/augments).
-- Depends on: 49_gtd_people.sql, 74_gtd_people_editable_and_resumes.sql.
-- pgvector already installed (migration 01 + email_embeddings). Idempotent.

CREATE EXTENSION IF NOT EXISTS vector;

-- 1536 = text-embedding-3-small (the same default the email embeddings use, so
-- one embedding model serves both). Switching to a different-dimension model
-- means recreating this column + re-embedding; the text hash below detects drift.
ALTER TABLE people ADD COLUMN IF NOT EXISTS capability_embedding vector(1536);
-- sha256 of the exact capability text embedded (role · title · skills · domain ·
-- résumé summary). Unchanged hash → the embedder skips this row next sweep.
ALTER TABLE people ADD COLUMN IF NOT EXISTS capability_text_hash TEXT;

-- The org's people table is small (dozens of rows), so an exact cosine scan is
-- cheap and an ANN index would add maintenance for no real gain. No ivfflat index
-- here (unlike email_embeddings, which spans a whole mailbox); revisit only if the
-- roster ever grows into the thousands.
