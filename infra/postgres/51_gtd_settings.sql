-- 51_gtd_settings.sql — per-user Task Manager settings (AI tiers + toggles).
--
-- What: user_settings — one row per user: which model tier each AI function
--       of the tasks app uses (assistant chat · mind-dump atomizer/dedup ·
--       email→task drafting · clarify cognition), plus behaviour toggles
--       (duplicate check on quick capture, auto-sync on open).
-- Why:  parity with the email app's per-account model roles (42_email_model_
--       roles.sql): the user picks cost/quality per function instead of one
--       global model. GTD settings are per USER (the GTD system is personal),
--       not per connected workspace.
-- Depends on: nothing (standalone). Idempotent.

-- == The `gtd_` name is retired (owner directive, 2026-09-21) ================
--
-- **THE RENAME LIVES IN THE FILE THAT CREATES THE TABLE, and that is what
-- makes it safe.** The obvious alternative -- one migration at the end that
-- renames -- breaks every earlier file on replay: an ALTER TABLE addresses a
-- name that is gone, and CREATE TABLE IF NOT EXISTS happily builds an empty
-- duplicate beside the real one. Both shapes were measured on 2026-09-21 and
-- both were abandoned. `tests/unit/test_people_rename_upgrade.py` carries the
-- full argument, and slice 1 (the People family) shipped on this mechanism.
--
-- One file answers for one table in all three states:
--
--   * Fresh install -- nothing to rename, the CREATE below makes the new
--     name directly.
--   * A database that predates this -- the old table is renamed WITH ITS
--     ROWS, and the CREATE below then finds the name taken and skips.
--   * Replay -- already renamed, the guard matches nothing. Idempotent.
--
-- relkind = 'r' so a view wearing the old name is left alone rather than
-- renamed into the table's place.
--
-- Index, constraint and POLICY names keep their old spelling. A rename does
-- not touch them, and no query names one.
--
-- WARNING: this block names the gtd_settings table, so it must survive a rename
-- sweep. Slice 1 added it BEFORE sweeping the tree, and the sweep rewrote
-- `ALTER TABLE gtd_settings RENAME TO user_settings` into `ALTER TABLE user_settings RENAME TO user_settings` -- a
-- silent no-op that left an upgraded database on the old tables with new
-- empty ones beside them. Sweep FIRST, add this SECOND. The old name is
-- spelled ONCE here, as a quoted literal passed to format().

DO $rename_user_settings$
DECLARE
    old_name CONSTANT text := 'gtd_settings';
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = old_name AND c.relkind = 'r'
           AND n.nspname = current_schema()
    ) AND to_regclass('public.user_settings') IS NULL THEN
        EXECUTE format('ALTER TABLE %I RENAME TO %I', old_name, 'user_settings');
        RAISE NOTICE 'renamed % -> user_settings', old_name;
    END IF;
END
$rename_user_settings$;

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    chat_model TEXT,              -- assistant rail (default tier-powerful)
    clarify_model TEXT,           -- clarify cognition when the agent takes it over (default tier-balanced)
    atomize_model TEXT,           -- mind-dump splitting + duplicate judgment (default tier-fast)
    email_capture_model TEXT,     -- email→task capture drafting (default tier-fast)
    capture_dedup BOOLEAN NOT NULL DEFAULT true,   -- background duplicate check on quick capture
    auto_sync_on_open BOOLEAN NOT NULL DEFAULT true, -- incremental provider pull when /tasks opens
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
