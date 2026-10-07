/**
 * The thinking trail sizes to its steps, and its icons share one column.
 *
 * The owner's screenshot (2026-10-07, the Projects rail) showed two steps
 * above an empty area three times their height, and a header icon right of
 * the step icons. `lib/trailLayout.ts` holds why.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ThinkingContainer from "@/components/ThinkingContainer";
import type { ToolEvent } from "@/components/MarkdownMessage";
import {
  TRAIL_AXIS,
  TRAIL_ICON_CELL,
  horizontalInsets,
  trailBodySize,
} from "@/lib/trailLayout";

function steps(n: number, active: boolean): ToolEvent[] {
  return Array.from({ length: n }, (_, i) => {
    const running = active && i === n - 1;
    return {
      id: `t${i}`,
      name: i % 2 ? "emit_generative_ui" : "vocabulary",
      status: running ? "running" : "done",
      startedAt: 10_000 + 1000 * i,
      endedAt: running ? undefined : 10_000 + 1000 * i + 1300,
    } as ToolEvent;
  });
}

/** Render the trail as a member first sees it: open while the turn runs. */
function render(n: number, active: boolean, reasoning?: string[]): string {
  return renderToStaticMarkup(
    createElement(ThinkingContainer, {
      toolEvents: steps(n, active),
      progressLines: [],
      reasoningBlocks: reasoning,
      isActive: active,
    }),
  );
}

/** The class attribute of every element carrying `attr`. */
function classesOf(html: string, attr: string): string[] {
  const re = new RegExp(`<[a-z]+[^>]*\\b${attr}="[^"]*"[^>]*>`, "g");
  return (html.match(re) ?? []).map((tag) => /class="([^"]*)"/.exec(tag)?.[1] ?? "");
}

describe("the trail body takes a cap, never a height", () => {
  it.each([true, false])("trailBodySize(%s) is a max-height only", (active) => {
    const cls = trailBodySize(active).split(/\s+/);
    expect(cls.some((c) => c.startsWith("max-h-"))).toBe(true);
    // `h-56` was the defect: a fixed window under two steps.
    expect(cls.filter((c) => /^(min-)?h-/.test(c))).toEqual([]);
  });

  it.each([1, 2, 5, 20])("a running trail with %i steps renders no fixed height", (n) => {
    for (const reasoning of [undefined, ["Read the vocabulary first."]]) {
      const [body] = classesOf(render(n, true, reasoning), "data-trail-body");
      expect(body).toBeDefined();
      expect(body.split(/\s+/).filter((c) => /^(min-)?h-/.test(c))).toEqual([]);
      expect(body).toContain("overflow-y-auto");
      expect(body).toContain(trailBodySize(true));
    }
  });

  it("a long trail scrolls inside its cap, and the finished cap leaves the answer in view", () => {
    expect(trailBodySize(true)).toBe("max-h-56");
    expect(trailBodySize(false)).toMatch(/^max-h-\[min\(32rem,60vh\)\]$/);
  });
});

describe("the header icon and every step icon share one column", () => {
  it("the axis line runs down the centre of the icon column", () => {
    const col = Number(/\bw-(\d+)\b/.exec(TRAIL_ICON_CELL)?.[1]);
    const axis = Number(/^left-(\d+)$/.exec(TRAIL_AXIS)?.[1]);
    expect(col).toBeGreaterThan(0);
    expect(axis * 2).toBe(col);
  });

  it.each([1, 2, 5, 20])("with %i steps, every icon sits in the same cell", (n) => {
    const html = render(n, true, ["Read the vocabulary first."]);
    const cells = classesOf(html, "data-trail-icon");
    // The header, one per step, and one for the reasoning entry.
    expect(cells.length).toBe(n + 2);
    for (const c of cells) expect(c.startsWith(TRAIL_ICON_CELL)).toBe(true);
    for (const c of cells) expect(horizontalInsets(c)).toEqual([]);
  });

  it("neither the header nor a step row insets its icon from the left", () => {
    const html = render(2, true);
    const [head] = classesOf(html, "data-trail-head");
    // A `px-3` on the header moved its icon 12px right of the step icons.
    expect(head).toBeDefined();
    expect(horizontalInsets(head)).toEqual([]);
    const rows = classesOf(html, "data-step-status");
    expect(rows.length).toBe(2);
    for (const r of rows) expect(horizontalInsets(r)).toEqual([]);
    const [body] = classesOf(html, "data-trail-body");
    expect(horizontalInsets(body)).toEqual([]);
  });
});

describe("a step duration is the UI font with tabular figures", () => {
  it("in the rows and in the finished header", () => {
    const done = renderToStaticMarkup(
      createElement(ThinkingContainer, {
        toolEvents: steps(2, false),
        progressLines: [],
        isActive: false,
      }),
    );
    const durations = [...classesOf(render(3, true), "data-step-duration"), ...classesOf(done, "data-step-duration")];
    expect(durations.length).toBeGreaterThanOrEqual(3);
    for (const d of durations) {
      expect(d).toContain("tabular-nums");
      expect(d).toContain("text-muted-foreground");
      expect(d).not.toContain("font-mono");
    }
  });
});
