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

/** True only when the server stored `draft_replies: true` for the mailbox. */
export function autoDraftRepliesOn(
  settings: Partial<Pick<AssistantSettings, "draft_replies">> | null | undefined,
): boolean {
  return settings?.draft_replies === true;
}
