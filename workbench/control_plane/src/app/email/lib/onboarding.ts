/**
 * The guided setup of a new mailbox, as pure decisions (WS-17 EM-T6d).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6d".
 *
 * Vitest in this tree runs in the node environment, so each decision lives
 * here and `components/OnboardingPanel.tsx` only draws what it returns.
 *
 * Two stages: `importing` (part 1) and `rules` (part 2, items 8 to 11). The
 * storage step of EM-T6e adds `storage` later.
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

import type { AssistantSettings, AutomationRule, EmailAccount } from "./types";

// ── The stage ───────────────────────────────────────────────────────────────

/** The step of the guided setup that a mailbox is in. `null` draws nothing. */
export type OnboardingStage = "importing" | "rules" | null;

type StageFields = Pick<
  EmailAccount,
  "importSince" | "onboardingDone" | "syncStatus" | "initialSyncDone"
> &
  Partial<Pick<EmailAccount, "syncEnabled">>;

/**
 * Which step of the guided setup to draw for one mailbox.
 *
 * - `null` when `importSince` is absent or null. That mailbox connected
 *   before EM-T6, or the gateway predates EM-T6a.
 * - `null` when the member closed the setup (`onboardingDone`).
 * - `null` while `syncStatus` is `error`. The reconnect banner owns that
 *   state (EM-T3b).
 * - `null` while sync is off (`syncEnabled === false`). A paused mailbox
 *   imports nothing, so a panel would freeze (EM-T6b review).
 * - `importing` while the first import runs. Only an explicit `false` counts,
 *   the same rule as `isFirstSyncPending` in `connect.ts`.
 * - `rules` when the import ended (`initialSyncDone === true`), until the
 *   member closes the setup (items 8 to 11).
 * - Otherwise `null`.
 */
export function onboardingStage(account: StageFields): OnboardingStage {
  if (!account.importSince) return null;
  if (account.onboardingDone === true) return null;
  if (account.syncStatus === "error") return null;
  if (account.syncEnabled === false) return null;
  if (account.initialSyncDone === false) return "importing";
  if (account.initialSyncDone === true) return "rules";
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

// ── The rules step (items 8 to 11, D-EM-15) ─────────────────────────────────

/**
 * The words of the rules step. They name no model and offer no model
 * choice: the rule engine is fixed (EM-T5b).
 *
 * ⚠️ The automatic rule run touches only mail that arrived after the first
 * enabled rule (owner decision (d), #576). So the step says that the mail
 * already imported is sorted by "Process past emails", and offers it.
 */
export const RULES_STEP_COPY = {
  title: "Set up AI rules",
  body:
    "AI rules sort your mail into labels such as Needs Reply, Newsletter and Receipt. " +
    "They sort each new message, and they can also sort the mail you imported.",
  recommended: "Use the recommended rules",
  chooseOwn: "Choose my own",
  skipForNow: "Skip for now",
  skipSetup: "Skip setup",
  readyTitle: "Your AI rules are on",
  readyBody: "They sort each new message. To sort the mail you imported, run them over it once.",
  readyBodyNoImport: "They sort each new message as it arrives.",
  processPast: "Sort my imported mail",
  draftLabel: "Draft replies for me",
  draftNote: "Metorite writes a draft for mail that needs a reply. It sends nothing.",
  insights: "See insights",
  done: "Done",
  failed: "Metorite could not save that. Try again.",
} as const;

/** "Metorite added 10 rules." for the presets that the install added. */
export function installedLine(installed: number): string | null {
  if (installed <= 0) return null;
  return installed === 1 ? "Metorite added 1 rule." : `Metorite added ${installed} rules.`;
}

/** What the rules step shows: still reading, the three choices, or the next actions. */
export type RulesStepPhase = "checking" | "choose" | "ready";

/**
 * `ready` once an ENABLED rule exists, because the drafting step and the
 * sort of past mail need one (item 10). `null` rules means the read is still
 * in flight. A failed read gives `[]`, so the member still sees the choices.
 */
export function rulesStepPhase(rules: ReadonlyArray<Pick<AutomationRule, "enabled">> | null): RulesStepPhase {
  if (rules === null) return "checking";
  return rules.some((r) => r.enabled) ? "ready" : "choose";
}

/**
 * The start date for "Process past emails" over the imported mail: the UTC
 * date of `importSince`, as YYYY-MM-DD. Null when the range was "Only new
 * mail" (under one day back), because then there is nothing to sort.
 */
export function processPastFrom(
  account: Pick<EmailAccount, "importSince">,
  now: Date,
): string | null {
  const since = parseDate(account.importSince);
  if (!since) return null;
  if (now.getTime() - since.getTime() < 86_400_000) return null;
  return since.toISOString().slice(0, 10);
}

/** The calls of the rules step, so a test can pass fakes. */
export interface RulesStepApi {
  listRules(accountId: string): Promise<ReadonlyArray<Pick<AutomationRule, "enabled">>>;
  installPresetRules(accountId: string): Promise<{ installed: string[] }>;
  getAssistantSettings(accountId: string): Promise<AssistantSettings>;
  saveAssistantSettings(settings: AssistantSettings): Promise<AssistantSettings>;
  finishOnboarding(accountId: string): Promise<unknown>;
}

/** "Use the recommended rules": one install call. Returns how many it added. */
export async function installRecommendedRules(api: RulesStepApi, accountId: string): Promise<number> {
  const res = await api.installPresetRules(accountId);
  return res.installed?.length ?? 0;
}

/**
 * The drafting switch (item 10, D-EM-6). It reads the settings and saves them
 * back with `draft_replies` changed and every other field as it was. The
 * switch opens OFF and calls this only when the member moves it.
 */
export async function setDraftReplies(api: RulesStepApi, accountId: string, on: boolean): Promise<void> {
  const current = await api.getAssistantSettings(accountId);
  await api.saveAssistantSettings({ ...current, draft_replies: on });
}
