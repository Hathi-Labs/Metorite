/**
 * The row of the waiting dock (WD-1, `projects_ai_chat.md` §24.11, D10).
 *
 * D10 (2026-11-28): the dock holds no card and no commit control. With the
 * flag on, every card draws in the thread as with the flag off, and ONE row
 * above the box names what waits. Review only scrolls. These tests hold the
 * rules of the row, and the source fences that keep the cards and the
 * message box as they are on main. `e2e/chat-waiting-dock.spec.ts` holds
 * the browser half.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { HITL_TARGET, type AskSources } from "@/lib/askPin";
import { tabLabel } from "@/lib/cardTabs";
import type { PendingConfirmation } from "@/lib/confirmationQueue";
import { OWNER_PICKER, ownerTurn } from "@/lib/ownerTurn.fixture";
import {
  HOLD_MS,
  nextPhase,
  reviewScrollTop,
  rowMore,
  waitingRow,
  type RowPhase,
} from "@/lib/waitingDock";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");

function sources(over: Partial<AskSources> = {}): AskSources {
  return {
    confirmations: [],
    elicitation: null,
    userInput: null,
    messages: [],
    runActive: true,
    answered: new Set(),
    ...over,
  };
}

const TASK: PendingConfirmation = { key: "t1", title: "Create this task?", detail: "«Ship the pack» · Launch" };
const MAIL: PendingConfirmation = { key: "m1", title: "Send this new email?" };
const SLOT: PendingConfirmation = { key: "s1", title: "Send this new email?", redraft: "updating" };

describe("what the row names (D10)", () => {
  it("nothing waits: no row", () => {
    expect(waitingRow(sources(), null)).toBeNull();
    // A slot of a redraft waits on the assistant, so alone it shows no row.
    expect(waitingRow(sources({ confirmations: [SLOT] }), null)).toBeNull();
  });

  it("the open tab of the queue, and 'and N more' over every kind that waits", () => {
    const src = sources({
      confirmations: [TASK, MAIL, SLOT],
      elicitation: { questions: [{ question: "Which depot?" }, { question: "Which day?" }] },
      userInput: { question: "Which day should the run start?" },
      messages: ownerTurn(),
    });
    // 2 approvals, 1 question card (two questions are one card), 1 ask_user
    // card and 1 generative-UI ask: 5, so the row says "and 4 more".
    expect(waitingRow(src, null)).toEqual({ label: tabLabel(TASK), more: 4, ask: HITL_TARGET, inner: "[data-confirmation-card]" });
    expect(waitingRow(src, "m1")?.label).toBe(tabLabel(MAIL));
    // A key the queue no longer holds names the first approval.
    expect(waitingRow(src, "gone")?.label).toBe(tabLabel(TASK));
    expect(rowMore(4)).toBe("and 4 more");
    expect(rowMore(0)).toBe("");
  });

  it("a question in the thread, then a generative-UI ask, each with its target", () => {
    const q = waitingRow(sources({ elicitation: { questions: [{ question: "Which depot?" }] } }), null);
    expect(q).toEqual({ label: "Which depot?", more: 0, ask: HITL_TARGET, inner: '[data-elicitation-card="open"]' });
    const u = waitingRow(sources({ userInput: { question: "Which day?" } }), null);
    expect(u).toMatchObject({ label: "Which day?", ask: HITL_TARGET });
    const g = waitingRow(sources({ messages: ownerTurn() }), null);
    expect(g).toEqual({
      label: "Which tags should I register on Metorite?",
      more: 0,
      ask: `genui:${OWNER_PICKER.request_id}`,
      inner: null,
    });
    // An answered ask waits no more.
    expect(waitingRow(sources({ messages: ownerTurn(), answered: new Set([OWNER_PICKER.request_id]) }), null)).toBeNull();
  });
});

describe("the height of the row (D10 rule 3)", () => {
  const step = (phase: RowPhase, waiting: boolean, pointerDown = false, quietMs = 0) =>
    nextPhase({ phase, waiting, pointerDown, quietMs });

  it("an arrival takes the height only when no pointer is down", () => {
    expect(step("hidden", true, true)).toBe("hidden");
    expect(step("hidden", true, false)).toBe("row");
    // The place is already held: the row names the new element at once.
    expect(step("empty", true, true)).toBe("row");
  });

  it("the last leave shows 'Nothing waits' in the same place, at once", () => {
    expect(step("row", false, false, 0)).toBe("empty");
    expect(step("row", false, true, 99_999)).toBe("empty");
  });

  it("the collapse waits 800 ms with no pointer down", () => {
    expect(HOLD_MS).toBe(800);
    expect(step("empty", false, false, HOLD_MS - 1)).toBe("empty");
    expect(step("empty", false, true, HOLD_MS * 10)).toBe("empty");
    expect(step("empty", false, false, HOLD_MS)).toBe("hidden");
    expect(step("hidden", false)).toBe("hidden");
  });
});

describe("Review (D10 rule 4)", () => {
  const port = { top: 100, bottom: 500 };
  it("a card that fits shows whole above the row", () => {
    // Below the room: the thread scrolls so its foot meets the foot of the room.
    expect(reviewScrollTop(1000, port, { top: 600, bottom: 700 })).toBe(1200);
    // Above the room: its top meets the top of the room.
    expect(reviewScrollTop(1000, port, { top: 0, bottom: 80 })).toBe(900);
  });
  it("a card taller than the room shows its top", () => {
    expect(reviewScrollTop(1000, port, { top: 900, bottom: 1500 })).toBe(1800);
    expect(reviewScrollTop(1000, port, { top: -300, bottom: 600 })).toBe(600);
  });
});

describe("the fences of D10", () => {
  const chat = read("../components/AgentChat.tsx");
  const row = read("../components/WaitingDock.tsx");

  it("no card file reads the dock", () => {
    for (const f of [
      "ConfirmationCard",
      "ConfirmationQueue",
      "ElicitationCard",
      "NewMailCardForm",
      "ReplyCardForm",
      "TaskCardForm",
      "EarlierDraftQuestion",
      "ui/CardTabs",
    ]) {
      expect(read(`../components/${f}.tsx`), f).not.toMatch(/waitingDock|WaitingDock|dockOn|inDock/);
    }
    expect(read("./cardTabs.ts")).not.toMatch(/waitingDock|question/);
    // The one prop of the queue that main does not have: it reports the open tab.
    expect(chat).toContain("onShown={reportShown}");
  });

  it("Enter in the box and the cards in the thread are the code of the flag off", () => {
    const keys = chat.slice(chat.indexOf("const handleKeyDown"), chat.indexOf("\n  };\n", chat.indexOf("const handleKeyDown")));
    expect(keys.length).toBeGreaterThan(200);
    expect(keys).not.toMatch(/dock/i);
    const cards = chat.slice(chat.indexOf("const renderHitlCards"), chat.indexOf("</RollupCard>", chat.indexOf("const renderHitlCards")));
    expect(cards).not.toMatch(/dock/i);
  });

  it("the row takes the place of the bar, inside the form, only with the flag on", () => {
    const form = chat.slice(chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(form).toMatch(/\{dockOn \? \(\s*<WaitingDock [^>]*row=\{dockRow\}[^>]*\/>\s*\) : \(\s*<AskPin ask=\{waiting\} threadRef=\{threadRef\} \/>\s*\)\}/);
    expect(chat.match(/<WaitingDock\s/g)?.length).toBe(1);
  });

  it("the row holds one button, Review, which commits nothing and keeps the focus", () => {
    expect(row.match(/<Button\b/g)?.length).toBe(1);
    expect(row).toContain("onClick={review}");
    expect(row).toContain("onMouseDown={(e) => e.preventDefault()}");
    // It never blurs, and it focuses only the card's heading, after the box check.
    expect(row).not.toMatch(/\.blur\(/);
    expect(row.match(/\.focus\(/g)?.length).toBe(1);
    expect(row.indexOf("if (boxHadFocus(boxRef.current)) return;")).toBeLessThan(row.indexOf("head.focus("));
    // No card and no answer path: the row imports no card and no respond call.
    expect(row).not.toMatch(/ConfirmationCard|ElicitationCard|ConfirmationQueue|respond|onAnswer|fetch\(/);
  });
});
