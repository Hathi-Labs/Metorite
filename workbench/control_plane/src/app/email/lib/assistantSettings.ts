// WS-17 EM-T7 — what the AI settings tab draws for automatic reply drafting.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.2 D-EM-6 and §10.4
// EM-T7. Automatic reply drafting is OFF for a new mailbox. A member turns it
// on in AI settings.
//
// The server owns the default: `GET /email/assistant/settings` answers
// `draft_replies: false` for a mailbox with no settings row. This module is the
// client half, so the switch reads OFF unless the server said `true`. A body
// with the field absent, or a value that is not `true`, draws OFF.
//
// It lives in `lib/` because vitest here runs in the node environment and
// cannot render `SettingsTab.tsx`. `assistantSettings.test.ts` runs this
// function over the fixture that the gateway test also reads, and scans the
// tab for the call.
import type { AssistantSettings } from "./types";

/**
 * The tier of the email chat when the tier policy does not cover the
 * email-assistant (D-EM-61). Our code picks it, and no member does. The
 * gateway's `EMAIL_TASK_TIERS["chat"]` holds the same value. A covered agent
 * gets no model at all (`governedModelProps`, D90).
 */
export const EMAIL_CHAT_TIER = "tier-powerful";

/**
 * The line under the Auto-run switch (WS-17 EM-G11c2,
 * `email_app_master_plan.md` §12.3.13.7 item 6). Product copy, word for word
 * from the spec. `assistantSettings.test.ts` pins the words and scans
 * `SettingsTab.tsx` for the use.
 */
export const AUTO_RUN_AI_LINE =
  "An AI model reads each new message to choose the rule that matches. Each action is one that you set.";

/**
 * What the Auto-run switch controls (WS-17 EM-G11c2 fix round 1, P1). OFF
 * stops more than the rules: the background Reply Zero labels and the sorting
 * sweep obey it too (§12.3.13.7, G3 and G4). A member's own action still runs.
 */
export const AUTO_RUN_DESCRIPTION =
  "Runs your enabled rules on new inbox mail as it arrives, and keeps the Reply Zero labels and the sorting sweep up to date. " +
  "Turn it off to stop all three on new mail. Your rules still apply when you run them yourself or process past emails.";

/** True only when the server stored `draft_replies: true` for the mailbox. */
export function autoDraftRepliesOn(
  settings: Partial<Pick<AssistantSettings, "draft_replies">> | null | undefined,
): boolean {
  return settings?.draft_replies === true;
}
