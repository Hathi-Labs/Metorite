-- ============================================================================
-- 360_email_signature_images.sql — the hosted images of email signatures
-- (WS-17 SG-1, Metorite task 2177).
--
-- What: one new table, `email_signature_images`. Each row is one logo or one
--       photo that a member uploaded for a signature. A confined child
--       process decoded it and wrote it again as PNG or JPEG, so the row
--       holds the output of that child and never the bytes that the member
--       sent. The public route `GET /email/signature-image/{org}/{token}.{ext}`
--       serves it to every reader of a mail, with no sign-in.
-- Why:  WS-17 SG-1, spec project-docs/specs/email_app_master_plan.md §23.3.4,
--       D-EM-67 and D-EM-68. Gmail does not draw a `data:` image, so sent
--       mail must carry an https image address that lives for ever.
-- Depends on: 130_org_access_control.sql (organization).
--
-- Expand only (R6): one new table and one index. No rename, no drop, and no
-- change to an existing table. Old code names no new object.
-- Idempotent per infra/postgres/README.md: CREATE TABLE IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, and an RLS block guarded on
-- relforcerowsecurity. A second run changes nothing.
-- Tenant-scoped (R5): `organization_id` with ENABLE and FORCE row level
-- security and one policy with USING and WITH CHECK, in the shape of 341.
-- `REFERENCES` comes before `DEFAULT`, because the tenancy ratchet in
-- tests/unit/test_tenancy_boundary.py matches the two with no comma between.
-- The foreign key is inline, never `ADD CONSTRAINT <table>_org_fk`, because
-- generated phase 3 adds that name.
-- The capability (D-EM-68): `public_token` holds 256 random bits as 43
-- URL-safe base64 characters. The public route binds `tenant_session(org)`
-- from the URL and selects the one row whose token matches, under RLS. So a
-- token of one org under the id of another finds nothing. The route uses no
-- MAC, so a rotation of a gateway secret never breaks a logo that went out.
-- One copy of each image: the pair `(organization_id, sha256)` is unique, and
-- `sha256` is the hash of the stored bytes.
-- Nothing deletes a row while the org exists (D-EM-67). A retire sets
-- `retired_at`, and the public route still serves the image, so a mail that
-- went out last year keeps its logo. The cascade of the org removes the rows.
-- `created_by` is the member of the session, never a value of the request. It
-- has no foreign key, so a member purge keeps the image of the org.
-- The caps (100 for each org, 20 for each member) live in the route, under an
-- advisory lock of the org (gateway/routes/email/signature_images.py).
-- The number is taken at build time (R1). On 2026-11-07 origin/main held 348
-- (348_agent_connection_revoke_reason.sql), and 346 is a gap. Open PR
-- branches held 349 to 354 (354_agent_avatars_key_per_tenant.sql), so this
-- file took 355. A re-check the same day found open PR branches with 355
-- (355_kb_links_graph_edges.sql, the KG-4 to KG-7 branches) and 356
-- (356_user_settings_appearance.sql). No main file, open PR or worktree held
-- 357, so this file took 357. Check it again at merge (premerge.sh).
-- 2026-11-08, at merge: main held 359 (359_pm_decisions_person_ask.sql) and
-- open PRs held 351, 356 and 358, so the file moved from 357 to 360.
-- Pinned by tests/unit/test_email_signature_images_r8.py, which finds this file
-- by CONTENT, never by number (R1).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS email_signature_images (
    id               UUID NOT NULL DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL
                     REFERENCES organization (id) ON DELETE CASCADE
                     DEFAULT current_setting('app.tenant_id', true)::uuid,
    -- The member of the session who uploaded it.
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set by a retire. The image leaves the picker and stays served.
    retired_at       TIMESTAMPTZ NULL,
    kind             TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    bytes            BYTEA NOT NULL,
    sha256           TEXT NOT NULL,
    public_token     TEXT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT email_signature_images_org_sha UNIQUE (organization_id, sha256),
    CONSTRAINT email_signature_images_org_token UNIQUE (organization_id, public_token),
    CONSTRAINT email_signature_images_created_by CHECK (
        created_by <> '' AND created_by = lower(created_by)
        AND char_length(created_by) <= 320),
    CONSTRAINT email_signature_images_kind CHECK (kind IN ('logo', 'photo')),
    CONSTRAINT email_signature_images_type CHECK (
        content_type IN ('image/png', 'image/jpeg')),
    CONSTRAINT email_signature_images_size CHECK (
        width BETWEEN 1 AND 480 AND height BETWEEN 1 AND 480),
    CONSTRAINT email_signature_images_bytes CHECK (
        octet_length(bytes) BETWEEN 1 AND 204800),
    CONSTRAINT email_signature_images_sha256 CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT email_signature_images_token CHECK (
        public_token ~ '^[A-Za-z0-9_-]{43}$')
);

-- The cap of one member.
CREATE INDEX IF NOT EXISTS idx_email_signature_images_created_by
    ON email_signature_images (organization_id, created_by);

DO $rls$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = to_regclass('email_signature_images')
           AND relrowsecurity
           AND relforcerowsecurity
    ) THEN
        ALTER TABLE email_signature_images ENABLE ROW LEVEL SECURITY;
        ALTER TABLE email_signature_images FORCE  ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS email_signature_images_tenant_isolation
            ON email_signature_images;
        CREATE POLICY email_signature_images_tenant_isolation
            ON email_signature_images
            USING      (organization_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
END
$rls$;

COMMENT ON TABLE email_signature_images IS
    'WS-17 SG-1: one hosted signature image, served for ever at a public URL. '
    'Only gateway/routes/email/signature_images.py writes it. '
    'email_app_master_plan.md §23.3.4.';

COMMIT;
