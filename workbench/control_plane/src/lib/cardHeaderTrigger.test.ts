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
 */
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), usePathname: () => "/chat" }));

import GenerativeUINode from "@/components/GenerativeUINode";
import RollupCard, { RollupContext } from "@/components/RollupCard";
import { OWN_HEAD_TEMPLATES } from "@/components/genUITemplates";
import { RollupRegistry } from "@/lib/cardRollup";
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

  it("plainTitle keeps the words and drops the marks", () => {
    expect(plainTitle(LINKED)).toBe("See the board for Ops");
    expect(plainTitle("**Bold** and `code`")).toBe("Bold and code");
  });
});
