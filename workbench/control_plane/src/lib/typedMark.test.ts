/**
 * The ONE typed mark (WS-63 D-PZ-37, the owner, 2026-11-02).
 *
 * Spec: `project-docs/specs/personalization.md` §15.3 and §15.11. Only a
 * message that the member typed in the message box carries `typed: true`.
 * The gateway binds the typed words only with it, and the summary job reads
 * only rows that carry it. This file fences the client half (R7):
 *
 * - the composer's send sets the mark, and so does the Ask field of the
 *   shell, which is one box with the composer;
 * - each other send path sends none: a "Follow up" pill, a choice in a
 *   bubble, a card action, a feed starter, the Fix prompt, a retry, a card
 *   answer, Continue and the integration help line;
 * - the request body carries the field only for the value `true`, in a send
 *   and in a steer, and the Next route passes it to the gateway;
 * - words that a program puts in the box take the mark away until the box
 *   is empty. The Fix prompt is such words;
 * - a `/chat?q=` link never carries the mark (round 5, F3);
 * - a held send keeps its mark through an app update.
 *
 * Vitest runs in node with no effects, so the wiring of `AgentChat.tsx` is
 * read from its source, as `AgentChat.picker.test.ts` does.
 *
 * Mutations this file catches, each red before the change: the composer
 * drops the mark; a pill, a choice or a card action passes it; the body
 * spreads a truthy value in place of `true`; the route drops the field;
 * `filled` keeps the mark for untyped words; an empty box keeps its taint;
 * the email Fix flow names its words typed; `flushHeld` drops the mark.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  holdForUpdate, registerSender, retryHeldNow, setRecoveryDeps, type SendOptions,
} from "@/lib/chatRecovery";
import { getSessionState } from "@/lib/chatStore";
import {
  __resetAssistantForTests, assistantState, openAssistant,
} from "@/lib/shell/assistant";
import { EMPTY_BOX, composerTyped, edited, filled, typedField } from "@/lib/typedMark";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

/** The code of `source` with its comments taken out: each block comment,
 *  and each line comment. A `//` behind a colon or a quote stays, so a URL
 *  in a string is still code. */
function codeOf(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'`\\])\/\/.*$/gm, "$1");
}

/** Each call of `fn(` in `source`, with its whole argument text. */
function callsOf(source: string, fn: string): string[] {
  const out: string[] = [];
  const re = new RegExp(`\\b${fn}\\(`, "g");
  for (let m = re.exec(source); m; m = re.exec(source)) {
    let depth = 1;
    let i = m.index + m[0].length;
    for (; i < source.length && depth > 0; i++) {
      if (source[i] === "(") depth++;
      else if (source[i] === ")") depth--;
    }
    out.push(source.slice(m.index + m[0].length, i - 1).replace(/\s+/g, " ").trim());
  }
  return out;
}

afterEach(() => {
  __resetAssistantForTests();
  setRecoveryDeps(null);
});

describe("the field of the mark", () => {
  it("is { typed: true } for true, and nothing for every other value", () => {
    expect(typedField(true)).toEqual({ typed: true });
    for (const odd of [false, undefined, null, "true", 1, {}]) expect(typedField(odd)).toEqual({});
  });
});

describe("the box", () => {
  it("an empty box sends typed words with the mark", () => {
    expect(composerTyped(EMPTY_BOX)).toBe(true);
  });

  it("words that the member did not type take the mark away until the box is empty", () => {
    const fix = filled(EMPTY_BOX, false);
    expect(composerTyped(fix)).toBe(false);
    // The member types more words beside them: still no mark.
    expect(composerTyped(edited(fix, "For the email X, create a rule. Also mine."))).toBe(false);
    // A typed give-back does not clean a tainted box.
    expect(composerTyped(filled(fix, true))).toBe(false);
    // The box is empty again: new words are typed.
    expect(composerTyped(edited(fix, ""))).toBe(true);
  });

  it("the member's own words that come back keep the mark", () => {
    expect(composerTyped(filled(EMPTY_BOX, true))).toBe(true);
  });
});

describe("each send path of the chat", () => {
  const chat = read("components/AgentChat.tsx");

  it("only the composer and the Ask field pass the mark to submitText", () => {
    const calls = callsOf(chat, "submitText");
    const marked = calls.filter((c) => c.includes("typed"));
    expect(marked).toEqual([
      "autoSend.text, askTyped ? { typed: true } : undefined",
      "input, typed ? { typed: true } : undefined",
    ]);
    // Every other call names its words only: no second argument.
    for (const call of calls.filter((c) => !c.includes("typed"))) {
      expect(call, call).not.toMatch(/,\s*\{/);
    }
    // The composer reads the mark of the box, never a fixed `true`.
    expect(chat).toContain("const typed = composerTyped(boxMarkRef.current);");
    expect(chat).toContain("const askTyped = autoSend.typed === true;");
  });

  it("a pill, a choice, a feed starter and a card action go through handleChoice, with no mark", () => {
    const choice = chat.match(/const handleChoice = useCallback\(\s*\(choice: string\) => \{([^}]*)\}/);
    expect(choice?.[1].trim()).toBe("submitText(choice);");
    // The feed starters, the starter pills under the day's brief (WS-62
    // CF-4b) and the "Follow up" pills.
    expect(chat.match(/onPick=\{handleChoice\}/g)).toHaveLength(3);
    // The choices and the card actions in a bubble (`MessageBubble.renderGenUi`).
    expect(chat).toMatch(/onChoice=\{handleChoice\}/);
    const bubble = read("components/MessageBubble.tsx");
    expect(bubble).toMatch(/else onChoice\?\.\(msg\);/);
  });

  it("no other path of the chat sends the mark to the hook", () => {
    const sends = callsOf(chat, "sendMessage");
    const steers = callsOf(chat, "steerMessage");
    // Only submitText passes the mark on, and only when it was asked to.
    expect(sends.filter((c) => c.includes("typed"))).toEqual(["trimmed, { typed: true }"]);
    expect(steers.filter((c) => c.includes("typed"))).toEqual(["trimmed, { typed: true }"]);
    // Continue, the integration help line, an edit and a card answer.
    for (const call of [...sends, ...callsOf(chat, "sendMessageRef.current")]) {
      if (!call.startsWith("trimmed, { typed")) expect(call, call).not.toContain("typed");
    }
  });

  it("the Fix prompt waits in the box with no mark", () => {
    // The email Fix flow hands its words through `pendingInput`, and names
    // them typed nowhere. AgentChat reads a missing flag as not typed.
    const email = read("app/email/components/EmailAssistantChat.tsx");
    expect(email).toContain("setPendingInput(pendingChatPrompt)");
    expect(email).not.toContain("pendingInputTyped");
    const page = read("app/email/page.tsx");
    expect(page).toContain("openAssistant({ draft: pendingChatPrompt });");
    expect(chat).toContain("boxMarkRef.current = filled(EMPTY_BOX, pendingInputTyped === true);");
  });

  it("the shell: the typed Ask carries the mark, a start pill and Get to know me do not", () => {
    const bar = read("lib/shell/ChatBar.tsx");
    expect(bar).toContain("openAssistant({ text: words, typed: true });");
    expect(bar).toContain("toChat(() => openAssistant({ text: s }));");
    expect(read("lib/shell/interviewMessage.ts")).not.toContain("typed");
    expect(read("lib/shell/AssistantDock.tsx"))
      .toContain("pendingInputTyped={!chat.recoveredInput && draftFor(st.draft, active.id)?.typed === true}");
  });

  it("a `/chat?q=` link never carries the mark", () => {
    // Anybody can write a link, a reply of Mira included (an in-app link in
    // her Markdown). So the words of `q` wait in the box with no mark, on
    // the full view and on the old chat page. The page has no reason to name
    // a mark at all, so its code (comments taken out) holds no "typed" in
    // any form: no `typed:` field, no shorthand `typed`, no computed value
    // and no `pendingInputTyped` prop. Mutations, each red: `typed: true`,
    // `typed: params.has("q")`, `pendingInputTyped={!recoveredInput &&
    // !!askInput}` and `pendingInputTyped={Boolean(askInput)}`.
    const page = codeOf(read("app/chat/page.tsx"));
    expect(page).toContain('draft: params.get("q") ?? undefined,');
    expect(page).not.toMatch(/typed/i);
    // Non-vacuity: the comments of the page name the mark, and they go.
    expect(read("app/chat/page.tsx")).toMatch(/typed mark/);
    // AgentChat reads a missing flag as not typed.
    expect(chat).toContain("boxMarkRef.current = filled(EMPTY_BOX, pendingInputTyped === true);");
    // The command bar's fallback builds the link with the words only.
    expect(read("lib/shell/CommandBar.tsx"))
      .toContain("router.push(`/chat?q=${encodeURIComponent(words)}`)");
  });

  it("the store keeps the mark of an ask only when it was typed", () => {
    openAssistant({ text: "what is due today", typed: true });
    expect(assistantState().ask?.typed).toBe(true);
    openAssistant({ text: "a start pill" });
    expect(assistantState().ask?.typed).toBeUndefined();
    openAssistant({ draft: "the Fix prompt" });
    expect(assistantState().draft?.typed).toBeUndefined();
  });
});

describe("the request", () => {
  it("a send and a steer spread the field, and the route passes it to the gateway", () => {
    const hook = read("hooks/useAgentChat.ts");
    expect(hook.match(/\.\.\.typedField\(opts\?\.typed\)/g)).toHaveLength(3);
    const route = read("app/api/agent/chat/route.ts");
    expect(route).toContain("steerFollowUp, cardAnswer, typed } = body;");
    expect(route).toContain("...typedField(typed),");
  });

  it("a held send keeps its mark, and an untyped one gets none", async () => {
    const tid = `typed-hold-${Math.random().toString(36).slice(2)}`;
    const sent: Array<[string, SendOptions | undefined]> = [];
    registerSender(tid, async (text, opts) => { sent.push([text, opts]); });
    setRecoveryDeps({ probe: async () => false, sleep: () => new Promise(() => {}) });
    holdForUpdate(tid, "remember I like oolong", { typed: true });
    holdForUpdate(tid, "a tapped pill");
    await retryHeldNow(tid);
    await vi.waitFor(() => expect(sent).toHaveLength(2));
    expect(sent).toEqual([["remember I like oolong", { typed: true }], ["a tapped pill", undefined]]);
    expect(getSessionState(tid).pendingTyped).toEqual([]);
  });
});
