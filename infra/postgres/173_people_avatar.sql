-- 173_people_avatar.sql — the display image (People Center P-8 / WS-28q).
--
-- What: `people.avatar` (a data URI) and `avatar_updated_at`.
-- Why:  owner-directed 2026-08-13 — "every user should have a unique image that
--       displays", with "a strict policy on the size of the image so that random
--       image sizes are not uploaded" and a crop.
--       Spec: project-docs/specs/people_center_app.md §3.1a, D-PC-17.
-- Depends on: 49_gtd_people.sql.
--
-- ── What is stored, and why the column can be this simple ───────────────────
-- **The stored value is the SERVER's re-encode, never the uploaded bytes**
-- (D-PC-17): every upload is decoded, cropped square, resized to exactly
-- 256x256 and re-encoded. So the column holds an image of known dimensions and
-- predictable weight — roughly 12-25 KB before base64, ~30 KB as a data URI —
-- and "random image sizes" is not a rule anybody has to enforce later, it is a
-- shape the data cannot take.
--
-- ⚠️ **JPEG, not WebP as §3.1a first said, and the reason is worth recording.**
-- The property that matters is the re-encode, not the container. The gateway
-- already depends on PyMuPDF (the résumé parser), which decodes, crops and
-- scales but cannot WRITE WebP; the only way to get WebP was to add Pillow to
-- the gateway's dependencies — a new wheel on the deploy path — to save about
-- 10 KB per person across a roster of dozens. JPEG at quality 82 through the
-- library already present is the same guarantee for no new dependency. Photos
-- have no transparency to lose, and an image WITH transparency is composited
-- onto white rather than onto black (verified, not assumed).
--
-- ── Why a column and not a file ─────────────────────────────────────────────
-- The deploy runs `git reset --hard`, which wipes untracked runtime files — a
-- recorded hazard for anything written into the attachments directory. The
-- database survives deploys; a file in the work tree does not. `agent_avatars`
-- (migration 64) already stores a sprite as a data URI for the same reason.
--
-- ── R6: expand half only ────────────────────────────────────────────────────
-- Both columns are nullable, added with IF NOT EXISTS, no constraint over
-- existing data, nothing renamed. A person with no avatar renders initials —
-- the fallback the directory already draws — and no external request is made
-- for one (Gravatar and its cousins would send a hash of every colleague's
-- address to a third party on every page load).
--
-- Idempotent. Tenancy (R5a): no new table, so nothing new to scope.

-- A `data:image/jpeg;base64,...` URI. TEXT rather than BYTEA because every
-- consumer is an <img src>, and a round trip through base64 at every read to
-- store 25% fewer bytes in a table of dozens of rows is a worse trade than it
-- sounds.
ALTER TABLE people ADD COLUMN IF NOT EXISTS avatar TEXT;

-- Cache busting. The data URI is inlined in the response, so the browser has
-- nothing to re-fetch — but the directory list and the person page are cached
-- by the client, and this is what tells a stale render it is stale. It is also
-- the honest answer to "when did this person last change their picture", which
-- `updated_at` cannot give once anything else on the row moves.
ALTER TABLE people ADD COLUMN IF NOT EXISTS avatar_updated_at TIMESTAMPTZ;

COMMENT ON COLUMN people.avatar IS
    'data:image/jpeg;base64 URI of the SERVER''s 256x256 re-encode. Never the '
    'uploaded bytes - People Center D-PC-17.';
