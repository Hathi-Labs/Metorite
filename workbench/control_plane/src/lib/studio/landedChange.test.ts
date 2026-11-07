import { describe, expect, it } from "vitest";

import { NEW_TRACKER, markOwn, observe, runUndo, undoDropsLive, BAR_LINES, barHeading, barLines, landChange, undoBlock, undoFailure, undoLabel, UNDO_BUSY, UNDO_FAILED, UNDO_REFUSED, UNDO_STALE } from "./landedChange";
import { SAVE_BUSY } from "./draftSave";
import type { PackageDraft } from "./packageDraft";

const draft = (manifest: unknown, hash = "h"): PackageDraft => ({ id: "p", name: "A", manifest, liveVersion: null, manifestHash: hash, objects: [] });
const m = (...objs: string[]) => ({ studio: 1, id: "a", name: "A", objects: objs.map((name) => ({ name, fields: [] })) });

describe("landChange", () => {
  it("is null for a first read", () => {
    expect(landChange(null, draft(m("x")), "other")).toBeNull();
  });
  it("is null when the manifest is the same", () => {
    expect(landChange(draft(m("x")), draft(m("x"), "h2"), "other")).toBeNull();
  });
  it("lands a reorder with its own line", () => {
    const l = landChange(draft(m("x", "y")), draft(m("y", "x"), "h2"), "other");
    expect(barLines(l!, true).lines).toEqual(["changes the order of the record types"]);
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

describe("the tracker", () => {
  const at = (manifest: unknown, hash: string, live: number | null = null): PackageDraft => ({ ...draft(manifest, hash), liveVersion: live });
  it("takes the first draft with no change", () => {
    const t = observe(NEW_TRACKER, at(m("x"), "h1"));
    expect(t.landed).toBeNull();
    expect(t.prev?.manifestHash).toBe("h1");
  });
  it("marks the page's own save as You saved", () => {
    let t = observe(NEW_TRACKER, at(m("x"), "h1"));
    t = markOwn(t, "you", "h2");
    t = observe(t, at(m("x", "y"), "h2"));
    expect(t.landed?.who).toBe("you");
    expect(t.own).toBeNull();
  });
  it("marks a poll from elsewhere as Mira or another tab", () => {
    let t = observe(NEW_TRACKER, at(m("x"), "h1"));
    t = markOwn(t, "you", "h2");
    t = observe(t, at(m("x", "z"), "h9"));
    expect(t.landed?.who).toBe("other");
    expect(t.own).not.toBeNull();
  });
  it("clears landed when the manifest moved and no line came (a stale Undo would wipe the newer save)", () => {
    let t = observe(NEW_TRACKER, at(m("x"), "h1"));
    t = observe(t, at(m("x", "y"), "h2"));
    expect(t.landed).not.toBeNull();
    t = observe(t, at("a", "h3"));
    t = observe(t, at("b", "h4"));
    expect(t.landed).toBeNull();
  });
  it("keeps landed when only the name moves, and clears it when a version goes live", () => {
    let t = observe(NEW_TRACKER, at(m("x"), "h1"));
    t = observe(t, at(m("x", "y"), "h2"));
    const kept = observe(t, { ...at(m("x", "y"), "h2"), name: "Renamed" });
    expect(kept.landed).toBe(t.landed);
    const live = observe(t, at(m("x", "y"), "h2", 1));
    expect(live.landed).toBeNull();
  });
  it("sends the previous manifest with the current hash through the one save", async () => {
    const calls: unknown[][] = [];
    const t0 = observe(NEW_TRACKER, at(m("x"), "h1"));
    const now = at(m("x", "y"), "h2");
    const t = observe(t0, now);
    const save = (async (...a: unknown[]) => { calls.push(a); return { kind: "saved", draft: at(m("x"), "h3"), added: [], skipped: [] }; }) as never;
    const r = await runUndo("pkg", now, t.landed!, save);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("pkg");
    expect(calls[0][1]).toEqual(m("x"));
    expect(calls[0][2]).toBe("h2");
    expect(r).toMatchObject({ kind: "saved", who: "undo" });
    const again = await runUndo("pkg", now, { ...t.landed!, who: "undo" }, save);
    expect(again).toMatchObject({ who: "redo" });
  });
  it("says why an undo did not land", async () => {
    const t = observe(observe(NEW_TRACKER, at(m("x"), "h1")), at(m("x", "y"), "h2"));
    const save = (async () => ({ kind: "stale", message: "x" })) as never;
    expect(await runUndo("p", at(m("x", "y"), "h2"), t.landed!, save)).toEqual({ kind: "note", text: UNDO_STALE });
  });
});

describe("undoDropsLive", () => {
  const live = [{ name: "units", fields: [{ key: "site" }] }];
  it("is off when Undo would drop a live record type or field", () => {
    expect(undoDropsLive(m(), live)).toMatch(/Undo is off\. This object is live/);
    expect(undoDropsLive({ objects: [{ name: "units", fields: [] }] }, live)).toMatch(/This field is live/);
  });
  it("is on when the earlier manifest keeps every live part, or nothing is live", () => {
    expect(undoDropsLive({ objects: [{ name: "units", fields: [{ key: "site" }, { key: "x" }] }] }, live)).toBeNull();
    expect(undoDropsLive(m(), [])).toBeNull();
  });
  it("turns the Undo button off with that reason", () => {
    const ok = { dirty: false, saving: false, fault: false, working: false };
    expect(undoBlock({ ...ok, liveDrop: "Undo is off. x" })).toBe("Undo is off. x");
  });
});
