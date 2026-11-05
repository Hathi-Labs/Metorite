/**
 * TC-1 (D172.4, 2026-11-03): a task card never covers the assistant. This
 * fence pins the layer order in `lib/layers.ts` and checks that the card
 * files take their layer from it.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { LAYER, LAYER_CLASS, TASK_CARD_FRAME } from "@/lib/layers";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf-8");

describe("the layer scale", () => {
  it("keeps every class string in step with its number", () => {
    for (const key of Object.keys(LAYER) as (keyof typeof LAYER)[]) {
      expect([`z-[${LAYER[key]}]`, `z-${LAYER[key]}`]).toContain(LAYER_CLASS[key]);
    }
  });

  it("puts the pill, the bottom bar and the assistant above the task card", () => {
    expect(LAYER.assistantDock).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.askBar).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.peekStrip).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.bottomNav).toBeGreaterThan(LAYER.taskCard);
    expect(LAYER.assistantSheet).toBeGreaterThan(LAYER.taskCard);
  });

  it("draws the card frames at the card layer", () => {
    expect(TASK_CARD_FRAME.desktop).toContain(LAYER_CLASS.taskCard);
    expect(TASK_CARD_FRAME.phone).toContain(LAYER_CLASS.taskCard);
  });

  it("keeps the desktop card in the page column and the phone card above the nav", () => {
    expect(TASK_CARD_FRAME.desktop).toMatch(/\babsolute\b/);
    expect(TASK_CARD_FRAME.desktop).not.toMatch(/\bfixed\b/);
    expect(TASK_CARD_FRAME.phone).toContain("task-card-phone");
    expect(TASK_CARD_FRAME.phone).not.toContain("bottom-[calc(");
  });
});

describe("the surfaces take their layer from the scale", () => {
  it("has no ad-hoc card layer in Projects or the task overlay", () => {
    const projects = read("app/projects/page.tsx");
    expect(projects).toContain("TASK_CARD_FRAME.phone");
    expect(projects).toContain("TASK_CARD_FRAME.desktop");
    expect(projects).not.toContain("fixed inset-0 z-[60]");
    const modal = read("app/tasks/components/TaskFocusModal.tsx");
    expect(modal).toContain("TASK_CARD_FRAME");
    expect(modal).not.toContain("z-[80]");
  });

  it("draws the shell parts from the scale", () => {
    expect(read("components/AppShell.tsx")).toContain("LAYER_CLASS.bottomNav");
    expect(read("lib/shell/ChatBar.tsx")).toContain("LAYER_CLASS.askBar");
    const dock = read("lib/shell/AssistantDock.tsx");
    expect(dock).toContain("LAYER_CLASS.assistantSheet");
    expect(dock).toContain("LAYER_CLASS.peekStrip");
    expect(dock).toContain("LAYER_CLASS.assistantDock");
  });
});

describe("a page's own layers cannot rise over the task card (TC-1 fix)", () => {
  it("contains the /tasks list scrollers and the Calendar grid in their own stacking context", () => {
    for (const f of [
      "app/tasks/components/TaskListGrouped.tsx",
      "app/tasks/components/EngageView.tsx",
      "app/tasks/components/WaitingForView.tsx",
    ]) {
      expect(read(f), f).toMatch(/className="isolate flex-1 overflow-y-auto/);
    }
    expect(read("app/calendar/CalendarView.tsx")).toMatch(
      /className="relative isolate flex h-full min-h-0 flex-col/,
    );
  });
});

describe("the phone card follows the nav and the peek strip (TC-1 fix)", () => {
  const css = read("app/globals.css");
  it("pads the card's foot by the nav clearance, and more while the peek strip shows", () => {
    expect(css).toMatch(/\.task-card-phone\s*\{\s*padding-bottom:\s*var\(--task-card-nav-clear\)/);
    expect(css).toMatch(
      /:root\[data-assistant-peek\] \.task-card-phone\s*\{\s*padding-bottom:\s*calc\(var\(--task-card-nav-clear\) \+ var\(--task-card-peek-h\)\)/,
    );
    expect(css).toMatch(/--task-card-nav-clear:\s*calc\(3\.5rem \+ env\(safe-area-inset-bottom/);
  });
});
