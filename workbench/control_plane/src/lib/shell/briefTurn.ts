/**
 * The one call for the day's brief as the first turn of a fresh chat (WS-62
 * CF-5, `chat_first_surface.md` §4.6, D-CF-6).
 *
 * `POST /api/chat/sessions/<id>/brief` asks the gateway to write the brief
 * as the chat's first assistant row. The server decides whether there is one
 * (zero messages, not yet today, a brief exists) and writes the row itself
 * (D-PM-39). This file only asks, once, and says what came back:
 *
 * - `{ preview }` when the server posted the brief. The chat then loads its
 *   messages and draws the card;
 * - `null` on a 204, on any error and on a network fault. The chat opens
 *   empty, with the starter pills. Nothing is retried, and nothing is shown.
 *
 * No model call happens on this path. The dock calls it only through
 * `wantsBrief` in `assistant.ts` (fence: `assistant.test.ts`).
 */

/** The route of the brief of one chat. */
export function briefPath(sessionId: string): string {
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/brief`;
}

export interface BriefPosted {
  /** The brief's lead line, which the session list shows as its preview. */
  preview: string;
}

export async function requestBrief(
  sessionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<BriefPosted | null> {
  try {
    const res = await fetchImpl(briefPath(sessionId), { method: "POST", cache: "no-store" });
    if (res.status !== 200) return null;
    const body = (await res.json()) as { posted?: unknown; preview?: unknown } | null;
    if (!body || body.posted !== true) return null;
    return { preview: typeof body.preview === "string" ? body.preview : "" };
  } catch {
    return null;
  }
}
