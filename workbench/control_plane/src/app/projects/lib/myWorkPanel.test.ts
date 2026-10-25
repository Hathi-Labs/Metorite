/**
 * Projects · the task panel in My work (WS-64 OP-2b, `one_projects_app.md`
 * §4.3 and §9 OP-2b).
 *
 * Fences: the open rule, the strip in the ONE panel, and the source rule that
 * `MyWorkHost` mounts no `TaskFocusModal`, while `/tasks` still does.
 */
import fs from "node:fs";
import path from "node:path";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { useTaskStore } from "@/app/tasks/lib/taskStore";
import type { MyTask } from "@/app/tasks/lib/types";

import { MyWorkStrip } from "../components/MyWorkStrip";
import {
  chatTaskTarget,
  openFromFocus,
  pickMine,
  showMyWorkStrip,
  statusDrifted,
  watchInsideFocus,
} from "./myWork";

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
    expect(host).toContain("watchInsideFocus(");
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
    expect(panel).toContain("showMyWorkStrip(");
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
    // A server render reads the store's INITIAL state, so seed that array.
    const seeded = useTaskStore.getInitialState().items;
    seeded.length = 0;
    seeded.push(item);
    const html = renderToStaticMarkup(createElement(MyWorkStrip, { taskId: "t1" }));
    expect(html).toContain('data-testid="my-work-strip"');
    for (const label of ["Disposition", "Context", "Energy", "Defer until"]) {
      expect(html, label).toContain(`Edit ${label}`);
    }
    expect(html).toContain("@desk");
  });

  it("draws nothing for a task the store does not hold", () => {
    useTaskStore.getInitialState().items.length = 0;
    expect(renderToStaticMarkup(createElement(MyWorkStrip, { taskId: "t1" }))).toBe("");
  });
});

describe("fix round 1", () => {
  it("1: the strip and the badge need the flag, and a company view keeps the server read", () => {
    const base = { stored: true, myWork: true, filedByMe: true };
    expect(showMyWorkStrip({ ...base, enabled: false })).toBe(false);
    expect(showMyWorkStrip({ ...base, enabled: true })).toBe(true);
    expect(showMyWorkStrip({ enabled: true, stored: true, myWork: false, filedByMe: false })).toBe(false);
    const fromStore = () => "store";
    expect(pickMine({ myWork: true, enabled: false, stored: {}, mine: "server", fromStore })).toBe("server");
    expect(pickMine({ myWork: false, enabled: true, stored: {}, mine: "server", fromStore })).toBe("server");
    expect(pickMine({ myWork: true, enabled: true, stored: {}, mine: "server", fromStore })).toBe("store");
  });

  it("2: a chat card on /projects opens the panel and never raises the store focus", () => {
    expect(chatTaskTarget("/projects", true, "a b")).toEqual({
      kind: "panel",
      href: "/projects?task=a%20b",
    });
    expect(chatTaskTarget("/projects", false, "t1").kind).toBe("modal");
    expect(chatTaskTarget("/chat", true, "t1")).toEqual({ kind: "panel", href: "/projects?task=t1" });
    const cards = code(read(__dirname, "..", "..", "..", "components", "tasks", "TaskToolCards.tsx"));
    expect(cards).toMatch(/target\.kind === "modal"[\s\S]*?openFocus\(id\)/);
  });

  it("2: only a focus raised inside My work is taken; another caller is not cleared", () => {
    let inside = false;
    const open = vi.fn();
    const off = watchInsideFocus(useTaskStore, () => inside, open);
    useTaskStore.getState().openFocus("outside");
    expect(open).not.toHaveBeenCalled();
    expect(useTaskStore.getState().focusedItemId).toBe("outside");
    useTaskStore.getState().closeFocus();
    inside = true;
    useTaskStore.getState().openFocus("row");
    expect(open).toHaveBeenCalledWith("row");
    expect(useTaskStore.getState().focusedItemId).toBeNull();
    off();
  });

  it("3: stay is an argument and no ref stays armed", () => {
    const page = code(read(PROJECTS, "page.tsx"));
    expect(page).not.toContain("stayInMyWork");
    expect(page).toContain("openTaskById(id, true)");
  });

  it("5: a status the store moved is a drift the panel reads again", () => {
    expect(statusDrifted({ statusId: "s2" }, "s1")).toBe(true);
    expect(statusDrifted({ statusId: "s1" }, "s1")).toBe(false);
    expect(statusDrifted(undefined, "s1")).toBe(false);
    expect(code(read(PROJECTS, "components", "TaskPanel.tsx"))).toContain("statusDrifted(");
  });
});
