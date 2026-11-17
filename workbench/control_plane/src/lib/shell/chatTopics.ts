/**
 * The topic chip of a chat (WS-CO CO-2b, `chat_organization.md` §6, D163).
 *
 * The chip in the chat header shows the topic of the chat, or "No topic". A
 * press opens a menu that moves the chat: the company topics, the member's
 * own topics, "No topic", and "New topic…". A move is the member's own, and
 * the automatic pass never moves it back (`setSessionTopic` in
 * `lib/sessions.ts`).
 *
 * This file holds the rules and the requests, and draws nothing, so the
 * tests read it. `TopicChip.tsx` draws it.
 *
 * Fence (R7): `chatTopics.test.ts`.
 */

import { invalidate } from "@/lib/dataCache";
import type { ChatSession, SessionTopic } from "@/lib/sessions";

import { shownTopic } from "./chatHistory";

/** The read of the topics. The path is the key of the read cache too. */
export const TOPICS_PATH = "/api/chat/topics";

/** The words of the chip when a chat has no topic. */
export const NO_TOPIC = "No topic";

/** The words of the row that makes a new topic. */
export const NEW_TOPIC = "New topic…";

/** The headings of the menu. */
export const COMPANY_HEADING = "Company topics";
export const MINE_HEADING = "My topics";

/** A topic as the gateway lists it. The list names no owner. */
export interface ChatTopic {
  id: string;
  name: string;
  scope: "company" | "member";
  hidden: boolean;
}

/** The words of an empty list: what is empty, why, and the next act (§10). */
export const NO_TOPICS_YET = "No topics yet. Make one, and this chat moves to it.";

/** The words of a failed read: what happened, and what to do next (§10). */
export const TOPICS_DID_NOT_LOAD = "The topics did not load. Close this menu and try again.";

/** One row of the menu, top to bottom. */
export type TopicMenuRow =
  | { kind: "heading"; label: string }
  | { kind: "topic"; topic: SessionTopic; current: boolean }
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "none"; current: boolean }
  | { kind: "new" };

/** The words of the chip: the topic of the chat, or "No topic". */
export function chipLabel(session: Pick<ChatSession, "topicId" | "topicName"> | null): string {
  return (session && shownTopic(session)?.name) || NO_TOPIC;
}

/**
 * The topics of an answer of `GET /api/chat/topics`. A row with a bad shape
 * drops out, and so does a hidden company topic: the chip never offers it.
 */
export function parseTopics(body: unknown): ChatTopic[] {
  const rows = body && typeof body === "object" ? (body as { topics?: unknown }).topics : null;
  if (!Array.isArray(rows)) return [];
  const out: ChatTopic[] = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const t = r as Record<string, unknown>;
    if (typeof t.id !== "string" || typeof t.name !== "string") continue;
    if (t.scope !== "company" && t.scope !== "member") continue;
    if (t.hidden === true) continue;
    out.push({ id: t.id, name: t.name, scope: t.scope, hidden: false });
  }
  return out;
}

/** The two rows that never move: they come first in each state of the list. */
export const FIXED_ROWS = 2;

/**
 * The rows of the menu (review P2-3). "New topic…" and "No topic" come FIRST,
 * so they keep their place while the list loads. Then the list: the company
 * topics, then the member's own topics, each under its heading, in the order
 * of the server. A heading with no topic under it is left out. While the list
 * is not read yet (`topics` undefined), one row says so. A list with no topic
 * says so too, and "New topic…" is the next act.
 */
export function topicMenuRows(topics: readonly ChatTopic[] | undefined, currentId: string | null): TopicMenuRow[] {
  const rows: TopicMenuRow[] = [{ kind: "new" }, { kind: "none", current: currentId === null }];
  if (topics === undefined) {
    rows.push({ kind: "loading" });
  } else if (!topics.some((t) => !t.hidden)) {
    rows.push({ kind: "empty" });
  } else {
    for (const [scope, label] of [["company", COMPANY_HEADING], ["member", MINE_HEADING]] as const) {
      const run = topics.filter((t) => t.scope === scope && !t.hidden);
      if (run.length === 0) continue;
      rows.push({ kind: "heading", label });
      for (const t of run) {
        rows.push({ kind: "topic", topic: { id: t.id, name: t.name }, current: t.id === currentId });
      }
    }
  }
  return rows;
}

/**
 * The row that takes the focus when the menu opens: the current topic, else
 * "New topic…". Neither moves the chat on Enter. "No topic" never takes it,
 * so a key pressed while the list loads cannot clear the topic.
 */
export function menuFocusIndex(rows: readonly TopicMenuRow[]): number {
  const current = rows.findIndex((r) => r.kind === "topic" && r.current);
  return current >= 0 ? current : rows.findIndex((r) => r.kind === "new");
}

/**
 * Whether the topic menu of the chat-first phone is open (review P2-1). The
 * menu opens FOR one chat, so a change of chat closes it, and a stale open
 * can never open it on another chat.
 */
export function topicMenuIsOpen(openFor: string | null, activeId: string | null | undefined): boolean {
  return openFor !== null && openFor === (activeId ?? null);
}

/** The words of a move that the server refused, or that never arrived (§10). */
export const MOVE_FAILED = "The chat did not move.";
export const MOVE_FAILED_NEXT = "Try again, or pick another topic.";

/** What a failed move needs from the toast of the app. */
export interface MoveFailureToast {
  show(spec: { key?: string; variant?: "error"; title: string; description?: string }): string;
}

/**
 * After a move failed (review P2-2): say so with the next step, and read the
 * topics again, because the menu drew a cached list that can hold a topic
 * that is gone. The list has already put the old topic back.
 */
export function topicMoveFailed(toast: MoveFailureToast, sessionId: string): void {
  invalidate(TOPICS_PATH);
  toast.show({ key: `chat-topic:${sessionId}`, variant: "error", title: MOVE_FAILED, description: MOVE_FAILED_NEXT });
}

/** The read of the topics. A refusal throws, so the read cache keeps its last answer. */
export async function fetchChatTopics(): Promise<ChatTopic[]> {
  const res = await fetch(TOPICS_PATH, { cache: "no-store" });
  if (!res.ok) throw new Error(`chat topics ${res.status}`);
  return parseTopics(await res.json());
}

/** The words of a create that failed for no reason the server gave (§10). */
export const CREATE_FAILED = "The topic was not made. Check the connection and try again.";

/** The words for a create that the server refused. */
export function createProblem(status: number, body: unknown): string {
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : null;
  if ((status === 409 || status === 422) && typeof detail === "string" && detail) return detail;
  return CREATE_FAILED;
}

/**
 * Make a private topic of the member (D163.2: a new topic starts private).
 * It gives the topic, or the words of the refusal for the member.
 */
export async function createChatTopic(name: string): Promise<{ topic: SessionTopic } | { problem: string }> {
  try {
    const res = await fetch(TOPICS_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const body: unknown = await res.json().catch(() => null);
    if (res.ok) {
      const t = body && typeof body === "object" ? (body as { topic?: unknown }).topic : null;
      const made = t && typeof t === "object" ? (t as Record<string, unknown>) : null;
      if (made && typeof made.id === "string" && typeof made.name === "string") {
        return { topic: { id: made.id, name: made.name } };
      }
    }
    return { problem: createProblem(res.status, body) };
  } catch {
    return { problem: createProblem(0, null) };
  }
}
