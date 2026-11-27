/**
 * The rules of the waiting dock (WD-1, `projects_ai_chat.md` §24.11, D9).
 *
 * D9 (2026-11-27): the dock holds the two question cards and ONE pointer row.
 * An approval card draws in the thread, as with the flag off.
 *
 * Mutations caught, by test:
 * - the dock draws with the flag off, with nothing in it, or for a slot of a
 *   redraft alone -> "draws only";
 * - a slot of a redraft counts as an item, a card of two questions counts as
 *   two, or the approvals leave the count -> "the count";
 * - the pointer names an old card, drops "and N more", or a tall card shows
 *   its foot after Review -> "the pointer";
 * - the cap ignores the phone, the keyboard or the two thread lines -> "the
 *   cap";
 * - the keyboard up does not fold, or a field of the card in focus folds ->
 *   "the fold" (K3, K7);
 * - an arrival moves the focus while the member types, on a desktop, or for a
 *   text item -> "K1 and K2";
 * - an approval moves the focus or the fold -> "K1 and K2";
 * - the box takes the focus back on a phone after the last item -> "K6";
 * - the live region speaks on a fall of the count -> "the live region";
 * - the thread line is a card or reads another count -> "the thread line";
 * - the fold row's button submits -> "audit rule 2";
 * - a press that began before the arm, or a held key, commits -> "the arm";
 * - a held Enter or an Enter just after a dock commit sends the box -> "the
 *   message box after a commit";
 * - the pointer names a card other than the queue's open tab -> "the pointer";
 * - the dock collapses before 800 ms, while a pointer is down, or within
 *   600 ms of a press in it, or the inert line has other words -> "the hold".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  MIN_DOCK_PX,
  FRESH_KEY_PROPS,
  ARM_MS,
  POINTER_LEAD,
  UNFOLD_LABEL,
  boxEnterBlocked,
  collapseWait,
  COLLAPSE_MS,
  HOLD_MS,
  LEFT_WORDS,
  leftKind,
  openApproval,
  QUIET_MS,
  pressIsFresh,
  PINNED_ACTIONS,
  TWO_LINES_PX,
  arrivalBlursBox,
  dockCap,
  dockCount,
  dockQuestionCount,
  arrivalUnfolds,
  dockFolded,
  dockShown,
  focusBoxAfterLast,
  isTextField,
  keyboardUp,
  liveAfter,
  liveAnnouncement,
  liveWords,
  newestApproval,
  picksOnly,
  pointerLine,
  pointerScrollTop,
  threadLine,
  userInputShape,
  waitingApprovals,
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

  it("with an approval (the pointer) or a question card", () => {
    expect(dockShown(true, two)).toBe(true);
    expect(dockShown(true, { ...NONE, elicitation: { questions: [{}] } })).toBe(true);
    expect(dockShown(true, { ...NONE, userInput: { choices: [], allowFreeform: true } })).toBe(true);
  });

  it("D9: a slot of a redraft alone waits on the assistant, so it draws no pointer", () => {
    expect(dockShown(true, { ...NONE, confirmations: [{ key: "s", redraft: "updating" }] })).toBe(false);
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

  it("D9: the count holds the approvals in the thread, and only the questions move the dock", () => {
    const src = { ...two, elicitation: { questions: [{}] } };
    expect(waitingApprovals(src)).toBe(2);
    expect(dockQuestionCount(src)).toBe(1);
    expect(dockQuestionCount(two)).toBe(0);
    expect(dockCount(src)).toBe(3);
  });
});

describe("the pointer (D9)", () => {
  it("names the newest approval that waits, and how many more", () => {
    expect(pointerLine("Task: Fix the extruder", 0)).toBe("Waits for you: Task: Fix the extruder");
    expect(pointerLine("Email to Kim", 2)).toBe("Waits for you: Email to Kim and 2 more");
    expect(POINTER_LEAD).toBe("Waits for you:");
    const cards = [{ key: "a" }, { key: "b" }, { key: "s", redraft: "updating" as const }];
    expect(newestApproval(cards)).toEqual({ card: { key: "b" }, more: 1 });
    expect(newestApproval([{ key: "s", redraft: "updating" as const }])).toBeNull();
    expect(newestApproval([])).toBeNull();
  });

  it("names the queue's OPEN tab, and counts the others (2026-11-28)", () => {
    const cards = [{ key: "a" }, { key: "b" }, { key: "c" }, { key: "s", redraft: "updating" as const }];
    expect(openApproval(cards, "a")).toEqual({ card: { key: "a" }, more: 2 });
    expect(openApproval(cards, "b")).toEqual({ card: { key: "b" }, more: 2 });
    // No report yet, a key that left, or a slot open: the newest that waits.
    expect(openApproval(cards, null)).toEqual({ card: { key: "c" }, more: 2 });
    expect(openApproval(cards, "gone")).toEqual({ card: { key: "c" }, more: 2 });
    expect(openApproval(cards, "s")).toEqual({ card: { key: "c" }, more: 2 });
    expect(openApproval([{ key: "s", redraft: "updating" as const }], "s")).toBeNull();
  });

  it("the chat reads the open tab from the queue, only while the dock is on", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    expect(chat).toContain("const reportShown = dockOn ? setQueueShown : undefined;");
    expect(chat).toContain("onShown={reportShown}");
    expect(chat).toContain("openApproval(confirmations.cards, queueShown)");
    const queue = readFileSync(join(__dirname, "..", "components/ConfirmationQueue.tsx"), "utf-8");
    expect(queue).toMatch(/useEffect\(\(\) => \{\s*onShown\?\.\(shownNow\);\s*\}, \[onShown, shownNow\]\);/);
  });

  it("Review shows a card that fits whole, with the least movement", () => {
    const port = { top: 100, bottom: 600 };
    // Below the room: its foot comes up to the foot of the room.
    expect(pointerScrollTop(1000, port, { top: 500, bottom: 700 })).toBe(1100);
    // Above the room: its top comes down to the top of the room.
    expect(pointerScrollTop(1000, port, { top: 50, bottom: 250 })).toBe(950);
    // In view: no move.
    expect(pointerScrollTop(1000, port, { top: 200, bottom: 400 })).toBe(1000);
  });

  it("Review shows the TOP of a card taller than the room, from every place", () => {
    const port = { top: 100, bottom: 600 };
    for (const target of [{ top: 300, bottom: 1300 }, { top: -400, bottom: 900 }, { top: -900, bottom: 200 }]) {
      const to = pointerScrollTop(1000, port, target);
      expect(to).toBe(1000 + (target.top - port.top));
    }
  });

  it("the chat moves the focus to the card's heading, never to a button, and the scroll is the thread's own", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    const part = chat.slice(chat.indexOf("const reviewApproval = () =>"), chat.indexOf("const renderHitlCards"));
    expect(part).toContain("pointerScrollTop(thread.scrollTop");
    expect(part).toContain('card.querySelector<HTMLElement>("[data-rollup-title]")');
    expect(part).toContain('head.setAttribute("tabindex", "-1")');
    expect(part).toContain("head.focus({ preventScroll: true })");
    // It only scrolls: no press, no tab change, no answer.
    expect(part).not.toMatch(/scrollIntoView|onAnswer|answerConfirmation|\.click\(|role="tab"/);
  });
});

describe("the cap (audit rule 5)", () => {
  const base = { chat: 800, viewport: 844, composer: 120, keyboardUp: false, cardFieldFocus: false };

  it("a desktop: 40% of the chat column (rule 3)", () => {
    expect(dockCap({ ...base, phone: false })).toBe(320);
  });

  it("a phone with the keyboard down: 50% of the visual viewport, under the room of the thread lines (rule 3)", () => {
    expect(dockCap({ ...base, phone: true, chat: 1000, viewport: 600 })).toBe(300);
    // A short chat column: two thread lines stay in view (F1).
    expect(dockCap({ ...base, phone: true, chat: 450, viewport: 844 })).toBe(450 - 120 - TWO_LINES_PX);
  });

  it("a phone with the keyboard up and a field in focus: the viewport less the composer and two lines", () => {
    expect(dockCap({ ...base, phone: true, viewport: 400, keyboardUp: true, cardFieldFocus: true })).toBe(400 - 120 - TWO_LINES_PX);
  });

  it("never below the floor: the Submit row and a part of the body always show (rule 10)", () => {
    expect(dockCap({ ...base, phone: true, viewport: 100, keyboardUp: true, cardFieldFocus: true })).toBe(MIN_DOCK_PX);
  });
});

describe("rule 10: the Submit row never scrolls (owner, 2026-11-27)", () => {
  it("globals.css holds the question's own Submit row at the foot of the dock's body", () => {
    const css = readFileSync(join(__dirname, "..", "app/globals.css"), "utf-8").replace(/\s+/g, " ");
    const rule = css.slice(css.indexOf("[data-dock-body] div:has("));
    for (const sel of PINNED_ACTIONS.split(", ")) expect(rule).toContain(sel);
    expect(rule).toMatch(/position: sticky; bottom: 0;/);
    // Theme tokens only, never a literal colour.
    expect(rule.slice(0, rule.indexOf("}"))).toContain("background: var(--card)");
  });

  it("D9: no selector of an approval is left in the dock's rules", () => {
    expect(PINNED_ACTIONS).toBe("div:has(> [data-elicitation-submit])");
    const css = readFileSync(join(__dirname, "..", "app/globals.css"), "utf-8");
    const dock = css.slice(css.indexOf("WD-1 (projects_ai_chat.md 24.11"));
    expect(dock).not.toMatch(/data-confirmation-approve|data-reply-send|data-dock-review|data-earlier-question/);
  });

  it("the dock draws no copy of a button: the fade takes no click and reads the tokens", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toContain("pointer-events-none");
    expect(dock).toContain("from-card to-transparent");
    expect(dock).not.toMatch(/data-confirmation-approve|data-reply-send|data-elicitation-submit/);
  });

  it("D9 requirement 4: a change of tab sets the dock's scroll back to the top", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toMatch(/useLayoutEffect\(\(\) => \{\s*if \(bodyRef\.current\) bodyRef\.current\.scrollTop = 0;\s*\}, \[openKey\]\);/);
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

  it("picks only (audit rule 6): a question card where every question has options", () => {
    expect(picksOnly([{ options: ["a"], allowFreeformInput: false }])).toBe(true);
    // The optional "Other" field does not make a text item.
    expect(picksOnly([{ options: ["a"], allowFreeformInput: true }])).toBe(true);
    expect(picksOnly([{ options: ["a"] }, { options: null }])).toBe(false);
    expect(picksOnly([{ options: [] }])).toBe(false);
    expect(picksOnly(userInputShape({ choices: ["Yes", "No"], allowFreeform: true }))).toBe(true);
    expect(picksOnly(userInputShape({ choices: [], allowFreeform: true }))).toBe(false);
  });

  it("D9: only the count of the dock's questions moves the focus, never an approval in the thread", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    expect(chat).toContain("const dockQ = dockQuestionCount(dockSources);");
    expect(chat).toContain("if (dockQ > prev && box) {");
    expect(chat).toContain("} else if (dockQ === 0 && prev > 0) {");
    expect(chat).toContain("}, [dockOn, dockQ, dockPicksOnly, phoneChrome, focusQuietly]);");
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

  it("AgentChat draws it as a plain line that only scrolls the thread, while a question is in the dock", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    const part = chat.slice(chat.indexOf("const renderHitlCards"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(part).toContain("const lineHere = dockOn && dockQ > 0;");
    const line = part.slice(part.indexOf("{lineHere && ("), part.indexOf("</Button>", part.indexOf("{lineHere && (")));
    expect(line).toContain("{threadLine(dockN)}");
    expect(line).toContain("onClick={scrollToBottom}");
    expect(line).not.toMatch(/<(RollupCard|ConfirmationQueue|ConfirmationCard)\b/);
  });
});

describe("audit rule 2: the fold row never submits", () => {
  it("its one button only opens the card, with a word that is no consent", () => {
    expect(UNFOLD_LABEL).toBe("Answer");
    expect(UNFOLD_LABEL).not.toMatch(/approve|reject|send|submit|discard/i);
  });

  it("the dock wires the button to the unfold and to no answer", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    const row = dock.slice(dock.indexOf("data-dock-fold-row"), dock.indexOf("<div hidden={folded}>"));
    expect(row).toContain("onClick={unfold}");
    expect(row).not.toMatch(/onAnswer|onApprove|onReject|onReply|onSubmit/);
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

  it("Submit and Not now of a question in the dock take the guard, and the card arms again on each change of view", () => {
    const card = readFileSync(join(__dirname, "..", "components/ElicitationCard.tsx"), "utf-8");
    expect(card).toContain("useArmed(inDock ? `q:${dockShown !== false}` : \"\", inDock && dockShown !== false)");
    expect(card).toContain("onClickCapture={inDock ? arm.guard : undefined}");
    expect(card).toContain("{...(inDock ? FRESH_KEY_PROPS : {})}");
    // The dock hands `dockShown` to each card: open tab, not folded, and not
    // under the inert line of a hold (F1).
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toContain("dockShown: isOpen && !folded && bodyShown,");
  });

  it("the guard reads only the controls of a question (D9)", () => {
    const fp = readFileSync(join(__dirname, "freshPress.ts"), "utf-8");
    const list = fp.slice(fp.indexOf("export const COMMIT_CONTROLS"), fp.indexOf('].join(", ")'));
    expect(list).toContain("[data-elicitation-submit]");
    expect(list).toContain("[data-not-now]");
    expect(list).not.toMatch(/confirmation|reply|earlier/);
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

  it("the card waits ARM_MS, the same value as an approval in the thread", () => {
    expect(ARM_MS).toBe(400);
    const card = readFileSync(join(__dirname, "..", "components/ConfirmationCard.tsx"), "utf-8");
    expect(card).toContain(`export const ARM_MS = ${ARM_MS};`);
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

describe("the hold: nothing moves under the pointer (verifier F1, 2026-11-28)", () => {
  const base = { now: 10_000, leftAt: 10_000, pointerDown: false, lastPressAt: Number.NEGATIVE_INFINITY };

  it("the numbers of rule 2", () => {
    expect(HOLD_MS).toBe(800);
    expect(QUIET_MS).toBe(600);
    expect(COLLAPSE_MS).toBe(150);
  });

  it("holds 800 ms after the item left", () => {
    expect(collapseWait(base)).toBe(800);
    expect(collapseWait({ ...base, now: 10_799 })).toBe(1);
    expect(collapseWait({ ...base, now: 10_800 })).toBe(0);
  });

  it("holds while a pointer is down, however long ago the item left", () => {
    expect(collapseWait({ ...base, now: 20_000, pointerDown: true })).toBeNull();
  });

  it("holds 600 ms after the last press in the dock", () => {
    // A second press of a double click 150 ms after the first.
    expect(collapseWait({ ...base, now: 10_800, lastPressAt: 10_150 })).toBe(0);
    expect(collapseWait({ ...base, now: 10_800, lastPressAt: 10_500 })).toBe(300);
    expect(collapseWait({ ...base, now: 11_099, lastPressAt: 10_500 })).toBe(1);
  });

  it("the inert line says why the item left, in three words only", () => {
    expect(LEFT_WORDS).toEqual({ sent: "Answer sent", notnow: "Not now", elsewhere: "Answered elsewhere" });
    expect(leftKind({ answered: "North depot" })).toBe("sent");
    expect(leftKind({ answered: "" })).toBe("sent");
    expect(leftKind({ declined: true })).toBe("notnow");
    expect(leftKind({})).toBe("elsewhere");
    expect(leftKind(null)).toBe("elsewhere");
  });

  it("the dock holds its height, and its line is no button", () => {
    const dock = readFileSync(join(__dirname, "..", "components/WaitingDock.tsx"), "utf-8");
    expect(dock).toContain("style={hold ? { minHeight: hold.rootH } : undefined}");
    const held = dock.slice(dock.indexOf("function Held("), dock.indexOf("export default function WaitingDock"));
    expect(held).toContain("data-dock-held={part}");
    expect(held).not.toMatch(/<button|<Button|onClick|role=/);
    // The collapse fades by opacity only (UI gate G3), and stops for reduced motion.
    expect(held).toContain("motion-safe:transition-opacity motion-safe:duration-150");
    expect(dock).toContain("prefersReducedMotion()");
    // An item that arrives in a hold draws only under rule 2.
    expect(dock).toContain("const bodyShown = count > 0 && (hold ? !hold.body && hold.bodyKept : true);");
    expect(dock).toContain("dockShown: isOpen && !folded && bodyShown,");
  });

  it("the chat keeps the dock mounted while the flag is on, so it can hold after its last item", () => {
    const chat = readFileSync(join(__dirname, "..", "components/AgentChat.tsx"), "utf-8");
    const host = chat.slice(chat.indexOf("<div ref={dockHostRef}"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(host).toContain("<WaitingDock");
    expect(host).toContain("left={dockLeft}");
    expect(host).not.toMatch(/showDock|\{\s*\w+ && \(\s*<WaitingDock/);
  });
});
