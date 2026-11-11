/**
 * MOB-2 fence for the phone row's decisions (`listPhoneRow.ts`).
 * Mutation run by hand: make `tickLabel` ignore `done`, or make `phoneRowParts`
 * return the complete tick while `selectMode` is on. Both fail below.
 */
import { describe, expect, it } from "vitest";

import type { StatusRow, TaskRow } from "./api";
import { PHONE_TICK_PX, phoneRowParts, tickLabel } from "./listPhoneRow";

const status = (id: string, category: string): StatusRow =>
  ({ id, name: id, category, color: "gray", position: 1, is_default: false } as unknown as StatusRow);
const STATUSES = [status("todo", "todo"), status("done", "done")];
const task = (over: Partial<TaskRow> = {}): TaskRow =>
  ({
    id: "t1",
    title: "Write the spec",
    status_id: "todo",
    task_number: 7,
    ...over,
  } as unknown as TaskRow);
const base = {
  statuses: STATUSES,
  shownFields: ["due_at"],
  path: ["Firmware", "Bootloader"],
  selectMode: false,
  selected: false,
  canComplete: true,
  nowMs: Date.parse("2026-11-06T12:00:00Z"),
};

describe("phoneRowParts", () => {
  it("gives all five parts and no # column", () => {
    const parts = phoneRowParts({ ...base, task: task({ due_at: "2026-11-20T00:00:00Z" }) });
    expect(parts.tick.kind).toBe("complete");
    expect(parts.title).toBe("Write the spec");
    expect(parts.project?.label).toBe("Firmware › Bootloader");
    expect(parts.due?.key).toBe("due");
    expect(parts.status?.id).toBe("todo");
    expect(JSON.stringify(parts)).not.toContain("#7");
  });

  it("one tap on the tick targets the closed lane", () => {
    const parts = phoneRowParts({ ...base, task: task() });
    expect(parts.tick).toMatchObject({ kind: "complete", done: false, toStatusId: "done" });
  });

  it("a finished task reopens", () => {
    const parts = phoneRowParts({ ...base, task: task({ status_id: "done" }) });
    expect(parts.tick).toMatchObject({ kind: "complete", done: true, toStatusId: "todo" });
  });

  it("select mode shows the checkbox in place of the tick", () => {
    const parts = phoneRowParts({ ...base, task: task(), selectMode: true, selected: true });
    expect(parts.tick).toEqual({ kind: "select", label: "Select Write the spec", checked: true });
  });

  it("no tick when the surface cannot write, or no lane is closed", () => {
    expect(phoneRowParts({ ...base, task: task(), canComplete: false }).tick.kind).toBe("none");
    expect(
      phoneRowParts({ ...base, task: task(), statuses: [status("todo", "todo")] }).tick.kind,
    ).toBe("none");
  });

  it("leaves out the project, and the due chip a view hides", () => {
    const parts = phoneRowParts({
      ...base,
      task: task({ due_at: "2026-11-20T00:00:00Z" }),
      path: null,
      shownFields: [],
    });
    expect(parts.project).toBeNull();
    expect(parts.due).toBeNull();
  });

  it("marks an overdue due chip as danger", () => {
    const parts = phoneRowParts({ ...base, task: task({ due_at: "2026-11-01T00:00:00Z" }) });
    expect(parts.due?.tone).toBe("danger");
  });
});

describe("tickLabel", () => {
  it("names the task and the direction", () => {
    expect(tickLabel("A", false)).toBe("Mark A done");
    expect(tickLabel("A", true)).toBe("Reopen A");
  });
  it("keeps the target at 40 px or more", () => {
    expect(PHONE_TICK_PX).toBeGreaterThanOrEqual(40);
  });
});
