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

import { autoDraftRepliesOn } from "./assistantSettings";
import { isFirstSyncPending } from "./connect";
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

/** What the mail pane draws for one mailbox whose first sync runs. */
export interface FirstSyncPanel<A> {
  account: A;
  surface: "progress" | "banner";
  /** True for two or more mailboxes: the panel draws the chip of its mailbox
   *  beside the address. One mailbox sees no change (§11.0). */
  named: boolean;
}

/**
 * One panel for each mailbox whose first sync runs (EM-T8f-3 item 2, §11.6
 * case 19). The mailbox in view comes first, then the others in the order of
 * the list. `isFirstSyncPending` decides which mailboxes run, the same rule as
 * the poll, so an errored or paused mailbox gets no panel.
 * `firstSyncSurface` decides what each panel draws.
 *
 * `inView` is the mailbox of the page, or null in All inboxes.
 */
export function firstSyncPanels<
  A extends Pick<EmailAccount, "id"> & StageFields & Pick<EmailAccount, "importPhase">,
>(accounts: ReadonlyArray<A>, inView: string | null): FirstSyncPanel<A>[] {
  const pending = accounts.filter((a) => isFirstSyncPending(a));
  const ordered = [
    ...pending.filter((a) => a.id === inView),
    ...pending.filter((a) => a.id !== inView),
  ];
  const named = accounts.length > 1;
  return ordered.map((account) => ({ account, surface: firstSyncSurface(account), named }));
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
  draftNeedsReplyRule: 'Reply drafts need the "Needs Reply" rule. Turn it on in AI Settings.',
  draftReadFailed: "Metorite could not read the drafting setting.",
  noneAdded: "Your recommended rules are there already, and they are off. Turn on a rule in AI Settings.",
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
