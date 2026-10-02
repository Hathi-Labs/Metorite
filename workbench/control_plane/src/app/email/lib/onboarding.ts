/**
 * The guided setup of a new mailbox, as pure decisions (WS-17 EM-T6d).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6d".
 *
 * Vitest in this tree runs in the node environment, so each decision lives
 * here and `components/OnboardingPanel.tsx` only draws what it returns.
 *
 * ⚠️ Part 1 of EM-T6d has one stage, `importing`. The rules step (items 8 to
 * 11) and the storage step (EM-T6e) add `rules` and `storage` to
 * `OnboardingStage` later, and the function keeps its shape for them.
 *
 * ⚠️ Every field this module reads is optional. A gateway before EM-T6a sends
 * no `importSince`, so the stage is `null`. A gateway with EM-T6a and before
 * EM-T6b sends no `importPhase`, so there is no progress to draw. In both
 * cases `firstSyncSurface` keeps the old `FirstSyncBanner`, which is true
 * for that gateway. A bar held at 0% would not be (orchestrator, fix
 * round 1).
 *
 * Fence: `onboarding.test.ts`.
 */

import type { EmailAccount } from "./types";

// ── The stage ───────────────────────────────────────────────────────────────

/** The step of the guided setup that a mailbox is in. `null` draws nothing. */
export type OnboardingStage = "importing" | null;

type StageFields = Pick<
  EmailAccount,
  "importSince" | "onboardingDone" | "syncStatus" | "initialSyncDone"
>;

/**
 * Which step of the guided setup to draw for one mailbox.
 *
 * - `null` when `importSince` is absent or null. That mailbox connected
 *   before EM-T6, or the gateway predates EM-T6a.
 * - `null` when the member closed the setup (`onboardingDone`).
 * - `null` while `syncStatus` is `error`. The reconnect banner owns that
 *   state (EM-T3b).
 * - `importing` while the first import runs. Only an explicit `false` counts,
 *   the same rule as `isFirstSyncPending` in `connect.ts`.
 * - Otherwise `null`. Part 2 of EM-T6d returns `rules` here.
 */
export function onboardingStage(account: StageFields): OnboardingStage {
  if (!account.importSince) return null;
  if (account.onboardingDone === true) return null;
  if (account.syncStatus === "error") return null;
  if (account.initialSyncDone === false) return "importing";
  return null;
}

// ── What the mail pane draws for a pending mailbox ─────────────────────────

/**
 * The progress panel, or the old banner, for a mailbox whose first sync runs.
 *
 * `progress` needs the stage `importing` AND an `importPhase` from the
 * gateway. EM-T6b writes the phase before the first batch, so its presence
 * means the gateway reports real progress. With no phase (a gateway with
 * EM-T6a only), the banner draws, because its text stays true there.
 *
 * The stage says where the member is in the setup. This says whether the
 * gateway gives a number to draw. Two questions, so two functions.
 */
export function firstSyncSurface(
  account: StageFields & Pick<EmailAccount, "importPhase">,
): "progress" | "banner" {
  return onboardingStage(account) === "importing" && !!account.importPhase ? "progress" : "banner";
}

// ── The progress of the import (D-EM-16) ────────────────────────────────────

/** The phase line for each phase of the import (EM-T6d item 6). */
export const IMPORT_PHASE_LINES = {
  counting: "Counting your mail",
  importing: "Importing your mail, newest first",
  /** A phase this UI does not know claims no order. */
  unknown: "Importing your mail",
} as const;

/** The detail before the first batch lands, with no estimate. */
export const IMPORT_WAITING_DETAIL = "Waiting for the first messages";

/** What the progress label names, for assistive technology. */
export const IMPORT_PROGRESS_LABEL = "Mail import progress";

export interface ImportProgressView {
  /** The phase line above the bar. */
  phaseLine: string;
  /** 0 to 100. */
  percent: number;
  /** The words under the bar. */
  detail: string;
  /** Which rule gave the percent. Tests read it. */
  basis: "estimate" | "range";
}

type ProgressFields = Pick<
  EmailAccount,
  "importSince" | "importReachedAt" | "importPhase" | "importCount" | "importEstimate"
>;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function clampPercent(p: number): number {
  if (!Number.isFinite(p)) return 0;
  return Math.max(0, Math.min(100, p));
}

/** A date or null. An unparseable value is null. */
function parseDate(v: string | null | undefined): Date | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * "14 Sep" in the member's own time zone, with the year when it is not the
 * year of `now`. Month names come from a fixed list, so the text does not
 * change with the version of the runtime's date data.
 */
export function shortDate(d: Date, now: Date): string {
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

/**
 * The share of the range that the import has done, 0 to 100 (EM-T6d item 5).
 *
 * `(now - importReachedAt) / (now - importSince)`. It is 0 until the first
 * batch lands, because `importReachedAt` is null until then.
 */
export function rangeDonePercent(
  account: Pick<EmailAccount, "importSince" | "importReachedAt">,
  now: Date,
): number {
  const since = parseDate(account.importSince);
  const reached = parseDate(account.importReachedAt);
  if (!since || !reached) return 0;
  const whole = now.getTime() - since.getTime();
  if (whole <= 0) return 0;
  return clampPercent(((now.getTime() - reached.getTime()) / whole) * 100);
}

/**
 * What the import panel draws (EM-T6d items 4 to 6).
 *
 * With an estimate above 0, the percent is `importCount / importEstimate`,
 * and the detail reads "1,240 of about 3,100 messages". With no estimate,
 * the percent is the share of the range done, and the detail reads
 * "back to 14 Sep".
 *
 * `locale` formats the counts. Absent, the runtime's locale formats them, the
 * same as the Projects import.
 */
export function importProgress(
  account: ProgressFields,
  opts: { now: Date; locale?: string },
): ImportProgressView {
  const phaseLine =
    account.importPhase === "counting"
      ? IMPORT_PHASE_LINES.counting
      : account.importPhase === "importing"
        ? IMPORT_PHASE_LINES.importing
        : IMPORT_PHASE_LINES.unknown;

  const estimate = account.importEstimate;
  if (typeof estimate === "number" && estimate > 0) {
    const count = account.importCount ?? 0;
    const fmt = new Intl.NumberFormat(opts.locale);
    return {
      phaseLine,
      percent: clampPercent((count / estimate) * 100),
      detail: `${fmt.format(count)} of about ${fmt.format(estimate)} messages`,
      basis: "estimate",
    };
  }

  const reached = parseDate(account.importReachedAt);
  return {
    phaseLine,
    percent: rangeDonePercent(account, opts.now),
    detail: reached ? `back to ${shortDate(reached, opts.now)}` : IMPORT_WAITING_DETAIL,
    basis: "range",
  };
}
