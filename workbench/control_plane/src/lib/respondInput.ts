/**
 * The one POST that answers a parked HITL tool: `/api/agent/respond-input`.
 *
 * Every blocking card (a confirmation, a question, an ask_user prompt, a
 * blocking generative UI) answers through here, so the rule for a failure
 * lives in one place:
 *
 * - `ok`: the server took the answer.
 * - `retry`: a network fault or a 5xx. The tool may still be waiting, so
 *   the caller restores the card and the member can try again.
 * - `drop`: any 4xx. 409 says no question waits on that id, and 403 says
 *   this member cannot answer it. A restored card fails the same way on
 *   every click. That loop is what the member saw on 2026-10-06.
 * - A 409 `run_restarted` is a `drop` that carries `resend`: the run that
 *   asked died with an app update, and the gateway hands back the question
 *   and the answer as one message. The caller sends THAT, not the bare
 *   answer, so the next run knows what was asked (incident 2026-10-09).
 *   A card whose run PARKED (WS-51 S2) comes back the same way.
 * - A 409 `answer_in_other_account` (WS-51 S2) is a `retry` that carries
 *   `otherAccount`: this tab drew the card for one account, and the browser
 *   is now signed in to another. The route refused it before the gateway, so
 *   nothing reached the other org. The caller keeps the card and says
 *   "Switch to <account> to answer".
 *
 * Fences: `src/lib/confirmationQueue.test.ts` ("the answer POST") and
 * `src/lib/respondInput.test.ts` (the other account).
 */

import { restartedCardMessage } from "@/lib/chatRecovery";

export type RespondOutcome = "ok" | "drop" | "retry";

/** The outcome, plus the message to send instead when the run restarted. */
export interface RespondResult {
  outcome: RespondOutcome;
  resend: string | null;
  /** The account that drew the card, when the browser is signed in to
   *  another one now (WS-51 S2). The answer was not sent. */
  otherAccount?: string | null;
}

export interface RespondInputBody {
  request_id: string;
  answer: string;
  was_freeform: boolean;
  /** Lets the gateway route the answer to the worker that owns the parked
   *  run (P1-2 cross-worker control bus). */
  thread_id: string;
  /** The account this tab drew the card for (WS-51 S2). The route refuses
   *  the answer when the browser is signed in to another account now. */
  as?: string | null;
}

/**
 * The route's refusal for an answer from a tab whose account is no longer the
 * signed-in one, or null to go on (WS-51 S2). `drawnFor` can only refuse,
 * never grant: the gateway still checks the room under the session's tenant.
 */
export function otherAccountRefusal(
  drawnFor: string | null | undefined,
  signedInAs: string,
): Response | null {
  const want = (drawnFor ?? "").trim().toLowerCase();
  if (!want || want === signedInAs.trim().toLowerCase()) return null;
  return new Response(
    JSON.stringify({ detail: { error: "answer_in_other_account", account: drawnFor } }),
    { status: 409, headers: { "Content-Type": "application/json" } },
  );
}

/** The account named by a 409 `answer_in_other_account`, or null. */
export function otherAccountOf(body: string): string | null {
  try {
    const d = (JSON.parse(body) as { detail?: { error?: unknown; account?: unknown } })?.detail;
    if (d && d.error === "answer_in_other_account" && typeof d.account === "string" && d.account) {
      return d.account;
    }
  } catch {
    /* not the route's JSON */
  }
  return null;
}

/** What a failed POST means. `status` is undefined for a network fault. */
export function respondFailure(status: number | undefined): "retry" | "drop" {
  if (status === undefined) return "retry";
  return status >= 500 ? "retry" : "drop";
}

export async function sendRespondInput(
  body: RespondInputBody,
  fetchFn: typeof fetch = fetch,
): Promise<RespondOutcome> {
  return (await sendRespondInputResult(body, fetchFn)).outcome;
}

export async function sendRespondInputResult(
  body: RespondInputBody,
  fetchFn: typeof fetch = fetch,
): Promise<RespondResult> {
  let res: Response;
  try {
    res = await fetchFn("/api/agent/respond-input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.error("respond-input failed — restoring HITL card", err);
    return { outcome: "retry", resend: null };
  }
  if (res.ok) return { outcome: "ok", resend: null };
  const text = await res.text().catch(() => "");
  const otherAccount = res.status === 409 ? otherAccountOf(text) : null;
  if (otherAccount) {
    // Not sent. The card stays, so the member can answer after the switch.
    return { outcome: "retry", resend: null, otherAccount };
  }
  const outcome = respondFailure(res.status);
  if (outcome === "drop") {
    const resend = res.status === 409 ? restartedCardMessage(text) : null;
    if (resend) return { outcome, resend };
    console.warn(`respond-input refused (${res.status}), card dropped`, text);
  } else {
    console.error(`respond-input failed (${res.status}) — restoring HITL card`, text);
  }
  return { outcome, resend: null };
}
