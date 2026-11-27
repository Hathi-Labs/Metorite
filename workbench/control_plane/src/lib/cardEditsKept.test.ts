// Task 1893: the member's edits of a waiting new-mail card survive a chat switch and a new mount.
//
// Found in the review of §18.11 fix round 5 (task 1650). The edits lived in a ref of one `AgentChat`.
// A chat switch resets the confirmation queue, and a new mount starts an empty ref, while the card log
// in `chatStore` redraws the waiting card from the server's form. So an untick came back ticked, and
// one click sent to that person. The edits of each thread now live in `chatStore`, beside the card log.
//
// R7 fences named here:
//   * `edits-per-thread`: `keepFormEdits` and `getFormEdits` keep the edits of each thread apart, an
//     empty map keeps nothing, and `clearCardLog` (a new run, the end of one) clears them with the log.
//   * `closes-only-what-left`: a card that left the queue drops its edits, a card that was never in the
//     queue before (the redraw of a switch) does not.
//   * `chat-wires-the-store`: `AgentChat` starts its ref from the store, reads the store of the new
//     thread on a switch, before the forms mount, writes each edit through, and prunes by transition.
//   * `a-switch-is-not-a-close`: the switch branch empties `heldRef`, so the reset of a switch drops
//     nothing.
//
// Mutation 1: read `NO_EDITS` on a switch. `chat-wires-the-store` turns red.
// Mutation 2: skip `keepFormEdits` in `onFormEdits`. `chat-wires-the-store` turns red.
// Mutation 3: let `clearCardLog` keep the edits. `edits-per-thread` turns red.
// Mutation 4: drop `closedEdits` for a card that was not in `before`. `closes-only-what-left` turns red.
// Mutation 5: do not empty `heldRef` on a switch. `a-switch-is-not-a-close` turns red.

import { beforeEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { clearCardLog, getFormEdits, keepFormEdits, noteCardEvent } from "@/lib/chatStore";
import { NO_EDITS, asNewMailEdits, closedEdits, formEditsReducer, type FormEdit, type FormEdits } from "@/lib/confirmationQueue";
import type { NewMailEdits } from "@/lib/replyCard";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel: string) => readFileSync(path.join(here, rel), "utf8").replace(/\r\n/g, "\n");

const A = "thread-a";
const B = "thread-b";
const CARD = "c".repeat(32);
const OTHER = "d".repeat(32);
/** The subject of kept mail edits: the map holds a task card's edits too (EAC-2), so a reader asks the guard. */
const subjectOf = (e: FormEdit | undefined) => asNewMailEdits(e)?.subject;
const edits = (subject: string): NewMailEdits => ({ subject, body: "text", ticks: { rows: [] } as never, fileTicks: [] });

function kept(...pairs: [string, NewMailEdits][]): FormEdits {
  return pairs.reduce((m, [key, value]) => formEditsReducer(m, { key, value }), NO_EDITS);
}

beforeEach(() => {
  clearCardLog(A);
  clearCardLog(B);
});

describe("the edits of each thread (CS, task 1893)", () => {
  it("edits-per-thread: a thread keeps its own edits, and another thread has none", () => {
    expect(getFormEdits(A)).toBeUndefined();
    const a = kept([CARD, edits("Rates")]);
    keepFormEdits(A, a);
    expect(getFormEdits(A)).toBe(a);
    expect(getFormEdits(B)).toBeUndefined();
    const b = kept([OTHER, edits("Other")]);
    keepFormEdits(B, b);
    expect(subjectOf(getFormEdits(A)?.get(CARD))).toBe("Rates");
    expect(subjectOf(getFormEdits(B)?.get(OTHER))).toBe("Other");
    expect(getFormEdits(A)?.has(OTHER)).toBe(false);
  });

  it("edits-per-thread: an empty map keeps nothing", () => {
    keepFormEdits(A, kept([CARD, edits("Rates")]));
    keepFormEdits(A, NO_EDITS);
    expect(getFormEdits(A)).toBeUndefined();
  });

  it("edits-per-thread: clearCardLog clears the edits of that thread with its card log, and no other", () => {
    noteCardEvent(A, "confirmation_requested", { request_id: CARD });
    keepFormEdits(A, kept([CARD, edits("Rates")]));
    keepFormEdits(B, kept([OTHER, edits("Other")]));
    clearCardLog(A);
    expect(getFormEdits(A)).toBeUndefined();
    expect(subjectOf(getFormEdits(B)?.get(OTHER))).toBe("Other");
  });
});

describe("closedEdits (task 1893)", () => {
  const map = kept([CARD, edits("Rates")], [OTHER, edits("Other")]);

  it("closes-only-what-left: a card that left the queue drops its edits, the others stay", () => {
    const out = closedEdits(map, new Set([CARD, OTHER]), new Set([OTHER]));
    expect(out.has(CARD)).toBe(false);
    expect(subjectOf(out.get(OTHER))).toBe("Other");
  });

  it("closes-only-what-left: a card that was never in the queue before does not count as left", () => {
    // The reset of a chat switch empties the queue. The state before it was the OTHER thread's.
    expect(closedEdits(map, new Set(), new Set())).toBe(map);
    expect(closedEdits(map, new Set(["x".repeat(32)]), new Set())).toBe(map);
  });

  it("closes-only-what-left: the same map comes back when no card left", () => {
    expect(closedEdits(map, new Set([CARD]), new Set([CARD, OTHER]))).toBe(map);
  });
});

describe("the wiring in AgentChat (task 1893)", () => {
  const chat = read("../components/AgentChat.tsx");
  const start = chat.indexOf("const formEditsRef = useRef<FormEdits>(");
  const block = chat.slice(start, chat.indexOf("const onCardDraft = useCallback"));

  it("chat-wires-the-store: the ref starts from the store, and a switch reads the store of the new thread", () => {
    expect(start).toBeGreaterThan(-1);
    expect(block).toContain("const formEditsRef = useRef<FormEdits>(getFormEdits(sessionId) ?? NO_EDITS);");
    expect(block).toMatch(/if \(editsThreadRef\.current !== sessionId\) \{[\s\S]*?formEditsRef\.current = getFormEdits\(sessionId\) \?\? NO_EDITS;[\s\S]*?\}/);
    // During render, not in an effect: a form reads its edits once, when it mounts, in the same commit.
    expect(block.indexOf("editsThreadRef.current !== sessionId")).toBeLessThan(block.indexOf("useEffect("));
  });

  it("chat-wires-the-store: each edit is written through to the store, for the thread it belongs to", () => {
    expect(block).toMatch(/const onFormEdits = useCallback\(\(key: string, value: FormEdit\) => \{[\s\S]*?keepFormEdits\(sessionId, formEditsRef\.current\);[\s\S]*?\}, \[sessionId\]\);/);
  });

  it("chat-wires-the-store: the prune runs by transition, and writes its result through", () => {
    expect(block).toContain("const next = closedEdits(formEditsRef.current, heldRef.current, now);");
    expect(block).toContain("heldRef.current = now;");
    expect(block).toMatch(/keepFormEdits\(sessionId, next\);[\s\S]*?\}, \[confirmations, sessionId\]\);/);
    expect(chat).not.toContain("liveEdits");
  });

  it("a-switch-is-not-a-close: the switch branch empties heldRef, so the reset of a switch drops nothing", () => {
    const sw = block.slice(block.indexOf("if (editsThreadRef.current !== sessionId) {"));
    const body = sw.slice(0, sw.indexOf("\n  }\n"));
    expect(body).toContain("editsThreadRef.current = sessionId;");
    expect(body).toContain("heldRef.current = new Set();");
  });
});
