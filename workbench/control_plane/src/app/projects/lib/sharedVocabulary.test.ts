/**
 * WS-42 PS-3 — the organization's shared vocabulary section.
 * Spec: project-docs/specs/projects_settings.md §7 row PS-3; D-PM-33.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  renamedNotice,
  sharedDeleteCopy,
  sharedMergeCopy,
  usageLine,
  vocabularyEmpty,
  vocabularyNotes,
} from "./sharedVocabulary";

describe("usageLine", () => {
  it("says nothing when the gateway sent no count, never a wrong zero", () => {
    expect(usageLine(undefined)).toBeNull();
  });
  it("counts open tasks, singular and plural", () => {
    expect(usageLine(0)).toBe("No open task uses it");
    expect(usageLine(1)).toBe("1 open task");
    expect(usageLine(7)).toBe("7 open tasks");
  });
});

describe("vocabularyNotes", () => {
  it("tells an admin what they can and cannot do", () => {
    const notes = vocabularyNotes({ can_edit: true, can_create: false });
    expect(notes.join(" ")).toMatch(/rename/);
    expect(notes.join(" ")).toMatch(/merge a tag into another shared tag, and delete/);
    expect(notes.join(" ")).toMatch(/not turned on/);
  });
  it("tells a member who can change them", () => {
    expect(vocabularyNotes({ can_edit: false, can_create: false }).join(" ")).toMatch(/owner or admin/);
  });
  it("drops the create note once creating is on", () => {
    expect(vocabularyNotes({ can_edit: true, can_create: true }).join(" ")).not.toMatch(/not turned on/);
  });
});

describe("vocabularyEmpty", () => {
  it("is empty only when all three lists are", () => {
    expect(vocabularyEmpty({ tags: [], fields: [], types: [] })).toBe(true);
    expect(vocabularyEmpty({ tags: [], fields: [], types: [{ id: "t", name: "Spike" }] })).toBe(false);
  });
});

describe("renamedNotice", () => {
  it("names every space, and the rewritten tasks when a tag moved them", () => {
    expect(renamedNotice("tag", "defect", 3)).toBe(
      "Renamed the shared tag to “defect” in every space. It was rewritten on 3 tasks.",
    );
    expect(renamedNotice("field", "Cost")).toBe("Renamed the shared field to “Cost” in every space.");
  });
});

describe("every delete and merge asks first, with the count (H-205)", () => {
  const src = readFileSync(join(__dirname, "..", "components", "SharedVocabulary.tsx"), "utf-8");
  it("reads the impact before a delete or merge is asked", () => {
    expect(src).toMatch(/impactOf\(group\.kind, entry\.id\)\.then[\s\S]*?act: "delete"/);
    expect(src).toMatch(/impactOf\("tags", entry\.id\)\.then[\s\S]*?act: "merge"/);
    // The write happens only from the dialog's confirm.
    expect(src.indexOf("deleteTag(")).toBeGreaterThan(src.indexOf("const remove"));
  });
  it("says what a delete reaches, and never a zero it did not read", () => {
    expect(sharedDeleteCopy("tags", "bug", { tasks: 3, projects: 2 }).body).toBe(
      "It is taken off 3 tasks across 2 spaces, and deleted for good. The tasks stay. This cannot be undone.",
    );
    expect(sharedDeleteCopy("fields", "Cost", null).body).toMatch(/could not be read/);
    expect(sharedDeleteCopy("types", "Spike", { tasks: 0, projects: 0 }).body).toMatch(/^No task uses it\./);
    expect(sharedMergeCopy("bug", "defect", { tasks: 1, projects: 1 }).body).toBe(
      "1 task across 1 space wears “defect” instead, and “bug” is deleted.",
    );
  });
  it("asks with the count before a tag rename", () => {
    expect(src).toMatch(/tagImpact\(/);
    expect(src).toMatch(/orgRenameCopy\(/);
  });
});
