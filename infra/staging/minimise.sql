-- ════════════════════════════════════════════════════════════════════════════
-- Reduce a restored copy of production to the organizations staging may hold.
-- WS-38 T-9, project-docs/specs/staging_and_daily_release.md §3 and §5.10.
--
-- Run it as a SUPERUSER on a THROWAWAY database that holds a restore of the
-- production dump, never on a live one:
--
--   psql -v ON_ERROR_STOP=1 -v keep_slugs=default,fracktalworks,hathi-labs-llp \
--        -d verify -f infra/staging/minimise.sql
--
-- `scripts/staging_export.sh` runs it inside the nightly verify container on the
-- production box, so the full copy never leaves that box (parent spec §3.4,
-- clause 1). Only the reduced dump travels to the staging box.
--
-- WHAT IT DOES, in one transaction:
--   1. Refuses unless every slug in keep_slugs names an organization.
--   2. Refuses when a public table has no organization_id and is not in the
--      reviewed list below. A new table must be classified here before staging
--      can take a copy. That is the fence for the next table someone adds.
--   3. Deletes every row of every other organization, in every org-scoped
--      table. A customer that is not on the list leaves NOTHING behind.
--   4. Keeps an identity only for a member of a kept organization.
--   5. Blanks every credential, in the kept organizations too. Staging holds
--      no production secret, so the ciphertext would only be dead weight.
--   6. Refuses when any credential-shaped column still holds a value. A new
--      credential column fails the export until it is blanked here.
--   7. Refuses when any JSON value looks like a live token (a bearer header,
--      an `sk-` key, a GitHub or Slack token, an AWS key, a private key).
--
-- FK triggers are off for the transaction (session_replication_role), because
-- the deletes run table by table in no dependency order. Rows of a kept
-- organization reference only rows of that organization, so nothing they need
-- is deleted.
-- ════════════════════════════════════════════════════════════════════════════

\set ON_ERROR_STOP on
BEGIN;
SET LOCAL session_replication_role = replica;
SELECT set_config('staging.keep_slugs', :'keep_slugs', true);

DO $minimise$
DECLARE
  slugs   text[] := string_to_array(current_setting('staging.keep_slugs'), ',');
  kept    uuid[];
  t       record;
  bad     text;
  n       bigint;
  deleted bigint := 0;
  -- Tables with NO organization_id, each with its reviewed rule.
  --   organization          filtered by id (below)
  --   schema_migrations     kept: the ledger the pending migrations apply against
  --   feature_catalog       kept: product reference data, no person in it
  --   user_identity         filtered to the members of the kept organizations
  --   agent_client          emptied: OAuth client registrations of outside agents
  --   auth_email_otp_token  emptied: one-time sign-in codes
  global_ok text[] := ARRAY['organization', 'schema_migrations', 'feature_catalog',
                            'user_identity', 'agent_client', 'auth_email_otp_token'];
BEGIN
  IF slugs IS NULL OR cardinality(slugs) = 0 OR slugs = ARRAY[''] THEN
    RAISE EXCEPTION 'keep_slugs is empty: refusing to export';
  END IF;
  SELECT array_agg(id) INTO kept FROM public.organization WHERE slug = ANY (slugs);
  IF kept IS NULL OR cardinality(kept) <> cardinality(slugs) THEN
    RAISE EXCEPTION 'keep_slugs names % slug(s), and % organization(s) match. Refusing.',
      cardinality(slugs), coalesce(cardinality(kept), 0);
  END IF;

  -- 2. Every table is classified, or nothing happens.
  SELECT string_agg(c.relname, ', ' ORDER BY c.relname) INTO bad
    FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
   WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
     AND c.relname <> ALL (global_ok)
     AND NOT EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                        AND NOT a.attisdropped);
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'unclassified table(s) with no organization_id: %. Classify each in infra/staging/minimise.sql.', bad;
  END IF;

  -- 3. Every other organization's rows go, NULL-org rows included.
  FOR t IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
       AND EXISTS (SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                      AND NOT a.attisdropped)
     ORDER BY 1
  LOOP
    EXECUTE format(
      'DELETE FROM public.%I WHERE organization_id IS NULL OR organization_id::text <> ALL ($1)',
      t.relname) USING kept::text[];
    GET DIAGNOSTICS n = ROW_COUNT;
    deleted := deleted + n;
  END LOOP;
  DELETE FROM public.organization WHERE id <> ALL (kept);
  RAISE NOTICE 'staging minimise: % row(s) of other organizations deleted', deleted;

  -- 4. Identities of the kept members only.
  DELETE FROM public.user_identity ui
   WHERE NOT EXISTS (SELECT 1 FROM public.org_membership m WHERE m.user_id = ui.id)
     AND NOT EXISTS (SELECT 1 FROM public.app_user u WHERE lower(u.email) = lower(ui.email));

  -- 5. Credentials, in the kept organizations too.
  TRUNCATE public.agent_client, public.auth_email_otp_token;
  DELETE FROM public.agent_token;
  DELETE FROM public.provider_keys;
  UPDATE public.email_accounts          SET credentials_encrypted = '', webhook_client_state = NULL;
  UPDATE public.integration_connections SET credentials_encrypted = NULL;
  UPDATE public.task_accounts           SET credentials_encrypted = '', last_delta_token = NULL;
  UPDATE public.wa_accounts             SET credentials_encrypted = '', webhook_verify_token = NULL;
  UPDATE public.workflows               SET hook_token = NULL;
  -- The legacy plaintext MCP auth (gateway/connections/mcp.py clears it only
  -- when a server moves to the encrypted registry, one org at a time).
  UPDATE public.mcp_servers             SET headers = '{}'::jsonb, env_vars = '{}'::jsonb;

  -- 6. Nothing credential-shaped is left. Counters (`*_tokens`, `token_cost`)
  --    are numbers, so the text and binary types alone are checked, by type
  --    OID: format_type would print `character varying(255)` and miss it.
  FOR t IN
    SELECT c.relname, a.attname
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
       AND a.attnum > 0 AND NOT a.attisdropped
       AND a.atttypid IN ('text'::regtype, 'bytea'::regtype, 'jsonb'::regtype,
                          'json'::regtype, 'varchar'::regtype, 'bpchar'::regtype)
       AND a.attname ~* '(secret|password|passwd|credential|private_key|api_?key|access_key|cookie|encrypted|cipher|refresh_token|verify_token|hook_token|token_hash|client_state|^token$|^headers$|env_vars|^env$)'
  LOOP
    -- Blank is '', an empty JSON object or array, or JSON null.
    EXECUTE format('SELECT count(*) FROM public.%I WHERE %I IS NOT NULL AND %I::text NOT IN (%L, %L, %L, %L)',
                   t.relname, t.attname, t.attname, '', '{}', '[]', 'null') INTO n;
    IF n > 0 THEN
      RAISE EXCEPTION 'credential column %.% still holds % value(s). Blank it in infra/staging/minimise.sql.',
        t.relname, t.attname, n;
    END IF;
  END LOOP;

  -- 7. A name says little about a JSON column, so its VALUES are checked too:
  --    nothing that looks like a live token may leave. A hit names the column,
  --    and the export stops until the column is blanked above.
  FOR t IN
    SELECT c.relname, a.attname
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition
       AND a.attnum > 0 AND NOT a.attisdropped
       AND a.atttypid IN ('jsonb'::regtype, 'json'::regtype)
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I WHERE %I::text ~ %L', t.relname, t.attname,
      '(Bearer [A-Za-z0-9._~+/=-]{16,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)')
      INTO n;
    IF n > 0 THEN
      RAISE EXCEPTION 'JSON column %.% holds % value(s) shaped like a live token. Blank it in infra/staging/minimise.sql.',
        t.relname, t.attname, n;
    END IF;
  END LOOP;
END
$minimise$;

COMMIT;

SELECT 'staging minimise: kept ' || string_agg(slug, ', ' ORDER BY slug) AS result
  FROM public.organization;
