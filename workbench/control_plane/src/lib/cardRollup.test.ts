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
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as rawElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it } from "vitest";

import RollupCard, { RollupContext, type RollupScope } from "@/components/RollupCard";
import { CollapsibleCard } from "@/components/ui/Collapsible";
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
    expect(rollupOpen({ pending: false, newest: false, long: true, focused: true })).toBe(true);
    expect(rollupOpen({ pending: false, newest: false, long: true, focused: true, manual: "closed" })).toBe(false);
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
