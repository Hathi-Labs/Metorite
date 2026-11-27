/**
 * The rules of the waiting dock (WD-1, `projects_ai_chat.md` §24.11).
 *
 * Mutations caught, by test:
 * - the dock draws with the flag off, or with nothing in it -> "draws only";
 * - a slot of a redraft counts as an item, or a card of two questions counts
 *   as two -> "the count";
 * - the cap ignores the phone, the keyboard or the two thread lines, or
 *   Expand covers the composer -> "the cap";
 * - the keyboard up does not fold, or a field of the card in focus folds ->
 *   "the fold" (K3, K7);
 * - an arrival moves the focus while the member types, on a desktop, or for a
 *   text item -> "K1 and K2";
 * - the box takes the focus back on a phone after the last item -> "K6";
 * - the live region speaks on a fall of the count -> "the live region";
 * - the thread line is a card or reads another count -> "the thread line";
 * - the fold row's button approves, rejects or sends -> "audit rule 2".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  TWO_LINES_PX,
  arrivalBlursBox,
  dockCap,
  dockCount,
  dockFolded,
  dockShown,
  focusBoxAfterLast,
  isTextField,
  keyboardUp,
  liveAfter,
  liveAnnouncement,
  liveWords,
  picksOnly,
  threadLine,
  unfoldLabel,
  userInputShape,
  type DockSources,
} from "./waitingDock";

const NONE: DockSources = { confirmations: [], elicitation: null, userInput: null };
const two = { confirmations: [{ key: "a" }, { key: "b" }], elicitation: null, userInput: null };

describe("the dock draws only while an item waits", () => {
  it("never with the flag off", () => {
    expect(dockShown(false, two)).toBe(false);
  });

  it("not with nothing in it", () => {
    expect(dockShown(true, NONE)).toBe(false);
  });

  it("with an approval, a question card, or a slot of a redraft", () => {
    expect(dockShown(true, two)).toBe(true);
    expect(dockShown(true, { ...NONE, elicitation: { questions: [{}] } })).toBe(true);
    expect(dockShown(true, { ...NONE, userInput: { choices: [], allowFreeform: true } })).toBe(true);
    expect(dockShown(true, { ...NONE, confirmations: [{ key: "s", redraft: "updating" }] })).toBe(true);
  });
});

describe("the count", () => {
  it("counts each card that waits on the member, and one tab for a card of many questions", () => {
    expect(dockCount(two)).toBe(2);
    expect(dockCount({ ...two, elicitation: { questions: [{}, {}, {}] } })).toBe(3);
    expect(dockCount({ ...two, userInput: { choices: ["Yes"], allowFreeform: false } })).toBe(3);
  });

  it("never counts a slot of a redraft", () => {
    expect(dockCount({ ...NONE, confirmations: [{ key: "s", redraft: "updating" }, { key: "a" }] })).toBe(1);
  });
});

describe("the cap (audit rule 5)", () => {
  const base = { chat: 800, viewport: 844, composer: 120, keyboardUp: false, cardFieldFocus: false, expanded: false };

  it("a desktop: 50% of the chat", () => {
    expect(dockCap({ ...base, phone: false })).toBe(400);
  });

  it("a phone with the keyboard down: 60% of the visual viewport, under the room of the thread lines", () => {
    expect(dockCap({ ...base, phone: true, chat: 1000, viewport: 600 })).toBe(360);
    // A short chat column: two thread lines stay in view (F1).
    expect(dockCap({ ...base, phone: true, chat: 500, viewport: 844 })).toBe(500 - 120 - TWO_LINES_PX);
  });

  it("a phone with the keyboard up and a field in focus: the viewport less the composer and two lines", () => {
    expect(dockCap({ ...base, phone: true, viewport: 400, keyboardUp: true, cardFieldFocus: true })).toBe(400 - 120 - TWO_LINES_PX);
  });

  it("Expand: the chat less the composer and two lines, never over the composer", () => {
    const cap = dockCap({ ...base, phone: false, expanded: true });
    expect(cap).toBe(800 - 120 - TWO_LINES_PX);
    expect(cap + 120).toBeLessThan(800);
  });

  it("never below zero", () => {
    expect(dockCap({ ...base, phone: true, viewport: 100, keyboardUp: true, cardFieldFocus: true })).toBe(0);
  });
});

describe("the fold (WD-D2, K3, K7)", () => {
  it("the member's own choice, with the keyboard down", () => {
    expect(dockFolded({ manual: false, keyboardUp: false, cardFieldFocus: false })).toBe(false);
    expect(dockFolded({ manual: true, keyboardUp: false, cardFieldFocus: false })).toBe(true);
  });

  it("K7: the keyboard up and no field of the card in focus folds the dock", () => {
    expect(dockFolded({ manual: false, keyboardUp: true, cardFieldFocus: false })).toBe(true);
  });

  it("K3: a field of the card in focus keeps it open, folded by hand or not", () => {
    expect(dockFolded({ manual: false, keyboardUp: true, cardFieldFocus: true })).toBe(false);
    expect(dockFolded({ manual: true, keyboardUp: true, cardFieldFocus: true })).toBe(false);
  });

  it("the keyboard is up only on a phone, and only past a bar's height", () => {
    expect(keyboardUp({ phone: false, viewport: 300, layout: 844 })).toBe(false);
    expect(keyboardUp({ phone: true, viewport: null, layout: 844 })).toBe(false);
    expect(keyboardUp({ phone: true, viewport: 780, layout: 844 })).toBe(false);
    expect(keyboardUp({ phone: true, viewport: 420, layout: 844 })).toBe(true);
  });

  it("a text field is a field that opens a keyboard", () => {
    expect(isTextField({ tagName: "TEXTAREA" })).toBe(true);
    expect(isTextField({ tagName: "INPUT", type: "email" })).toBe(true);
    expect(isTextField({ tagName: "INPUT", type: "checkbox" })).toBe(false);
    expect(isTextField({ tagName: "BUTTON" })).toBe(false);
    expect(isTextField(null)).toBe(false);
  });
});

describe("K1 and K2: an arrival never moves the focus while the member types", () => {
  const idle = { phone: true, boxFocused: true, boxText: "", msSinceKey: 10_000, picksOnly: true };

  it("blurs an idle box on a phone for a picks-only item, so the keyboard drops", () => {
    expect(arrivalBlursBox(idle)).toBe(true);
  });

  it("not while the box holds text, or a key went down in the last 3 s", () => {
    expect(arrivalBlursBox({ ...idle, boxText: "hello" })).toBe(false);
    expect(arrivalBlursBox({ ...idle, msSinceKey: 1200 })).toBe(false);
  });

  it("never on a desktop, never for a text item, never when the box has no focus", () => {
    expect(arrivalBlursBox({ ...idle, phone: false })).toBe(false);
    expect(arrivalBlursBox({ ...idle, picksOnly: false })).toBe(false);
    expect(arrivalBlursBox({ ...idle, boxFocused: false })).toBe(false);
  });

  it("picks only (audit rule 6): an approval of any kind, or a question card where every question has options", () => {
    expect(picksOnly({ kind: "approval" })).toBe(true);
    expect(picksOnly({ kind: "question", questions: [{ options: ["a"], allowFreeformInput: false }] })).toBe(true);
    // The optional "Other" field does not make a text item.
    expect(picksOnly({ kind: "question", questions: [{ options: ["a"], allowFreeformInput: true }] })).toBe(true);
    expect(picksOnly({ kind: "question", questions: [{ options: ["a"] }, { options: null }] })).toBe(false);
    expect(picksOnly({ kind: "question", questions: [{ options: [] }] })).toBe(false);
    expect(picksOnly({ kind: "question", questions: userInputShape({ choices: ["Yes", "No"], allowFreeform: true }) })).toBe(true);
    expect(picksOnly({ kind: "question", questions: userInputShape({ choices: [], allowFreeform: true }) })).toBe(false);
  });
});

describe("K6: after the last item", () => {
  it("the box takes the focus back on a desktop only", () => {
    expect(focusBoxAfterLast({ touch: false })).toBe(true);
    expect(focusBoxAfterLast({ touch: true })).toBe(false);
  });
});

describe("the live region (the a11y line)", () => {
  it("speaks when the count goes up, and only then", () => {
    expect(liveAnnouncement(0, 1)).toBe("1 item waits for you");
    expect(liveAnnouncement(1, 3)).toBe("3 items wait for you");
    expect(liveAnnouncement(3, 2)).toBeNull();
    expect(liveAnnouncement(2, 2)).toBeNull();
    expect(liveWords(2)).toBe("2 items wait for you");
  });

  it("1, then 0, then 1: the second arrival speaks again", () => {
    let words = "";
    words = liveAfter(0, 1, words);
    expect(words).toBe("1 item waits for you");
    words = liveAfter(1, 0, words);
    expect(words).toBe("");
    words = liveAfter(0, 1, words);
    expect(words).toBe("1 item waits for you");
    // A fall that leaves items keeps the words.
    expect(liveAfter(3, 2, "3 items wait for you")).toBe("3 items wait for you");
  });
});

describe("the thread line (WD-D1)", () => {
  it("one line with the count, never a card", () => {
    expect(threadLine(1)).toBe("Waiting for you: 1 item, below");
    expect(threadLine(4)).toBe("Waiting for you: 4 items, below");
  });

  it("AgentChat draws it as a plain line that only scrolls the thread", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    const part = chat.slice(chat.indexOf("const renderDockThreadPart"), chat.indexOf("const renderHitlCards"));
    expect(part).toContain("{threadLine(dockN)}");
    expect(part).toContain("onClick={scrollToBottom}");
    expect(part).not.toMatch(/<(RollupCard|ConfirmationQueue|ConfirmationCard)\b/);
  });
});

describe("audit rule 2: the fold row never approves, rejects or sends", () => {
  it("its one button only opens the card, with a word that is no consent", () => {
    expect(unfoldLabel("approval")).toBe("Review");
    expect(unfoldLabel("question")).toBe("Answer");
    for (const w of [unfoldLabel("approval"), unfoldLabel("question")]) {
      expect(w).not.toMatch(/approve|reject|send|submit|discard/i);
    }
  });

  it("the queue wires the button to `onUnfold` and to no answer", () => {
    const q = readFileSync(join(__dirname, "..", "components/ConfirmationQueue.tsx"), "utf-8");
    const fold = q.slice(q.indexOf("function DockQueue"), q.indexOf("<div hidden={folded}>"));
    expect(fold).toContain("onClick={onUnfold}");
    expect(fold).not.toMatch(/onAnswer|onApprove|onReject|onReply|onSubmit/);
  });
});
