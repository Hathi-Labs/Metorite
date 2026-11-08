/**
 * The approval card of a Studio record change in the chat (STU-B2c-2c).
 *
 * Spec: `project-docs/specs/ai_studio.md` §14, row B2c-2c, and §20.4. A record
 * write tool of Mira (`studio_tools._record_write`, B2c-2b) calls the door as
 * a delegated call with the ask marker. The door opens ONE request that waits
 * for the member (`app_requests.open_studio_request`), and the tool result
 * names it. `components/studio/StudioToolCards.tsx` draws the shared
 * `AppRequestCard` for that request, beside the other tool cards of a turn.
 * This file holds every rule of that card, so the node-env vitest holds them:
 *
 * 1. **Which result draws a card.** An ask of one of the three write tools:
 *    status 202, not saved, a state that waits or is approved, and a request
 *    id that is a UUID. A resend, a refusal and a running call draw nothing.
 * 2. **One press.** A press while another one is on its way, or after a
 *    decision landed, does nothing. A retry of the same decision after a
 *    refusal reuses its key, so the gateway decides once.
 * 3. **The answer back to Mira.** After a decision lands, the card sends ONE
 *    follow-up message into the same chat. It is fixed words, the request id
 *    and the decision. No value of the request goes in it, and it carries no
 *    typed mark. Mira's next turn sends the change again with the request id
 *    (the resend of B2c-2b). Mira never calls the decision route, so she never
 *    approves her own change.
 * 4. **What the card says.** "Sent to the chat" only after the chat sent the
 *    words. When the chat refused the send, the words wait in the composer,
 *    and the card says to press Send.
 *
 * No stream event, no `request_confirmation` and no decision tool: the card
 * of the store is the one approval. Fence: `studioCardState.test.ts`.
 */

import type { AppRequest, AppRequestDecision } from "@/lib/appRequest";
import type { AskOutcome } from "@/lib/shell/assistant";
import { bareToolName } from "@/lib/toolSteps";

/** The record write tools of B2c-2b. Each asks the member first. */
export const STUDIO_CARD_TOOLS: readonly string[] = [
  "studio_record_create",
  "studio_record_update",
  "studio_record_delete",
];

/** The canonical text of a UUID, as `studio_tools._skill_path` writes it. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** True for a request id that the card may read and name. */
export function isRequestId(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

/** The states of an ask result (`studio_tools._asked`). */
const ASKED_STATES: ReadonlySet<string> = new Set(["awaiting", "approved"]);

/**
 * The head of an ask result when the JSON does not parse, for example after
 * the stream cut a long result. The keys of `studio_tools._result` and
 * `_asked` come first, in this order, in JSON or in a Python repr. The match
 * starts at the first character, so text later in a result never makes one.
 */
const ASKED_HEAD =
  /^\{\s*["']ok["']:\s*(?:false|False),\s*["']errors["']:\s*\[\],\s*["']diff["']:\s*\[\],\s*["']saved["']:\s*(?:false|False),\s*["']status["']:\s*202,\s*["']state["']:\s*["'](awaiting|approved)["'],\s*["']request_id["']:\s*["']([0-9a-f-]{36})["']/;

/** A tool event as the chat holds it. */
export interface ToolEventLike {
  name: string;
  result?: string;
  status: string;
}

/**
 * The request id of a write tool result that ASKED, or null. Only one of the
 * three write tools, only a call that finished, and only the ask shape: a
 * 202 that saved nothing. A resend (200), a refusal and a running call give
 * null, so they draw no card.
 */
export function askedRequestId(e: ToolEventLike): string | null {
  if (e.status !== "done" || !e.result) return null;
  if (!STUDIO_CARD_TOOLS.includes(bareToolName(e.name))) return null;
  const text = e.result.trim();
  try {
    const v = JSON.parse(text) as Record<string, unknown> | null;
    if (!v || typeof v !== "object" || Array.isArray(v)) return null;
    const asked = v.ok === false && v.saved === false && v.status === 202
      && typeof v.state === "string" && ASKED_STATES.has(v.state);
    return asked && isRequestId(v.request_id) ? v.request_id : null;
  } catch {
    const m = ASKED_HEAD.exec(text);
    return m && isRequestId(m[2]) ? m[2] : null;
  }
}

/** The request ids that one turn asked for, each once, in order. */
export function askedRequestIds(events: readonly ToolEventLike[] | undefined): string[] {
  const out: string[] = [];
  for (const e of events ?? []) {
    const id = askedRequestId(e);
    if (id && !out.includes(id)) out.push(id);
  }
  return out;
}

/**
 * The follow-up message of one decision: fixed words, the request id and the
 * decision. Nothing else from the request goes in it (B2c-2c). An id that is
 * not a UUID gives "", and the card then sends nothing.
 */
export function followUpText(requestId: string, decision: AppRequestDecision): string {
  if (!isRequestId(requestId)) return "";
  const said = decision === "approve" ? "approved" : "declined";
  return `I ${said} the change on the card. Request id: ${requestId}.`;
}

/** The local state of one card. The server's request is the rest. */
export interface CardLocal {
  /** The decision on its way, so its button shows the verb and a spinner. */
  busy: AppRequestDecision | null;
  /** The decision that landed from this card. */
  decided: AppRequestDecision | null;
  /** Why the last press did not land. */
  error: string | null;
  /** The ask of the follow-up message, or null before one. */
  askSeq: number | null;
  /** The key of the last press. A retry of the same decision reuses it. */
  key: { decision: AppRequestDecision; value: string } | null;
}

export const IDLE: CardLocal = { busy: null, decided: null, error: null, askSeq: null, key: null };

/** A press may go: nothing is on its way, and no decision landed here. */
export function mayPress(local: CardLocal): boolean {
  return local.busy === null && local.decided === null;
}

/** What one press needs from the page. The component passes the real ones. */
export interface PressDeps {
  /** POST the decision to the BFF (`decideRequest`). It throws on a refusal. */
  decide: (requestId: string, decision: AppRequestDecision, key: string) => Promise<unknown>;
  /** Send the words into the same chat (`openAssistant({ text })`). The ask seq. */
  tell: (text: string) => number | null;
  /** A new key for a press. */
  newKey: () => string;
  /** The words of a refusal for the member. */
  refusal: (err: unknown) => string;
}

/**
 * One press of Approve or Decline. `get` reads the card's state at once, and
 * `set` writes it at once, so a second press in the same tick sees the first.
 *
 * The press posts the decision once. Only when it lands does the card send
 * the follow-up, once. A refused decision sends nothing and keeps its key for
 * a retry. Returns true when the decision landed.
 */
export async function pressDecision(
  requestId: string,
  decision: AppRequestDecision,
  get: () => CardLocal,
  set: (next: CardLocal) => void,
  deps: PressDeps,
): Promise<boolean> {
  const before = get();
  if (!mayPress(before)) return false;
  const key = before.key?.decision === decision ? before.key.value : deps.newKey();
  set({ ...before, busy: decision, error: null, key: { decision, value: key } });
  try {
    await deps.decide(requestId, decision, key);
  } catch (err) {
    set({ ...get(), busy: null, error: deps.refusal(err) });
    return false;
  }
  const text = followUpText(requestId, decision);
  const askSeq = text ? deps.tell(text) : null;
  set({ ...get(), busy: null, decided: decision, askSeq });
  return true;
}

/**
 * The request as the card draws it. A decision that landed here wins over a
 * read that still says it waits, so the buttons go at once and never come
 * back while the read catches up.
 */
export function shownRequest(server: AppRequest, local: CardLocal): AppRequest {
  if (!local.decided || server.state !== "awaiting") return server;
  return {
    ...server,
    state: local.decided === "approve" ? "approved" : "declined",
    decision: local.decided,
  };
}

/** The line beside the state of a decided card: what became of the follow-up. */
export function followUpNote(local: CardLocal, outcome: AskOutcome | null): string | null {
  if (!local.decided || local.askSeq === null) return null;
  if (outcome === "sent") return "Sent to the chat.";
  if (outcome === "kept") return "Your answer waits in the message box. Press Send to send it.";
  if (outcome === "lost") {
    const said = local.decided === "approve" ? "approved" : "declined";
    return `Your answer did not reach the chat. Tell the assistant that you ${said} the change.`;
  }
  return "Sending your answer to the chat…";
}

/** The most cards that keep their state for a remount. Memory only. */
export const MAX_REMEMBERED = 100;

const remembered = new Map<string, CardLocal>();

/**
 * The state of a card that drew before, for a remount in this page: a switch
 * of chat and back, or a new turn that draws the bubble again. A reload of
 * the page starts empty, and the card reads the request again.
 */
export function rememberedCard(requestId: string): CardLocal {
  return remembered.get(requestId) ?? IDLE;
}

/** Keep the state of one card. It holds no value of the request. */
export function rememberCard(requestId: string, local: CardLocal): void {
  remembered.delete(requestId);
  remembered.set(requestId, local);
  while (remembered.size > MAX_REMEMBERED) {
    const oldest = remembered.keys().next().value;
    if (oldest === undefined) break;
    remembered.delete(oldest);
  }
}

/** For tests: forget every card. */
export function __forgetCardsForTests(): void {
  remembered.clear();
}
