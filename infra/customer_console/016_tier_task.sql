-- 016 — a tier serves ONE kind of job, and the registry says which. D68.
--
-- 🔴 **The owner's observation (2026-08-30), verbatim in spirit: "why does
-- every tier ask WHICH JOB?"** The slate (015) already dedicates a tier to
-- each capability — tier-stt IS speech-to-text, tier-image IS image
-- generation — and the chat bands (fast/balanced/powerful/code) are quality
-- bands OF CHAT. A per-tier job dropdown re-asks a question the tier's own
-- name already answered, and lets an operator bind speech-to-text onto
-- "Fast" by mis-click.
--
-- So the registry gains `task`: the tier's category. The board stops asking,
-- the binding and rate writes refuse a mismatch, and the (task, tier) call
-- grammar (D60) is untouched — this narrows what is CONFIGURABLE, never what
-- a call can name.
--
-- ⚠️ **Nullable, deliberately (R6).** A ghost tier (a binding whose tier is
-- not registered) has no registry row at all, and a registry row written
-- before this migration has no task. NULL means "uncategorised": the board
-- shows it under its own heading and the mismatch checks do not fire. The
-- slate's own rows are backfilled below, so every shipped tier is
-- categorised from the first read.

ALTER TABLE tier_catalog
    ADD COLUMN IF NOT EXISTS task TEXT REFERENCES task_catalog(slug);

UPDATE tier_catalog SET task = v.task
FROM (VALUES
    ('tier-fast',     'chat'),
    ('tier-balanced', 'chat'),
    ('tier-powerful', 'chat'),
    ('tier-code',     'chat'),
    ('tier-vision',   'vision'),
    ('tier-image',    'image'),
    ('tier-stt',      'transcribe'),
    ('tier-tts',      'speak'),
    ('tier-embed',    'embed'),
    ('tier-video',    'video'),
    ('tier-music',    'music')
) AS v(slug, task)
WHERE tier_catalog.slug = v.slug AND tier_catalog.task IS NULL;

COMMENT ON COLUMN tier_catalog.task IS
    'The ONE kind of job this tier serves (D68). The chat bands say chat; '
    'each capability tier says its capability. NULL = uncategorised (a '
    'pre-016 row); the mismatch refusals in the binding and tier-rate '
    'writes fire only when this is set.';
