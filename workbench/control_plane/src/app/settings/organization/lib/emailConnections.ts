/**
 * Organisation → Email: the mapper and the copy (WS-17 EM-T3d).
 *
 * Owning spec: `project-docs/specs/email_app_master_plan.md` §10.4.3,
 * "EM-T3d — pre-approval and the connected-member count".
 *
 * D-EM-4: an admin sees how many members connected a mailbox, and never their
 * mail. The gateway's `GET /email/admin/connections` answers seven integers.
 * This module keeps those seven and nothing else, so a field the gateway grows
 * later cannot reach the page by accident.
 *
 * Two rules live here because a component cannot be tested in this tree
 * (vitest is node-env):
 *
 * 1. **A read that is not seven integers is a FAILED read.** It never becomes
 *    a row of zeros. "0 members" is an answer, and a failure must not look
 *    like one.
 * 2. **The copy never says the organization approved the app.** Microsoft
 *    holds the approval and Metorite records nothing, so it cannot know.
 *    `emailConnections.test.ts` refuses the word.
 */

/** The seven counts, as the tab draws them. */
export interface ConnectionCounts {
  members: number;
  mailboxes: number;
  microsoft: number;
  gmail: number;
  imap: number;
  syncErrors: number;
  firstSyncPending: number;
}

/** Each count and its wire name, in the order the tab draws them. */
const WIRE: ReadonlyArray<readonly [keyof ConnectionCounts, string]> = [
  ["members", "members"],
  ["mailboxes", "mailboxes"],
  ["microsoft", "microsoft"],
  ["gmail", "gmail"],
  ["imap", "imap"],
  ["syncErrors", "sync_errors"],
  ["firstSyncPending", "first_sync_pending"],
];

/**
 * The seven integer counts of the payload, or `null`.
 *
 * Every other field is dropped. A missing count, or one that is not a
 * non-negative integer, makes the whole payload `null`: half an answer is
 * not an answer.
 */
export function mapConnectionCounts(raw: unknown): ConnectionCounts | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const out: Partial<ConnectionCounts> = {};
  for (const [key, wire] of WIRE) {
    const v = r[wire];
    if (typeof v !== "number" || !Number.isInteger(v) || v < 0) return null;
    out[key] = v;
  }
  return out as ConnectionCounts;
}

/** The state of the count read. `loading` means nothing to show yet. */
export type CountsRead =
  | { state: "loading" }
  | { state: "failed" }
  | { state: "ready"; counts: ConnectionCounts };

/** One HTTP answer, as a count read. A refusal and an outage both fail. */
export function readConnectionCounts(ok: boolean, payload: unknown): CountsRead {
  if (!ok) return { state: "failed" };
  const counts = mapConnectionCounts(payload);
  return counts ? { state: "ready", counts } : { state: "failed" };
}

/** The label under each count. Short, because each sits under a number. */
export const COUNT_LABELS: Record<keyof ConnectionCounts, string> = {
  members: "Members connected",
  mailboxes: "Mailboxes",
  microsoft: "Microsoft 365",
  gmail: "Gmail",
  imap: "IMAP",
  syncErrors: "Sync errors",
  firstSyncPending: "Waiting for first sync",
};

export interface CountTile {
  key: keyof ConnectionCounts;
  value: number;
  label: string;
}

/** The seven counts, in a fixed order, each with its label. */
export function countTiles(counts: ConnectionCounts): CountTile[] {
  return WIRE.map(([key]) => ({ key, value: counts[key], label: COUNT_LABELS[key] }));
}

/** Fixed copy only. Nothing from a request appears in it. */
export const EMAIL_TAB_COPY = {
  approval: {
    title: "Approve Metorite for Microsoft 365",
    body:
      "Some Microsoft 365 organizations let only an admin add a new app. " +
      "If yours does, an admin of your Microsoft directory can approve Metorite one time for all members. " +
      "Then each member connects a mailbox with no request to IT.",
    link: "Open the Microsoft approval page",
    note:
      "The page opens in a new browser tab. Microsoft keeps the approval, not Metorite, " +
      "so this tab cannot show if the approval is in place.",
    notAdmin: "Not an admin of your Microsoft directory? Send this link to one.",
    unavailable:
      "The approval link is not available. This deployment has no Microsoft mail app, " +
      "or Metorite could not read it.",
  },
  counts: {
    title: "Connected mailboxes",
    intro:
      "Counts only. You see how many members connected a mailbox, and never their mail.",
    removed: "A member you remove counts here until you delete them permanently.",
    failed: "Metorite could not read the mailbox counts.",
    retry: "Try again",
  },
} as const;
