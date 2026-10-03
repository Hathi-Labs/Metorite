/**
 * Settings for each mailbox, as pure decisions (WS-17 EM-T8f-2, §11.7.6 of
 * `project-docs/specs/email_app_master_plan.md`).
 *
 * Vitest in this tree runs in the node environment, so each decision lives
 * here and the components only draw what it returns. Four parts:
 *
 * 1. The header of the automation views names the mailbox that they change,
 *    and offers a picker with no All inboxes option (MB-11, §11.4).
 * 2. The rules step of a new mailbox offers "Copy the rules of <label>" for
 *    each other mailbox (D-EM-24). A copy runs once.
 * 3. The disconnect dialog names the mailbox, and the new default when the
 *    mailbox is the default (D-EM-25, §11.6 case 18).
 * 4. The From picker of Notes names each mailbox as "label · address"
 *    (§11.6 case 23).
 *
 * ⚠️ No import from `components/` and none from `emailStore.ts`. The store
 * imports `nextDefaultAfter` from here, so an import back would be a cycle.
 *
 * Fence: `mailboxSettings.test.ts`.
 */

import { chatMailboxName, type PersonaAccount } from "./emailAssistantPersona";
import { mailboxLabel } from "./mailbox";
import type { AutomationRule, EmailAccount, RuleCopyResult } from "./types";

/** One option of a mailbox picker. It has the shape of `SelectOption` in
 *  `components/ui/SelectButton.tsx`, and it does not import it. */
export interface MailboxOption {
  value: string;
  label: string;
  hint?: string;
  keywords?: string;
}

type Named = Pick<EmailAccount, "id" | "emailAddress" | "displayLabel">;

// ── 1. The header of the automation views (MB-11) ──────────────────────────

/**
 * The options of the mailbox picker in the automation header: each mailbox,
 * by label, with the address as its hint. There is no All inboxes option,
 * because a setting always names one mailbox (§11.0 rule 5).
 */
export function settingsPickerOptions(accounts: ReadonlyArray<Named>): MailboxOption[] {
  return accounts.map((a) => ({
    value: a.id,
    label: mailboxLabel(a),
    hint: a.emailAddress,
    keywords: a.emailAddress,
  }));
}

/** What the automation header draws for its mailbox. */
export interface SettingsHeaderView<A> {
  account: A;
  /** The label of the mailbox (`mailboxLabel`). */
  label: string;
  address: string;
  /** "label · address", or the address once when the two are the same. */
  name: string;
  /** Two or more mailboxes: the chip and the picker show (§11.0). */
  several: boolean;
  /** The picker. Empty for one mailbox. */
  options: MailboxOption[];
}

/**
 * The mailbox that the automation views act on, named for the header. Null
 * when no mailbox is selected, or when the list does not hold it yet.
 *
 * The header names the mailbox for one mailbox too (§11.4 says "Always"). The
 * chip and the picker show only for two or more, as each chip does (§11.0).
 */
export function settingsHeader<A extends Named>(
  accounts: ReadonlyArray<A>,
  accountId: string | null,
): SettingsHeaderView<A> | null {
  const account = accountId ? accounts.find((a) => a.id === accountId) : undefined;
  if (!account) return null;
  const several = accounts.length > 1;
  return {
    account,
    label: mailboxLabel(account),
    address: account.emailAddress,
    name: chatMailboxName(account),
    several,
    options: several ? settingsPickerOptions(accounts) : [],
  };
}

/**
 * The guard of a load in a view of one mailbox (EM-T8f-2 review F1).
 *
 * The header picker changes the mailbox while a view can still wait for the
 * answer of the mailbox before. `AutomationView` remounts its views on a
 * change of mailbox, and this guard is the second defence inside each view.
 * `begin` gives a token. `current` is true only for the token of the last
 * load begun, so an answer that a later load overtook lands nowhere.
 */
export interface LoadGuard {
  begin(): number;
  current(token: number): boolean;
}

export function loadGuard(): LoadGuard {
  let last = 0;
  return {
    begin: () => ++last,
    current: (token) => token === last,
  };
}

/**
 * One load of a view of one mailbox. Only the newest load lands: the data,
 * the error AND the end of the load. An older answer, for example of the
 * mailbox before a pick, calls none of the three. So it can neither show the
 * rules of A under the name of B, nor clear the spinner of the load of B.
 */
export function guardedLoad<T>(
  guard: LoadGuard,
  request: () => Promise<T>,
  land: { data: (value: T) => void; error: (err: Error) => void; done: () => void },
): Promise<void> {
  const token = guard.begin();
  return Promise.resolve()
    .then(request)
    .then(
      (value) => {
        if (guard.current(token)) land.data(value);
      },
      (err: unknown) => {
        if (guard.current(token)) land.error(err instanceof Error ? err : new Error(String(err)));
      },
    )
    .finally(() => {
      if (guard.current(token)) land.done();
    });
}

/**
 * A pick in the header picker. It selects the mailbox through the store
 * (`selectAccount`), because `RulesTab` reads the folders of the selected
 * mailbox. It also clears the Process past date of the guided setup, which
 * belongs to the mailbox of the setup and not to the pick.
 *
 * A pick of the mailbox in view, or of an id that is not a mailbox of the
 * member (All inboxes is one), does nothing. Returns true when it selected.
 */
export function pickSettingsMailbox(
  id: string,
  ctx: {
    accounts: ReadonlyArray<{ id: string }>;
    current: string | null;
    selectAccount: (id: string) => void;
    clearProcessPastFrom: () => void;
  },
): boolean {
  if (!id || id === ctx.current) return false;
  if (!ctx.accounts.some((a) => a.id === id)) return false;
  ctx.clearProcessPastFrom();
  ctx.selectAccount(id);
  return true;
}

// ── 2. The copy of rules in the rules step (D-EM-24) ───────────────────────

/**
 * The mailbox that the rules step names: its own mailbox, when the member has
 * two or more. With one mailbox the step does not change (§11.0).
 */
export function rulesStepMailbox<A extends { id: string }>(
  accounts: ReadonlyArray<A>,
  selfId: string,
): A | null {
  if (accounts.length < 2) return null;
  return accounts.find((a) => a.id === selfId) ?? null;
}

/** One "Copy the rules of <label>" choice. */
export interface CopyChoice {
  id: string;
  label: string;
}

/**
 * One copy choice for each OTHER mailbox of the member, in the order of the
 * list. None with one mailbox.
 */
export function rulesStepCopyChoices(
  accounts: ReadonlyArray<Named>,
  selfId: string,
): CopyChoice[] {
  return accounts.filter((a) => a.id !== selfId).map((a) => ({ id: a.id, label: mailboxLabel(a) }));
}

/** The words of the copy in the rules step. */
export const COPY_STEP = {
  /** The text of each copy button. */
  copyFrom: (label: string) => `Copy the rules of ${label}`,
  failed: "Metorite could not copy the rules. Try again.",
} as const;

/**
 * The plain words for each reason of `left_out`. These MIRROR the
 * `LEFT_OUT_*` constants of `routes/email/automation/rule_copy.py`, and
 * `mailboxSettings.test.ts` reads that file and fails on a reason with no
 * words here.
 */
export const LEFT_OUT_WORDS: Readonly<Record<string, (source: string) => string>> = {
  disabled: (source) => `It is off in ${source}.`,
  forward_to_own_address: () =>
    "It forwards mail to one of your own mailboxes, and that can send mail around in a loop.",
  reply_rule_exists: () => "This mailbox has a reply rule already, and a mailbox keeps only one.",
};

/** The words for a reason that this UI does not know. */
export const LEFT_OUT_UNKNOWN = "Metorite did not copy it.";

/** The answer of a copy, in plain words. */
export interface CopyReport {
  summary: string;
  /** One line for the copied names, then each rename, then each rule left out. */
  lines: string[];
}

/**
 * The answer of `POST /email/rules/copy` in plain words: the copied rules,
 * each new name and each rule left out with its reason. `source` is the label
 * of the mailbox the rules came from.
 */
export function copyReport(result: RuleCopyResult, source: string): CopyReport {
  const n = result.copied.length;
  const summary =
    n === 0 && result.leftOut.length === 0
      ? `${source} has no rules to copy.`
      : n === 0
        ? `Metorite copied no rules from ${source}.`
        : `Metorite copied ${n} ${n === 1 ? "rule" : "rules"} from ${source}.`;
  const lines: string[] = [];
  if (n > 0) lines.push(`Copied: ${result.copied.join(", ")}.`);
  for (const r of result.renamed) {
    lines.push(`"${r.name}" is now "${r.copiedAs}", because this mailbox has a rule with that name.`);
  }
  for (const r of result.leftOut) {
    const words = LEFT_OUT_WORDS[r.reason];
    lines.push(`Not copied: "${r.name}". ${words ? words(source) : LEFT_OUT_UNKNOWN}`);
  }
  return { summary, lines };
}

/**
 * The guard of the copy. A second copy into the same mailbox adds each rule
 * again as "(copy)", so:
 * - `start` refuses while a copy runs. A double click starts one copy.
 * - `start` refuses a pair that copied before. A copy is made once (D-EM-24).
 * - A failed copy does not count as made, so the member can try again. The
 *   step then reads the rules again, and a copy that landed moves the step on.
 *
 * `start` gives the promise of the copy, or null when it refuses. The check
 * and the mark are in one synchronous call, so no render can come between.
 */
export interface RuleCopier {
  start(fromId: string, toId: string): Promise<RuleCopyResult> | null;
  copied(fromId: string, toId: string): boolean;
}

export function ruleCopier(
  copyRules: (fromId: string, toId: string) => Promise<RuleCopyResult>,
): RuleCopier {
  let running = false;
  const done = new Set<string>();
  const key = (fromId: string, toId: string) => `${fromId}>${toId}`;
  return {
    start(fromId, toId) {
      if (running || !fromId || !toId || fromId === toId || done.has(key(fromId, toId))) {
        return null;
      }
      running = true;
      return Promise.resolve()
        .then(() => copyRules(fromId, toId))
        .then((result) => {
          done.add(key(fromId, toId));
          return result;
        })
        .finally(() => {
          running = false;
        });
    },
    copied: (fromId, toId) => done.has(key(fromId, toId)),
  };
}

/** The fields of a rule that the rules step reads. */
export type StepRule = Pick<AutomationRule, "enabled" | "name" | "system_type">;

/**
 * The end of one copy in the rules step (EM-T8f-2 review F4). In order:
 * 1. A finished copy gives its answer in plain words to `sink.report`. A
 *    failed copy calls `sink.failed`.
 * 2. The step reads the rules again, after a failure too, because a copy
 *    whose answer was lost can still have landed. An enabled rule moves the
 *    step on.
 * 3. When that read fails, the copied names stand in for it as enabled rules,
 *    as after the install of the presets. So a copy that landed still moves
 *    the step on. With no copied name, the rules stay as they were.
 */
export async function runRuleCopy(
  pending: Promise<RuleCopyResult>,
  source: string,
  reread: () => Promise<ReadonlyArray<StepRule>>,
  sink: {
    report: (report: CopyReport) => void;
    failed: () => void;
    rules: (rules: ReadonlyArray<StepRule>) => void;
  },
): Promise<void> {
  let copied: string[] = [];
  try {
    const result = await pending;
    copied = result.copied;
    sink.report(copyReport(result, source));
  } catch {
    sink.failed();
  }
  const fresh = await reread().catch(() => null);
  if (fresh) sink.rules(fresh);
  else if (copied.length > 0) {
    sink.rules(copied.map((name) => ({ enabled: true, name, system_type: null })));
  }
}

// ── 3. The disconnect dialog (D-EM-25, §11.6 case 18) ──────────────────────

/** The fields that the election of a new default reads. */
export interface DefaultCandidate {
  id: string;
  isDefault?: boolean;
  /** ISO text with six digits of microseconds (EM-T8f-1). */
  createdAt?: string | null;
}

/**
 * The order in which the gateway elects a new default:
 * `ORDER BY created_at, id` in `delete_account` of
 * `routes/email/transport/accounts.py`.
 *
 * - `createdAt` compares as text. The gateway sends six digits of
 *   microseconds and one offset, so the order of the text is the order of
 *   time, and microseconds count.
 * - A missing `createdAt` sorts last, as NULL does in an ascending order of
 *   Postgres.
 * - `id` breaks a tie. A lower-case UUID sorts as text the way Postgres
 *   sorts the `uuid` type.
 */
export function byElectionOrder(a: DefaultCandidate, b: DefaultCandidate): number {
  const ca = a.createdAt || null;
  const cb = b.createdAt || null;
  if (ca !== cb) {
    if (ca === null) return 1;
    if (cb === null) return -1;
    return ca < cb ? -1 : 1;
  }
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * The mailbox that becomes the default when `removedId` disconnects, or null.
 * Null when the removed mailbox is not the default, or when no mailbox is
 * left. The dialog names this mailbox before the removal, and the store
 * marks it after the removal. One function, so the two agree.
 *
 * ⚠️ It never reads the order of the list. A default that the member set in
 * this session moves the flag and not the row, so the first row can be any
 * mailbox (EM-T8f-2 STATUS DRIFT).
 */
export function nextDefaultAfter<A extends DefaultCandidate>(
  accounts: ReadonlyArray<A>,
  removedId: string,
): A | null {
  const removed = accounts.find((a) => a.id === removedId);
  if (!removed?.isDefault) return null;
  const left = accounts.filter((a) => a.id !== removedId);
  if (left.length === 0) return null;
  return [...left].sort(byElectionOrder)[0];
}

/** The names that the disconnect dialog draws. */
export interface DisconnectNames {
  /** "label · address" of the mailbox that goes. */
  name: string;
  /** "label · address" of the new default, or null. */
  nextDefault: string | null;
}

export function disconnectNames(
  account: (Named & DefaultCandidate) | null,
  accounts: ReadonlyArray<Named & DefaultCandidate>,
): DisconnectNames {
  if (!account) return { name: "this mailbox", nextDefault: null };
  const next = nextDefaultAfter(accounts, account.id);
  return { name: chatMailboxName(account), nextDefault: next ? chatMailboxName(next) : null };
}

/**
 * The names that the disconnect dialog draws now (EM-T8f-2 review F3).
 *
 * While the disconnect runs (`busy`), the dialog keeps the names it held. The
 * store drops the mailbox before the dialog closes. The names read from that
 * list would then lose the new default during the close. When not busy, the
 * live names win. The held object comes back when the two are equal, so the
 * caller can compare by identity and set its state only on a change.
 */
export function holdNames(held: DisconnectNames, live: DisconnectNames, busy: boolean): DisconnectNames {
  if (busy) return held;
  return held.name === live.name && held.nextDefault === live.nextDefault ? held : live;
}

// ── 4. The From picker of Notes (§11.6 case 23) ────────────────────────────

/** A mailbox as the gateway sends it (snake case), the shape Notes reads. */
export type NotesMailbox = PersonaAccount & { id: string; is_default?: boolean | null };

/**
 * The options of the From picker in Notes: "label · address" for each
 * mailbox, so the member sees which address sends the follow-up.
 */
export function notesFromOptions(accounts: ReadonlyArray<NotesMailbox>): MailboxOption[] {
  return accounts.map((a) => ({
    value: a.id,
    label: chatMailboxName(a),
    keywords: a.email_address ?? a.emailAddress ?? undefined,
  }));
}

/** The mailbox that the follow-up starts on: the default, else the first. */
export function notesFromStart(accounts: ReadonlyArray<NotesMailbox>): string {
  return accounts.find((a) => a.is_default)?.id ?? accounts[0]?.id ?? "";
}
