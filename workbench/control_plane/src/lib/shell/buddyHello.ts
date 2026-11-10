/**
 * The hello (WS-BUD BUD-1, D177, `onboarding.md` §13): the first thing a new
 * member sees in the assistant.
 *
 * `POST /api/chat/sessions/<id>/buddy-hello` asks the gateway to write the
 * hello as the chat's first assistant row, named "Welcome". The server
 * claims `buddy_hello_at` first (first write wins), so two tabs make ONE row.
 * It writes every word (D-PM-39). This file only asks, once per chat, and
 * says what came back:
 *
 * - `{ preview, title }` when the server posted the hello. The chat then loads
 *   its messages and draws the row and its chips;
 * - `null` on a 204 (another tab won, or the room is shared), on any error and
 *   on a network fault. The chat opens as usual and nothing is retried.
 *
 * No model call happens on this path. The three chips run on the client
 * (`runHelloChip`): the tour starts by a move to `TOUR_HREF`, the question
 * goes out as the member's own message, and the third closes the panel.
 */
import { closeAssistant } from "./assistant";
import { TOUR_HREF } from "./guide";

/** The route of the hello of one chat. */
export function helloPath(sessionId: string): string {
  return `/api/chat/sessions/${encodeURIComponent(sessionId)}/buddy-hello`;
}

/** The chip that sends this question as the member's message. */
export const HELLO_QUESTION = "What can you do here?";

/** The row id of the hello (`HELLO_MESSAGE_ID` in `gateway/routes/chat_brief.py`). */
export const HELLO_ROW_ID = "buddy-hello";

/** Whether a message is the hello. It gets no follow-up pills and no feedback row. */
export function isHelloRow(id: unknown): boolean {
  return id === HELLO_ROW_ID;
}

export interface HelloPosted {
  preview: string;
  title: string;
}

export async function requestHello(
  sessionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<HelloPosted | null> {
  try {
    const res = await fetchImpl(helloPath(sessionId), { method: "POST", cache: "no-store" });
    if (res.status !== 200) return null;
    const body = (await res.json()) as { posted?: unknown; preview?: unknown; title?: unknown } | null;
    if (!body || body.posted !== true) return null;
    return {
      preview: typeof body.preview === "string" ? body.preview : "",
      title: typeof body.title === "string" && body.title ? body.title : "Welcome",
    };
  } catch {
    return null;
  }
}

const _claimed = new Set<string>();
const _helloChats = new Set<string>();
const _announced = new Set<string>();

/** True once for each chat: a second mount of the dock asks nothing. */
export function claimHello(sessionId: string): boolean {
  if (!sessionId || _claimed.has(sessionId)) return false;
  _claimed.add(sessionId);
  _helloChats.add(sessionId);
  return true;
}

/** Whether the hello opened in this chat. The day's brief never lands there. */
export function opensHello(sessionId: string): boolean {
  return _helloChats.has(sessionId);
}

/**
 * True once for a chat whose hello opened in THIS page: the screen reader
 * hears the greeting one time (`AgentChat`'s status line). A chat the member
 * reopens later, or in another page, announces nothing.
 */
export function announceHelloOnce(sessionId: string): boolean {
  if (!sessionId || !_helloChats.has(sessionId) || _announced.has(sessionId)) return false;
  _announced.add(sessionId);
  return true;
}

let _priorSession: string | null = null;

/**
 * The chat the panel showed before the hello opened it (`null` for none). The
 * landing notes it, so a hello that leaves no row (a 204, a refusal, a
 * fault) can put the panel back as it was (`undoHello` in `AssistantDock`).
 */
export function noteBeforeHello(sessionId: string | null): void {
  _priorSession = sessionId;
}

/** The chat to restore after a hello that left nothing. Read once. */
export function takeBeforeHello(): string | null {
  const id = _priorSession;
  _priorSession = null;
  return id;
}

/** Tests only. */
export function __resetHelloForTests(): void {
  _claimed.clear();
  _helloChats.clear();
  _announced.clear();
  _priorSession = null;
}

export type HelloChip = "tour" | "ask" | "look";

/**
 * A tap on a chip of the hello. `send` is the card's `onAction`, which sends
 * words as the member's message. `go` moves the page. The tour never starts
 * in place: stop 4 (`home.my-day`) lives on `/` only, so the tap goes to
 * `TOUR_HREF` from any page and the host starts the guide from the address
 * (`guide.ts`, D-GD-1: the member asked for it).
 */
export function runHelloChip(
  id: string,
  send: ((text: string) => void) | undefined,
  go?: (href: string) => void,
): void {
  if (id === "tour") go?.(TOUR_HREF);
  else if (id === "ask") send?.(HELLO_QUESTION);
  else if (id === "look") closeAssistant();
}
