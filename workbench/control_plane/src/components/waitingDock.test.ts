/**
 * The markup of the waiting dock (WD-1, `projects_ai_chat.md` §24.11).
 *
 * Mutations caught, by test:
 * - an approval and a question draw as two cards, not as tabs of ONE card ->
 *   "one card with tabs";
 * - the count of a mixed row says "to approve" -> "one card with tabs";
 * - a question card unmounts on a change of tab, so its picks are lost ->
 *   "every question card stays mounted";
 * - the fold row draws Approve, Reject, Send or Submit -> "the fold row";
 * - the fold drops a card (unmounts it) -> "the fold row";
 * - the dock has a close control, or no live region -> "the frame";
 * - the dock draws a second scroll box -> "the frame";
 * - the flag off draws a tab row with "waiting" words -> "the flag off".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import ConfirmationQueue, { pickShown, reconcileShown, type DockQuestion } from "@/components/ConfirmationQueue";
import ElicitationCard from "@/components/ElicitationCard";
import WaitingDock, { WaitingDockLive } from "@/components/WaitingDock";
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

function dock(opts: { cards?: PendingConfirmation[]; questions?: DockQuestion[]; folded?: boolean; count?: number }) {
  return renderToStaticMarkup(
    createElement(WaitingDock, {
      count: opts.count ?? 2,
      phone: false,
      initialFolded: opts.folded,
      children: (folded: boolean, unfold: () => void) =>
        createElement(ConfirmationQueue, {
          cards: opts.cards ?? [approval],
          onAnswer: () => {},
          questions: opts.questions ?? [question("ask:q1", "Which printer?")],
          folded,
          onUnfold: unfold,
        }),
    }),
  );
}

describe("one card with tabs (WD-D3)", () => {
  it("an approval and a question are two tabs of one card, approvals first", () => {
    const html = dock({});
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html.match(/role="tab"/g)?.length).toBe(2);
    expect(html.indexOf("Task: Fix the extruder")).toBeLessThan(html.indexOf("Question: Which printer?"));
  });

  it("a row with a question says 'N waiting', a row of approvals says 'N to approve'", () => {
    expect(dock({})).toContain("2 waiting");
    expect(dock({})).not.toContain("to approve");
    expect(dock({ cards: [approval, approval2], questions: [] })).toContain("2 to approve");
  });

  it("a question tab has the question icon, and an unopened tab a dot", () => {
    const html = dock({});
    expect(html).toContain("lucide-message-circle-question");
    expect(html).toContain("data-unseen");
  });

  it("every question card stays mounted: a closed tab is hidden, not gone", () => {
    const html = dock({ questions: [question("ask:q1", "Which printer?"), question("ask:q2", "Which day?")] });
    expect(html).toContain('data-dock-question="ask:q1"');
    expect(html).toContain('data-dock-question="ask:q2"');
    expect(html).toContain("Which day?");
    expect(html).toMatch(/<div hidden="" data-dock-question="ask:q1">/);
  });

  it("one question card and nothing else draws no tab row", () => {
    const html = dock({ cards: [], count: 1 });
    expect(html).not.toContain("data-card-tabs=");
    expect(html).toContain("data-elicitation-card");
  });
});

describe("the fold row (WD-D2, audit rule 2)", () => {
  it("one row: the tabs, the open item and a Review button that only opens it", () => {
    const html = dock({ folded: true });
    expect(html).toContain('data-waiting-dock="folded"');
    expect(html).toContain("data-dock-fold-row");
    expect(html).toContain('data-dock-unfold="approval"');
    expect(html).toContain(">Review<");
  });

  it("a question in front reads Answer", () => {
    expect(dock({ cards: [], folded: true, count: 1 })).toContain('data-dock-unfold="question"');
  });

  it("the row itself holds no Approve, Reject, Send or Submit", () => {
    const html = dock({ folded: true });
    const row = html.slice(html.indexOf("data-dock-fold-row"), html.indexOf('<div hidden=""'));
    expect(row).not.toMatch(/data-confirmation-approve|data-elicitation-submit|>Approve<|>Reject<|>Send<|>Submit</);
  });

  it("the fold keeps each card mounted, hidden", () => {
    const html = dock({ folded: true });
    expect(html).toContain("data-confirmation-card");
    expect(html).toContain("data-elicitation-card");
    // The tab row draws once: in the fold row, not in the hidden card too.
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
  });
});

describe("the frame", () => {
  it("a fold control and no close control that drops an item", () => {
    const html = dock({});
    expect(html).toContain("data-dock-fold");
    expect(html).not.toMatch(/aria-label="(Close|Dismiss)/);
  });

  it("one polite live region: the first arrival speaks, and an empty dock says nothing", () => {
    // It starts from 0, so a region that mounts with the first item speaks.
    const live = (count: number) => renderToStaticMarkup(createElement(WaitingDockLive, { count }));
    expect(live(2)).toMatch(/role="status" aria-live="polite"[^>]*data-dock-live="">2 items wait for you</);
    expect(live(1)).toContain(">1 item waits for you<");
    expect(live(0)).toMatch(/data-dock-live=""><\/p>/);
    // The dock itself draws no second region.
    expect(dock({})).not.toContain("data-dock-live");
  });

  it("the chat mounts the region while the flag is on, outside the dock", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    expect(chat).toContain("{dockOn && <WaitingDockLive count={dockN} />}");
  });

  it("the body is the one scroll box, with a cap", () => {
    const html = dock({});
    expect(html).toMatch(/data-dock-body="" class="overflow-y-auto[^"]*" style="max-height:/);
  });

  it("every control is a native button", () => {
    const html = dock({});
    expect(html).not.toMatch(/role="button"/);
  });
});

describe("the flag off: the queue draws as before", () => {
  it("no dock shape and today's count words", () => {
    const html = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [approval, approval2], onAnswer: () => {} }));
    expect(html).not.toContain("data-dock-queue");
    expect(html).toContain("2 to approve");
    expect(html).not.toContain("waiting");
  });
});

describe("auto-advance and the shown tab across both kinds (audit rule 3)", () => {
  it("a new approval never takes the view from the question the member reads", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["ask:q1"], [{ key: "c1" }, { key: "ask:q1" }], { key: "ask:q1", index: 0 }, q);
    expect(next.shown.key).toBe("ask:q1");
  });

  it("an Undo of an approval still opens the restored card", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["c2", "ask:q1"], [{ key: "c1" }, { key: "c2" }, { key: "ask:q1" }], { key: "ask:q1", index: 1 }, q);
    expect(next.shown.key).toBe("c1");
  });

  it("an answered question hands the view to the next tab, as for an approval", () => {
    const items = [{ key: "c1" }, { key: "ask:q2" }];
    expect(pickShown(items, { key: "ask:q1", index: 1 })).toBe(1);
  });
});
