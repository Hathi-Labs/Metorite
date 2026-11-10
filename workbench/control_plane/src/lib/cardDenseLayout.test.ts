/**
 * The dense chat card (owner feedback, 2026-10-10, a screenshot in dark
 * mode): "space is wasted on the left-hand side because of the toggle".
 *
 * Before: the body of a receipt lined up under the TITLE, past the chevron
 * and the icon, so each row started about 60 px in. Each task row took two
 * lines, "#141 title" over "status Backlog".
 *
 * Now:
 *
 * - the body starts at the card's content padding, in line with the
 *   chevron, for every card that folds (`ui/Collapsible.tsx`);
 * - a task row is ONE line: `#141`, the title (it truncates, and the whole
 *   text stays in the row), the status as its pill, the open icon;
 * - the pill is the shared `StatusChip`, in the hue `statusAccent` gives,
 *   named "Status: Backlog" for a screen reader, with no "status " word;
 * - a receipt whose body is one short line ("Nothing was created.") draws
 *   that line on its title row, so the card is one row.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the row draws the "status " word again (`TaskRowView` uses
 *   `taskMetaForPeople(row.meta)` in place of `facts.rest`) -> "a receipt
 *   row is one line" (run 2026-10-10: red);
 * - the body box takes `pl-[calc(25px+1rem)]` again -> "the body has no
 *   hanging indent" (run 2026-10-10: red).
 *
 * The browser twin is the visual review in the PR body.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement as rawElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GenerativeUINode from "@/components/GenerativeUINode";
import RollupCard, { RollupContext } from "@/components/RollupCard";
import ProjectToolCards, { LIST_ROW_BOX, LIST_ROWS_BOX, receiptLead } from "@/components/projects/ProjectToolCards";
import { RollupRegistry, resetManualStates, setManualState } from "@/lib/cardRollup";
import { statusAccent } from "@/lib/statusAccent";

const createElement = rawElement as unknown as (type: unknown, props: unknown, ...children: unknown[]) => ReactElement;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push() {} }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));

beforeEach(() => resetManualStates());

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const LONG = "Design the MCP connection architecture for Metorite, with every external server";
const STATUSES = ["«Backlog» · backlog", "«In progress» · in_progress", "«Done» · done", "«Backlog» · backlog", "«Backlog»"];
const RECEIPT = [
  "Created 5 tasks in «MCP & External».",
  ...[141, 142, 143, 144, 145].flatMap((n, i) => [
    `- #${n} «${i === 0 ? LONG : `Wire the MCP connector step ${i + 1}`}» · status ${STATUSES[i]}`,
    `  full_id: ${uuid(n)}`,
  ]),
].join("\n");
const BATCH = { id: "t-batch", name: "create_tasks", status: "done", result: RECEIPT, args: {} };
const UPDATED = {
  id: "t-upd",
  name: "update_task",
  status: "done",
  args: {},
  result: `Updated:\n- #146 «Write the docs» · status «In progress» · in_progress · due 2026-10-12\n  full_id: ${uuid(146)}`,
};

function inTranscript(child: ReactElement): string {
  const registry = new RollupRegistry<Element>(() => 0);
  registry.register("tool:newer", {} as Element);
  return renderToStaticMarkup(
    createElement(RollupContext.Provider, { value: { registry, waiting: new Set() } }, child),
  );
}

const receipt = (events: unknown[] = [BATCH]) => createElement(ProjectToolCards, { toolEvents: events });

/** Each task row's markup, from its open tag to the next row. */
function rowsOf(html: string): string[] {
  return html.split("<div data-task-row=").slice(1);
}

describe("the body has no hanging indent", () => {
  it("the fold primitive has no indent to give", () => {
    const src = readFileSync(fileURLToPath(new URL("../components/ui/Collapsible.tsx", import.meta.url)), "utf8");
    expect(src).not.toMatch(/BODY_INDENT\s*=/);
    expect(src).not.toMatch(/indent\?:\s*boolean/);
    const cards = readFileSync(
      fileURLToPath(new URL("../components/projects/ProjectToolCards.tsx", import.meta.url)),
      "utf8",
    );
    expect(cards).not.toContain("<RollupBody indent");
  });

  for (const state of ["closed", "open"] as const) {
    it(`a ${state} receipt, a generative card and a framed card draw no title-aligned padding`, () => {
      if (state === "open") {
        setManualState("tool:t-batch", "open");
        setManualState("genui:m-1:0", "open");
        setManualState("genui:m-1:1", "open");
      }
      const html = [
        inTranscript(receipt()),
        inTranscript(
          createElement(RollupCard, { id: "genui:m-1:0", title: "Pipeline", rows: 9, header: "own" },
            createElement(GenerativeUINode, { spec: { type: "card", props: { title: "Pipeline" }, children: [] } })),
        ),
        inTranscript(createElement(RollupCard, { id: "genui:m-1:1", title: "Table", rows: 9 }, createElement("table", null))),
        renderToStaticMarkup(receipt()),
      ].join("");
      expect(html).not.toMatch(/pl-\[calc\(/);
    });
  }

  it("the rows reach the card's content edge: the list bleeds by the unit its rows pad back", () => {
    expect(LIST_ROWS_BOX).toContain("-mx-1");
    expect(LIST_ROW_BOX).toContain("px-1");
    const html = inTranscript(receipt());
    expect(html).toContain(LIST_ROWS_BOX);
  });
});

describe("a receipt row is one line", () => {
  it("draws the status as its pill, named for a screen reader, with no 'status ' word", () => {
    const rows = rowsOf(inTranscript(receipt()));
    expect(rows).toHaveLength(5);
    for (const row of rows) {
      expect(row).toMatch(/role="img" aria-label="Status: (Backlog|In progress|Done)"/);
      // The visible text holds no "status " word: tags removed, then read.
      const text = row.replace(/<[^>]*>/g, " ");
      expect(text).not.toMatch(/\bstatus\b/i);
    }
  });

  it("one line, not two: no block span under the title", () => {
    const [row] = rowsOf(inTranscript(receipt()));
    expect(row).not.toContain('class="block');
    expect(count(row, "data-task-row-title")).toBe(1);
  });

  it("the pill takes the lane's hue from statusAccent, never the member's accent", () => {
    const rows = rowsOf(inTranscript(receipt()));
    const inProgress = statusAccent({ category: "in_progress", name: "In progress" });
    const done = statusAccent({ category: "done", name: "Done" });
    expect(rows[1]).toContain(`${inProgress.soft} ${inProgress.text}`);
    expect(rows[2]).toContain(`${done.soft} ${done.text}`);
    for (const row of rows) expect(row).not.toMatch(/(bg|text)-primary/);
  });

  it("a long title truncates, and its whole text stays in the row", () => {
    const [row] = rowsOf(inTranscript(receipt()));
    const title = /<span[^>]*data-task-row-title=""[^>]*class="([^"]*)"[^>]*>([\s\S]*?)<\/span><span role="img"/.exec(row);
    expect(title, "the title box").not.toBeNull();
    expect(title![1]).toContain("truncate");
    expect(title![1]).toContain("min-w-0");
    // The text is whole: CSS cuts it, so the reader and the tip get all of it.
    expect(title![2].replace(/<[^>]*>/g, "")).toContain(LONG);
  });

  it("the pill wraps under the title only when it cannot fit, and the icon stays on the first line", () => {
    const [row] = rowsOf(inTranscript(receipt()));
    // The title and the pill share one wrapping box; the title asks for 60 %.
    const box = /<button type="button" class="([^"]*)"/.exec(row)?.[1] ?? "";
    expect(box).toContain("flex-wrap");
    expect(row).toMatch(/data-task-row-title="" class="[^"]*basis-3\/5/);
    // The open icon is outside that box.
    expect(row).toMatch(/<\/button><button[^>]*aria-label="Open in Projects"/);
  });

  it("a row keeps a 40 px target on a touch screen", () => {
    const [row] = rowsOf(inTranscript(receipt()));
    expect(count(row, "pointer-coarse:min-h-[40px]")).toBe(2);
  });
});

describe("the other receipts that list rows", () => {
  it("a task update draws its task as the same one-line row, with no second link", () => {
    const html = inTranscript(receipt([UPDATED]));
    const rows = rowsOf(html);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain('aria-label="Status: In progress"');
    expect(rows[0]).toContain("due 2026-10-12");
    // "Updated:" restates the title, and the row is the link.
    expect(html).not.toContain(">Updated:<");
    expect(count(html, "Open in Projects")).toBe(2); // the icon's title and its name
  });

  it("the lead line stays when it says more than the title", () => {
    expect(receiptLead("Updated:\n- #1 «a»\n  full_id: x")).toBe("");
    expect(receiptLead("- #5 «Fix» · status «To do»\n  full_id: x")).toBe("");
    expect(receiptLead("Added to «Backlog» under #12 «Parent»:")).toBe("Added to «Backlog» under #12 «Parent»:");
  });

  it("'Nothing was created' is one row: the line sits on the title row", () => {
    const html = inTranscript(receipt([{ ...BATCH, id: "t-none", result: "Nothing was created." }]));
    expect(count(html, "Nothing was created.")).toBe(1);
    expect(html).toMatch(/>Not done<\/span><\/span><span data-rollup-aside="" [^>]*>Nothing was created\.<\/span>/);
    expect(html).not.toMatch(/pl-\[calc\(/);
  });

  it("a longer line, or several, stays in the body, where it wraps", () => {
    const long = `Row 3 has no title. ${"Every task needs one. ".repeat(6)}Nothing was created.`;
    const html = inTranscript(receipt([{ ...BATCH, id: "t-long", result: long }]));
    expect(html).not.toContain("data-rollup-aside");
    expect(html).toContain("Row 3 has no title.");
  });
});

const count = (html: string, words: string) => html.split(words).length - 1;
