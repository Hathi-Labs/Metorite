import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { boxHadFocus, refocusAfterPress, refocusQuietly } from "./composerFocus";

// Task 2867 (owner, 2026-11-11): Send must not open a closed phone keyboard.

describe("refocusAfterPress", () => {
  it("a phone with the keyboard closed at the press: no focus, so no keyboard", () => {
    expect(refocusAfterPress({ touch: true, hadFocus: false })).toBe(false);
  });
  it("a phone with the keyboard open at the press: the box keeps it", () => {
    expect(refocusAfterPress({ touch: true, hadFocus: true })).toBe(true);
  });
  it("a desktop always takes the focus back, as before", () => {
    expect(refocusAfterPress({ touch: false, hadFocus: false })).toBe(true);
    expect(refocusAfterPress({ touch: false, hadFocus: true })).toBe(true);
  });
});

describe("refocusQuietly", () => {
  it("never opens a phone keyboard, and a desktop focuses", () => {
    expect(refocusQuietly({ touch: true })).toBe(false);
    expect(refocusQuietly({ touch: false })).toBe(true);
  });
});

describe("boxHadFocus", () => {
  const box = {};
  it("is true only when the box is the active element", () => {
    expect(boxHadFocus(box, box)).toBe(true);
    expect(boxHadFocus(box, {})).toBe(false);
    expect(boxHadFocus(null, null)).toBe(false);
    expect(boxHadFocus(undefined, undefined)).toBe(false);
  });
});

describe("AgentChat refocus sites (fence)", () => {
  const src = readFileSync(join(__dirname, "..", "components", "AgentChat.tsx"), "utf-8");
  const chatSend = readFileSync(join(__dirname, "..", "components", "ChatSendButton.tsx"), "utf-8");

  it("keeps only the two bare focus calls where the member asked to edit", () => {
    // suggestToComposer (a chip of a new-mail card) and the pendingInput
    // effect put words in the box FOR the member to edit. Every other site
    // goes through focusAfterPress or focusQuietly.
    const bare = src.match(/inputRef\.current\?\.focus\(\)/g) ?? [];
    expect(bare.length).toBe(2);
  });

  it("Send refocuses through the press rule, and the composer notes the press", () => {
    const submit = src.slice(src.indexOf("const handleSubmit"), src.indexOf("const handleKeyDown"));
    expect(submit).toContain("focusAfterPress()");
    expect(submit).not.toMatch(/\.focus\(\)/);
    expect(src).toContain("onPointerDownCapture={() => { pressFocusRef.current = boxHadFocus(inputRef.current); }}");
  });

  it("a mouse-down on Send keeps the focus where it was", () => {
    expect(chatSend).toMatch(/onMouseDown=\{\(e[^)]*\) => e\.preventDefault\(\)\}/);
  });
});
