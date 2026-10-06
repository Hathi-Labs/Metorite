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
 *
 * Fence: `src/lib/confirmationQueue.test.ts` ("the answer POST").
 */

export type RespondOutcome = "ok" | "drop" | "retry";

export interface RespondInputBody {
  request_id: string;
  answer: string;
  was_freeform: boolean;
  /** Lets the gateway route the answer to the worker that owns the parked
   *  run (P1-2 cross-worker control bus). */
  thread_id: string;
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
  let res: Response;
  try {
    res = await fetchFn("/api/agent/respond-input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.error("respond-input failed — restoring HITL card", err);
    return "retry";
  }
  if (res.ok) return "ok";
  const text = await res.text().catch(() => "");
  const outcome = respondFailure(res.status);
  if (outcome === "drop") {
    console.warn(`respond-input refused (${res.status}), card dropped`, text);
  } else {
    console.error(`respond-input failed (${res.status}) — restoring HITL card`, text);
  }
  return outcome;
}
