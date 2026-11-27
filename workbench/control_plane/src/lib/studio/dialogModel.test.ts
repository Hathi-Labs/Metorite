/**
 * STU-AF6 fence: which add, edit and delete buttons an app shows, who sees them, and that a double
 * press makes one write. The 422 under a field is `formModel.afterSend`, checked here on a dialog case.
 */
import { describe, expect, it, vi } from "vitest";

import type { StudioDataDoor } from "./dataDoor";
import { editTitle, newButtons, recordButtons } from "./dialogModel";
import { afterSend, sendRecord } from "./formModel";
import { previewControls } from "./packagePreview";

const objects = [{ name: "visit", label: "Visit" }, { name: "site", label: null }];
const actions = [
  { id: "visit_list", uses: "object:visit.list" },
  { id: "visit_create", uses: "object:visit.create" },
  { id: "visit_update", uses: "object:visit.update" },
  { id: "visit_delete", uses: "object:visit.delete" },
  { id: "site_list", uses: "object:site.list" },
];

describe("newButtons", () => {
  it("gives New <Object> from a declared create", () => {
    expect(newButtons(["visit"], objects, actions, true)).toEqual([
      { object: "visit", label: "New Visit", action: "object:visit.create", title: "New Visit" },
    ]);
  });
  it("gives no button for an object with no declared create", () => {
    expect(newButtons(["site"], objects, actions, true)).toEqual([]);
  });
  it("gives one button for an object that the page shows twice, and skips a missing name", () => {
    expect(newButtons(["visit", null, "visit", "nope"], objects, actions, true)).toHaveLength(1);
  });
  it("gives a viewer no button, through the controls of J2a", () => {
    const viewer = previewControls("viewer", actions);
    expect(newButtons(["visit"], objects, viewer.actions, viewer.owner)).toEqual([]);
    expect(newButtons(["visit"], objects, actions, false)).toEqual([]);
  });
  it("gives no button for an action that fills values by itself", () => {
    const set = [{ id: "visit_create", uses: "object:visit.create", set: { status: "new" } }];
    expect(newButtons(["visit"], objects, set, true)).toEqual([]);
  });
  it("names the object by its name when it has no label", () => {
    const a = [{ id: "site_create", uses: "object:site.create" }];
    expect(newButtons(["site"], objects, a, true)[0].label).toBe("New site");
  });
});

describe("recordButtons", () => {
  it("shows Edit and Delete from the declared update and delete", () => {
    expect(recordButtons("visit", actions, true)).toEqual({ edit: true, remove: true });
  });
  it("shows only what the app declares", () => {
    expect(recordButtons("visit", [actions[2]], true)).toEqual({ edit: true, remove: false });
    expect(recordButtons("site", actions, true)).toEqual({ edit: false, remove: false });
  });
  it("shows a viewer neither", () => {
    expect(recordButtons("visit", actions, false)).toEqual({ edit: false, remove: false });
    const viewer = previewControls("viewer", actions);
    expect(recordButtons("visit", viewer.actions, viewer.owner)).toEqual({ edit: false, remove: false });
  });
  it("titles the Edit dialog", () => {
    expect(editTitle(objects[0])).toBe("Edit Visit");
  });
});

describe("the write of a dialog", () => {
  it("makes one record on a double press", async () => {
    let release: (v: { ok: true }) => void = () => {};
    const run = vi.fn(() => new Promise<{ ok: true }>((r) => { release = r; }));
    const door = { scope: "t", read: vi.fn(), run } as unknown as StudioDataDoor;
    const busy = { current: false };
    const first = sendRecord(door, "object:visit.create", { title: "A" }, busy);
    const second = await sendRecord(door, "object:visit.create", { title: "A" }, busy);
    expect(second.kind).toBe("busy");
    release({ ok: true });
    expect((await first).kind).toBe("sent");
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("shows a 422 under its field and keeps the values", () => {
    const fields = [{ key: "title", type: "text", label: "Title" }] as never;
    const out = afterSend(fields, { title: "x" }, {
      kind: "sent", ok: false, message: "No", fieldErrors: { title: "Too short" },
    }, { savedMessage: "Saved", reset: true });
    expect(out.errors).toEqual({ title: "Too short" });
    expect(out.drafts).toEqual({ title: "x" });
    expect(out.focus).toBe("title");
    expect(out.toast).toBeNull();
  });
});
