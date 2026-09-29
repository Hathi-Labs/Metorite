/**
 * WS-42 PS-3 — the organization's shared vocabulary section.
 * Spec: project-docs/specs/projects_settings.md §7 row PS-3; D-PM-33.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { renamedNotice, usageLine, vocabularyEmpty, vocabularyNotes } from "./sharedVocabulary";

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
    expect(notes.join(" ")).toMatch(/rename them/);
    expect(notes.join(" ")).toMatch(/cannot be merged or deleted/);
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

describe("the section offers only what D-PM-33 allows", () => {
  const src = readFileSync(join(__dirname, "..", "components", "SharedVocabulary.tsx"), "utf-8");
  it("never deletes or merges an org-wide row", () => {
    expect(src).not.toMatch(/deleteTag|deleteField|deleteType|mergeTag/);
  });
  it("asks with the count before a tag rename", () => {
    expect(src).toMatch(/tagImpact\(/);
    expect(src).toMatch(/orgRenameCopy\(/);
  });
});
