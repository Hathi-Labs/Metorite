/**
 * The chat card roll-up (owner request, 2026-10-10): a long card folds when
 * a newer card arrives, and it stays open when it is the newest, when it
 * waits on the member, or when the member opened it by hand.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `rollupOpen` loses its pending rule -> "a pending approval card never
 *   rolls up" (run 2026-10-10: red);
 * - the newest rule wins over a manual choice -> "a manual open beats the
 *   automatic rule" (run 2026-10-10: red);
 * - `AgentChat` stops wrapping the approval group -> "the approval group is
 *   a card of the transcript".
 * - `rollupOpen` lets the focus open a card (`held || newest`) -> "focus on a
 *   shut card's header holds it shut".
 * - `BatchReceiptCard` loses `header="own"` -> "one toggle, one place, inside
 *   the card". The wrapper then draws a second header outside the receipt's
 *   border, which is the owner's report.
 *
 * The owner's feedback the same day: the "Roll up" control sat above the
 * open card at the right, and the folded card's chevron at the left. Now the
 * card's own title row is the one toggle, with the chevron at its left in
 * both states. The browser twin is `e2e/chat-card-rollup.spec.ts`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as rawElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GenerativeUINode, { genUiRootIsCard } from "@/components/GenerativeUINode";
import RollupCard, { RollupContext, type RollupScope } from "@/components/RollupCard";
import ProjectToolCards from "@/components/projects/ProjectToolCards";
import { CARD_FRAME_CLASS, CollapsibleCard } from "@/components/ui/Collapsible";
import { HITL_TARGET, waitingTargets, type AskSources } from "@/lib/askPin";
import {
  LONG_PX,
  LONG_ROWS,
  RollupRegistry,
  TURN_ATTR,
  isLong,
  manualStateOf,
  resetManualStates,
  rollupOpen,
  setManualState,
  showRollupToggle,
  turnThenArrival,
} from "@/lib/cardRollup";
import { EMPTY_CONFIRMATIONS, confirmationReducer } from "@/lib/confirmationQueue";

/** `createElement` with children passed the way JSX passes them. */
const createElement = rawElement as unknown as (type: unknown, props: unknown, ...children: unknown[]) => ReactElement;

// A receipt's rows open a task through the router. No router runs here.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push() {} }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));

/** A registry ordered by a number: a later card has a bigger one. */
const byPosition = () => new RollupRegistry<number>((a, b) => a.node - b.node);

beforeEach(() => resetManualStates());

describe("what counts as long", () => {
  it("is a measure: taller than the line, or more rows than four", () => {
    expect(isLong(LONG_PX + 1)).toBe(true);
    expect(isLong(LONG_PX)).toBe(false);
    expect(isLong(0, LONG_ROWS + 1)).toBe(true);
    expect(isLong(120, LONG_ROWS)).toBe(false);
  });

  it("an unmeasured card (height 0) is not long", () => {
    expect(isLong(0)).toBe(false);
  });
});

describe("a long card rolls up when a newer card arrives", () => {
  it("the newest card stays open, and the older long one folds", () => {
    const reg = byPosition();
    reg.register("tool:t-batch", 1);
    expect(reg.newest()).toBe("tool:t-batch");
    const receipt = () =>
      rollupOpen({ pending: false, newest: reg.newest() === "tool:t-batch", long: isLong(0, 5) });
    expect(receipt()).toBe(true);

    reg.register("tool:t-upd", 2); // the newer card
    expect(reg.newest()).toBe("tool:t-upd");
    expect(receipt()).toBe(false);
  });

  it("orders by position, not by the order the cards mounted in", () => {
    const reg = byPosition();
    reg.register("late-in-the-page", 9);
    reg.register("mounted-last", 3);
    expect(reg.newest()).toBe("late-in-the-page");
  });

  it("inside one turn, the card that came last is the newest, though it draws higher", () => {
    // A turn draws its generative-UI cards above its receipts. A table that
    // came after a write must still be the newest (review round 1).
    const turn = { id: "m-1" };
    const el = (y: number) =>
      ({
        y,
        closest: (sel: string) => (sel === `[${TURN_ATTR}]` ? turn : null),
        compareDocumentPosition: () => 0,
      }) as unknown as Element;
    const reg = new RollupRegistry<Element>(turnThenArrival);
    reg.register("tool:t-batch", el(2)); // the receipt, lower in the turn
    reg.register("genui:m-1:0", el(1)); // the table, later in time
    expect(reg.newest()).toBe("genui:m-1:0");
  });

  it("across turns, the later turn wins, whatever arrived first", () => {
    const at = (turn: object, pos: number) =>
      ({
        pos,
        closest: () => turn,
        compareDocumentPosition(other: { pos: number }) {
          return other.pos < pos ? 2 : 4; // 2: the other one precedes this one
        },
      }) as unknown as Element;
    const reg = new RollupRegistry<Element>(turnThenArrival);
    reg.register("late-turn", at({ t: 2 }, 9));
    reg.register("early-turn", at({ t: 1 }, 1));
    expect(reg.newest()).toBe("late-turn");
  });

  it("tells its listeners only when the newest changes", () => {
    const reg = byPosition();
    let heard = 0;
    reg.subscribe(() => heard++);
    reg.register("a", 1);
    reg.register("b", 0); // older: the newest stays "a"
    reg.register("c", 2);
    reg.unregister("b");
    expect(heard).toBe(2);
    reg.unregister("c");
    expect(reg.newest()).toBe("a");
  });

  it("draws the folded card in the component: header, count and summary, shut", () => {
    const registry = new RollupRegistry<Element>(() => 0);
    // A newer card is already in the transcript.
    registry.register("tool:newer", {} as Element);
    const scope: RollupScope = { registry, waiting: new Set() };
    const html = renderToStaticMarkup(
      createElement(
        RollupContext.Provider,
        { value: scope },
        createElement(
          RollupCard,
          { id: "tool:t-batch", title: "Tasks created", rows: 5, summary: "#141 Wire the connector" },
          createElement("div", null, "the five rows"),
        ),
      ),
    );
    expect(html).toContain('data-rollup="closed"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Tasks created");
    expect(html).toContain("· 5");
    expect(html).toContain("#141 Wire the connector");
  });
});

// ── One toggle, one place, inside the card ─────────────────────────────────

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const RECEIPT = [
  "Created 5 tasks in «MCP & External».",
  ...[141, 142, 143, 144, 145].flatMap((n, i) => [
    `- #${n} «Wire the MCP connector step ${i + 1}» · Backlog`,
    `  full_id: ${uuid(n)}`,
  ]),
].join("\n");
const BATCH = { id: "t-batch", name: "create_tasks", status: "done", result: RECEIPT, args: {} };

/** A transcript in which a newer card exists, so the receipt is older. */
function inTranscript(child: ReactElement): string {
  const registry = new RollupRegistry<Element>(() => 0);
  registry.register("tool:newer", {} as Element);
  return renderToStaticMarkup(
    createElement(RollupContext.Provider, { value: { registry, waiting: new Set() } }, child),
  );
}

const receipt = () => createElement(ProjectToolCards, { toolEvents: [BATCH] });
/** How many times the text holds the words. */
const count = (html: string, words: string) => html.split(words).length - 1;
/** The toggle's own markup, from its tag to its close. A button holds no button. */
const toggleOf = (html: string) => /<button[^>]*data-rollup-toggle[^>]*>[\s\S]*?<\/button>/.exec(html)?.[0] ?? "";
/** The toggle's open tag. */
const toggleTag = (html: string) => /<button[^>]*data-rollup-toggle[^>]*>/.exec(html)?.[0] ?? "";
/** The receipt's own border: `toneFor`'s frame. */
const RECEIPT_FRAME = /<div class="rounded-lg border px-2\.5 py-2 [^"]*">/;
const TITLED_CARD = {
  type: "card",
  props: { title: "Pipeline" },
  children: [{ type: "text", props: { text: "rows" } }],
};

describe("one toggle, one place, inside the card", () => {
  for (const state of ["closed", "open"] as const) {
    it(`a ${state} "Tasks created" receipt has ONE toggle, and it is the first row inside its border`, () => {
      if (state === "open") setManualState("tool:t-batch", "open");
      const html = inTranscript(receipt());
      expect(html).toContain(`data-rollup="${state}"`);
      expect(count(html, "data-rollup-toggle"), "one toggle per card").toBe(1);
      expect(count(html, "aria-expanded="), "one control that folds").toBe(1);
      // The receipt's border opens, and its first child is the toggle.
      const frame = RECEIPT_FRAME.exec(html);
      expect(frame, "the receipt draws its own border").not.toBeNull();
      expect(html.slice(frame!.index + frame![0].length)).toMatch(/^<button[^>]*data-rollup-toggle/);
      // The title draws once: there is no second header above the card.
      expect(count(html, ">Tasks created<"), "one title").toBe(1);
      expect(toggleOf(html)).toContain(">Tasks created<");
      expect(html).not.toContain("Roll up");
    });
  }

  it("the chevron is the toggle's first child in both states, and only its turn changes", () => {
    const closed = toggleOf(inTranscript(receipt()));
    setManualState("tool:t-batch", "open");
    const open = toggleOf(inTranscript(receipt()));
    const chevron = /^<button[^>]*><span data-rollup-chevron="" aria-hidden="true" class="([^"]*)">/;
    const shut = chevron.exec(closed);
    const opened = chevron.exec(open);
    expect(shut, "shut: the chevron leads the row").not.toBeNull();
    expect(opened, "open: the chevron leads the row").not.toBeNull();
    expect(count(closed, "data-rollup-chevron")).toBe(1);
    expect(shut![1]).toContain("-rotate-90");
    expect(opened![1]).not.toContain("-rotate-90");
    expect(shut![1].replace("-rotate-90", "").trim()).toBe(opened![1].trim());
    expect(shut![1]).toContain("motion-reduce:transition-none");
  });

  it("shut, the row adds the summary; open, it does not", () => {
    const closed = toggleOf(inTranscript(receipt()));
    expect(closed).toContain("· 5");
    expect(closed).toContain("#141 Wire the MCP connector step 1");
    setManualState("tool:t-batch", "open");
    const open = toggleOf(inTranscript(receipt()));
    expect(open).toContain("· 5");
    expect(open).not.toContain("#141");
  });

  for (const state of ["closed", "open"] as const) {
    it(`${state}, the toggle names the panel, and the rows' links sit in the panel, not in the toggle`, () => {
      if (state === "open") setManualState("tool:t-batch", "open");
      const html = inTranscript(receipt());
      const controls = /aria-controls="([^"]+)"/.exec(toggleTag(html))?.[1];
      expect(controls, "aria-controls").toBeTruthy();
      expect(html).toContain(`id="${controls}"`);
      expect(toggleOf(html)).not.toContain("Open in Projects");
      const panel = html.slice(html.indexOf(`id="${controls}"`));
      expect(count(panel, 'aria-label="Open in Projects"')).toBe(5);
    });
  }

  it("outside a transcript, the receipt keeps its plain title row and has no toggle", () => {
    const html = renderToStaticMarkup(receipt());
    expect(html).not.toContain("data-rollup-toggle");
    expect(html).not.toContain("aria-expanded");
    expect(count(html, ">Tasks created<")).toBe(1);
  });

  it("a titled generative-UI card hands its own title row to the toggle", () => {
    const html = inTranscript(
      createElement(RollupCard, { id: "genui:m-1:0", title: "Pipeline", rows: 9, header: "own" },
        createElement(GenerativeUINode, { spec: TITLED_CARD })),
    );
    expect(count(html, "data-rollup-toggle")).toBe(1);
    expect(count(html, ">Pipeline<")).toBe(1);
    expect(html).toMatch(/<div class="rounded-lg border border-border\/60 bg-card\/50 p-3"><button[^>]*data-rollup-toggle/);
  });

  it("MessageBubble hands the toggle to a root card, and only to a root card", () => {
    expect(genUiRootIsCard(TITLED_CARD)).toBe(true);
    expect(genUiRootIsCard({ root: TITLED_CARD })).toBe(true);
    expect(genUiRootIsCard({ type: "table", props: {} })).toBe(false);
    const src = readFileSync(fileURLToPath(new URL("../components/MessageBubble.tsx", import.meta.url)), "utf8");
    expect(src).toContain('const header = genUiRootIsCard(spec) ? "own" : "frame";');
    expect(count(src, "header={header}")).toBe(2);
  });

  it("a card with no title row gets a frame, and the toggle is its first row", () => {
    const html = inTranscript(
      createElement(RollupCard, { id: "genui:m-1:1", title: "Table", rows: 9 }, createElement("table", null)),
    );
    expect(count(html, "data-rollup-toggle")).toBe(1);
    expect(html).toContain(`<div data-rollup-frame="" class="${CARD_FRAME_CLASS}"><button`);
  });

  it("a generative-UI card in the side panel (no chat card) keeps its title", () => {
    const html = renderToStaticMarkup(createElement(GenerativeUINode, { spec: TITLED_CARD }));
    expect(html).toContain(">Pipeline<");
    expect(html).not.toContain("<button");
  });
});

describe("a short card has no toggle", () => {
  it("by the rule", () => {
    expect(showRollupToggle({ pending: false, long: false })).toBe(false);
    expect(rollupOpen({ pending: false, newest: false, long: false })).toBe(true);
  });

  it("in the primitive: no button, and the body shows", () => {
    const html = renderToStaticMarkup(
      createElement(CollapsibleCard, { open: false, onOpenChange: () => {}, toggle: false, label: "Not done" },
        createElement("p", null, "Nothing was created."),
      ),
    );
    expect(html).not.toContain("data-rollup-toggle");
    expect(html).not.toContain("<button");
    expect(html).toContain('data-rollup="open"');
    expect(html).toContain("Nothing was created.");
  });

  it("outside a transcript, a card draws as it always did", () => {
    const html = renderToStaticMarkup(
      createElement(RollupCard, { id: "x", title: "Tasks created", rows: 9 }, createElement("p", null, "body")),
    );
    expect(html).toBe("<p>body</p>");
  });
});

describe("a pending approval card never rolls up", () => {
  it("not when it is older, long, or closed by hand", () => {
    expect(rollupOpen({ pending: true, newest: false, long: true })).toBe(true);
    expect(rollupOpen({ pending: true, newest: false, long: true, manual: "closed" })).toBe(true);
    expect(showRollupToggle({ pending: true, long: true })).toBe(false);
  });

  it("the pin's sources say what waits: a queued approval is the inline group", () => {
    const confirmations = confirmationReducer(EMPTY_CONFIRMATIONS, {
      type: "requested",
      value: { request_id: "req-c1", title: "Create 5 tasks in MCP & External" },
    }).cards;
    const src: AskSources = {
      confirmations,
      elicitation: null,
      userInput: null,
      messages: [],
      runActive: true,
      answered: new Set(),
    };
    expect(waitingTargets(src).has(HITL_TARGET)).toBe(true);
    expect(waitingTargets({ ...src, confirmations: [] }).size).toBe(0);
  });

  it("a waiting generative-UI ask draws open, with no toggle, though a newer card exists", () => {
    const registry = new RollupRegistry<Element>(() => 0);
    registry.register("tool:newer", {} as Element);
    const scope: RollupScope = { registry, waiting: new Set(["genui:req-plan"]) };
    const html = renderToStaticMarkup(
      createElement(
        RollupContext.Provider,
        { value: scope },
        createElement(
          RollupCard,
          { id: "genui:req-plan", title: "Plan", rows: 12, askTarget: "genui:req-plan" },
          createElement("div", null, "plan rows"),
        ),
      ),
    );
    expect(html).toContain('data-rollup="open"');
    expect(html).not.toContain("data-rollup-toggle");
  });

  it("the approval group is a card of the transcript, and it waits", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/AgentChat.tsx", import.meta.url)), "utf8");
    expect(src).toMatch(/<RollupCard id=\{HITL_TARGET\}[^>]*\bpending\b/);
    expect(src).toContain("<RollupContext.Provider value={rollupScope}>");
  });
});

describe("a manual open beats the automatic rule", () => {
  it("open by hand keeps an older long card open, and a close by hand shuts the newest", () => {
    expect(rollupOpen({ pending: false, newest: false, long: true, manual: "open" })).toBe(true);
    expect(rollupOpen({ pending: false, newest: true, long: true, manual: "closed" })).toBe(false);
  });

  it("an automatic fold waits while the focus is inside the card, and a toggle by hand does not", () => {
    expect(rollupOpen({ pending: false, newest: false, long: true, held: true })).toBe(true);
    expect(rollupOpen({ pending: false, newest: false, long: true, held: true, manual: "closed" })).toBe(false);
  });

  it("focus on a shut card's header holds it shut: only the click opens it", () => {
    // A mouse press focuses the header before the click. If the focus opened
    // the card, the click would land on an open card and shut it again.
    expect(rollupOpen({ pending: false, newest: false, long: true, held: false })).toBe(false);
    expect(rollupOpen({ pending: false, newest: true, long: true, held: false })).toBe(false);
    expect(rollupOpen({ pending: false, newest: false, long: true, held: false, manual: "open" })).toBe(true);
  });

  it("the choice lives for the page's life, by the card's id", () => {
    setManualState("tool:t-batch", "open");
    expect(manualStateOf("tool:t-batch")).toBe("open");
    expect(manualStateOf("tool:other")).toBeUndefined();
    const registry = new RollupRegistry<Element>(() => 0);
    registry.register("tool:newer", {} as Element);
    // A remount (a replay) reads the choice back.
    const html = renderToStaticMarkup(
      createElement(
        RollupContext.Provider,
        { value: { registry, waiting: new Set() } },
        createElement(RollupCard, { id: "tool:t-batch", title: "Tasks created", rows: 5 }, "rows"),
      ),
    );
    expect(html).toContain('data-rollup="open"');
    expect(html).toContain('aria-expanded="true"');
  });
});
