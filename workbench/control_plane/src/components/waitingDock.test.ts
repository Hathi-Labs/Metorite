/**
 * The markup of the waiting dock (WD-1, `projects_ai_chat.md` §24.11).
 *
 * Mutations caught, by test:
 * - an approval and a question draw as two cards, not as tabs of ONE card ->
 *   "one card with tabs";
 * - the questions do not come first (D4) -> "one card with tabs";
 * - the count of a mixed row says "to approve" -> "one card with tabs";
 * - a question card unmounts on a change of tab, so its picks are lost ->
 *   "every question card stays mounted";
 * - one item draws no header row, so Expand and Fold have no place (D1) ->
 *   "the header row";
 * - the Compact view shows Send or Approve for an item it does not show
 *   whole (D3) -> "Compact and Expanded";
 * - the Expanded Send does not name its count -> "Compact and Expanded";
 * - the fold row draws Approve, Reject, Send or Submit -> "the fold row";
 * - the dock has a close control, or no live region -> "the frame";
 * - the flag off draws a dock shape -> "the flag off".
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import ConfirmationQueue, { pickShown, reconcileShown, type DockQuestion } from "@/components/ConfirmationQueue";
import ElicitationCard from "@/components/ElicitationCard";
import WaitingDock, { WaitingDockLive, type DockRender } from "@/components/WaitingDock";
import { questionTabLabel } from "@/lib/cardTabs";
import type { PendingConfirmation } from "@/lib/confirmationQueue";
import type { DockMode } from "@/lib/waitingDock";

const approval: PendingConfirmation = { key: "c1", title: "Create this task?", detail: "«Fix the extruder»" };
const approval2: PendingConfirmation = { key: "c2", title: "Set this reminder?", detail: "«Call Rajendra»" };
/** An approval with more than its title: fields in its text. */
const fieldsCard: PendingConfirmation = {
  key: "c3",
  title: "Create this task?",
  detail: "«Ship the pack» · Launch",
  context: "project: «Launch»\nname: «Ship the pack»\ndue_at: 2026-12-01",
  fenced: true,
};

const mailForm = (cc: string[], body = "Hi Kim,") => ({
  kind: "new", message_id: "", mailbox: "Work", subject: "Rates", body,
  recipients: {
    reply: { to: ["kim@contoso.test", "lena@contoso.test"], cc },
    reply_all: { to: ["kim@contoso.test", "lena@contoso.test"], cc },
  },
  files: [], notices: [], note: "",
});
const mail = (key: string, cc: string[], body?: string): PendingConfirmation => ({
  key,
  title: "Send this new email?",
  detail: "From Work · Rates",
  reply: mailForm(cc, body) as never,
});

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

function dock(opts: { cards?: PendingConfirmation[]; questions?: DockQuestion[]; folded?: boolean; count?: number; mode?: DockMode }) {
  return renderToStaticMarkup(
    createElement(WaitingDock, {
      count: opts.count ?? 2,
      phone: false,
      initialFolded: opts.folded,
      children: (d: DockRender) =>
        createElement(ConfirmationQueue, {
          cards: opts.cards ?? [approval],
          onAnswer: () => {},
          questions: opts.questions ?? [question("ask:q1", "Which printer?")],
          folded: d.folded,
          onUnfold: d.unfold,
          dockControls: d.controls,
          dockMode: opts.mode ?? d.mode,
          onExpand: d.expand,
        }),
    }),
  );
}

describe("one card with tabs (WD-D3, D4)", () => {
  it("a question and an approval are two tabs of one card, the question first", () => {
    const html = dock({});
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html.match(/role="tab"/g)?.length).toBe(2);
    expect(html.indexOf("Question: Which printer?")).toBeLessThan(html.indexOf("Task: Fix the extruder"));
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
    expect(html).toMatch(/<div hidden="" data-dock-question="ask:q2">/);
  });
});

describe("the header row (D1)", () => {
  it("one item draws the header too: its count, its tab, Expand and Fold", () => {
    for (const html of [dock({ cards: [], count: 1 }), dock({ questions: [], count: 1 })]) {
      const row = html.slice(html.indexOf("data-card-tabs="), html.indexOf("data-card-tabs-probe"));
      expect(row).toContain("data-card-tabs-trailing");
      expect(row).toContain('aria-label="Fold the card to one row"');
      expect(row).toContain('aria-label="Expand the card"');
    }
  });

  it("no row of controls sits above the card", () => {
    const html = dock({});
    expect(html.indexOf("data-dock-controls")).toBeGreaterThan(html.indexOf("data-dock-body"));
  });
});

describe("Compact and Expanded (D2, D3)", () => {
  it("Compact: an approval with more than its title shows Review, and no Approve", () => {
    const html = dock({ cards: [fieldsCard], questions: [], count: 1, mode: "compact" });
    expect(html).toContain("data-dock-review");
    expect(html).not.toContain("data-confirmation-approve");
    expect(html).not.toContain("data-card-field");
  });

  it("Compact: an approval that is only its title shows Approve", () => {
    const html = dock({ cards: [approval], questions: [], count: 1, mode: "compact" });
    expect(html).toContain("data-confirmation-approve");
    expect(html).not.toContain("data-dock-review");
  });

  it("Compact: an email with a Cc row draws one line and three lines of text, and shows Review, not Send", () => {
    const body = "Hi Kim,\nLine one.\nLine two.\nLine three.\nLine four.";
    const html = dock({ cards: [mail("m1", ["geo@acme.test"], body)], questions: [], count: 1, mode: "compact" });
    expect(html).toContain("To Kim, Lena · Cc 1 · Rates");
    expect(html).toContain("Hi Kim,\nLine one.\nLine two.…");
    expect(html).toContain("data-dock-review");
    expect(html).not.toContain("data-reply-send");
    expect(html).not.toContain('aria-label="Recipients"');
  });

  it("Compact: a short email to two people, shown whole by address, shows Send", () => {
    const html = dock({ cards: [mail("m2", [])], questions: [], count: 1, mode: "compact" });
    expect(html).toContain("To kim@contoso.test, lena@contoso.test · Rates");
    expect(html).toContain("data-reply-send");
    expect(html).not.toContain("data-dock-review");
  });

  it("Expanded: the whole card, and Send names how many get the email", () => {
    const html = dock({ cards: [mail("m3", ["geo@acme.test"])], questions: [], count: 1, mode: "expanded" });
    expect(html).toContain('aria-label="Recipients"');
    expect(html).toMatch(/data-reply-send=""[^>]*>[\s\S]*?Send to 3</);
  });

  it("a question shows whole in Compact, with its Submit", () => {
    const html = dock({ cards: [], count: 1, mode: "compact" });
    expect(html).toContain("data-elicitation-submit");
  });
});

describe("the fold row (WD-D2, audit rule 2)", () => {
  it("one row: the tabs, the open item and a button that only opens it", () => {
    const html = dock({ folded: true });
    expect(html).toContain('data-waiting-dock="folded"');
    expect(html).toContain("data-dock-fold-row");
    // D4: the question is the first tab.
    expect(html).toContain('data-dock-unfold="question"');
    expect(html).toContain(">Answer<");
    expect(dock({ folded: true, questions: [], count: 1 })).toContain(">Review<");
  });

  it("the row itself holds no Approve, Reject, Send or Submit", () => {
    const html = dock({ folded: true });
    const row = html.slice(html.indexOf("data-dock-fold-row"), html.indexOf('<div hidden=""'));
    expect(row).not.toMatch(/data-confirmation-approve|data-elicitation-submit|>Approve<|>Reject<|>Send<|>Submit</);
  });

  it("the fold keeps each card mounted, hidden, and the header draws once", () => {
    const html = dock({ folded: true, questions: [], count: 1 });
    expect(html).toContain("data-confirmation-card");
    expect(html.match(/data-card-tabs=/g)?.length).toBe(1);
    expect(html).not.toContain("data-dock-controls");
  });
});

describe("the frame", () => {
  it("a fold control and no close control that drops an item", () => {
    const html = dock({});
    expect(html).toContain("data-dock-fold");
    expect(html).not.toMatch(/aria-label="(Close|Dismiss)/);
  });

  it("one polite live region: the first arrival speaks, and an empty dock says nothing", () => {
    const live = (count: number) => renderToStaticMarkup(createElement(WaitingDockLive, { count }));
    expect(live(2)).toMatch(/role="status" aria-live="polite"[^>]*data-dock-live="">2 items wait for you</);
    expect(live(1)).toContain(">1 item waits for you<");
    expect(live(0)).toMatch(/data-dock-live=""><\/p>/);
    expect(dock({})).not.toContain("data-dock-live");
  });

  it("the chat mounts the region while the flag is on, outside the dock", () => {
    const chat = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");
    const host = chat.slice(chat.indexOf("<div ref={dockHostRef}"), chat.indexOf("<form onSubmit={handleSubmit}"));
    expect(host).toContain("<WaitingDockLive count={dockN} />");
    expect(chat).toContain("{dockOn && (");
  });

  it("the body is the one scroll box, with a cap", () => {
    expect(dock({})).toMatch(/data-dock-body="" class="overflow-y-auto[^"]*" style="max-height:/);
  });

  it("every control is a native button", () => {
    expect(dock({})).not.toMatch(/role="button"/);
  });
});

describe("the flag off: the queue draws as before", () => {
  it("no dock shape, today's count words, and every person of an email", () => {
    const html = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [approval, approval2], onAnswer: () => {} }));
    expect(html).not.toContain("data-dock-queue");
    expect(html).toContain("2 to approve");
    expect(html).not.toContain("waiting");
    const email = renderToStaticMarkup(createElement(ConfirmationQueue, { cards: [mail("m4", ["geo@acme.test"])], onAnswer: () => {} }));
    expect(email).toContain('aria-label="Recipients"');
    expect(email).toMatch(/data-reply-send=""[^>]*>[\s\S]*?Send</);
    expect(email).not.toMatch(/Send to \d/);
    expect(email).not.toContain("data-dock-review");
  });
});

describe("the shown tab across both kinds (D4, audit rule 3)", () => {
  it("a new approval never takes the view from the question the member reads", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["ask:q1"], [{ key: "ask:q1" }, { key: "c1" }], { key: "ask:q1", index: 0 }, q);
    expect(next.shown.key).toBe("ask:q1");
  });

  it("a new question never takes the view from the approval the member reads", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["c1"], [{ key: "ask:q1" }, { key: "c1" }], { key: "c1", index: 0 }, q);
    expect(next.shown.key).toBe("c1");
  });

  it("an Undo of an approval still opens the restored card", () => {
    const q = new Set(["ask:q1"]);
    const next = reconcileShown(["ask:q1", "c2"], [{ key: "ask:q1" }, { key: "c1" }, { key: "c2" }], { key: "ask:q1", index: 0 }, q);
    expect(next.shown.key).toBe("c1");
  });

  it("an answered question hands the view to the next tab, as for an approval", () => {
    const items = [{ key: "ask:q2" }, { key: "c1" }];
    expect(pickShown(items, { key: "ask:q1", index: 0 })).toBe(0);
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
