/**
 * The Auto-run notice of an existing Gmail mailbox (WS-17 EM-G11c2, owner
 * decision 2026-11-08, task 2267, `email_app_master_plan.md` §12.3.13.7).
 *
 * A Gmail mailbox with at least one ENABLED rule and NO stored Auto-run choice
 * sorts no new mail, because a missing row reads OFF for Gmail. The notice
 * says so and offers "Turn on", which saves `auto_run = true` through the
 * existing settings PUT (`setAutoRun`). "Not now" is kept for each mailbox in
 * `localStorage`.
 *
 * Who sees it: Gmail only (a missing row is ON for Outlook and IMAP), and only
 * with `auto_run_chosen === false`. A member who stored OFF sees nothing. The
 * read makes no write. Fence: `autoRunNotice.test.ts`.
 */
import { setAutoRun, type RulesStepApi } from "./onboarding";
import { onRulesChanged } from "./rulesChanged";
import type { EmailAccount } from "./types";

/** The calls the notice makes. They are the rules step's own calls. */
export type AutoRunNoticeApi = Pick<RulesStepApi, "listRules" | "getAssistantSettings" | "saveAssistantSettings">;

export const AUTO_RUN_NOTICE_COPY = {
  turnOn: "Turn on",
  notNow: "Not now",
  failed:
    "Metorite could not turn on your rules for new mail, so nothing changed. Press Turn on to try again.",
} as const;

/** The words of the notice. One rule reads "Your rule is", not "Your 1 rules". */
export function autoRunNoticeText(count: number, address: string): string {
  return count === 1
    ? `Your rule is not sorting new mail in ${address}.`
    : `Your ${count} rules are not sorting new mail in ${address}.`;
}

// ── "Not now", kept for each mailbox ────────────────────────────────────────

/** Ids only, never mail (DESIGN_SYSTEM rule 9). A device that refuses storage
 *  keeps nothing, and the notice shows again on the next visit. */
export const AUTO_RUN_NOT_NOW_KEY = "metorite.email.autoRunNotNow";

/** The most ids the list holds. The oldest id goes first. */
const NOT_NOW_MAX = 50;

type NotNowStore = Pick<Storage, "getItem" | "setItem">;

function localStore(): NotNowStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The ids with "Not now". A missing store, a refusal or text that is not a list gives []. */
export function readAutoRunNotNow(store: NotNowStore | null = localStore()): string[] {
  try {
    const raw = store?.getItem(AUTO_RUN_NOT_NOW_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string" && x !== "") : [];
  } catch {
    return [];
  }
}

/** Add `id`, write the list, and return it. A refused write still returns the
 *  list, so the notice goes for this visit. */
export function rememberAutoRunNotNow(id: string, store: NotNowStore | null = localStore()): string[] {
  const next = [...readAutoRunNotNow(store).filter((x) => x !== id), id].slice(-NOT_NOW_MAX);
  try {
    store?.setItem(AUTO_RUN_NOT_NOW_KEY, JSON.stringify(next));
  } catch {
    // A full or refused store keeps nothing. The list above still holds.
  }
  return next;
}

// ── Who sees it ─────────────────────────────────────────────────────────────

/**
 * The count of enabled rules when the notice shows, else null. Reads only. A
 * failed read gives null: the notice guesses nothing.
 */
export async function loadAutoRunNotice(
  api: AutoRunNoticeApi,
  account: Pick<EmailAccount, "id" | "provider">,
  notNow: ReadonlyArray<string> = readAutoRunNotNow(),
): Promise<number | null> {
  if (account.provider !== "gmail" || notNow.includes(account.id)) return null;
  try {
    const settings = await api.getAssistantSettings(account.id);
    // Only an explicit "no stored choice". A stored OFF reads true, and an
    // older server that sends nothing reads as stored.
    if (settings.auto_run_chosen !== false) return null;
    const enabled = (await api.listRules(account.id)).filter((r) => r.enabled).length;
    return enabled >= 1 ? enabled : null;
  } catch {
    return null;
  }
}

/**
 * Read the count now, and again after each rule write in this app (task 2451).
 * `onCount` gets each answer. Only the newest read may set it, so a slow read
 * that started before a change cannot bring back an old count. Returns the
 * function that stops the watch. The page calls it on unmount and on a change
 * of mailbox. Vitest runs in node here, so the page effect is this function,
 * and `autoRunNotice.test.ts` drives it with an `EventTarget`.
 */
export function watchAutoRunNotice(
  api: AutoRunNoticeApi,
  account: Pick<EmailAccount, "id" | "provider">,
  onCount: (count: number | null) => void,
  target?: EventTarget | null,
  notNow: () => ReadonlyArray<string> = () => readAutoRunNotNow(),
): () => void {
  let live = true;
  let newest = 0;
  const read = () => {
    const mine = ++newest;
    void loadAutoRunNotice(api, account, notNow()).then((n) => {
      if (live && mine === newest) onCount(n);
    });
  };
  read();
  const stop = onRulesChanged(read, target);
  return () => {
    live = false;
    stop();
  };
}

/** "Turn on": the existing settings save, with `auto_run` changed only. */
export async function turnOnAutoRun(api: AutoRunNoticeApi, accountId: string): Promise<void> {
  await setAutoRun(api, accountId, true);
}
