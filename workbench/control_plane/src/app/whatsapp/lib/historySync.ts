// WS-20 WA-C3 (spec `whatsapp_message_manager.md` §12.4.1 P11, P12): what
// the member reads about the coexistence history import. Pure, so a test
// reaches every state with no DOM (vitest runs in the node environment).

import type { WaAccount } from "./types";

/** What `POST /whatsapp/connect/embedded` answers in `history_sync`. */
export type ConnectHistorySync = "requested" | "failed" | "pending" | null;

/** The copy of the connect's last step, for each answer. Each one says
 *  what is true: a request is not an import, and a pending import has not
 *  started. */
export function connectHistoryCopy(state: ConnectHistorySync | undefined): string {
  switch (state) {
    case "requested":
      return (
        "Metorite asked Meta for the last six months of your 1:1 chats. They " +
        "arrive over the next hours, and new messages arrive from now on. " +
        "Numbers shows the progress."
      );
    case "failed":
      return (
        "Your number is connected, but Meta did not start the history import. " +
        "Start it again from Numbers in the next 24 hours. New messages arrive " +
        "from now on."
      );
    case "pending":
      return (
        "Your number is connected. The history import has not started, so " +
        "your inbox starts from now. New messages land in your triage queue " +
        "as they arrive."
      );
    default:
      return (
        "New messages land in your triage queue as they arrive. Older chats " +
        "are not imported, so your inbox starts from now."
      );
  }
}

export type HistoryTone = "muted" | "success" | "destructive";

export type HistoryLine = {
  text: string;
  tone: HistoryTone;
  /** Show "Start history import": pending or failed, inside Meta's window. */
  canStart: boolean;
};

type HistoryFields = Pick<
  WaAccount,
  | "history_sync_state"
  | "history_sync_error"
  | "history_import_progress"
  | "history_sync_deadline"
>;

/** True when Meta's 24-hour window has closed, or when no deadline is
 *  known. With no deadline the member cannot start the import, so the
 *  safe answer is "passed". */
export function historyDeadlinePassed(
  deadline: string | null | undefined,
  now: Date
): boolean {
  if (!deadline) return true;
  const at = Date.parse(deadline);
  if (Number.isNaN(at)) return true;
  return now.getTime() >= at;
}

const WINDOW_PASSED =
  "The 24-hour window has passed, so disconnect the number and connect it again to import the history.";

/** The one history line of an account on Numbers, or null for an account
 *  that is not coexistence. */
export function accountHistoryLine(a: HistoryFields, now: Date): HistoryLine | null {
  const state = a.history_sync_state ?? null;
  if (state === null) return null;
  const passed = historyDeadlinePassed(a.history_sync_deadline, now);
  switch (state) {
    case "complete":
      return { text: "History imported", tone: "success", canStart: false };
    case "declined":
      return {
        text:
          a.history_sync_error ||
          "History sharing is off in the WhatsApp Business app.",
        tone: "muted",
        canStart: false,
      };
    case "requested": {
      const pct = a.history_import_progress;
      return {
        text:
          typeof pct === "number"
            ? `Importing history · ${Math.max(0, Math.min(100, pct))}%`
            : "History import requested",
        tone: "muted",
        canStart: false,
      };
    }
    case "failed": {
      const reason = a.history_sync_error
        ? `History import failed: ${a.history_sync_error}`
        : "History import failed.";
      return {
        text: passed ? `${reason} ${WINDOW_PASSED}` : reason,
        tone: "destructive",
        canStart: !passed,
      };
    }
    case "pending":
      return {
        text: passed
          ? `History import not started. ${WINDOW_PASSED}`
          : "History import not started",
        tone: "muted",
        canStart: !passed,
      };
    default:
      return { text: `History import: ${state}`, tone: "muted", canStart: false };
  }
}
