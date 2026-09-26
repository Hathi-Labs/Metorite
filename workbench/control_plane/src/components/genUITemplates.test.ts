/**
 * The pure halves of the Projects chat's templates (WS-27bm S4).
 *
 * The catalog, the registry and the tool docstring are held to one list by
 * `tests/unit/test_genui_catalog_lockstep.py`. This file covers the one
 * function a wrong input would break silently: the app link a row carries.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  TEMPLATE_CATALOG,
  TEMPLATE_REGISTRY,
  isDateCell,
  shownDelta,
  taskHref,
} from "./genUITemplates";
import { PROJECTS_CHANGED_EVENT } from "./projects/ProjectToolCards";

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";

describe("taskHref", () => {
  it("links a uuid to the task deep link", () => {
    expect(taskHref(ID)).toBe(`/projects?task=${ID}`);
  });

  it("refuses anything that is not a uuid, so a row cannot carry a link elsewhere", () => {
    for (const bad of ["", "../admin", "javascript:alert(1)", 7, null, undefined, `${ID}x`]) {
      expect(taskHref(bad)).toBeNull();
    }
  });

  it("uses the base it is given, and never links an empty base to a bare id", () => {
    expect(taskHref(ID, "/projects?app=reports&task=")).toBe(`/projects?app=reports&task=${ID}`);
    // A report table passes "" and its rows carry no id; a row that did
    // would become a bare uuid href, which is why rows there have none.
    expect(taskHref("", "")).toBeNull();
  });
});

describe("the catalog and the registry", () => {
  it("name the same templates", () => {
    expect(TEMPLATE_CATALOG.map((t) => t.name).sort()).toEqual(Object.keys(TEMPLATE_REGISTRY).sort());
  });

  it("carry the five Projects views", () => {
    const names = new Set(TEMPLATE_CATALOG.map((t) => t.name));
    for (const name of ["timeline", "taskBoard", "dataGrid", "reportCard", "planCard"]) {
      expect(names.has(name)).toBe(true);
    }
  });
});

describe("the refresh seam", () => {
  it("is one named event", () => {
    expect(PROJECTS_CHANGED_EVENT).toBe("cc-projects-changed");
  });
});

// ── WS-27bm S9 — a delta the model made up, and a date that wrapped ─────────

describe("shownDelta", () => {
  it("drops a delta that only copies the value", () => {
    // The owner's screenshot: "Overdue 2" with a red "▼ 2" no tool printed.
    expect(shownDelta({ label: "Overdue", value: 2, delta: -2 })).toBeNull();
    expect(shownDelta({ label: "Overdue", value: "2", delta: 2 })).toBeNull();
  });

  it("keeps a delta that says what it is a change over", () => {
    expect(shownDelta({ value: 2, delta: -2, period: "since last week" })).toBe(-2);
    expect(shownDelta({ value: 2, delta: 2, deltaLabel: "vs last sprint" })).toBe(2);
  });

  it("keeps a delta that differs from the value, and a zero", () => {
    expect(shownDelta({ value: 12, delta: 3 })).toBe(3);
    expect(shownDelta({ value: 0, delta: 0 })).toBe(0);
  });

  it("draws nothing when no delta was sent", () => {
    expect(shownDelta({ value: 5 })).toBeNull();
  });
});

describe("isDateCell", () => {
  it("knows a date, with or without a time", () => {
    for (const cell of ["2026-09-30", "2026-09-30 10:00", "2026-09-30T10:00:00"]) {
      expect(isDateCell(cell)).toBe(true);
    }
  });

  it("does not hold a sentence or a number to one line", () => {
    for (const cell of ["Due 2026-09-30 or later", 42, "", null, "2026-09"]) {
      expect(isDateCell(cell)).toBe(false);
    }
  });
});

// ── WS-27bm S10 — the plan card's "Waits on" control ────────────────────────

describe("the plan card's Waits on control", () => {
  const plan = (tasks: unknown[]) =>
    renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY.planCard({ project: { name: "Steps" }, tasks })));
  const row = (key: string, after: string[] = []) =>
    ({ key, title: key, owner: "a@x.io", effort_mins: 30, due: "2026-10-01", after });

  it("draws on each row that has another row to wait on", () => {
    const html = plan([row("t1"), row("t2", ["t1"]), row("t3", ["t2"])]);
    // t1 has no option (both others wait on it). t2 and t3 each have one.
    expect(html.match(/Waits on/g) ?? []).toHaveLength(2);
  });

  it("is absent from a plan of one task", () => {
    expect(plan([row("t1")])).not.toContain("Waits on");
  });
});
