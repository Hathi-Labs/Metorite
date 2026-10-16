/**
 * "Tell me on WhatsApp" in the Rules UI (WS-59 RE-5, review finding P1-4).
 *
 * The Rules UI reads `GET /api/me/reach/status` once. The action is in the
 * list only when Reach is available to the member. When Reach is available
 * and the member has no WhatsApp link, the action shows disabled, with the
 * step that turns it on. Any other answer reads as not available.
 */
import type { RuleActionType } from "./types";

export interface ReachStatus {
  available: boolean;
  linked: boolean;
}

export const REACH_OFF: ReachStatus = { available: false, linked: false };

/** The hint on the disabled action. */
export const LINK_FIRST = "Link WhatsApp in My Profile first";

/** The status of a response body. Anything else is not available. */
export function readReachStatus(status: number, body: unknown): ReachStatus {
  if (status !== 200 || !body || typeof body !== "object") return REACH_OFF;
  const b = body as Record<string, unknown>;
  const available = b.available === true;
  return { available, linked: available && b.linked === true };
}

export async function fetchReachStatus(): Promise<ReachStatus> {
  try {
    const res = await fetch("/api/me/reach/status", { cache: "no-store" });
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    return readReachStatus(res.status, body);
  } catch {
    return REACH_OFF;
  }
}

export interface ActionOption {
  type: RuleActionType;
  disabled: boolean;
  /** The text after the label, or null. */
  hint: string | null;
}

/**
 * The options of one action's type list. NOTIFY_ME is left out when Reach
 * is not available, and disabled when the member has no link. The type
 * that the action already holds always stays in the list, so the select
 * shows an existing rule as it is.
 */
export function actionOptions(
  types: readonly RuleActionType[],
  reach: ReachStatus,
  current: RuleActionType,
): ActionOption[] {
  const out: ActionOption[] = [];
  for (const type of types) {
    if (type !== "NOTIFY_ME" || current === "NOTIFY_ME") {
      out.push({ type, disabled: false, hint: null });
    } else if (reach.available) {
      out.push({ type, disabled: !reach.linked, hint: reach.linked ? null : LINK_FIRST });
    }
  }
  return out;
}
