/**
 * The storage of a mailbox in Metorite, as pure decisions (WS-17 EM-T6e).
 *
 * Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e", and
 * the decisions D1 to D7 there. The meter and the two routes are EM-T6c.
 *
 * Vitest in this tree runs in the node environment, so each decision lives
 * here. `components/StorageNotice.tsx`, `components/StorageStep.tsx` and
 * `components/RemoveOlderMailDialog.tsx` only draw what this module returns.
 *
 * ⚠️ **Metorite's copy ONLY (D-EM-14).** A removal deletes the copy that
 * Metorite keeps. It never deletes or changes mail at the provider. Every
 * word below says so, and `storage.test.ts` scans the copy for a sentence
 * that says otherwise.
 *
 * ⚠️ This module imports no other module of `lib/` except `utils.ts` and
 * the types. `onboarding.ts` and `mailbox.ts` import it, so an import back
 * would make a cycle.
 *
 * Fence: `storage.test.ts`.
 */

import type { EmailAccount, OlderMailPreview, RemoveOlderResult } from "./types";
import { shortDate } from "./utils";

/** One MB, as the gateway counts it: the setting in MB times 1,048,576. */
export const BYTES_PER_MB = 1_048_576;

type MeterFields = Partial<Pick<EmailAccount, "storedBytes" | "storageLimitBytes">>;

// ── The limit (A1) ──────────────────────────────────────────────────────────

/**
 * True when the meter of the mailbox is at or over its limit.
 *
 * The rule of `email_ingestion.storage.at_limit` (EM-T6c): a null meter has
 * not run yet, and it is not at the limit. A meter equal to the limit is at
 * it. A gateway before EM-T6c sends no limit, and then nothing is at a limit.
 */
export function atStorageLimit(account: MeterFields): boolean {
  const limit = account.storageLimitBytes;
  const stored = account.storedBytes;
  if (typeof limit !== "number" || !Number.isFinite(limit)) return false;
  if (typeof stored !== "number" || !Number.isFinite(stored)) return false;
  return stored >= limit;
}

/**
 * Which storage notice a mailbox earns, before any other surface wins:
 * - `limit` at or over the limit (scope item 1).
 * - `gap` in the phase `limit` under the limit (D2): the first import stopped
 *   at the limit, and nothing resumes it until EM-T6f.
 * - `null` otherwise, and always when the gateway sends no limit.
 */
export function storageNoticeKind(
  account: MeterFields & Partial<Pick<EmailAccount, "importPhase">>,
): "limit" | "gap" | null {
  if (typeof account.storageLimitBytes !== "number") return null;
  if (atStorageLimit(account)) return "limit";
  return account.importPhase === "limit" ? "gap" : null;
}

/** The words of the switcher mark (UC-12, D3), or null under the limit. */
export function storageMark(account: MeterFields): "At the storage limit" | null {
  return atStorageLimit(account) ? "At the storage limit" : null;
}

// ── The words (D4) ──────────────────────────────────────────────────────────

/**
 * The fixed words of the notice, the step and the dialog. A function of
 * `name` names the mailbox: its label with two or more mailboxes, or null
 * for one mailbox, which reads "This mailbox" (D3).
 */
export const STORAGE_COPY = {
  action: "Remove older mail from Metorite",
  keep: "Keep it as it is",
  stepTitle: "Mailbox storage",
  dialogTitle: "Remove older mail from Metorite",
  metoriteOnly: "This removes mail from Metorite only.",
  kept: "Your rules, senders and unsent drafts stay.",
  loadOlderNote: "Load older can import this mail again, until the mailbox is at its limit.",
  keepNewest: "Keep the newest",
  pickDate: "A date",
  dateLabel: "Remove mail received before",
  chooseFirst: "Choose how much mail to keep.",
  chooseDay: "Choose a day.",
  dateInvalid: "Choose a day in the past.",
  counting: "Counting the mail…",
  previewNone: "Metorite holds no mail from before this date.",
  previewFailed: "Metorite could not count this mail. Try again.",
  confirm: "Remove from Metorite",
  cancel: "Cancel",
  close: "Close",
  removing: "Removing the mail. This can take a minute or two.",
  following: "The removal continues. Metorite checks it every 5 seconds.",
  unconfirmed:
    "Metorite cannot confirm the removal. Open this mailbox again later to see its storage.",
  failed: "Metorite could not finish the removal. Try again.",
  confirmedBefore: "Metorite removed the mail from before this date.",
} as const;

/** The sentence that says which mailbox does NOT change (D-EM-14). */
export function providerUnchanged(provider: EmailAccount["provider"] | undefined): string {
  if (provider === "gmail") return "Your Gmail mailbox does not change.";
  if (provider === "imap") return "Your mailbox on the mail server does not change.";
  return "Your Outlook mailbox does not change.";
}

/** "This mailbox" for one mailbox, else the label (D3). */
function subject(name: string | null): string {
  return name ?? "This mailbox";
}

/**
 * A size in whole MB, "512 MB". Under 1 MB it reads "less than 1 MB", so a
 * small preview never reads "about 0 MB". `locale` formats the number, and
 * the runtime's locale formats it when absent.
 */
export function formatMb(bytes: number, locale?: string): string {
  const b = Number.isFinite(bytes) && bytes > 0 ? bytes : 0;
  if (b > 0 && b < BYTES_PER_MB) return "less than 1 MB";
  return `${new Intl.NumberFormat(locale).format(Math.round(b / BYTES_PER_MB))} MB`;
}

/** The meter in words: "This mailbox uses 512 MB of its 500 MB in Metorite." */
export function meterSentence(
  stored: number,
  limit: number,
  name: string | null,
  opts: { now?: boolean; locale?: string } = {},
): string {
  const verb = opts.now ? "now uses" : "uses";
  return `${subject(name)} ${verb} ${formatMb(stored, opts.locale)} of its ${formatMb(limit, opts.locale)} in Metorite.`;
}

/** What a notice draws: the text, and the action for the `limit` kind. */
export type StorageNoticeView =
  | { kind: "limit"; text: string; action: string }
  | { kind: "gap"; text: string };

/**
 * The notice of one mailbox (scope item 1, D2, D4), or null.
 *
 * - `limit`: "This mailbox uses 512 MB of its 500 MB in Metorite. Metorite
 *   stopped importing older mail." with the action.
 * - `gap`: "Metorite imported this mailbox back to 14 Sep. It stopped there
 *   at the storage limit." with no action (D2). With no `importReachedAt`,
 *   it names no date.
 */
export function storageNotice(
  account: MeterFields & Partial<Pick<EmailAccount, "importPhase" | "importReachedAt">>,
  opts: { name: string | null; now: Date; locale?: string },
): StorageNoticeView | null {
  const kind = storageNoticeKind(account);
  if (kind === "limit") {
    return {
      kind,
      text:
        meterSentence(account.storedBytes ?? 0, account.storageLimitBytes ?? 0, opts.name, { locale: opts.locale }) +
        " Metorite stopped importing older mail.",
      action: STORAGE_COPY.action,
    };
  }
  if (kind === "gap") {
    const box = opts.name ?? "this mailbox";
    const reached = account.importReachedAt ? new Date(account.importReachedAt) : null;
    const text =
      reached && !Number.isNaN(reached.getTime())
        ? `Metorite imported ${box} back to ${shortDate(reached, opts.now)}. It stopped there at the storage limit.`
        : `Metorite stopped the import of ${box} at the storage limit.`;
    return { kind, text };
  }
  return null;
}

/**
 * True only for a count of exactly 0 (review round 1). NaN, a negative count
 * and no count are not 0. Do not write `!(messages > 0)` for this test. It
 * reads NaN as 0, so a broken answer would end the follow-up as "done".
 */
export function noMessagesLeft(messages: number): boolean {
  return messages === 0;
}

/** The preview in words: "12,400 messages, about 380 MB" (D4). */
export function previewLine(preview: Pick<OlderMailPreview, "messages" | "bytes">, locale?: string): string {
  if (noMessagesLeft(preview.messages)) return STORAGE_COPY.previewNone;
  // A count that is not a number is a failed count, never "no mail".
  if (!(preview.messages > 0)) return STORAGE_COPY.previewFailed;
  const count = new Intl.NumberFormat(locale).format(preview.messages);
  const noun = preview.messages === 1 ? "message" : "messages";
  const size = preview.bytes < BYTES_PER_MB ? formatMb(preview.bytes, locale) : `about ${formatMb(preview.bytes, locale)}`;
  return `${count} ${noun}, ${size}`;
}

/** "Metorite removed 12,400 messages." */
export function removedLine(removed: number, locale?: string): string {
  return removed === 1
    ? "Metorite removed 1 message."
    : `Metorite removed ${new Intl.NumberFormat(locale).format(removed)} messages.`;
}

// ── The choices of the dialog (scope item 6) ────────────────────────────────

/** "Keep the newest" 1, 2, 3 or 6 months. 6 is the ceiling of the import. */
export const KEEP_NEWEST_MONTHS: readonly number[] = [1, 2, 3, 6];

/** "1 month", "2 months". */
export function monthsLabel(months: number): string {
  return months === 1 ? "1 month" : `${months} months`;
}

const DAY_MS = 86_400_000;

/**
 * The `before` that keeps the newest `months` months: the ISO instant of
 * LOCAL midnight of the day `months * 30` days before `now`. A month is 30
 * days, the rule of the import window of the gateway (EM-T6a). Never a bare
 * date, because the gateway reads a bare date as UTC (D4).
 */
export function keepNewestBefore(months: number, now: Date): string {
  const day = new Date(now.getTime() - months * 30 * DAY_MS);
  return new Date(day.getFullYear(), day.getMonth(), day.getDate()).toISOString();
}

/**
 * The `before` of a date that the member picked: the ISO instant of LOCAL
 * midnight of that day. Null for a value that is not a real day, and for a
 * day that does not start in the past, because the gateway answers 400 then.
 */
export function beforeOfDate(value: string, now: Date): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]) - 1, Number(m[3])];
  const day = new Date(y, mo, d);
  if (day.getFullYear() !== y || day.getMonth() !== mo || day.getDate() !== d) return null;
  if (day.getTime() >= now.getTime()) return null;
  return day.toISOString();
}

/** Today as `YYYY-MM-DD` in the member's time zone, for the `max` of the date field. */
export function dateInputValue(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A choice of the dialog. `before` is null for a date that is not valid. */
export type RemovalChoice =
  | { kind: "months"; months: number; before: string }
  | { kind: "date"; date: string; before: string | null };

// ── The dialog, as a reducer (A8, A9, A11, A13) ─────────────────────────────

/** The preview of one `before`. `key` is the `before` the dialog asked for. */
export type PreviewState =
  | { state: "idle" }
  | { state: "loading"; key: string }
  | { state: "ready"; key: string; answer: OlderMailPreview }
  | { state: "failed"; key: string };

/**
 * The `before` that the confirm sends, or null while the confirm stays
 * disabled (A8, A9).
 *
 * Only a preview of the CURRENT choice counts, and only with one message or
 * more. The value is the `before` of the preview's answer, so the removal
 * takes exactly the mail that the member saw counted.
 */
export function confirmBefore(preview: PreviewState, currentKey: string | null): string | null {
  if (!currentKey || preview.state !== "ready" || preview.key !== currentKey) return null;
  return preview.answer.messages > 0 ? preview.answer.before : null;
}

/**
 * What a failed removal means (D1):
 * - `busy`: a 409. A sync holds the mailbox. The detail of the gateway shows
 *   as it is, and the choice stays (A11).
 * - `follow`: a 502, a 504 or a network error. The proxy stopped waiting, and
 *   the removal can still run. Nothing proves a failure (A13).
 * - `failed`: any other answer. The request failed, and some mail can be
 *   gone already, so the words claim no count.
 */
export type RemovalFailure =
  | { kind: "busy"; detail: string }
  | { kind: "follow" }
  | { kind: "failed"; detail: string };

export function removalFailure(error: unknown): RemovalFailure {
  const status = (error as { status?: unknown } | null)?.status;
  const message = error instanceof Error ? error.message : "";
  if (status === 409) {
    return { kind: "busy", detail: message || "A sync is running for this mailbox. Try again when it ends." };
  }
  if (typeof status !== "number" || status === 502 || status === 504) return { kind: "follow" };
  // A 4xx carries the reason of the gateway, for example a date that is not
  // in the past. A 5xx detail is not for the member.
  const own = status >= 400 && status < 500 && message && !message.startsWith("Gateway error");
  return { kind: "failed", detail: own ? message : STORAGE_COPY.failed };
}

/** How often the D1 follow-up reads the preview, and when it gives up. */
export const FOLLOW_UP_POLL_MS = 5_000;
export const FOLLOW_UP_LIMIT_MS = 180_000;

export type RemovalPhase = "choose" | "removing" | "following" | "done" | "unconfirmed";

export interface RemovalState {
  choice: RemovalChoice | null;
  preview: PreviewState;
  phase: RemovalPhase;
  /** The `before` that the removal sent, from the confirm on. */
  before: string | null;
  /** A 409 detail or a failure, under the choices. */
  message: string | null;
  /** The count of the answer. Null after a follow-up, which knows no count. */
  removed: number | null;
  /** The meter after the removal, from the answer or from a read of the accounts. */
  meter: { stored: number; limit: number } | null;
  /** When the follow-up started, in ms. */
  followStartedAt: number | null;
  /** The answers of the follow-up so far. A change arms the next read. */
  polls: number;
}

export type RemovalEvent =
  | { type: "pick"; choice: RemovalChoice }
  | { type: "previewAnswered"; key: string; answer: OlderMailPreview | null }
  | { type: "confirm"; before: string }
  | { type: "removed"; result: RemoveOlderResult }
  | { type: "removeFailed"; failure: RemovalFailure; now: number }
  | { type: "followAnswered"; answer: Pick<OlderMailPreview, "messages"> | null; now: number }
  | { type: "meterRead"; account: MeterFields | null };

export function initialRemovalState(): RemovalState {
  return {
    choice: null,
    preview: { state: "idle" },
    phase: "choose",
    before: null,
    message: null,
    removed: null,
    meter: null,
    followStartedAt: null,
    polls: 0,
  };
}

function meterOf(stored: number | null | undefined, limit: number | null | undefined) {
  return typeof stored === "number" && typeof limit === "number" && limit > 0 ? { stored, limit } : null;
}

/**
 * The one state machine of the dialog. Each change of the dialog goes
 * through it, so a test can drive every path with no DOM.
 */
export function removalReducer(s: RemovalState, e: RemovalEvent): RemovalState {
  switch (e.type) {
    case "pick": {
      if (s.phase !== "choose") return s;
      const key = e.choice.before;
      return { ...s, choice: e.choice, message: null, preview: key ? { state: "loading", key } : { state: "idle" } };
    }
    case "previewAnswered": {
      // A late answer for an earlier choice changes nothing (A8).
      if (s.preview.state !== "loading" || s.preview.key !== e.key) return s;
      return {
        ...s,
        preview: e.answer ? { state: "ready", key: e.key, answer: e.answer } : { state: "failed", key: e.key },
      };
    }
    case "confirm": {
      if (s.phase !== "choose" || confirmBefore(s.preview, s.choice?.before ?? null) !== e.before) return s;
      return { ...s, phase: "removing", before: e.before, message: null };
    }
    case "removed": {
      if (s.phase !== "removing") return s;
      return {
        ...s,
        phase: "done",
        removed: e.result.removed,
        meter: meterOf(e.result.storedBytes, e.result.storageLimitBytes),
      };
    }
    case "removeFailed": {
      if (s.phase !== "removing") return s;
      if (e.failure.kind === "busy") return { ...s, phase: "choose", message: e.failure.detail };
      if (e.failure.kind === "follow") return { ...s, phase: "following", followStartedAt: e.now, polls: 0 };
      // The count can have changed, so the preview of the choice runs again.
      const key = s.choice?.before ?? null;
      return {
        ...s,
        phase: "choose",
        message: e.failure.detail,
        preview: key ? { state: "loading", key } : { state: "idle" },
      };
    }
    case "followAnswered": {
      if (s.phase !== "following") return s;
      if (e.answer && noMessagesLeft(e.answer.messages)) return { ...s, phase: "done", removed: null, meter: null };
      if (s.followStartedAt !== null && e.now - s.followStartedAt >= FOLLOW_UP_LIMIT_MS) {
        return { ...s, phase: "unconfirmed" };
      }
      return { ...s, polls: s.polls + 1 };
    }
    case "meterRead": {
      if (s.phase !== "done") return s;
      return { ...s, meter: meterOf(e.account?.storedBytes, e.account?.storageLimitBytes) ?? s.meter };
    }
  }
}

/**
 * The `before` of a confirm that `removalReducer` takes, or null when the
 * reducer refuses it (review round 1). The dialog sends the POST only for a
 * value, so the reducer is the one guard of the removal. A second guard in
 * the dialog could drift from it and send a POST for a refused confirm.
 */
export function acceptedConfirm(s: RemovalState): string | null {
  const before = confirmBefore(s.preview, s.choice?.before ?? null);
  if (before === null) return null;
  // A refusal gives back the same state. In the phase `removing` that state
  // reads `removing` too, so the test is "a new state", not only the phase.
  const next = removalReducer(s, { type: "confirm", before });
  return next !== s && next.phase === "removing" ? before : null;
}

// ── After a removal (A14) ───────────────────────────────────────────────────

/**
 * The account with the meter of a removal's answer. The page writes it into
 * the store before it reads the accounts again, so a failed re-read still
 * shows the new meter (the pattern of EM-T6d fix round 1).
 */
export function withRemovalMeter<A extends MeterFields>(account: A, result: RemoveOlderResult): A {
  return { ...account, storedBytes: result.storedBytes, storageLimitBytes: result.storageLimitBytes };
}

// ── "Keep it as it is" (D6) ─────────────────────────────────────────────────

/**
 * Where "Keep it as it is" keeps the ids of the mailboxes that the member
 * keeps at the limit. Ids only, never mail or a meter (DESIGN_SYSTEM rule 9).
 * A device that refuses storage keeps nothing, and the step shows again.
 */
export const STORAGE_KEPT_KEY = "metorite.email.storageKept";

/** The most ids the list holds. The oldest id goes first. */
const KEPT_MAX = 50;

type KeptStore = Pick<Storage, "getItem" | "setItem">;

function localStore(): KeptStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The kept ids. A missing store, a refusal or text that is not a list gives []. */
export function readStorageKept(store: KeptStore | null = localStore()): string[] {
  try {
    const raw = store?.getItem(STORAGE_KEPT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string" && x !== "") : [];
  } catch {
    return [];
  }
}

/** Add `id` to the kept ids, write them, and return them. A refused write
 *  still returns the list, so the page moves on for this visit. */
export function keepStorage(id: string, store: KeptStore | null = localStore()): string[] {
  const next = [...readStorageKept(store).filter((x) => x !== id), id].slice(-KEPT_MAX);
  try {
    store?.setItem(STORAGE_KEPT_KEY, JSON.stringify(next));
  } catch {
    // A full or refused store keeps nothing. The list above still holds.
  }
  return next;
}
