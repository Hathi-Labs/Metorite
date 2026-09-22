/**
 * The pure halves of the Projects chat's templates (WS-27bm S4).
 *
 * The catalog, the registry and the tool docstring are held to one list by
 * `tests/unit/test_genui_catalog_lockstep.py`. This file covers the one
 * function a wrong input would break silently: the app link a row carries.
 */
import { describe, expect, it } from "vitest";

import { TEMPLATE_CATALOG, TEMPLATE_REGISTRY, taskHref } from "./genUITemplates";
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
