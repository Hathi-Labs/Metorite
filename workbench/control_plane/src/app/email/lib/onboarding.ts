/**
 * The guided setup of a new mailbox, as pure decisions (WS-17 EM-T6d).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6d".
 *
 * Vitest in this tree runs in the node environment, so each decision lives
 * here and `components/OnboardingPanel.tsx` only draws what it returns.
 *
 * Three stages: `importing` (part 1), `storage` (EM-T6e, D6) and `rules`
 * (part 2, items 8 to 11).
 *
 * ⚠️ Every field this module reads is optional. A gateway before EM-T6a sends
 * no `importSince`, so the stage is `null`. A gateway with EM-T6a and before
 * EM-T6b sends no `importPhase`, so there is no progress to draw. In both
 * cases `importPanelShows` is false, because a bar held at 0% would not be
 * true (orchestrator, fix round 1).
 *
 * EM-S9 (§14.6.9) adds `syncBanners`, the rows of the sync banner in the
 * header. `FirstSyncBanner` went with it.
 *
 * Fence: `onboarding.test.ts`.
 */

import type { ProcessPastEstimate, ProcessPastStatus } from "./api";
import { autoDraftRepliesOn } from "./assistantSettings";
import { atStorageLimit } from "./storage";
import type { AssistantSettings, AutomationRule, EmailAccount, RuleCopyResult } from "./types";
import { shortDate } from "./utils";

// `shortDate` moved to `utils.ts` with EM-T6e, so `storage.ts` reads it with
// no import of this module. The re-export keeps its callers working.
export { shortDate };

// ── The stage ───────────────────────────────────────────────────────────────

/** The step of the guided setup that a mailbox is in. `null` draws nothing. */
export type OnboardingStage = "importing" | "storage" | "rules" | null;

type StageFields = Pick<
  EmailAccount,
  "importSince" | "onboardingDone" | "syncStatus" | "initialSyncDone"
> &
  Partial<Pick<EmailAccount, "syncEnabled" | "importPhase" | "storedBytes" | "storageLimitBytes">>;

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
 * - `storage` when the import ended at the storage limit (EM-T6e, D6). Three
 *   things must be true: the phase is `limit`, the meter is at the limit
 *   (`atStorageLimit`), and the member did not choose "Keep it as it is"
 *   (`storageKept`). EM-T6c writes `initial_sync_done = true` with the phase
 *   `limit`, so this arm sits under the ended import.
 * - `rules` when the import ended (`initialSyncDone === true`), until the
 *   member closes the setup (items 8 to 11). A mailbox that the member kept
 *   at the limit, or that a removal took under it, moves on to `rules` here.
 *   The storage notice still names it (D3).
 * - Otherwise `null`.
 */
export function onboardingStage(
  account: StageFields,
  opts: { storageKept?: boolean } = {},
): OnboardingStage {
  if (!account.importSince) return null;
  if (account.onboardingDone === true) return null;
  if (account.syncStatus === "error") return null;
  if (account.syncEnabled === false) return null;
  if (account.initialSyncDone === false) return "importing";
  if (account.initialSyncDone === true) {
    if (account.importPhase === "limit" && atStorageLimit(account) && !opts.storageKept) return "storage";
    return "rules";
  }
  return null;
}

// ── The import panel of the mailbox in view ────────────────────────────────

/**
 * True when the mail pane draws `OnboardingPanel` for this mailbox.
 *
 * It needs the stage `importing` AND an `importPhase` from the gateway.
 * EM-T6b writes the phase before the first batch, so its presence means the
 * gateway reports real progress. A bar held at 0% would not be true.
 *
 * The page asks this for the mailbox in view only. Each other import draws
 * a row of the sync banner (`syncBanners`, EM-S9).
 */
export function importPanelShows(account: StageFields & Pick<EmailAccount, "importPhase">): boolean {
  return onboardingStage(account) === "importing" && !!account.importPhase;
}

// ── The sync banner in the header (EM-S9, §14.4.6, D-EM-58) ────────────────

/** The phases that draw a row. `starting` is a first sync with no phase yet:
 *  the seconds after the sign-in, before the scheduler writes `counting`.
 *  EM-S9b writes `resyncing`. This module reads it already, but EM-S9b still
 *  widens the poll of the page (§14.6.9b item 5). `isFirstSyncPending` in
 *  `connect.ts` needs `initialSyncDone` false, and a Resync keeps it true, so
 *  until then a `resyncing` row does not refresh. The fence is
 *  `email-sync-banner-polls` in `onboarding.test.ts`. */
export type SyncBannerPhase = "starting" | "counting" | "importing" | "resyncing";

/** The words of each phase. Each one says that the sync still runs. */
export const SYNC_BANNER_LINES: Record<SyncBannerPhase, string> = {
  starting: "Starting sync",
  counting: "Counting mail to import",
  importing: "Importing mail",
  resyncing: "Resyncing mail",
};

/** The phase that EM-S6 adds: the mailbox waits for the member to choose a
 *  range, so no sync runs and no row draws. */
export const AWAITING_RANGE_PHASE = "awaiting_range";

/** What the progress label of each row names, for assistive technology. */
export const SYNC_BANNER_LABEL = "Mailbox sync progress";

/** The name of the banner region. */
export const SYNC_BANNER_REGION = "Mailbox sync";

/** The highest percent before the phase ends (§14.4.6). The row goes away
 *  when the phase ends, so the banner never claims 100% for a running sync. */
export const SYNC_BANNER_MAX_PERCENT = 99;

/** One row of the sync banner. */
export interface SyncBannerRow<A> {
  account: A;
  phase: SyncBannerPhase;
  /** The words of the phase. */
  line: string;
  /** 0 to 99 with an estimate. `null` with none: the row draws the count. */
  percent: number | null;
  /** "1,240 of about 3,100 messages", "1,240 messages so far" or "Starting".
   *  Empty for the phase `starting`, which shows no bar and no count. */
  detail: string;
}

type BannerFields = Pick<EmailAccount, "id"> &
  Partial<
    Pick<
      EmailAccount,
      "initialSyncDone" | "syncStatus" | "syncEnabled" | "importPhase" | "importCount" | "importEstimate"
    >
  >;

/** The phase of a row, or null when the mailbox draws no row. */
function bannerPhase(account: BannerFields): SyncBannerPhase | null {
  if (account.syncStatus === "error") return null;
  if (account.syncEnabled === false) return null;
  const phase = account.importPhase;
  if (phase === AWAITING_RANGE_PHASE) return null;
  if (phase === "resyncing") return "resyncing";
  if (account.initialSyncDone !== false) return null;
  // D-EM-58: a banner always shows while a sync runs, so the seconds before
  // the scheduler writes the first phase draw a row too.
  if (phase === null || phase === undefined) return "starting";
  if (phase === "counting" || phase === "importing") return phase;
  return null;
}

/**
 * Each mailbox whose import writes progress, in the order of the list
 * (§14.4.6, D-EM-58).
 *
 * - The first import: `initialSyncDone` is false, and `importPhase` is
 *   `counting` or `importing` (EM-S9).
 * - The start of the first import: `initialSyncDone` is false, and
 *   `importPhase` is null or absent. The row reads "Starting sync", with no
 *   bar and no count (D-EM-58).
 * - A Resync: `importPhase` is `resyncing` (EM-S9b).
 * - No row for a sync error (the reconnect banner owns it), for sync off, or
 *   for `awaiting_range` (EM-S6).
 * - A mailbox out of view still draws its row.
 * - One surface, not two. The mailbox in view (`inView`) draws no row while
 *   its `OnboardingPanel` shows (`importPanelShows`).
 *
 * The progress: with an estimate above 0, the percent is
 * `importCount / importEstimate`, capped at 99. With no estimate, the row
 * shows the count.
 */
export function syncBanners<A extends BannerFields & StageFields>(
  accounts: ReadonlyArray<A>,
  inView: string | null = null,
  opts: { locale?: string } = {},
): SyncBannerRow<A>[] {
  const fmt = new Intl.NumberFormat(opts.locale);
  const rows: SyncBannerRow<A>[] = [];
  for (const account of accounts) {
    const phase = bannerPhase(account);
    if (!phase) continue;
    if (account.id === inView && importPanelShows(account)) continue;
    const count =
      typeof account.importCount === "number" && account.importCount > 0 ? account.importCount : 0;
    const estimate = account.importEstimate;
    const line = SYNC_BANNER_LINES[phase];
    if (phase === "starting") {
      // No phase yet, so no number is true: no bar and no count.
      rows.push({ account, phase, line, percent: null, detail: "" });
    } else if (typeof estimate === "number" && estimate > 0) {
      rows.push({
        account,
        phase,
        line,
        percent: Math.min(SYNC_BANNER_MAX_PERCENT, Math.floor(clampPercent((count / estimate) * 100))),
        detail: `${fmt.format(count)} of about ${fmt.format(estimate)} messages`,
      });
    } else {
      rows.push({
        account,
        phase,
        line,
        percent: null,
        detail: count > 0 ? `${fmt.format(count)} ${count === 1 ? "message" : "messages"} so far` : "Starting",
      });
    }
  }
  return rows;
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
  draftNeedsReplyRule: 'Reply drafts need the "Needs Reply" rule. Turn it on in AI Settings.',
  draftReadFailed: "Metorite could not read the drafting setting.",
  noneAdded: "Your recommended rules are there already, and they are off. Turn on a rule in AI Settings.",
  insights: "See insights",
  done: "Done",
  failed: "Metorite could not save that. Try again.",
  // EM-S10 (§14.4.7, D-EM-59): the count before the sort.
  sortCounting: "Metorite is counting your imported mail.",
  sortCountFailed: "Metorite could not count your imported mail, so it cannot start a sort yet.",
  sortRetry: "Count again",
  sortRunning: "Sorting… Metorite is sorting your imported mail. This step shows what is left when the run ends.",
  sortDone: "Your imported mail is sorted.",
  sortNothing: "Metorite found no imported mail to sort.",
  sortChooseRange: "Choose a range to sort",
  sortStatusLabel: "AI calls to sort your imported mail",
} as const;

// ── The estimate before the sort (EM-S10, §14.4.7, D-EM-59) ────────────────

/**
 * The most days one run of Process past emails covers. It MIRRORS
 * `_PROCESS_PAST_MAX_SPAN_DAYS` in `routes/email/automation/runner.py`, and
 * `onboardingRules.test.ts` parses that file and fails when they differ. The
 * dialog in `RulesTab.tsx` reads this constant too.
 */
export const PROCESS_PAST_MAX_SPAN_DAYS = 366;

/** How often the step reads the status of a run that is still going. */
export const SORT_POLL_MS = 5_000;

/**
 * What the step knows about the cost of "Sort my imported mail".
 *
 * ⚠️ Owner requirement (AI cost): a sort never starts without a count shown.
 * Only `ready` with a count enables the action. `loading` and `failed` keep it
 * disabled, and `failed` offers "Count again" instead. `running` offers
 * nothing while a run goes on. `tooWide` names the span cap before the dialog
 * refuses it, and the dialog counts the narrower range before it can start.
 */
export type SortEstimate =
  | { state: "loading" }
  | { state: "ready"; estimate: Pick<ProcessPastEstimate, "in_range" | "eligible" | "will_process" | "capped" | "limit"> }
  | { state: "failed" }
  | { state: "running" }
  | { state: "tooWide"; days: number };

/** What the step draws for the sort. `action` null draws no sort action. */
export interface SortEstimateView {
  /** The line under the actions: the AI calls, or why there is no number. */
  line: string;
  /** When one run cannot sort all of it: the size of a run, and what waits. */
  capLine: string | null;
  /** The text of the sort action, or null when the step offers none. */
  action: string | null;
  /** True only when the member may start: a count is on screen, or the
   *  action opens the dialog to choose a range that the dialog counts. */
  enabled: boolean;
  /** True when the count failed, so the step draws "Count again". */
  retry: boolean;
}

/**
 * The words for the estimate (EM-S10 item 1). Each sorted message is one AI
 * call (D-EM-7), so `will_process` is the count of calls. When `capped` is
 * true, one run sorts `limit` messages, the oldest first. The step offers the
 * next run after this one ends: `readSortState` reads the run status first,
 * and while a run goes on it reads it again every `SORT_POLL_MS`. When the run
 * is no longer `running`, it reads the estimate again.
 *
 * ⚠️ `limit` comes from the answer, never from a constant here. The route
 * clamps it at 2000 (`runner.py`), and Process past emails runs with the
 * request default, 1000. A fixed "2,000" here would name a run that the
 * dialog never starts.
 */
export function sortEstimateView(sort: SortEstimate, locale?: string): SortEstimateView {
  const fmt = new Intl.NumberFormat(locale);
  const base = { capLine: null, retry: false };
  if (sort.state === "loading") {
    return { ...base, line: RULES_STEP_COPY.sortCounting, action: RULES_STEP_COPY.processPast, enabled: false };
  }
  if (sort.state === "failed") {
    return { ...base, line: RULES_STEP_COPY.sortCountFailed, action: RULES_STEP_COPY.processPast, enabled: false, retry: true };
  }
  if (sort.state === "running") {
    return { ...base, line: RULES_STEP_COPY.sortRunning, action: null, enabled: false };
  }
  if (sort.state === "tooWide") {
    return {
      ...base,
      line:
        `Your imported mail covers ${fmt.format(sort.days)} days. One run covers at most ` +
        `${fmt.format(PROCESS_PAST_MAX_SPAN_DAYS)} days. Choose a shorter range, and Process past ` +
        "emails counts the AI calls before it sorts.",
      action: RULES_STEP_COPY.sortChooseRange,
      enabled: true,
    };
  }
  const e = sort.estimate;
  if (e.eligible <= 0 || e.will_process <= 0) {
    return {
      ...base,
      line: e.in_range > 0 ? RULES_STEP_COPY.sortDone : RULES_STEP_COPY.sortNothing,
      action: null,
      enabled: false,
    };
  }
  const calls = e.will_process;
  const line =
    calls === 1
      ? "Sorting it takes 1 AI call."
      : `Sorting it takes ${fmt.format(calls)} AI calls, one for each message.`;
  if (!e.capped) {
    return { ...base, line, action: RULES_STEP_COPY.processPast, enabled: true };
  }
  const rest = Math.max(0, e.eligible - calls);
  return {
    ...base,
    line,
    capLine:
      `One run sorts ${fmt.format(e.limit)} messages, the oldest first. ` +
      `${fmt.format(rest)} more wait. After this run ends, this step offers the next run.`,
    action: `Sort the next ${fmt.format(calls)} messages`,
    enabled: true,
  };
}

/** Reads the estimate for the import range. A failed read is `failed`. */
export async function readSortEstimate(
  api: Pick<RulesStepApi, "estimateSort">,
  accountId: string,
  from: string,
): Promise<SortEstimate> {
  try {
    return { state: "ready", estimate: await api.estimateSort(accountId, from) };
  } catch {
    return { state: "failed" };
  }
}

/** The days from `from` (YYYY-MM-DD) to `now`, both ends counted, as the
 *  route counts them (`(end - start).days + 1`). */
export function sortSpanDays(from: string, now: Date): number {
  const start = Date.parse(`${from}T00:00:00Z`);
  if (Number.isNaN(start)) return 0;
  return Math.floor((now.getTime() - start) / 86_400_000) + 1;
}

/**
 * What the step shows for the sort (EM-S10 fix round 1). In order:
 * 1. A run that is still going is `running`. A failed status read counts as
 *    no run, because the route refuses a second run on one mailbox.
 * 2. A range wider than the cap is `tooWide`, with no estimate read.
 * 3. Otherwise the estimate.
 */
export async function readSortState(
  api: Pick<RulesStepApi, "estimateSort" | "sortStatus">,
  accountId: string,
  from: string,
  now: Date,
): Promise<SortEstimate> {
  const status = await api.sortStatus(accountId).catch(() => null);
  if (status?.status === "running") return { state: "running" };
  const days = sortSpanDays(from, now);
  if (days > PROCESS_PAST_MAX_SPAN_DAYS) return { state: "tooWide", days };
  return readSortEstimate(api, accountId, from);
}

/** The wait before the next read, or null when the step reads no more. */
export function sortPollDelay(sort: SortEstimate): number | null {
  return sort.state === "running" ? SORT_POLL_MS : null;
}

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
 * date of `importSince`, as YYYY-MM-DD. Null when nothing was imported.
 *
 * Two rules tell that, and the first needs EM-T6b:
 * - A gateway with EM-T6b sends `importPhase`. A finished import with no
 *   imported row (`importCount` null or 0) has nothing to sort. "Only new
 *   mail" is that case: EM-T6a writes `import_phase = 'done'` and never
 *   writes `import_count` for it.
 * - With no `importPhase` (before EM-T6b), only a range under one day back
 *   tells it. After that day an "Only new mail" mailbox looks like a range,
 *   and the action shows. Recorded, not solved, in §10.4.7.
 */
export function processPastFrom(
  account: Pick<EmailAccount, "importSince" | "importPhase" | "importCount">,
  now: Date,
): string | null {
  const since = parseDate(account.importSince);
  if (!since) return null;
  if (account.importPhase && !(typeof account.importCount === "number" && account.importCount > 0)) {
    return null;
  }
  if (now.getTime() - since.getTime() < 86_400_000) return null;
  return since.toISOString().slice(0, 10);
}

// ── The reply rule that drafting acts through (D-EM-6) ──────────────────────

/**
 * The reply rule as the gateway names it. These MIRROR `_REPLY_SYSTEM_TYPES`
 * and `_REPLY_RULE_NAMES` in `routes/email/automation/rules.py`, which
 * `sync_draft_reply_action` and `reply_rule_drafts` read. A mirror goes
 * stale, so `onboardingRules.test.ts` parses both tuples out of rules.py and
 * fails when they differ.
 */
export const REPLY_SYSTEM_TYPES: readonly string[] = ["REPLY", "TO_REPLY"];
export const REPLY_RULE_NAMES: readonly string[] = ["needs reply", "reply", "to reply"];

type RuleShape = Pick<AutomationRule, "enabled" | "name" | "system_type">;

/** `_is_reply_rule` of rules.py: the system type, or the trimmed lower-case name. */
export function isReplyRule(rule: Pick<AutomationRule, "name" | "system_type">): boolean {
  return (
    REPLY_SYSTEM_TYPES.includes((rule.system_type ?? "").toUpperCase()) ||
    REPLY_RULE_NAMES.includes((rule.name ?? "").trim().toLowerCase())
  );
}

/**
 * True when an enabled reply rule exists. The drafting switch shows only
 * then, because a draft is an action on that rule (item 10, `rules.py`).
 */
export function hasEnabledReplyRule(rules: ReadonlyArray<RuleShape> | null): boolean {
  return (rules ?? []).some((r) => r.enabled && isReplyRule(r));
}

/** The calls of the rules step, so a test can pass fakes. */
export interface RulesStepApi {
  listRules(accountId: string): Promise<ReadonlyArray<RuleShape>>;
  installPresetRules(accountId: string): Promise<{ installed: string[] }>;
  getAssistantSettings(accountId: string): Promise<AssistantSettings>;
  saveAssistantSettings(settings: AssistantSettings): Promise<AssistantSettings>;
  /** The PATCH of item 11. It returns the server's copy of the account. */
  finishOnboarding(accountId: string): Promise<EmailAccount>;
  /** "Copy the rules of <label>" (EM-T8f-2, D-EM-24): `POST /email/rules/copy`.
   *  The step guards it with `ruleCopier` in `lib/mailboxSettings.ts`. */
  copyRules(fromAccountId: string, toAccountId: string): Promise<RuleCopyResult>;
  /** The count before "Sort my imported mail" (EM-S10):
   *  `GET /email/rules/process-past/estimate` from the start of the import. */
  estimateSort(accountId: string, startDate: string): Promise<ProcessPastEstimate>;
  /** The status of the last Process past emails run (EM-S10 fix round 1):
   *  `GET /email/rules/process-past/status`. */
  sortStatus(accountId: string): Promise<Pick<ProcessPastStatus, "status">>;
}

/**
 * What the drafting switch draws. `loading` and `failed` keep it disabled:
 * a switch that guesses OFF while drafting is ON lets the member click Done
 * believing drafting is off (fix round 1, P1).
 */
export type DraftSwitch = { state: "loading" } | { state: "ready"; on: boolean } | { state: "failed" };

/**
 * Reads the stored drafting setting for the switch. ON only for a stored
 * `draft_replies: true` (`autoDraftRepliesOn`, EM-T7), so a new mailbox reads
 * OFF (D-EM-6). A failed read is `failed`, never a guess.
 */
export async function readDraftSwitch(api: RulesStepApi, accountId: string): Promise<DraftSwitch> {
  try {
    return { state: "ready", on: autoDraftRepliesOn(await api.getAssistantSettings(accountId)) };
  } catch {
    return { state: "failed" };
  }
}

/** "Use the recommended rules": one install call. Returns the names it added. */
export async function installRecommendedRules(api: RulesStepApi, accountId: string): Promise<string[]> {
  const res = await api.installPresetRules(accountId);
  return res.installed ?? [];
}

/**
 * The drafting switch (item 10, D-EM-6). It reads the settings and saves them
 * back with `draft_replies` changed and every other field as it was. The
 * switch shows the stored value (`readDraftSwitch`) and calls this only when
 * the member moves it.
 */
export async function setDraftReplies(api: RulesStepApi, accountId: string, on: boolean): Promise<void> {
  const current = await api.getAssistantSettings(accountId);
  await api.saveAssistantSettings({ ...current, draft_replies: on });
}
