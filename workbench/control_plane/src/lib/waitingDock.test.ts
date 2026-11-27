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
 * - the fold row's button approves, rejects or sends -> "audit rule 2";
 * - a press that began before the arm, or a held key, commits -> "the arm";
 * - a held Enter or an Enter just after a dock commit sends the box -> "the
 *   message box after a commit".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { personName } from "./cardTabs";

import {
  MIN_DOCK_PX,
  FRESH_KEY_PROPS,
  ARM_MS,
  boxEnterBlocked,
  pressIsFresh,
  previewLines,
  sendLabel,
  PINNED_ACTIONS,
  TWO_LINES_PX,
  peopleLine,
  arrivalBlursBox,
  dockCap,
  dockCount,
  arrivalUnfolds,
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

  it("Compact, a desktop: 40% of the chat column (D2)", () => {
    expect(dockCap({ ...base, phone: false })).toBe(320);
  });

  it("Compact, a phone with the keyboard down: 50% of the visual viewport, under the room of the thread lines (D2)", () => {
    expect(dockCap({ ...base, phone: true, chat: 1000, viewport: 600 })).toBe(300);
    // A short chat column: two thread lines stay in view (F1).
    expect(dockCap({ ...base, phone: true, chat: 450, viewport: 844 })).toBe(450 - 120 - TWO_LINES_PX);
  });

  it("a phone with the keyboard up and a field in focus: the viewport less the composer and two lines", () => {
    expect(dockCap({ ...base, phone: true, viewport: 400, keyboardUp: true, cardFieldFocus: true })).toBe(400 - 120 - TWO_LINES_PX);
  });

  it("Expand: the chat less the composer and two lines, never over the composer", () => {
    const cap = dockCap({ ...base, phone: false, expanded: true });
    expect(cap).toBe(800 - 120 - TWO_LINES_PX);
    expect(cap + 120).toBeLessThan(800);
  });

  it("never below the floor: the action row and a part of the body always show (rule 9)", () => {
    expect(dockCap({ ...base, phone: true, viewport: 100, keyboardUp: true, cardFieldFocus: true })).toBe(MIN_DOCK_PX);
  });
});

describe("rule 9: the actions never scroll (owner, 2026-11-27)", () => {
  it("globals.css holds the card's own action rows at the foot of the dock's body", () => {
    const css = readFileSync(join(__dirname, "..", "app/globals.css"), "utf-8").replace(/\s+/g, " ");
    const rule = css.slice(css.indexOf("[data-dock-body] :is("));
    for (const sel of PINNED_ACTIONS.split(", ")) expect(rule).toContain(sel);
    expect(rule).toMatch(/position: sticky; bottom: 0;/);
    // Theme tokens only, never a literal colour.
    expect(rule.slice(0, rule.indexOf("}"))).toContain("background: var(--card)");
  });

  it("the dock draws no copy of a button: the fade takes no click and reads the tokens", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toContain("pointer-events-none");
    expect(dock).toContain("from-card to-transparent");
    expect(dock).not.toMatch(/data-confirmation-approve|data-reply-send|data-elicitation-submit/);
  });

  it("the people line: the To names that go, each other row as a count, and who is left out", () => {
    const rows = { to: ["kim@contoso.test", "Ravi Kumar <ravi@contoso.test>", "lena@contoso.test"], cc: ["geo@acme.test", "ops@acme.test"] };
    const all = new Set(["kim@contoso.test", "ravi kumar <ravi@contoso.test>", "lena@contoso.test", "geo@acme.test", "ops@acme.test"]);
    expect(peopleLine(rows, all, personName)).toBe("To Kim, Ravi Kumar, Lena · Cc 2");
    const less = new Set(["kim@contoso.test", "ravi kumar <ravi@contoso.test>", "lena@contoso.test", "ops@acme.test"]);
    expect(peopleLine(rows, less, personName)).toBe("To Kim, Ravi Kumar, Lena · Cc 1 · 1 left out");
    expect(peopleLine({ to: ["kim@contoso.test"], cc: [] }, new Set(), personName)).toBe("To nobody · 1 left out");
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

  it("K3: a field of the card in focus stops the automatic fold, and never the member's own Fold", () => {
    expect(dockFolded({ manual: false, keyboardUp: true, cardFieldFocus: true })).toBe(false);
    expect(dockFolded({ manual: true, keyboardUp: true, cardFieldFocus: true })).toBe(true);
    expect(dockFolded({ manual: true, keyboardUp: false, cardFieldFocus: true })).toBe(true);
  });

  it("K2: a new item unfolds the dock, and a fall or no change does not", () => {
    expect(arrivalUnfolds(1, 2)).toBe(true);
    expect(arrivalUnfolds(0, 1)).toBe(true);
    expect(arrivalUnfolds(2, 1)).toBe(false);
    expect(arrivalUnfolds(2, 2)).toBe(false);
  });

  it("the dock wires K2 and the focus test to the rules here", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toContain("if (arrivalUnfolds(seenCount, count)) setManual(false);");
    expect(dock).toContain("if (pressingRef.current) return;");
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
  });

  it("2, then 1, then 2: the second rise speaks again, though its words repeat", () => {
    let words = liveAfter(0, 2, "");
    expect(words).toBe("2 items wait for you");
    words = liveAfter(2, 1, words);
    expect(words).toBe("");
    words = liveAfter(1, 2, words);
    expect(words).toBe("2 items wait for you");
    // No change keeps the words.
    expect(liveAfter(2, 2, words)).toBe(words);
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

describe("the fresh press (24.11, 2026-11-27)", () => {
  it("a held key presses no commit button: its default action does not run", () => {
    let prevented = 0;
    const ev = (repeat: boolean) => ({ repeat, preventDefault: () => { prevented++; } });
    FRESH_KEY_PROPS.onKeyDown(ev(false));
    expect(prevented).toBe(0);
    FRESH_KEY_PROPS.onKeyDown(ev(true));
    expect(prevented).toBe(1);
  });

  it("each commit button of the dock takes the guard, and the card arms again on each change of view", () => {
    const read = (f: string) => readFileSync(join(__dirname, "..", f), "utf-8");
    const card = read("components/ConfirmationCard.tsx");
    // Round 7: the arm is keyed on what the member sees, not on the view alone.
    expect(card).toContain("useArmed(dockMode ? `${dockMode}:${dockShown !== false}:${asking || formAsking}` : \"\", dockShown !== false)");
    expect(card).toContain("onClickCapture={dockMode ? arm.guard : undefined}");
    expect(card).toMatch(/data-confirmation-reject=""\s+\{\.\.\.fresh\}/);
    expect(card).toMatch(/data-confirmation-approve=""\s+\{\.\.\.fresh\}/);
    for (const f of ["components/NewMailCardForm.tsx", "components/ReplyCardForm.tsx"]) {
      const src = read(f);
      expect(src).toMatch(/data-reply-send=""\s+\{\.\.\.fresh\}/);
      expect(src).toMatch(/data-reply-save=""\s+\{\.\.\.fresh\}/);
      // Review is its own element: a key that no commit button holds.
      expect(src).toContain('<Button key="review"');
    }
    expect(read("components/EarlierDraftQuestion.tsx")).toContain('data-earlier-delete="" {...fresh}');
  });
});

describe("the previews", () => {
  it("the first three lines of a text, and whether more follow", () => {
    expect(previewLines("a\nb\nc\nd")).toEqual({ lines: ["a", "b", "c"], more: true });
    expect(previewLines("a\nb\n")).toEqual({ lines: ["a", "b"], more: false });
    expect(sendLabel(5)).toBe("Send to 5");
  });
});

describe("the arm (24.11 rule 7, round 7)", () => {
  const NONE = Number.NEGATIVE_INFINITY;
  const log = { pointerDownAt: NONE, pointerUpAt: NONE, keyDownAt: NONE, keyUpAt: NONE, repeatAt: NONE };
  const ARMED = 1000;

  it("a pointer press counts from its pointerdown", () => {
    expect(pressIsFresh({ ...log, pointerDownAt: 1100, pointerUpAt: 1150, armedAt: ARMED, now: 1151 })).toBe(true);
    // Down before the arm, up after it: no commit.
    expect(pressIsFresh({ ...log, pointerDownAt: 900, pointerUpAt: 1300, armedAt: ARMED, now: 1301 })).toBe(false);
  });

  it("a key press counts from its keydown, and a held key never presses", () => {
    expect(pressIsFresh({ ...log, keyDownAt: 1200, armedAt: ARMED, now: 1200 })).toBe(true);
    // Space: down before the arm, up after it.
    expect(pressIsFresh({ ...log, keyDownAt: 900, keyUpAt: 1300, armedAt: ARMED, now: 1301 })).toBe(false);
    expect(pressIsFresh({ ...log, keyDownAt: 1200, repeatAt: 1290, armedAt: ARMED, now: 1300 })).toBe(false);
  });

  it("a click with no press (a screen reader) needs the arm only", () => {
    expect(pressIsFresh({ ...log, pointerDownAt: 10, pointerUpAt: 20, armedAt: ARMED, now: 5000 })).toBe(true);
  });

  it("the card waits ARM_MS", () => {
    expect(ARM_MS).toBe(400);
  });
});

describe("the message box after a commit (round 7)", () => {
  it("with the dock, a held Enter or an Enter within ARM_MS of a commit sends nothing", () => {
    expect(boxEnterBlocked({ dock: true, repeat: true, msSinceCommit: 10_000 })).toBe(true);
    expect(boxEnterBlocked({ dock: true, repeat: false, msSinceCommit: 120 })).toBe(true);
    expect(boxEnterBlocked({ dock: true, repeat: false, msSinceCommit: ARM_MS + 1 })).toBe(false);
  });

  it("with the flag off, the box is the box of main", () => {
    expect(boxEnterBlocked({ dock: false, repeat: true, msSinceCommit: 0 })).toBe(false);
  });
});
