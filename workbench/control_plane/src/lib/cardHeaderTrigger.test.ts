/**
 * No link and no control inside a card's fold toggle (review of #945, P2).
 *
 * The toggle is the header row, a `<button>`. A title drawn as a link put an
 * `<a>` inside it: invalid HTML, and one click both followed the link and
 * folded the card. Every report card in chat did it, because `reportLink()`
 * always returns an address. Each card that draws its own head is rendered
 * here as an older long card, so its toggle shows, with a title that links.
 *
 * Mutation caught: `TemplateHead` or the root `card` passes a link, or
 * `GenUiText`, as the header's title again.
 *
 * The follow-up of #945 widens it to every template of the catalog and to
 * the Email cards. An Email card puts its controls (Disable, Open, Read all)
 * BESIDE its header. A static render cannot measure a body, so the mock
 * below hands each card a row count: the real `RollupCard` then draws the
 * toggle it draws in the browser, and every rule of the fold still applies.
 * Its browser twin is `e2e/email-chat-cards.spec.ts`.
 */
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));
vi.mock("@/components/RollupCard", async (load) => {
  const real = await load<typeof import("@/components/RollupCard")>();
  const { createElement: h } = await import("react");
  return {
    ...real,
    default: (p: import("@/components/RollupCard").RollupCardProps) => h(real.default, { ...p, rows: p.rows ?? 9 }),
  };
});

import EmailToolCards, { emailEvidence } from "@/components/email/EmailToolCards";
import GenerativeUINode from "@/components/GenerativeUINode";
import type { ToolEvent } from "@/components/MarkdownMessage";
import RollupCard, { RollupContext } from "@/components/RollupCard";
import { OWN_HEAD_TEMPLATES } from "@/components/genUITemplates";
import { RollupRegistry, resetManualStates, setManualState } from "@/lib/cardRollup";
import { plainTitle } from "@/lib/fencedText";

const UUID = "00000000-0000-4000-8000-000000000023";
const LINKED = "See [the board](/projects) for «Ops»";

const DATA: Record<string, Record<string, unknown>> = {
  timeline: { title: "Activity on #23", taskId: UUID, rows: [{ at: "2026-10-12T09:00:00Z", type: "comment", actor: "a", body: "x" }] },
  taskBoard: { title: LINKED, columns: [{ name: "Backlog", tasks: [{ id: UUID, title: "t" }] }] },
  dataGrid: { title: LINKED, columns: ["Title"], rows: [{ id: UUID, cells: ["t"] }] },
  reportCard: { title: "Weekly report", period: "6 to 12 Oct", stats: [{ label: "Done", value: 1 }] },
  planCard: { title: LINKED, project: { name: "P" }, tasks: [{ key: "a", title: "t", owner: "o", effort_mins: 5, due: "2026-10-20" }] },
  optionPicker: { title: LINKED, options: [{ id: "a", label: "A" }] },
  weatherCard: { location: LINKED, tempC: 20, condition: "rain" },
  statDashboard: { title: LINKED, stats: [{ label: "Open", value: 3 }] },
  sparkTrend: { label: LINKED, value: 4, series: [1, 2, 4] },
  comparison: { title: LINKED, options: [{ name: "A", rows: [{ label: "Price", value: "1" }] }] },
  progressTracker: { title: LINKED, steps: [{ label: "One", state: "done" }] },
  recipeCard: { title: LINKED, description: "Tea", steps: ["Boil"] },
  flightStatus: { airline: "IndiGo", flightNo: "6E 1", status: "in-air", from: { code: "BLR" }, to: { code: "DEL" } },
  trainStatus: { operator: "IR", trainNo: "12028", status: "delayed", delayMin: 5, from: { station: "SBC" }, to: { station: "MAS" } },
  formCard: { title: LINKED, fields: [{ name: "n", label: "Name" }] },
  barChart: { title: LINKED, bars: [{ label: "Ops", value: 2 }] },
  chart: { type: "bar", title: LINKED, labels: ["Ops"], values: [2] },
};

/** An older long card in a transcript: its toggle shows. */
function folded(spec: unknown): string {
  const registry = new RollupRegistry<Element>(() => 0);
  registry.register("tool:newer", {} as Element);
  const card: ReactElement = createElement(RollupCard, {
    id: "genui:m-1:0",
    title: "Card",
    rows: 9,
    header: "own",
    children: createElement(GenerativeUINode, { spec, onAction: () => {} }),
  });
  return renderToStaticMarkup(createElement(RollupContext.Provider, { value: { registry, waiting: new Set<string>() } }, card));
}

/** The same card, opened by hand: the toggle then draws its aside. */
function openCard(name: string): string {
  setManualState("genui:m-1:0", "open");
  try {
    return folded({ type: "template", props: { name, data: DATA[name] } });
  } finally {
    resetManualStates();
  }
}

/** The toggle's markup, from its tag to its close. */
const toggleOf = (html: string) => /<button[^>]*data-rollup-toggle[^>]*>[\s\S]*?<\/button>/.exec(html)?.[0] ?? "";

describe("a fold toggle holds no link and no control", () => {
  for (const name of OWN_HEAD_TEMPLATES) {
    it(name, () => {
      const html = folded({ type: "template", props: { name, data: DATA[name] } });
      const toggle = toggleOf(html);
      expect(toggle, `${name} drew no toggle`).not.toBe("");
      expect(toggle).not.toMatch(/<a[\s>]|<input|<select|<textarea|<button[^>]*>[\s\S]*<button/);
      expect(toggle).not.toContain("«");
    });
  }

  it("the root card", () => {
    const html = folded({ type: "card", props: { title: LINKED }, children: [{ type: "text", props: { text: "x" } }] });
    const toggle = toggleOf(html);
    expect(toggle).toContain("See the board for Ops");
    expect(toggle).not.toMatch(/<a[\s>]/);
  });

  it("the link of a linked title draws beside the toggle, not in it", () => {
    const html = folded({ type: "template", props: { name: "timeline", data: DATA.timeline } });
    const link = /<a [^>]*data-template-open=""[^>]*>/.exec(html)?.[0] ?? "";
    expect(link).toContain('href="/projects?task=');
    expect(toggleOf(html)).not.toContain("data-template-open");
  });

  it("a status mark draws beside the toggle, not in it, open or shut", () => {
    for (const name of ["flightStatus", "trainStatus"]) {
      for (const state of ["shut", "open"] as const) {
        const html = state === "open" ? openCard(name) : folded({ type: "template", props: { name, data: DATA[name] } });
        expect(html, `${name} ${state}`).toContain("data-template-end");
        expect(toggleOf(html), `${name} ${state}`).not.toContain("data-template-end");
        // The badge's words: a shut toggle hides its aside, so an open one is
        // where a badge put in the toggle shows.
        expect(toggleOf(html), `${name} ${state}`).not.toMatch(/in-air|delayed/);
      }
    }
  });

  it("an open toggle holds no link and no control either", () => {
    for (const name of OWN_HEAD_TEMPLATES) {
      const toggle = toggleOf(openCard(name));
      expect(toggle, `${name} drew no toggle`).not.toBe("");
      expect(toggle, name).not.toMatch(/<a[\s>]|<input|<select|<textarea|<button[^>]*>[\s\S]*<button/);
    }
  });

  it("plainTitle keeps the words and drops the marks", () => {
    expect(plainTitle(LINKED)).toBe("See the board for Ops");
    expect(plainTitle("**Bold** and `code`")).toBe("Bold and code");
  });
});

// ── The Email cards ─────────────────────────────────────────────────────────

const RULE_ID = "aaaaaaaa-1111-4000-8000-000000000001";
const ev = (id: string, name: string, args: Record<string, unknown>, result: string): ToolEvent =>
  ({ id, name, args, result, status: "done" }) as ToolEvent;

/** The flow cards of one turn, each a card that can fold. */
const FLOW: ToolEvent[] = [
  ev("t-draft", "draft_reply", { email_id: "m1" }, "Draft from Ops (mailbox acct-1):\n\nHello"),
  ev("t-rule", "create_rule", { account_id: "acct-1", name: "Invoices", instructions: "Invoices", label: "Finance" }, `Created rule 'Invoices' (id=${RULE_ID}).`),
  ev("t-settings", "update_assistant_settings", { account_id: "acct-1", auto_run: true }, "Settings updated."),
  ev("t-label", "manage_inbox", { action: "label", message_ids: ["m2"], add_labels: ["Finance"] }, "Labelled 1 email."),
  ev("t-act", "add_knowledge", {}, "Saved the entry [the guide](/kb/1)."),
  ev("t-none", "send_email", {}, "Not sent. The member cancelled."),
  ev("t-groups", "present_email_groups", {}, "## Finance (2)\n• id=g1 | Priya: Quote\n• id=g2 | AP: Invoice"),
];

/** The cards in a transcript, all older than a newer card. */
function inTranscript(node: ReactElement): string {
  const registry = new RollupRegistry<Element>(() => 0);
  registry.register("tool:newer", {} as Element);
  return renderToStaticMarkup(createElement(RollupContext.Provider, { value: { registry, waiting: new Set<string>() } }, node));
}

/** Each card's markup, by its roll-up id. */
function cards(html: string): Record<string, string> {
  const out: Record<string, string> = {};
  const parts = html.split('data-rollup-card="').slice(1);
  for (const part of parts) out[part.slice(0, part.indexOf('"'))] = part;
  return out;
}

const CONTROL = /<a[\s>]|<input|<select|<textarea|<button[^>]*>[\s\S]*<button/;

/** The turn's cards, shut (older and long) or opened by hand. A shut
 *  toggle hides its aside, so a control put there shows only when open. */
function flowCards(state: "shut" | "open"): Record<string, string> {
  if (state === "open") for (const e of FLOW) setManualState(`tool:${e.id}`, "open");
  try {
    return cards(inTranscript(createElement(EmailToolCards, { toolEvents: FLOW })));
  } finally {
    resetManualStates();
  }
}

describe("an Email card's open toggle holds no link and no control", () => {
  for (const e of FLOW.filter((x) => x.id !== "t-draft")) {
    it(e.name, () => {
      const card = flowCards("open")[`tool:${e.id}`];
      expect(card, e.name).toContain('data-rollup="open"');
      const toggle = toggleOf(card);
      expect(toggle, `${e.name} drew no toggle`).not.toBe("");
      expect(toggle).not.toMatch(CONTROL);
      for (const label of ["Disable", "Delete", "Open", "Read all"]) {
        expect(toggle, label).not.toMatch(new RegExp(String.raw`>\s*${label}\s*<`));
      }
    });
  }
});

describe("an Email card's fold toggle holds no link and no control", () => {
  const byId = flowCards("shut");

  it("draws every flow card through the roll-up", () => {
    expect(Object.keys(byId).sort()).toEqual(FLOW.map((e) => `tool:${e.id}`).sort());
  });

  for (const e of FLOW.filter((x) => x.id !== "t-draft")) {
    it(e.name, () => {
      const toggle = toggleOf(byId[`tool:${e.id}`]);
      expect(toggle, `${e.name} drew no toggle`).not.toBe("");
      expect(toggle).not.toMatch(CONTROL);
    });
  }

  it("the controls of a card sit beside its toggle", () => {
    for (const [id, label] of [["tool:t-rule", "Disable"], ["tool:t-rule", "Delete"], ["tool:t-settings", "Open"]]) {
      expect(byId[id], label).toContain(label);
      expect(toggleOf(byId[id]), label).not.toContain(label);
    }
  });

  it("a title cut to one line keeps its whole words as its tooltip (review of #1012)", () => {
    expect(toggleOf(byId["tool:t-rule"])).toContain('title="Created rule: Invoices"');
    // A row with no body, so no toggle: the plain title row keeps it too.
    const moved = ev("t-move", "manage_inbox", { action: "move", folder: "Vendors and suppliers", message_ids: ["a", "b"] }, "Moved 2 emails.");
    const out = inTranscript(createElement(EmailToolCards, { toolEvents: [moved] }));
    expect(out).toContain('title="Moved 2 emails → Vendors and suppliers"');
  });

  it("the draft waits on the member: no toggle, so it never folds", () => {
    expect(byId["tool:t-draft"]).toContain("Draft reply");
    expect(byId["tool:t-draft"]).not.toContain("data-rollup-toggle");
    expect(byId["tool:t-draft"]).toContain("Save to Drafts");
  });

  it("a read card of the trail keeps its controls out of the toggle too", () => {
    const list = ev("t-list", "query_inbox", {}, "id=e1 | Priya: Quote\nid=e2 | AP: Invoice");
    const out = inTranscript(createElement("div", null, emailEvidence(list)));
    const toggle = toggleOf(out);
    expect(toggle).not.toBe("");
    expect(toggle).not.toMatch(CONTROL);
    expect(out).toContain("Read all");
    expect(toggle).not.toContain("Read all");
  });
});
