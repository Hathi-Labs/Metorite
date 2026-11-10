-- ============================================================================
-- 249_crm_deal_contacts_ext_source.sql - which deal-contact links a provider owns.
--
-- What: one nullable column, `crm_deal_contacts.ext_source`. The mirror sync
--       writes 'zoho' on the links it makes. A link a person makes here has NULL.
-- Why:  WS-53 CRM-Q5. The roles sync removes the links that Zoho no longer
--       lists. Without a marker it could remove a link that a person made on a
--       mirrored deal. Now it removes only the rows marked 'zoho'.
-- Depends on: 144_crm.sql (crm_deal_contacts), 241_crm_tenancy.sql.
--
-- Expand only (R6): one nullable column, no default, no rename, no drop. Old
-- code names no new object. Idempotent: ADD COLUMN IF NOT EXISTS.
-- Tenant scope (R5): the table already has `organization_id` and its policy
-- from 241. This column changes neither.
-- ============================================================================

ALTER TABLE crm_deal_contacts ADD COLUMN IF NOT EXISTS ext_source TEXT;
