/**
 * Projects · the task panel in My work (WS-64 OP-2b, `one_projects_app.md`
 * §4.3 and §9 OP-2b).
 *
 * Fences: the open rule, the strip in the ONE panel, and the source rule that
 * `MyWorkHost` mounts no `TaskFocusModal`, while `/tasks` still does.
 */
import fs from "node:fs";
import path from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { useTaskStore } from "@/app/tasks/lib/taskStore";
import type { MyTask } from "@/app/tasks/lib/types";

import { MyWorkStrip } from "../components/MyWorkStrip";
import { openFromFocus } from "./myWork";

const PROJECTS = path.join(__dirname, "..");
const TASKS = path.join(__dirname, "..", "..", "tasks");
const read = (...p: string[]) => fs.readFileSync(path.join(...p), "utf8");
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("the open rule", () => {
  it("a focused row closes the store focus and opens the Projects panel", () => {
    const close = vi.fn();
    const open = vi.fn();
    expect(openFromFocus("t1", close, open)).toBe(true);
    expect(close).toHaveBeenCalledTimes(1);
    expect(open).toHaveBeenCalledWith("t1");
  });

  it("no focused row opens nothing", () => {
    const close = vi.fn();
    const open = vi.fn();
    expect(openFromFocus(null, close, open)).toBe(false);
    expect(close).not.toHaveBeenCalled();
    expect(open).not.toHaveBeenCalled();
  });

  it("the store still raises focus for /tasks, whose page mounts TaskFocusModal", () => {
    useTaskStore.getState().openFocus("t9");
    expect(useTaskStore.getState().focusedItemId).toBe("t9");
    useTaskStore.getState().closeFocus();
    expect(useTaskStore.getState().focusedItemId).toBeNull();
    expect(code(read(TASKS, "page.tsx"))).toContain("<TaskFocusModal");
  });
});

describe("the sources", () => {
  const host = code(read(PROJECTS, "components", "MyWorkHost.tsx"));

  it("MyWorkHost mounts no TaskFocusModal for a row click", () => {
    expect(host).not.toContain("TaskFocusModal");
    expect(host).toContain("openFromFocus(");
  });

  it("the page passes the panel opener, and the panel knows it is in My work", () => {
    const page = code(read(PROJECTS, "page.tsx"));
    expect(page).toMatch(/<MyWorkHost[\s\S]*?onOpenTask=/);
    expect(page).toContain("myWork={isMyWorkId(app)}");
  });

  it("a change in the panel is read back into the task store", () => {
    const page = code(read(PROJECTS, "page.tsx"));
    expect(page).toContain("useTaskStore.getState().refreshItem(fresh.id)");
  });

  it("the panel draws the strip above the fields, from the shared rows", () => {
    const panel = code(read(PROJECTS, "components", "TaskPanel.tsx"));
    expect(panel).toContain("above={strip}");
    expect(panel).toMatch(/myWork \|\| filedByMe\(mine\)/);
    const strip = code(read(PROJECTS, "components", "MyWorkStrip.tsx"));
    expect(strip).toContain("<PlanningRows");
    expect(strip).toContain("@/app/tasks/components/ItemDetail");
  });
});

describe("the overlay strip in the panel", () => {
  const item = {
    id: "t1",
    title: "Write the brief",
    disposition: "NEXT",
    context: "@desk",
    energy: "high",
    isTriaged: true,
  } as unknown as MyTask;

  it("renders disposition, context, energy and defer for a My work task", () => {
    useTaskStore.setState({ items: [item] });
    const html = renderToStaticMarkup(<MyWorkStrip taskId="t1" />);
    expect(html).toContain('data-testid="my-work-strip"');
    for (const label of ["Disposition", "Context", "Energy", "Defer until"]) {
      expect(html, label).toContain(`Edit ${label}`);
    }
    expect(html).toContain("@desk");
  });

  it("draws nothing for a task the store does not hold", () => {
    useTaskStore.setState({ items: [] });
    expect(renderToStaticMarkup(<MyWorkStrip taskId="t1" />)).toBe("");
  });
});
