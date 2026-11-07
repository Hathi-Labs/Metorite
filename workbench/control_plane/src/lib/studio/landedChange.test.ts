import { describe, expect, it } from "vitest";

import { BAR_LINES, barHeading, barLines, landChange, undoBlock, undoFailure, undoLabel, UNDO_BUSY, UNDO_FAILED, UNDO_REFUSED, UNDO_STALE } from "./landedChange";
import { SAVE_BUSY } from "./draftSave";
import type { PackageDraft } from "./packageDraft";

const draft = (manifest: unknown, hash = "h"): PackageDraft => ({ id: "p", name: "A", manifest, liveVersion: null, manifestHash: hash, objects: [] });
const m = (...objs: string[]) => ({ studio: 1, id: "a", name: "A", objects: objs.map((name) => ({ name, fields: [] })) });

describe("landChange", () => {
  it("is null for a first read", () => {
    expect(landChange(null, draft(m("x")), "other")).toBeNull();
  });
  it("is null when the manifest is the same, or only reordered", () => {
    expect(landChange(draft(m("x")), draft(m("x"), "h2"), "other")).toBeNull();
    expect(landChange(draft(m("x", "y")), draft(m("y", "x"), "h2"), "other")).toBeNull();
  });
  it("keeps the earlier manifest and the plain lines", () => {
    const l = landChange(draft(m("x")), draft(m("x", "y"), "h2"), "you");
    expect(l?.who).toBe("you");
    expect(l?.before).toEqual(m("x"));
    expect(barLines(l!, true).lines).toEqual(["adds the record type «y»"]);
  });
});

describe("the words", () => {
  it("names the actor as far as the page knows", () => {
    expect(barHeading("you")).toBe("You saved:");
    expect(barHeading("other")).toMatch(/Mira or another tab/);
    expect(barHeading("undo")).toBe("You undid the last change:");
    expect(barHeading("redo")).toBe("You put the change back:");
    expect(undoLabel("undo")).toBe("Redo");
    expect(undoLabel("redo")).toBe("Undo");
    expect(undoLabel("you")).toBe("Undo");
  });
  it("shows the first lines, then all of them", () => {
    const l = landChange(draft(m()), draft(m("a", "b", "c", "d", "e"), "h2"), "other")!;
    expect(barLines(l, false)).toEqual({ lines: expect.any(Array), hidden: 2 });
    expect(barLines(l, false).lines).toHaveLength(BAR_LINES);
    expect(barLines(l, true).lines).toHaveLength(5);
  });
  it("gives the reason that Undo is off", () => {
    const ok = { dirty: false, saving: false, fault: false, working: false };
    expect(undoBlock(ok)).toBeNull();
    expect(undoBlock({ ...ok, dirty: true })).toMatch(/unsaved/);
    expect(undoBlock({ ...ok, saving: true })).toMatch(/save is running/);
    expect(undoBlock({ ...ok, working: true })).toMatch(/save is running/);
    expect(undoBlock({ ...ok, fault: true })).toMatch(/does not parse/);
  });
  it("explains each failed undo", () => {
    expect(undoFailure({ kind: "stale", message: "x" })).toBe(UNDO_STALE);
    expect(undoFailure({ kind: "invalid", message: "x" })).toBe(UNDO_REFUSED);
    expect(undoFailure({ kind: "failed", message: SAVE_BUSY })).toBe(UNDO_BUSY);
    expect(undoFailure({ kind: "failed", message: "other" })).toBe(UNDO_FAILED);
  });
});
