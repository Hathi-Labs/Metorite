/**
 * "Continue in a fresh chat": the way through a held chat
 * (`one_assistant_context.md` §9, slice WT-2).
 *
 * A chat that read mail from outside senders holds back its writes. The
 * server says so to the web chat with one CUSTOM event on the turn,
 * `chat_continue_offer`. It sends the event only while `CHAT_CONTINUE_ORGS`
 * holds the member's org, so this file never reads a flag. No event means no
 * pill (the server twin is `CONTINUE_OFFER_EVENT` in
 * `packages/acb_skills/acb_skills/egress.py`).
 *
 * The tap calls `POST /api/chat/sessions/<id>/continue`. The server makes the
 * new chat and returns `{ session_id, text }`. The shell opens that chat and
 * puts `text` in the composer. This file never builds the text, never reads
 * the old chat's rows, and never sends. The member presses Send.
 *
 * Pure apart from the one fetch. Fence (R7): `chatContinue.test.ts`.
 */

export const CONTINUE_OFFER_EVENT = "chat_continue_offer";

/** The words on the pill. */
export const CONTINUE_LABEL = "Continue in a fresh chat";

/** The small header over the pill, in the idiom of the follow-up pills. */
export const CONTINUE_HEADING = "This chat is on hold";

/** What the member reads when the new chat did not open. */
export const CONTINUE_COPY = {
  /** 404: the flag is off for the org, or the chat is not open to the member. */
  closed: "This chat cannot move to a fresh chat now.",
  /** 409: the chat holds no message that the member typed. */
  nothing: "This chat has no message of yours to carry over.",
  /** 503, a fault and a network failure: nothing was made, so a retry is safe. */
  retry: "The fresh chat did not open. Try again in a moment.",
} as const;

export interface ContinueOpened {
  ok: true;
  sessionId: string;
  text: string;
}

export interface ContinueRefused {
  ok: false;
  status: number;
  copy: string;
}

export type ContinueResult = ContinueOpened | ContinueRefused;

/** The route of the tap. */
export function continuePath(sessionId: string): string {
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/continue`;
}

/**
 * True when a turn carries the offer. The server marks the turn, and the chat
 * draws the pill under the newest such turn only.
 */
export function continueOffered(
  customEvents: ReadonlyArray<{ name: string }> | undefined,
): boolean {
  return (customEvents ?? []).some((e) => e.name === CONTINUE_OFFER_EVENT);
}

/** The id of the message that carries the newest offer, or null. */
export function newestOfferId(
  messages: ReadonlyArray<{
    id: string;
    role: string;
    streaming?: boolean;
    customEvents?: ReadonlyArray<{ name: string }>;
  }>,
): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "assistant" && continueOffered(m.customEvents)) {
      return m.streaming ? null : m.id;
    }
  }
  return null;
}

/** The short plain words for a refused status. */
export function continueCopy(status: number): string {
  if (status === 404) return CONTINUE_COPY.closed;
  if (status === 409) return CONTINUE_COPY.nothing;
  return CONTINUE_COPY.retry;
}

/**
 * Ask the server to continue *sourceId* in a fresh chat. Never throws.
 * The request names no text, no org and no member: the server reads the
 * member from the session and takes the words from its own rows.
 */
export async function requestContinue(
  sourceId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ContinueResult> {
  try {
    const res = await fetchImpl(continuePath(sourceId), { method: "POST", cache: "no-store" });
    if (res.status !== 200) {
      return { ok: false, status: res.status, copy: continueCopy(res.status) };
    }
    const body = (await res.json()) as { session_id?: unknown; text?: unknown } | null;
    const sessionId = typeof body?.session_id === "string" ? body.session_id : "";
    const text = typeof body?.text === "string" ? body.text : "";
    if (!sessionId || !text) return { ok: false, status: 503, copy: CONTINUE_COPY.retry };
    return { ok: true, sessionId, text };
  } catch {
    return { ok: false, status: 503, copy: CONTINUE_COPY.retry };
  }
}
