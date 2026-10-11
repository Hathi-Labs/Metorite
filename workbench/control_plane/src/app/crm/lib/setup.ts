// ── Change CRM setup (WS-53 CRM-U5b, spec crm_platform.md §3.4) ─────────────
//
// An admin can send the organization back to the first-open choice. The
// gateway allows it only with 0 records and no live Zoho connection. This file
// holds the words and the decisions, so they have tests. The card that draws
// them is `components/SetupPanel.tsx`.

import { CrmError } from "./api";
import type { ConnectionInfo, CrmMode } from "./connect";

export const SETUP_ANCHOR = "crm-setup";

export const CONFIRM_TITLE = "Change your CRM setup?";
export const CONFIRM_BODY =
  "The pipeline stages and lost reasons go, and you choose again how your CRM works.";
export const CONNECTED_REASON = "Disconnect Zoho first, then change the setup.";
export const RECORDS_REASON = "This CRM has records, so its setup cannot change.";
export const FAILED_TOAST = "The setup did not change. Try again.";

/** The mode, as the card says it. */
export function setupLine(mode: CrmMode): string {
  if (mode === "native") return "Metorite is your CRM";
  if (mode === "mirror") return "Synced from Zoho";
  return "Not set up";
}

/**
 * Why the button is off, or `null` when the click may go. The page knows the
 * connection. It holds no count of records, so the gateway decides that one
 * and its 409 comes back as a toast.
 */
export function setupBlock(mode: CrmMode, connection: ConnectionInfo | null): string | null {
  if (mode === null) return null;
  if (connection?.status === "connected") return CONNECTED_REASON;
  return null;
}

/** The words of a failed change: the gateway's `message` for a 409. */
export function setupFailure(err: unknown): string {
  return err instanceof CrmError && err.message ? err.message : FAILED_TOAST;
}

/**
 * The confirm. It sends `{mode: null}`, then wakes every CRM watcher so the
 * page draws the first-open choice. Returns `null` on success, or the toast
 * words on a refusal. It never throws.
 */
export async function changeSetup(deps: {
  save: () => Promise<unknown>;
  invalidate: () => void;
}): Promise<string | null> {
  try {
    await deps.save();
  } catch (err) {
    return setupFailure(err);
  }
  deps.invalidate();
  return null;
}
