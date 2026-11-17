/**
 * HRD-17c: the fence of the hold. It reads the source of `AgentChat.tsx`.
 * No render harness mounts AgentChat, so this file pins the wiring by text,
 * and `lib/historyHold.test.ts` pins the logic.
 *
 * Mutations this suite catches (R7):
 * - a button or card sender calls `submitText` with no hold mark, so its
 *   words are lost while the history loads;
 * - `submitText` holds a call that has no mark (the typed box and the chat bar);
 * - the drain effect is gone, or it runs while the history still loads;
 * - the hold survives a thread switch (no clear on `sessionId`);
 * - the hold path touches `localStorage`, `sessionStorage` or the outbox;
 * - the textarea or the send button loses its `disabled` string.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf8").replace(/\r\n/g, "\n");
const lib = readFileSync(join(__dirname, "..", "lib", "historyHold.ts"), "utf8");

function between(src: string, from: string, to: string): string {
  const a = src.indexOf(from);
  const b = src.indexOf(to, a);
  expect(a, `missing: ${from}`).toBeGreaterThan(-1);
  expect(b, `missing: ${to}`).toBeGreaterThan(a);
  return src.slice(a, b);
}

describe("the programmatic senders go through the hold", () => {
  it("submitText holds only a call that carries the hold mark", () => {
    const body = between(chat, "const submitText = useCallback(", "[isRunActive, effectiveRuntime");
    expect(body).toContain("if (!opts?.hold) return false;");
    expect(body).toContain("holdRef.current?.hold(");
    // The old guard stays for an unmarked call.
    expect(body).not.toMatch(/if \(!trimmed \|\| loadingHistory\) return false;/);
  });

  it("each button and card sender passes the mark", () => {
    expect(chat).toContain("submitText(choice, HOLD_WORDS)");
    expect(chat).toContain("submitText(answer, hold)");
    expect(chat).toContain("submitText(plan.resend, HOLD_WORDS)");
    expect(chat).toMatch(/submitText\(`\$\{answer\}: \$\{card\.key\}`, \{\s*hold:/);
    expect(chat).toContain("submitText(message, HOLD_WORDS)");
    expect(chat).toContain("submitText(answer, heldBack(giveBack))");
    expect(chat).toMatch(/submitText\(formattedAnswers\(answers\), \{ hold:/);
  });

  it("no sender calls submitText with the words alone", () => {
    const bare = chat.match(/submitText\((?!text: string)[^,()]*(?:\([^()]*\))?[^,()]*\)/g) ?? [];
    expect(bare.filter((c) => !c.includes("autoSend.text"))).toEqual([]);
  });

  it("the typed box and the chat bar do not pass the hold mark", () => {
    const form = between(chat, "submitText(withFiles ?", "\n");
    expect(form).not.toContain("hold");
    const bar = between(chat, "const sent = submitText(autoSend.text", "\n");
    expect(bar).not.toContain("hold");
  });
});

describe("the drain and the clear", () => {
  it("the drain waits for the load, then goes through submitText", () => {
    const eff = between(chat, "HRD-17c: the held sends go out", "// D99: the chat bar's words");
    expect(eff).toContain("if (loadingHistory) return;");
    expect(eff).toContain("holdRef.current?.release()");
    expect(eff).toContain("submitText(h.text, h.opts)");
    expect(eff).toContain("[loadingHistory]");
  });

  it("the drain sits before the chat bar's effect", () => {
    expect(chat.indexOf("HRD-17c: the held sends go out")).toBeLessThan(chat.indexOf("const autoSentRef"));
  });

  it("a thread switch and an unmount clear the hold", () => {
    const eff = between(chat, "A send held for this thread never posts", "const submitText = useCallback(");
    expect(eff).toContain("return () => hold?.clear();");
    expect(eff).toContain("[sessionId]");
  });

  it("the card answer gets its card back at the deadline", () => {
    const fn = between(chat, "const handleGenUiHitl = useCallback(", "[postRespondInput, submitText]");
    expect(fn).toContain("next.delete(requestId)");
  });
});

describe("the hold is memory only", () => {
  it("the pure module touches no storage and no outbox", () => {
    for (const bad of ["localStorage", "sessionStorage", "chatOutbox"]) expect(lib).not.toContain(bad);
  });

  it("the hold path in AgentChat touches no storage and no outbox", () => {
    const path = between(chat, "HRD-17c: the sends that wait for the history", "const submitText = useCallback(");
    const drain = between(chat, "HRD-17c: the held sends go out", "// D99: the chat bar's words");
    for (const bad of ["localStorage", "sessionStorage", "chatOutbox"]) {
      expect(path).not.toContain(bad);
      expect(drain).not.toContain(bad);
    }
    expect(chat).not.toMatch(/from "@\/lib\/chatOutbox"/);
  });

  it("the deadline lives in one exported constant", () => {
    expect(lib).toContain("export const HISTORY_HOLD_DEADLINE_SECONDS = 20;");
  });
});

describe("the composer stays disabled while the history loads", () => {
  it("keeps the disabled strings", () => {
    expect(chat).toContain("disabled={loadingHistory}");
    expect(chat).toContain(
      'disabled={primary === "send" ? !canSendMessage(input, att.items) || loadingHistory : stopping}',
    );
  });
});
