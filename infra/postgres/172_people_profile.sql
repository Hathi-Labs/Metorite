-- 172_people_profile.sql — the person record grows a profile (People Center P-3).
--
-- What: the columns §3 of `project-docs/specs/people_center_app.md` names —
--       the self-describing half (§3.1), the employment half (§3.2), one
--       availability field (§3.4) and the private contact half (§3.5).
-- Why:  the record answers "name, department, some skill words" today, which is
--       enough to render a directory and not enough to reason with. WS-28g adds
--       what the assignment/reporting AI actually needs (timezone, working
--       hours, seniority, engagement dates, stated ceilings) and what a person
--       needs in order to describe themselves. Every column here is cited in
--       the spec's §3 table beside the question it answers; a column that names
--       no question does not belong (spec §3.6).
-- Depends on: 49_gtd_people.sql, 74_gtd_people_editable_and_resumes.sql,
--       148_people_key_shape.sql.
--
-- ── R6: this is the EXPAND half, and nothing here may block a deploy ────────
-- `apply_migrations.sh` applies migrations BEFORE services restart, so the
-- code currently running meets this schema first. Therefore:
--   * every column is NULLABLE — no NOT NULL, no SET NOT NULL;
--   * nothing is renamed, dropped or re-typed;
--   * no CHECK is added over existing data. The two vocabularies this
--     introduces (`employment_type`, `seniority`) are validated in the ROUTE
--     against one shared tuple, exactly as `status` is since 148 — see
--     `routes/people/fields.py`. Narrowing them into database CHECKs is P-6,
--     the contract half, in a later release once real data exists (D-PC-8).
-- The array columns carry `DEFAULT '{}'`, which on PG 11+ is a catalog-only
-- change: no table rewrite, no ACCESS EXCLUSIVE held over a scan. That matters
-- here for the reason recorded in scripts/gen_tenant_migration.py — a 14h44m
-- outage caused by a queued ALTER, not by a slow one.
--
-- Idempotent: `ADD COLUMN IF NOT EXISTS` everywhere; apply_migrations.sh
--       re-runs 02+ on every deploy.
--
-- Tenancy (R5a): no new TABLE, so nothing new to scope. `people` is
--       already covered by the generated MT-1b migration and stays so.

-- ── §3.1 · Identity & directory — the half a person writes about themselves ──
-- Write class: SELF (the subject, or an `admin:members:manage` holder).
-- Read tier:   DIRECTORY (any `feature:people` holder).
ALTER TABLE people ADD COLUMN IF NOT EXISTS preferred_name TEXT;
ALTER TABLE people ADD COLUMN IF NOT EXISTS pronouns TEXT;
ALTER TABLE people ADD COLUMN IF NOT EXISTS location TEXT;
-- An IANA name ('Asia/Kolkata'), not an offset: an offset is wrong twice a year
-- in half the world, and the question this answers ("is a 9am call rude for
-- them") is asked on a specific future date.
ALTER TABLE people ADD COLUMN IF NOT EXISTS timezone TEXT;
-- {"days": [1,2,3,4,5], "start": "09:00", "end": "17:00"} — a record the
-- product never filters on, so JSONB rather than five columns (spec §7).
ALTER TABLE people ADD COLUMN IF NOT EXISTS working_hours JSONB;
ALTER TABLE people ADD COLUMN IF NOT EXISTS bio TEXT;
-- {"github": "...", "linkedin": "..."} — professional, public-facing links.
ALTER TABLE people ADD COLUMN IF NOT EXISTS links JSONB;
ALTER TABLE people ADD COLUMN IF NOT EXISTS languages TEXT[] DEFAULT '{}';
-- What they WANT to work on. An assigner that only optimises for fit gives the
-- same person the same work forever, which is how people leave.
ALTER TABLE people ADD COLUMN IF NOT EXISTS interests TEXT[] DEFAULT '{}';

-- ── §3.2 · Employment — what the ORGANISATION records about them ─────────────
-- Write class: ADMIN. A product where you can promote yourself is not an org
-- chart, so title/role/manager/status (all pre-existing) and everything here
-- stay out of the self class.
-- Read tier:   HR (`admin:members:read` or self).
ALTER TABLE people ADD COLUMN IF NOT EXISTS employee_id TEXT;
-- 'employee' | 'contractor' | 'intern' | 'vendor' | 'agent'.
-- ⚠️ Deliberately BESIDE `status`, not merged into it (D-PC-8). 148's CHECK
-- mixes a lifecycle (active/alumni/invited) with an engagement type
-- (contractor) because that is the vocabulary the data already carried; R6
-- forbids renaming it in place. Where both are set, this column is the fact
-- and `status` is the lifecycle.
ALTER TABLE people ADD COLUMN IF NOT EXISTS employment_type TEXT;
ALTER TABLE people ADD COLUMN IF NOT EXISTS start_date DATE;
-- An engagement end. Assignment past it is a mistake the picker warns about
-- (spec §6.1) — which is the whole reason to store it.
ALTER TABLE people ADD COLUMN IF NOT EXISTS end_date DATE;
-- 'junior' | 'mid' | 'senior' | 'lead' | 'principal'. Coarse ON PURPOSE: it
-- feeds "should this person own it or review it", never a pay band (§3.6).
ALTER TABLE people ADD COLUMN IF NOT EXISTS seniority TEXT;
ALTER TABLE people ADD COLUMN IF NOT EXISTS cost_center TEXT;

-- ── §3.4 · Availability ──────────────────────────────────────────────────────
-- A person's own stated ceiling on parallel work. Write class SELF: a
-- suggester should respect the number the person gives before an hours figure
-- it half-invented from unestimated tasks.
ALTER TABLE people ADD COLUMN IF NOT EXISTS max_concurrent_tasks INT;

-- ── §3.5 · Private — self, or an `admin:members:manage` holder, and nobody
--          else (D-PC-3: NOT `admin:members:read`, which is the manager-ish
--          grant — a manager seeing skills is the point of the HR tier, a
--          manager seeing an emergency contact is not) ─────────────────────────
--
-- ⚠️ Migration 49 recorded that "personal phone numbers are deliberately NOT
-- imported". This reverses that knowingly, on the owner's 2026-08-13 directive,
-- and two things keep the reversal honest: the private read tier above, and the
-- fact that `scripts/import_hr_people.py` still does not populate any of these.
-- They arrive only when a person types them about themselves, or an HR admin
-- does.
ALTER TABLE people ADD COLUMN IF NOT EXISTS phone TEXT;
-- {"name": "...", "relation": "...", "phone": "..."}
ALTER TABLE people ADD COLUMN IF NOT EXISTS emergency_contact JSONB;
-- Off-boarding, and reaching an alumnus. ADMIN-write: it is an identity fact
-- about the engagement, not a self-description.
ALTER TABLE people ADD COLUMN IF NOT EXISTS personal_email TEXT;
-- ⚠️ 'MM-DD', TEXT, and NOT a DATE (D-PC-9). The team can say happy birthday
-- without the product ever holding a date of birth — which is half of an
-- identity-theft pair and answers no question this spec asks. A DATE column
-- would invite somebody to store the year "since it is the same field".
ALTER TABLE people ADD COLUMN IF NOT EXISTS birthday TEXT;

COMMENT ON COLUMN people.birthday IS
    'MM-DD only. Never a full date of birth — People Center spec D-PC-9.';
COMMENT ON COLUMN people.employment_type IS
    'employee|contractor|intern|vendor|agent. Validated in the route against '
    'routes/people/fields.EMPLOYMENT_TYPES; a database CHECK is P-6 (D-PC-8).';
COMMENT ON COLUMN people.seniority IS
    'junior|mid|senior|lead|principal. Validated in the route against '
    'routes/people/fields.SENIORITY_LEVELS. Feeds own-vs-review, never pay.';

-- The directory filters on these two (spec §5.1 adds a timezone filter and an
-- "available now" derived from it). Small table — dozens of rows — so these
-- earn their place by keeping the planner honest as the roster grows, not by
-- rescuing a slow query today.
CREATE INDEX IF NOT EXISTS idx_gtd_people_timezone ON people (timezone);
CREATE INDEX IF NOT EXISTS idx_gtd_people_employment_type
    ON people (employment_type);
