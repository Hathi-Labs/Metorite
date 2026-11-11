-- ============================================================================
-- 407_studio_draft_revisions.sql — the save history of a Studio draft
-- (WS-STU G17, Metorite task 2390).
--
-- What: one new table and one new column.
--       `studio_draft_revisions` holds one revision of the draft of one
--       package (an `apps` row with `format = 'manifest'`): its number in the
--       draft, the whole manifest, its hash, a diff against the revision
--       before it, a message, and the author (a member, Mira or the system).
--       `app_versions.draft_revision` names the revision that a publish took.
--       gateway/studio/packages_store.py is the one writer of both.
-- Why:  ai_studio.md §6 "Git in the IDE" (D202.9) and the §14 G17 row. G5
--       keeps one undo step only. A revision store comes first, so a member
--       can revert to any save, and a publish can name its revision.
-- Depends on: 114_custom_apps.sql (apps, app_versions), 130_org_access_control.sql
--             (organization), 323_studio_objects.sql (the key apps_org_id_key).
--
-- Expand only (R6): one new table, its key, one RLS block and one nullable
-- column on `app_versions` with no default. No rename, no drop, and no change
-- to a row. Old code names no new object, and an old INSERT into
-- `app_versions` leaves the new column NULL.
-- Idempotent per infra/postgres/README.md: ADD COLUMN IF NOT EXISTS, CREATE
-- TABLE IF NOT EXISTS, and an RLS block guarded on relforcerowsecurity.
-- ADD COLUMN takes the ACCESS EXCLUSIVE lock of `app_versions` for a moment,
-- so the file sets lock_timeout first. The runner tries the file again on a
-- lock timeout.
--
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 323 and
-- 402. `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The org of a new row comes from the DEFAULT, the bound tenant. The writer
-- never names it. The foreign key to the package is the pair
-- (organization_id, app_id), as in 323, because a foreign key check skips
-- RLS. So a revision never names a package of another org. It cascades: a
-- deleted package takes its revisions.
--
-- The order (G17 "two saves at once give two revisions in order"). The
-- writer holds the package row `FOR UPDATE` and takes the next number in the
-- INSERT itself (MAX + 1). `UNIQUE (app_id, seq)` is the backstop, and it
-- serves the list, newest first.
--
-- History is append-only. The store never updates or deletes a revision. A
-- revert writes a NEW revision with the old manifest, and `restores` names
-- the number that it copied.
--
-- The size caps. `manifest` holds at most 262144 bytes of JSON text, the
-- bound of `apps_manifest_package` (322). `diff` holds at most 262144 bytes.
-- A larger diff is not stored, and the store writes a short note in its
-- place. `message` holds at most 500 characters. The store refuses first,
-- with the same numbers.
--
-- People in a row. `author` is a member email, `mira` or `metorite`, and
-- `on_behalf_of` is the member who owns the package. Neither is a person
-- column of the member purge (`owner_email`, `user_email`, `member_email`):
-- a revision is part of the package, which the purge keeps (D63, as for
-- studio_records in 323).
--
-- `app_versions.draft_revision` has no foreign key. The package row lock of
-- a publish makes the read and the write one step, and a foreign key would
-- add a lock between `app_versions` and this table for no gain.
--
-- The number is taken at build time (R1). On 2026-11-11 origin/main held
-- 402, and open pull request branches held 403, 404, 405 and 406. This file
-- was 406 first, and an open pull request took 406. Check it again at merge.
-- Pinned by tests/unit/test_studio_draft_revisions_r8.py, which finds this
-- file by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';

-- ── 1. The revision that a publish took ─────────────────────────────────────
ALTER TABLE app_versions
    ADD COLUMN IF NOT EXISTS draft_revision INTEGER NULL;

-- ── 2. The revisions of a draft ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS studio_draft_revisions (
    organization_id   UUID NOT NULL
                      REFERENCES organization (id) ON DELETE CASCADE
                      DEFAULT current_setting('app.tenant_id', true)::uuid,
    id                UUID NOT NULL DEFAULT gen_random_uuid(),
    -- The package: an `apps` row of the same org.
    app_id            UUID NOT NULL,
    -- The number of the revision in its draft: 1, 2, 3 and on.
    seq               INTEGER NOT NULL,
    -- The whole draft after this save, so a revert never needs a replay.
    manifest          JSONB NOT NULL,
    -- studio_manifest.manifest_hash of `manifest`, the one hash of a draft.
    manifest_hash     TEXT NOT NULL,
    -- A unified diff of the revision before it, as text. '' for revision 1.
    diff              TEXT NOT NULL DEFAULT '',
    message           TEXT NOT NULL DEFAULT '',
    -- `member`, `mira` or `system`. The server sets it, never a body.
    author_kind       TEXT NOT NULL,
    -- A member email, `mira` or `metorite`.
    author            TEXT NOT NULL,
    -- The member who owns the package and for whom the save ran.
    on_behalf_of      TEXT NOT NULL,
    -- The number of the revision that a revert copied. NULL for a save.
    restores          INTEGER NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id),
    CONSTRAINT studio_draft_revisions_seq_key UNIQUE (app_id, seq),
    CONSTRAINT studio_draft_revisions_package_fk FOREIGN KEY (organization_id, app_id)
        REFERENCES apps (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT studio_draft_revisions_seq CHECK (seq >= 1),
    CONSTRAINT studio_draft_revisions_manifest_object CHECK (
        jsonb_typeof(manifest) = 'object'),
    CONSTRAINT studio_draft_revisions_manifest_size CHECK (
        octet_length(manifest::text) <= 262144),
    CONSTRAINT studio_draft_revisions_hash CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT studio_draft_revisions_diff_size CHECK (octet_length(diff) <= 262144),
    CONSTRAINT studio_draft_revisions_message CHECK (char_length(message) <= 500),
    CONSTRAINT studio_draft_revisions_author_kind CHECK (
        author_kind IN ('member', 'mira', 'system')),
    CONSTRAINT studio_draft_revisions_author CHECK (
        author <> '' AND author = lower(author)),
    CONSTRAINT studio_draft_revisions_on_behalf_of CHECK (
        on_behalf_of <> '' AND on_behalf_of = lower(on_behalf_of)),
    CONSTRAINT studio_draft_revisions_restores CHECK (
        restores IS NULL OR (restores >= 1 AND restores < seq))
);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('studio_draft_revisions')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE studio_draft_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE studio_draft_revisions FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS studio_draft_revisions_tenant_isolation ON studio_draft_revisions;
        CREATE POLICY studio_draft_revisions_tenant_isolation ON studio_draft_revisions
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE studio_draft_revisions IS
    'One save of the draft of one Studio package, append-only (WS-STU G17). '
    'Only gateway/studio/packages_store.py writes it. ai_studio.md §14 G17.';
COMMENT ON COLUMN app_versions.draft_revision IS
    'The studio_draft_revisions.seq that a Studio publish took, or NULL (WS-STU G17).';

COMMIT;
