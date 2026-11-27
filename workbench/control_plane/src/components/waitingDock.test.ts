/**
 * The markup of the waiting dock (WD-1, `projects_ai_chat.md` §24.11, D9).
 *
 * D9 (2026-11-27): the dock holds the two question cards and ONE pointer row
 * for the approvals. An approval card always draws in the thread, exactly as
 * with the flag off.
 *
 * Mutations caught, by test:
 * - an approval card, or a commit control of one, draws in the dock -> "the
 *   pointer" and "approvals carry no dock code";
 * - the pointer row names no card, or drops "and N more" -> "the pointer";
 * - an empty dock draws its frame, or its inert line is a button -> "the hold";
 * - a slot of a redraft alone draws the pointer -> lib test "draws only";
 * - two questions draw as two cards, not as tabs of ONE card -> "two
 *   questions";
 * - a question card unmounts on a change of tab, so its picks are lost ->
 *   "two questions";
 * - the fold row draws Submit -> "the fold row";
 * - the dock has a close control, or no live region -> "the frame";
 * - a component of an approval reads the dock again -> "approvals carry no
 *   dock code".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import ConfirmationQueue from "@/components/ConfirmationQueue";
import ElicitationCard from "@/components/ElicitationCard";
import WaitingDock, { WaitingDockLive, type DockPointer, type DockQuestion } from "@/components/WaitingDock";
import { questionTabLabel } from "@/lib/cardTabs";
import type { PendingConfirmation } from "@/lib/confirmationQueue";

const approval: PendingConfirmation = { key: "c1", title: "Create this task?", detail: "«Fix the extruder»" };
const approval2: PendingConfirmation = { key: "c2", title: "Set this reminder?", detail: "«Call Rajendra»" };

const question = (key: string, text: string): DockQuestion => ({
  key,
  label: questionTabLabel(text),
  title: text,
  render: (slot) =>
    createElement(ElicitationCard, {
      key,
      id: key,
      questions: [{ header: "Pick", question: text, multiSelect: false, options: [{ label: "Yes" }, { label: "No" }] }],
      onSubmit: () => {},
      onDecline: () => {},
      ...slot,
    }),
});

const POINTER: DockPointer = { label: "Task: Fix the extruder", more: 0, onReview: () => {} };

function dock(opts: { questions?: DockQuestion[]; pointer?: DockPointer | null; folded?: boolean }) {
  return renderToStaticMarkup(
    createElement(WaitingDock, {
      questions: opts.questions ?? [question("ask:q1", "Which printer?")],
      pointer: opts.pointer === undefined ? POINTER : opts.pointer,
      phone: false,
      initialFolded: opts.folded,
    }),
  );
}

/** A control that commits an approval. None may draw in the dock (D9). */
const APPROVAL_COMMIT = /data-confirmation-approve|data-confirmation-card|data-reply-send|data-reply-save|data-earlier-keep|data-earlier-delete|>Approve<|>Reject<|>Send</;

describe("the pointer (D9)", () => {
  it("one row names the approval the queue shows, with Review, and no card", () => {
    const html = dock({});
    expect(html.match(/data-dock-pointer=""/g)?.length).toBe(1);
    expect(html).toMatch(/data-dock-pointer=""[\s\S]*?Waits for you:[\s\S]*?Task: Fix the extruder/);
    expect(html).toMatch(/data-dock-pointer-review=""[^>]*>[\s\S]*?Review</);
    expect(html).not.toMatch(APPROVAL_COMMIT);
  });

  it("'and N more' when more approvals wait", () => {
    const html = dock({ pointer: { ...POINTER, more: 2 } });
    expect(html).toMatch(/Task: Fix the extruder<span[^>]*> and 2 more<\/span>/);
    expect(dock({})).not.toContain("more");
  });

  it("with only approvals waiting, the dock is the pointer row alone", () => {
    const html = dock({ questions: [] });
    expect(html).toContain('data-waiting-dock="pointer"');
    expect(html).toContain("data-dock-pointer");
    expect(html).not.toContain("data-dock-body");
    expect(html).not.toContain("data-dock-fold");
  });

  it("with no approval waiting, no pointer", () => {
    expect(dock({ pointer: null })).not.toContain("data-dock-pointer");
  });

  it("the Review is a native button, and the row draws no tab of an approval", () => {
    const html = dock({});
    expect(html).toMatch(/<button[^>]*data-dock-pointer-review=""/);
    expect(html).not.toMatch(/role="tab"/);
  });
});

describe("two questions (WD-D3)", () => {
  const two = () => dock({ questions: [question("ask:q1", "Which printer?"), question("ask:q2", "Which day?")] });

  it("are two tabs of one card, the first open, the count 'N waiting'", () => {
    const html = two();
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html.match(/role="tab"/g)?.length).toBe(2);
    expect(html.indexOf("Question: Which printer?")).toBeLessThan(html.indexOf("Question: Which day?"));
    expect(html).toContain("2 waiting");
    expect(html).not.toContain("to approve");
  });

  it("a question tab has the question icon, and an unopened tab a dot", () => {
    const html = two();
    expect(html).toContain("lucide-message-circle-question");
    expect(html).toContain("data-unseen");
  });

  it("every question card stays mounted: a closed tab is hidden and inert, not gone", () => {
    const html = two();
    expect(html).toContain('data-dock-question="ask:q1"');
    expect(html).toMatch(/<div hidden="" inert="" data-dock-question="ask:q2">/);
    expect(html).toContain("Which day?");
  });

  it("one question draws no tab row", () => {
    expect(dock({})).not.toContain("data-card-tabs=");
  });
});

describe("a question in the dock", () => {
  it("keeps Submit and Not now, and draws no X (WD-D2)", () => {
    const html = dock({});
    expect(html).toContain("data-elicitation-submit");
    expect(html).toContain(">Not now<");
    expect(html).not.toContain("data-not-now-close");
  });

  it("outside the dock the question keeps its X", () => {
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, {
        questions: [{ header: "Pick", question: "Which?", multiSelect: false, options: [{ label: "Yes" }] }],
        onSubmit: () => {},
        onDecline: () => {},
      }),
    );
    expect(html).toContain("data-not-now-close");
  });

  it("the Fold control sits on the card's title row, inside the scroll box", () => {
    const html = dock({});
    expect(html.indexOf("data-dock-fold=")).toBeGreaterThan(html.indexOf("data-dock-body"));
    expect(html).toContain('aria-label="Fold the card to one row"');
  });
});

describe("the fold row (WD-D2, audit rule 2)", () => {
  it("one row: the open question and a button that only opens it", () => {
    const html = dock({ folded: true });
    expect(html).toContain('data-waiting-dock="folded"');
    expect(html).toContain("data-dock-fold-row");
    expect(html).toContain('data-dock-unfold="question"');
    expect(html).toContain(">Answer<");
  });

  it("the row itself holds no Submit, and the card stays mounted under it", () => {
    const html = dock({ folded: true });
    const row = html.slice(html.indexOf("data-dock-fold-row"), html.indexOf('<div hidden=""'));
    expect(row).not.toMatch(/data-elicitation-submit|>Submit<|>Not now</);
    expect(html).toContain("data-elicitation-card");
  });

  it("the pointer row stays while the questions fold", () => {
    expect(dock({ folded: true })).toContain("data-dock-pointer");
  });
});

describe("the frame", () => {
  it("no close control that drops an item", () => {
    expect(dock({})).not.toMatch(/aria-label="(Close|Dismiss)/);
  });

  it("one polite live region: the first arrival speaks, and an empty dock says nothing", () => {
    const live = (count: number) => renderToStaticMarkup(createElement(WaitingDockLive, { count }));
    expect(live(2)).toMatch(/role="status" aria-live="polite"[^>]*data-dock-live="">2 items wait for you</);
    expect(live(1)).toContain(">1 item waits for you<");
    expect(live(0)).toMatch(/data-dock-live=""><\/p>/);
    expect(dock({})).not.toContain("data-dock-live");
  });

  it("the chat mounts the region while the flag is on, outside the dock, with the count of all that waits", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    const host = chat.slice(chat.indexOf("<div ref={dockHostRef}"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(host).toContain("<WaitingDockLive count={dockN} />");
    expect(chat).toContain("const dockN = dockCount(dockSources);");
    expect(chat).toContain("{dockOn && (");
  });

  it("the body is the one scroll box, with a cap", () => {
    expect(dock({})).toMatch(/data-dock-body="" class="overflow-y-auto[^"]*" style="max-height:/);
  });

  it("every control is a native button", () => {
    expect(dock({})).not.toMatch(/role="button"/);
  });
});

describe("approvals carry no dock code (D9)", () => {
  // Seven rounds of review each found a send of content the member did not
  // see in a dock view of an approval. The fix is structural: the approval
  // components are the components of main, and none of them reads the dock.
  const FILES = [
    "ConfirmationQueue.tsx",
    "ConfirmationCard.tsx",
    "NewMailCardForm.tsx",
    "ReplyCardForm.tsx",
    "TaskCardForm.tsx",
    "EarlierDraftQuestion.tsx",
  ];
  for (const f of FILES) {
    it(f, () => {
      const src = readFileSync(join(__dirname, f), "utf-8");
      expect(src).not.toMatch(/dock|Dock|freshPress|waitingDock/);
    });
  }

  it("the chat draws the queue in the thread with the props of main, and never in the dock", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    expect(chat.match(/<ConfirmationQueue\b/g)?.length).toBe(1);
    const thread = chat.slice(chat.indexOf("const renderHitlCards = ()"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(thread).toContain("<ConfirmationQueue");
    const queue = thread.slice(thread.indexOf("<ConfirmationQueue"), thread.indexOf("/>", thread.indexOf("<ConfirmationQueue")));
    expect(queue).not.toMatch(/dock|folded|questions|onExpand/i);
  });

  it("the queue in the thread draws as on main: today's count words and every person of an email", () => {
    const html = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [approval, approval2], onAnswer: () => {} }));
    expect(html).toContain("2 to approve");
    expect(html).not.toContain("waiting");
    expect(html).toContain("data-confirmation-approve");
  });
});

describe("a failed send keeps the question's picks and typed text", () => {
  const q = [{ header: "Depot", question: "Which depot?", multiSelect: false, options: [{ label: "North" }, { label: "South" }] }];

  it("a card that mounts again starts from the chat's draft", () => {
    const html = renderToStaticMarkup(
      createElement(ElicitationCard, {
        questions: q,
        onSubmit: () => {},
        initialDraft: { answers: { Depot: { selected: ["South"] } }, freeformTexts: { Depot: "via the ring road" } },
      }),
    );
    expect(html).toContain("via the ring road");
    expect(html).toMatch(/aria-pressed="true"[^>]*>[\s\S]*?South/);
  });

  it("the chat holds the draft by request id, for both question cards", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    expect(chat).toContain("{...draftProps(elicitation.requestId)}");
    expect(chat).toContain("{...draftProps(userInput.requestId)}");
    expect(chat).toContain("questionDraftsRef.current.set(rid, d)");
  });
});

describe("the hold (F1, 2026-11-28)", () => {
  it("an empty dock draws no frame: no item, no pointer and no hold", () => {
    const html = dock({ questions: [], pointer: null });
    expect(html).toMatch(/<section hidden=""/);
    expect(html).not.toContain("data-waiting-dock=");
  });

  it("outside a hold no inert line draws, and the dock has no floor", () => {
    const html = dock({});
    expect(html).not.toContain("data-dock-held");
    expect(html).not.toContain("min-height");
  });
});
