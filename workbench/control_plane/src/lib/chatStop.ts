/**
 * The chat's Stop asks the server to cancel the run, and says what happened
 * (UXD-4).
 *
 * Before this, Stop set the chat idle at once and swallowed a failed cancel.
 * The member read "stopped" while the run went on and could still write. Now
 * the chat waits for the server: an `ok` answer stops the run in the UI too,
 * and a failure leaves the run live and says why beside the Stop button. A
 * second Stop (or Esc) tries again.
 *
 * Fence: `src/lib/chatStop.test.ts`.
 */

import type { SessionStreamState } from "@/lib/chatStore";

export type CancelOutcome = { ok: true } | { ok: false; reason: string };

/** Why a cancel did not land, in words the member can act on. */
export function stopFailureText(status: number | undefined, body = ""): string {
  if (status === undefined) {
    return "Could not stop the run: Metorite could not be reached. Try again.";
  }
  if (status === 403) {
    try {
      const detail = (JSON.parse(body) as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.trim()) return detail.trim();
    } catch {
      /* not JSON */
    }
    return "You cannot stop this run.";
  }
  if (status === 401) return "Your session ended. Sign in again, then stop the run.";
  return "Could not stop the run. Try again.";
}

/** POST the cancel and read its answer. Never throws. */
export async function cancelRun(
  threadId: string,
  fetchFn: typeof fetch = fetch,
): Promise<CancelOutcome> {
  let res: Response;
  try {
    res = await fetchFn("/api/agent/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threadId }),
      keepalive: true,
    });
  } catch {
    return { ok: false, reason: stopFailureText(undefined) };
  }
  if (res.ok) return { ok: true };
  const body = await res.text().catch(() => "");
  return { ok: false, reason: stopFailureText(res.status, body) };
}

// ── The session-state steps of a Stop ──────────────────────────────────────
// The hook (`useAgentChat.stopGeneration`) applies these in order. They are
// pure, so a test can hold each one.

/** Stop pressed: the button says it is working, and the run stays live. */
export function stopStarted(prev: SessionStreamState): SessionStreamState {
  return { ...prev, stopping: true, stopError: null };
}

/** The cancel failed: the run stays live, and the reason shows. */
export function stopFailed(prev: SessionStreamState, reason: string): SessionStreamState {
  return { ...prev, stopping: false, stopError: reason };
}

/** The server stopped the run: the chat goes idle. */
export function stopConfirmed(prev: SessionStreamState): SessionStreamState {
  return {
    ...prev,
    abortController: null,
    isLoading: false,
    recovering: false,
    runStatus: "idle",
    stopping: false,
    stopError: null,
    // A Stop asks for no follow-up, and a steer answer still on its way owes
    // none (`stopEpoch`). The server answers every tab "stopped".
    steerOwed: false,
    stopEpoch: (prev.stopEpoch ?? 0) + 1,
  };
}
