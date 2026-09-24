/**
 * Projects' delete confirmation: true words, one dialog (2026-09-24).
 *
 * A Projects delete runs `DELETE FROM pm_tasks` and has no Undo, so the copy
 * must say it cannot be undone. The dialog is the shared `ConfirmDialog`,
 * the one My Tasks draws too. `window.confirm` is gone from the page.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { deleteTaskCopy, deleteTasksCopy } from "./deleteCopy";

const page = () =>
  readFileSync(resolve(__dirname, "..", "page.tsx"), "utf-8").replace(/\r\n/g, "\n");

describe("the words", () => {
  it("one task: deleted for good, and the subtasks are kept", () => {
    const copy = deleteTaskCopy({ title: "Ship it", subtasks: 2 });
    expect(copy.title).toBe("Delete this task?");
    expect(copy.subject).toBe("Ship it");
    expect(copy.body).toBe("It is deleted for good. This cannot be undone.");
    expect(copy.note).toBe("Its 2 subtasks are kept and moved up a level, not deleted.");
    expect(copy.confirmLabel).toBe("Delete");
  });

  it("no subtask sentence for a task with none", () => {
    expect(deleteTaskCopy({ title: "Solo", subtasks: 0 }).note).toBeNull();
    expect(deleteTaskCopy(null).subject).toBeNull();
  });

  it("the bulk bar says the same", () => {
    const copy = deleteTasksCopy(3);
    expect(copy.title).toBe("Delete 3 tasks?");
    expect(copy.body).toBe("They are deleted for good. This cannot be undone.");
    expect(deleteTasksCopy(1).title).toBe("Delete 1 task?");
  });

  it("never promises an undo, because this path has none", () => {
    for (const copy of [deleteTaskCopy({ title: "x", subtasks: 1 }), deleteTasksCopy(4)]) {
      expect(`${copy.body} ${copy.note ?? ""}`).not.toMatch(/you can undo/i);
    }
  });
});

describe("the page asks through the shared dialog", () => {
  it("has no window.confirm for a task delete", () => {
    expect(page()).not.toMatch(/window\.confirm\(/);
  });

  it("mounts ConfirmDialog with this file's copy, for both delete paths", () => {
    const src = page();
    expect(src).toMatch(/import ConfirmDialog from "@\/components\/ui\/ConfirmDialog";/);
    expect(src).toMatch(/<ConfirmDialog\s+open=\{Boolean\(confirmingDelete\)\}\s+\{\.\.\.deleteCopy\}/);
    expect(src).toMatch(/deleteTasksCopy\(confirmingDelete\.ids\.length\)/);
    // Each path only opens the dialog until the dialog calls back.
    expect(src).toMatch(/setConfirmingDelete\(\{ kind: "one", taskId \}\)/);
    expect(src).toMatch(/setConfirmingDelete\(\{ kind: "bulk", ids \}\)/);
    expect(src).toMatch(/void deleteTaskById\(pending\.taskId, true\)/);
    expect(src).toMatch(/void applyBulkAction\("delete", pending\.ids\)/);
  });
});
