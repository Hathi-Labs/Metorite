/**
 * The chat's card policy (spec `projects_ai_chat.md` §29, owner 2026-10-19).
 *
 * The owner asked where the chat's generative-UI cards should appear, and
 * when they can close. Every card is one of five families, and each family
 * has one rule. This file holds one test group per rule, and the catalog
 * check: every template names its family.
 *
 * Mutations that each make this file fail:
 * - drop `answered` from `rollupOpen` -> rule 1 "folds once answered";
 * - drop `summary={askOutcome(…)}` in MessageBubble -> rule 1 source fence;
 * - drop `clamp` from the result card in MessageBubble -> rule 2;
 * - drop a `family` from TEMPLATE_CATALOG, or mark planCard a result ->
 *   "every template names its family".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as rawElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import RollupCard, { RollupContext } from "@/components/RollupCard";
import { TEMPLATE_CATALOG, TEMPLATE_REGISTRY } from "@/components/genUITemplates";
import { CLAMP_PX, RollupRegistry, UNCUT_PX, clampShows, rollupOpen } from "@/lib/cardRollup";
import {
  ASK_TEMPLATES,
  CARD_FAMILIES,
  PLACEMENT,
  familyOf,
  genUiFamily,
  genUiFlow,
} from "@/lib/chatPlacement";

const createElement = rawElement as unknown as (type: unknown, props: unknown, ...children: unknown[]) => ReactElement;
const source = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8").replace(/\r\n/g, "\n");
const template = (name: string, data: Record<string, unknown> = {}) => ({ type: "template", props: { name, data } });

describe("every template names its family", () => {
  it("is a family of the five, and WAITS exactly for the templates the member sends", () => {
    for (const t of TEMPLATE_CATALOG) {
      expect(CARD_FAMILIES, t.name).toContain(t.family);
      expect(t.family === "waits", t.name).toBe(ASK_TEMPLATES.has(t.name));
      expect(genUiFamily(template(t.name)), t.name).toBe(t.family);
    }
  });

  it("has one entry per template the chat can draw", () => {
    expect(TEMPLATE_CATALOG.map((t) => t.name).sort()).toEqual(Object.keys(TEMPLATE_REGISTRY).sort());
  });
});

describe("rule 1 — a card that waits folds to its outcome once answered", () => {
  it("never folds while it waits, and folds once answered, even as the newest or with the focus in it", () => {
    expect(rollupOpen({ pending: true, answered: false, newest: true, long: true })).toBe(true);
    expect(rollupOpen({ pending: false, answered: true, newest: true, long: false })).toBe(false);
    expect(rollupOpen({ pending: false, answered: true, newest: true, long: false, held: true })).toBe(false);
    // The member may open it again by hand.
    expect(rollupOpen({ pending: false, answered: true, manual: "open", newest: false, long: true })).toBe(true);
  });

  it("draws the folded header with the outcome, never a bare title", () => {
    const registry = new RollupRegistry<Element>(() => 0);
    const html = renderToStaticMarkup(
      createElement(
        RollupContext.Provider,
        { value: { registry, waiting: new Set() } },
        // `rows` stands in for the body the browser measures, so the toggle draws.
        createElement(RollupCard, { id: "genui:p", title: "Plan", rows: 1, answered: true, summary: "19 tasks sent for approval" },
          createElement("p", null, "body")),
      ),
    );
    expect(html).toContain("19 tasks sent for approval");
    expect(html).toContain('aria-expanded="false"');
  });

  it("the chat hands every waiting generative-UI card its answer and its outcome", () => {
    const bubble = source("../components/MessageBubble.tsx");
    expect(bubble).toMatch(/answered=\{answered !== undefined\} summary=\{askOutcome\(spec, answered\) \|\| undefined\}/);
  });

  it("a form that has its answer stays locked after a remount, and shows the values it sent", () => {
    const html = renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY.formCard(
      { title: "When", fields: [{ name: "due", label: "Due", type: "text", value: "Monday" }] },
      { onAction: () => {}, answered: 'Save — {"due":"Friday"}' },
    ), null));
    expect(html).toContain('value="Friday"');
    expect(html).not.toContain('value="Monday"');
    expect(html).toMatch(/<input[^>]*disabled=""[^>]*value="Friday"|<input[^>]*value="Friday"[^>]*disabled=""/);
  });
});

describe("rule 2 — a result card sits at its place in the text, folds, and never closes", () => {
  it("draws between the text that came before it and the text that came after it", () => {
    const flow = genUiFlow([{ text: "Here is the open work." }, { text: "Should I link them?" }], [1]);
    expect(flow).toEqual([
      { kind: "text", text: "Here is the open work." },
      { kind: "cards", indexes: [0] },
      { kind: "text", text: "Should I link them?" },
    ]);
  });

  it("opens cut past the cap, with Show all, and only a result card does", () => {
    const tall = CLAMP_PX + 1;
    const base = { clamp: true, pending: false, all: false, wasCut: false };
    expect(clampShows({ ...base, naturalPx: tall })).toBe(true);
    expect(clampShows({ ...base, naturalPx: CLAMP_PX })).toBe(false);
    expect(clampShows({ ...base, naturalPx: tall, all: true })).toBe(false);
    expect(clampShows({ ...base, naturalPx: tall, pending: true })).toBe(false);
    expect(clampShows({ ...base, naturalPx: tall, clamp: false })).toBe(false);
    // An unmeasured body never cuts.
    expect(clampShows({ ...base, naturalPx: 0 })).toBe(false);
  });

  it("stays cut once cut, so a cut body that measures at the cap never loops (review of #1160)", () => {
    const base = { clamp: true, pending: false, all: false };
    // The cut body measures exactly the cap, or less with the padding left out.
    expect(clampShows({ ...base, wasCut: true, naturalPx: CLAMP_PX })).toBe(true);
    expect(clampShows({ ...base, wasCut: true, naturalPx: CLAMP_PX - 6 })).toBe(true);
    // "Show all" uncuts it.
    expect(clampShows({ ...base, wasCut: true, naturalPx: CLAMP_PX + 200, all: true })).toBe(false);
  });

  it("uncuts when the content shrinks under the fade, and only then, so it cannot loop", () => {
    const base = { clamp: true, pending: false, all: false, wasCut: true };
    expect(clampShows({ ...base, naturalPx: UNCUT_PX + 1 })).toBe(true);
    expect(clampShows({ ...base, naturalPx: UNCUT_PX })).toBe(false);
    expect(clampShows({ ...base, naturalPx: 120 })).toBe(false);
    // An uncut body at that height does not cut again.
    expect(clampShows({ ...base, wasCut: false, naturalPx: UNCUT_PX })).toBe(false);
    // A lost measure keeps the cut.
    expect(clampShows({ ...base, naturalPx: 0 })).toBe(true);
  });

  it("keeps one wrapper in the tree, so a cut never remounts the card's body", () => {
    const card = source("../components/RollupCard.tsx");
    // The wrapper's presence follows the `clamp` prop only, never the cut.
    expect(card).toContain("{clamp ? (");
    expect(card).not.toMatch(/\{cut \? \(/);
    expect(card).toContain('data-rollup-cut={shownCut ? "on" : "off"}');
  });

  it("the chat cuts its result cards, and no result card carries a close", () => {
    const bubble = source("../components/MessageBubble.tsx");
    expect(bubble).toMatch(/<RollupCard key=\{i\} id=\{target\} title=\{title\} icon=\{icon\} header=\{header\} clamp>/);
    const tasks = source("../components/tasks/TaskToolCards.tsx");
    const at = tasks.indexOf("function PlanResultCard");
    const plan = tasks.slice(at, tasks.indexOf("\nfunction ", at + 1));
    expect(plan).not.toContain("onDismiss");
    const email = source("../components/email/EmailToolCards.tsx");
    expect(email).toContain("items.push(<EmailGroupsCard key={e.id} event={e} />);");
  });
});

describe("rule 3 — a write is a receipt", () => {
  it("names every write tool a receipt, and every read a result in its step", () => {
    for (const [name, p] of Object.entries(PLACEMENT)) {
      if (p === "write") expect(familyOf(name), name).toBe("receipt");
      if (p === "evidence") expect(familyOf(name), name).toBe("result");
      if (p === "ask") expect(familyOf(name), name).toBe("waits");
    }
  });
});

describe("rule 4 — a notice is a slim line the member dismisses", () => {
  it("keeps its dismiss, stored by its key", () => {
    const card = source("../components/ChatErrorCard.tsx");
    expect(card).toContain("dismissKey");
    expect(card).toMatch(/Dismiss/);
  });
});

describe("rule 5 — progress lives in the step area, and has no card", () => {
  it("gives the to-do list no card family", () => {
    expect(familyOf("manage_todo_list")).toBeUndefined();
  });
});
