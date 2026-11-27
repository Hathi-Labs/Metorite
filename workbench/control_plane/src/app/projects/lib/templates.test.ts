/**
 * WS-39 TPL-1b: the project menu item and the template picker.
 *
 * The words and the rules are pure (`templates.ts`, `projectMenu.ts`,
 * `tree.ts`) and are tested as values. The dialogs and the page are source
 * tests, because `vitest.config.ts` runs in `node` with no DOM.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import type { ProjectRow } from "./api";
import { type ProjectMenuHandlers, projectMenuItems } from "./projectMenu";
import {
  BLANK_CHOICE,
  appliedMessage,
  canSaveAsTemplate,
  countsLine,
  isStartDate,
  pickerReady,
  refusalText,
  templateOptionLabel,
  TEMPLATE_KEEPS,
  TEMPLATE_LEAVES_OUT,
  type TemplateRow,
} from "./templates";
import { childCreationOptions } from "./tree";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const project = { id: "p1", name: "Install", status: "active" } as unknown as ProjectRow;

const base = (): ProjectMenuHandlers => ({
  onSetState: vi.fn(),
  onArchive: vi.fn(),
  onUnarchive: vi.fn(),
  onRename: vi.fn(),
});

const labels = (h: ProjectMenuHandlers, level: "space" | "folder" | "project" | "subproject") =>
  projectMenuItems(project, h, undefined, level).flatMap((i) => (i.kind === "item" ? [i.label] : []));

describe("the project menu item", () => {
  it("is absent while the flag does not hold the org", () => {
    expect(labels(base(), "project")).not.toContain("Save as template");
  });
  it("shows on a project and a subproject with the flag", () => {
    const h = { ...base(), onSaveAsTemplate: vi.fn() };
    expect(labels(h, "project")).toContain("Save as template");
    expect(labels(h, "subproject")).toContain("Save as template");
  });
  it("never shows on a space or a folder, which hold no tasks", () => {
    const h = { ...base(), onSaveAsTemplate: vi.fn() };
    expect(labels(h, "space")).not.toContain("Save as template");
    expect(labels(h, "folder")).not.toContain("Save as template");
  });
  it("opens the dialog with the project, and saves nothing itself", () => {
    const onSaveAsTemplate = vi.fn();
    const items = projectMenuItems(project, { ...base(), onSaveAsTemplate }, undefined, "project");
    const item = items.find((i) => i.kind === "item" && i.label === "Save as template");
    if (item?.kind !== "item") throw new Error("no item");
    item.onSelect();
    expect(onSaveAsTemplate).toHaveBeenCalledWith(project);
  });
  it("canSaveAsTemplate follows the flag and the level", () => {
    expect(canSaveAsTemplate(false, "project")).toBe(false);
    expect(canSaveAsTemplate(true, "project")).toBe(true);
    expect(canSaveAsTemplate(true, "space")).toBe(false);
  });
});

describe("the New project picker entry", () => {
  it("is absent without the flag", () => {
    expect(childCreationOptions("project", 1).some((o) => o.template)).toBe(false);
  });
  it("follows the blank entry and never leads it", () => {
    const options = childCreationOptions("project", 1, true);
    expect(options[0]).toMatchObject({ kind: "project", label: "New project" });
    expect(options[1]).toMatchObject({ label: "New from template…", template: true, level: "project" });
    expect(options.map((o) => o.kind)).toEqual(["project", "project", "folder"]);
  });
  it("keeps the level word of a subproject, and offers none below the floor", () => {
    expect(childCreationOptions("project", 2, true)[1].level).toBe("subproject");
    expect(childCreationOptions("project", 3, true)).toEqual([]);
  });
});

describe("the picker rules", () => {
  it("blank needs a name", () => {
    expect(pickerReady({ templateId: BLANK_CHOICE, name: "  ", startDate: "" })).toBe(false);
    expect(pickerReady({ templateId: BLANK_CHOICE, name: "Alpha", startDate: "" })).toBe(true);
  });
  it("a template needs a real start date", () => {
    expect(pickerReady({ templateId: "t1", name: "", startDate: "" })).toBe(false);
    expect(pickerReady({ templateId: "t1", name: "", startDate: "2026-02-30" })).toBe(false);
    expect(pickerReady({ templateId: "t1", name: "", startDate: "3001-01-01" })).toBe(false);
    expect(pickerReady({ templateId: "t1", name: "", startDate: "2026-12-01" })).toBe(true);
  });
  it("isStartDate refuses a day that does not exist", () => {
    expect(isStartDate("2028-02-29")).toBe(true);
    expect(isStartDate("2027-02-29")).toBe(false);
  });
});

describe("the words", () => {
  const row: TemplateRow = {
    id: "t1",
    kind: "project",
    name: "Install",
    description: null,
    work_style: null,
    counts: { statuses: 5, types: 1, fields: 0, tags: 2, tasks: 12 },
    created_by: "a@b.c",
    created_at: null,
    updated_at: null,
  };
  it("shows the counts of each template, and leaves zero counts out", () => {
    expect(countsLine(row.counts)).toBe("12 tasks, 5 statuses, 1 task type, 2 tags");
    expect(templateOptionLabel(row)).toBe("Install — 12 tasks, 5 statuses, 1 task type, 2 tags");
  });
  it("lists what the template keeps and leaves out", () => {
    expect(TEMPLATE_KEEPS.join(" ")).toMatch(/Statuses.*Task types.*Custom fields.*Tags.*task tree/);
    expect(TEMPLATE_LEAVES_OUT).toEqual(["Assignees", "Comments", "History"]);
  });
  it("explains the inherited statuses and names the next step", () => {
    const text = appliedMessage(
      {
        project: { id: "n", name: "Install A" },
        template_id: "t1",
        replayed: false,
        own_statuses: false,
        created: row.counts,
      },
      "Client Projects",
    );
    expect(text).toContain("uses the statuses of Client Projects");
    expect(text).toContain("Ask an organization admin");
  });
  it("prefers the route's own sentence, and falls back in plain words", () => {
    expect(refusalText(403, "Save it from such a project.")).toBe("Save it from such a project.");
    expect(refusalText(404, "Not Found")).toContain("not switched on");
    expect(refusalText(403, "")).toContain("Ask");
  });
});

describe("the dialogs and the page", () => {
  const save = read("app/projects/components/SaveAsTemplateDialog.tsx");
  const pick = read("app/projects/components/NewFromTemplateDialog.tsx");
  const page = read("app/projects/page.tsx");
  it("use the house primitives and show the refusal in place", () => {
    for (const src of [save, pick]) {
      expect(src).toContain('from "@/components/ui/Modal"');
      expect(src).toContain('from "@/components/ui/Button"');
      expect(src).toContain('role="alert"');
    }
  });
  it("the Save dialog names what is kept and what is left out", () => {
    expect(save).toContain("The template keeps");
    expect(save).toContain("The template leaves out");
    expect(save).toContain("TEMPLATE_LEAVES_OUT");
  });
  it("the picker offers blank first, shows counts, and asks the start date for a template only", () => {
    expect(pick).toContain("BLANK_CHOICE");
    expect(pick).toContain("templateOptionLabel");
    expect(pick).toContain('type="date"');
    expect(pick).toContain("{chosen ? (");
  });
  it("the page offers the entries only with the flag", () => {
    expect(page).toContain("templatesOn");
    expect(page).toMatch(/\.\.\.\(templatesOn[\s\S]*onSaveAsTemplate[\s\S]*onNewFromTemplate/);
    expect(page).toContain("templatesApi.list");
  });
  it("the page counts both dialogs as an open overlay", () => {
    expect(page).toContain("Boolean(savingTemplateOf)");
    expect(page).toContain("Boolean(pickingUnder)");
  });
});
