/**
 * The choices of "Clean older mail" in the Email Cleaner, as pure decisions
 * (WS-17 EM-T6d, after the EM-T6a review).
 *
 * After EM-T6a, every deep download stops at the ceiling of the import
 * window: 180 days, which is 6 months of 30 days (D-EM-10). The old choices
 * offered 2, 3 and 5 years and "Everything", and each one stopped at 6
 * months. So the labels were false. These choices are what the gateway does.
 *
 * ⚠️ Each choice sends an explicit `since_date`. EM-T6a reads a null as
 * "back to the member's import range", which can be only 30 days, so a null
 * would quietly fetch less than the label says.
 *
 * Fence: `cleanOlderMail.test.ts`.
 */

import { MAX_IMPORT_MONTHS } from "./connect";

/** The ceiling of the gateway's import window, in days (EM-T6a: a month is 30 days). */
export const IMPORT_CEILING_DAYS = MAX_IMPORT_MONTHS * 30;

export interface CleanOlderMailChoice {
  label: string;
  days: number;
}

/** The choices, shortest first. The last one is the ceiling. */
export const CLEAN_OLDER_MAIL_CHOICES: readonly CleanOlderMailChoice[] = [
  { label: "1 month", days: 30 },
  { label: "3 months", days: 90 },
  { label: "6 months", days: IMPORT_CEILING_DAYS },
];

/**
 * The `since_date` of a choice: the UTC date `days` before `now`, as
 * YYYY-MM-DD. A value over the ceiling is held at the ceiling, so no choice
 * asks for mail that the gateway would not fetch.
 */
export function cleanOlderMailSince(days: number, now: Date = new Date()): string {
  const span = Math.max(0, Math.min(days, IMPORT_CEILING_DAYS));
  return new Date(now.getTime() - span * 86_400_000).toISOString().slice(0, 10);
}

/** The tooltip of a choice. */
export function cleanOlderMailTitle(choice: CleanOlderMailChoice): string {
  return `Fetch the last ${choice.label} of mail, then categorize it`;
}
