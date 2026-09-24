-- 214 — a notification kind for the follow-up nudge (WS-27bk wave 6, H-113).
--
-- `pm_task_personal` has carried `waiting_on`, `delegated_at`, `expected_by`
-- and `last_nudged_at` since migration 188, and the Tasks app already draws
-- all four. The one thing missing was the nudge itself: the act that tells the
-- person you are waiting on that you are waiting on them.
--
-- `specs/project_management_app.md` §9.12.9 routes it "through the notification
-- path that exists", which is `pm_notifications` and `notifications.notify()`.
-- That path validates `kind` twice — once in Python against
-- `NOTIFICATION_KINDS`, and once here. So the kind has to arrive in both, and
-- `tests/unit/test_projects_nudge.py` fails if the two disagree.
--
-- ⚠️ **This was 213 until 2026-09-23.** Another session merged
-- `213_pm_activities_seq.sql` while this branch was open, and the R1 check
-- at merge caught it. R1 exists because that has happened three times in
-- two weeks — and a check that cannot fail is why it happened once more.
--
-- ⚠️ **In-app only, and that is the whole scope.** An outward message — mail,
-- WhatsApp — to a real person is Action-Broker work and owner-gated
-- (CLAUDE.md §3a rule 3). Two comments in the tree said the nudge as a whole
-- was owner-gated; they were written about the outward one, and this migration
-- lands with both of them corrected to say which is which.
--
-- R6 — this only WIDENS a CHECK, so it is an expand step with no contract half
-- owed. Old code never writes 'nudge', so the pre-restart gateway keeps working
-- through the deploy window against the wider constraint.

ALTER TABLE pm_notifications DROP CONSTRAINT IF EXISTS pm_notifications_kind_check;

ALTER TABLE pm_notifications
    ADD CONSTRAINT pm_notifications_kind_check
    CHECK (kind IN ('assigned', 'mention', 'comment', 'nudge'));
