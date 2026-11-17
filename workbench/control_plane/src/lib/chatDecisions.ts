/**
 * The decisions that a chat keeps for the member, and Undo (HRD-12b,
 * `chat_failure_modes.md` §6). The server is `gateway/routes/admin/
 * chat_decisions.py`, behind the flag `CHAT_CONSTRAINTS`: with it off the read
 * says `enabled: false` and the chip draws nothing.
 *
 * Both calls go through the relay of `/api/auth/me/chat-decisions`. The
 * member and the tenant are the session's, so the body names a chat and an item
 * and nothing else. Fence: `chatDecisions.test.ts`.
 */

/** A chat id: the same rule as the gateway. It becomes a part of a key. */
export const SESSION_ID = /^[A-Za-z0-9_.:-]{1,128}$/;
/** An item id: 12 hex digits, from the server. */
export const ITEM_ID = /^[0-9a-f]{12}$/;

export interface Decision {
  id: string;
  kind: string;
  text: string;
}

export interface DecisionList {
  enabled: boolean;
  items: Decision[];
}

const BASE = "/api/auth/me/chat-decisions";

/** A read of the list, kept strict: a shape that is not a list reads as none. */
export function readDecisions(raw: unknown): DecisionList {
  const o = (raw ?? {}) as Record<string, unknown>;
  if (o.enabled !== true || !Array.isArray(o.items)) return { enabled: false, items: [] };
  const items: Decision[] = [];
  for (const it of o.items) {
    const d = (it ?? {}) as Record<string, unknown>;
    if (typeof d.id === "string" && ITEM_ID.test(d.id) && typeof d.text === "string" && d.text.trim()) {
      items.push({ id: d.id, kind: typeof d.kind === "string" ? d.kind : "decision", text: d.text });
    }
  }
  return { enabled: true, items };
}

/** The decisions of a chat, or null when they cannot be read (no chip then). */
export async function fetchDecisions(sessionId: string, fetchImpl: typeof fetch = fetch): Promise<DecisionList | null> {
  if (!SESSION_ID.test(sessionId)) return null;
  try {
    const res = await fetchImpl(`${BASE}?session_id=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!res.ok) return null;
    return readDecisions(await res.json());
  } catch {
    return null;
  }
}

/** The answer of an Undo: it worked, the decision was gone already, or it failed. */
export type UndoResult = "done" | "gone" | "failed";

/** Take one decision back. A 404 means it is gone already. */
export async function undoDecision(sessionId: string, id: string, fetchImpl: typeof fetch = fetch): Promise<UndoResult> {
  if (!SESSION_ID.test(sessionId) || !ITEM_ID.test(id)) return "failed";
  try {
    const res = await fetchImpl(`${BASE}/undo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, id }),
      cache: "no-store",
    });
    if (res.ok) return "done";
    return res.status === 404 ? "gone" : "failed";
  } catch {
    return "failed";
  }
}

/** The line above the chips, and the words of each control. */
export const CHIP_WORDS = {
  lead: "Kept for this chat",
  undo: "Undo",
  undoLabel: (text: string) => `Undo: ${text}`,
  failed: "Could not undo. Try again.",
} as const;
