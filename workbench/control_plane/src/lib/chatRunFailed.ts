/**
 * A failed turn survives a reload (AO-3b, task 3692).
 *
 * AO-3 draws the "AI is taking a breather" card live, and saves no reply, so
 * no empty bubble draws. A reload then showed the member's message with no
 * reply and no card. Now the chat route saves a typed marker on the reply
 * row, in `custom_events`, as `run_interrupted` does. No migration.
 *
 * Three rules, each one pure so a test can pin it:
 *
 * 1. **Save.** `failedMarker` builds the marker. The route adds it when the
 *    run ends in the `ai_unavailable` failure.
 * 2. **Draw.** `foldFailedTurns` turns a marker row into the same
 *    `__ERROR__` card the live path draws, when it is the LAST turn of the
 *    thread. Any later turn means the member moved on, so the marker goes.
 *    A retry that gets an answer ends the failure the same way.
 * 3. **Never to the model.** `isFailedTurn` names the row, so the history
 *    builder skips it. It holds no words, and an empty assistant turn
 *    confuses the model.
 *
 * Fence: `chatRunFailed.test.ts`.
 */
import type { ChatMessage } from "@/lib/chatStore";
import { runErrorView } from "@/lib/runErrors";

/** The marker a failed turn carries on its saved reply row. */
export const RUN_FAILED_EVENT = "run_failed";

/** The failure codes whose card survives a reload. */
export const SAVED_FAILURE_CODES: ReadonlySet<string> = new Set(["ai_unavailable"]);

/** The custom event to save for a failed run. */
export function failedMarker(
  code: string,
  ref: string | null | undefined,
  segmentCutoff = 0,
): { name: string; value: { code: string; ref: string | null }; segmentCutoff: number } {
  return { name: RUN_FAILED_EVENT, value: { code, ref: ref ?? null }, segmentCutoff };
}

type Row = Pick<ChatMessage, "role" | "customEvents">;

/** The marker's code and ref, or null when the row holds none. */
export function failedMarkerOf(m: Row): { code: string; ref: string | null } | null {
  const e = (m.customEvents ?? []).find((x) => x.name === RUN_FAILED_EVENT);
  if (!e) return null;
  const v = (e.value ?? {}) as { code?: unknown; ref?: unknown };
  if (typeof v.code !== "string" || !SAVED_FAILURE_CODES.has(v.code)) return null;
  return { code: v.code, ref: typeof v.ref === "string" ? v.ref : null };
}

/** True for a saved failure row: an assistant turn that holds only the marker. */
export function isFailedTurn(m: Row): boolean {
  return m.role === "assistant" && failedMarkerOf(m) !== null;
}

/** A stale `__ERROR__` system row of an older build. A saved failure card is not one. */
export function isStaleErrorRow(m: Pick<ChatMessage, "role" | "content" | "customEvents">): boolean {
  return m.role === "system" && (m.content ?? "").startsWith("__ERROR__") && failedMarkerOf({ role: "assistant", customEvents: m.customEvents }) === null;
}

/**
 * The thread with each failure row drawn as its card, or dropped.
 *
 * The card keeps the row's id, so a poll that merges the same row by id finds
 * the live card and adds no second one. Only the last turn keeps its card.
 * `tail` is false for a window of older rows: no marker there is the last.
 */
export function foldFailedTurns<
  T extends Pick<ChatMessage, "id" | "role" | "content" | "timestamp" | "customEvents">,
>(messages: readonly T[], tail = true): T[] {
  let last = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "system" && messages[i].content.startsWith("__ERROR__")) continue;
    last = i;
    break;
  }
  const out: T[] = [];
  messages.forEach((m, i) => {
    if (!isFailedTurn(m)) {
      out.push(m);
      return;
    }
    if (!tail || i !== last) return;
    const marker = failedMarkerOf(m)!;
    const view = runErrorView({ raw: "", code: marker.code, ref: marker.ref, status: null });
    // The card keeps its marker, so a reload filter that drops stale
    // `__ERROR__` rows knows this one is saved (`isStaleErrorRow`).
    out.push({ ...m, role: "system", content: `__ERROR__${JSON.stringify(view)}` });
  });
  return out;
}
