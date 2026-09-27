/**
 * D-PM-38 decisions 2 and 4 (Subtasks S5) — the words, the defaults, the
 * bulk summary and the Undo rule both task apps read.
 *
 * - The complete prompt asks only when open children > 0, and its default
 *   answer is "Only this task".
 * - The move and archive boxes start TICKED, and draw nothing at 0.
 * - The bulk bar names the parents in a selection and their open subtasks.
 * - Undo puts back each task's EXACT prior status, and keeps a newer move.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { IncludeSubtasksBox } from "@/components/SubtaskCascade";

import {
  CASCADE_DEFAULTS,
  archiveReceipt,
  bulkSubtaskSummary,
  bulkSummaryLine,
  completePrompt,
  completeReceipt,
  includeSubtasksLabel,
  moveReceipt,
  openSubtasks,
  revertCascade,
  revertSummary,
} from "./subtaskCascade";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) =>
  readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

describe("the complete prompt (decision 2)", () => {
  it("asks only when some subtask is still open", () => {
    expect(completePrompt(openSubtasks({ done: 3, total: 3 }))).toBeNull();
    expect(completePrompt(openSubtasks(undefined))).toBeNull();
    expect(completePrompt(openSubtasks({ done: 1, total: 4 }))).not.toBeNull();
  });

  it("says the owner's sentence, with the two actions", () => {
    expect(completePrompt(3)).toEqual({
      title: "3 subtasks are still open",
      body: 'Complete them too? "Only this task" completes this task and leaves them open.',
      confirmLabel: "Complete all",
      cancelLabel: "Only this task",
    });
    expect(completePrompt(1)?.title).toBe("1 subtask is still open");
    expect(completePrompt(1)?.body).toMatch(/^Complete it too\?/);
  });

  it("defaults to Only this task: focused, and what Escape means", () => {
    expect(CASCADE_DEFAULTS.complete).toBe(false);
    const dialog = read("components/SubtaskCascade.tsx");
    // Focus opens on the cancel button, which is "Only this task".
    expect(dialog).toMatch(/defaultFocus="cancel"/);
    // Escape and the close button call onCancel, which answers false.
    expect(dialog).toMatch(/onCancel=\{\(\) => onAnswer\(false\)\}/);
    expect(dialog).toMatch(/onConfirm=\{\(\) => onAnswer\(true\)\}/);
    // A question, not a delete: no destructive red.
    expect(dialog).toMatch(/confirmVariant="primary"/);
  });

  it("never draws a prompt for a task with no open subtasks", () => {
    const dialog = read("components/SubtaskCascade.tsx");
    expect(dialog).toMatch(/if \(open <= 0\) return Promise\.resolve\(false\);/);
  });
});

describe("the move and archive box (decision 4)", () => {
  it("starts ticked", () => {
    expect(CASCADE_DEFAULTS.move).toBe(true);
    expect(CASCADE_DEFAULTS.archive).toBe(true);
  });

  it("draws the count, ticked, and nothing at 0", () => {
    const on = renderToStaticMarkup(
      createElement(IncludeSubtasksBox, { count: 3, checked: true, onChange: () => {} }),
    );
    expect(on).toContain("Include 3 subtasks");
    expect(on).toMatch(/type="checkbox"[^>]*checked=""/);
    const none = renderToStaticMarkup(
      createElement(IncludeSubtasksBox, { count: 0, checked: true, onChange: () => {} }),
    );
    expect(none).toBe("");
  });

  it("says a ticked move over a hidden subtask is refused", () => {
    const html = renderToStaticMarkup(
      createElement(IncludeSubtasksBox, {
        count: 2, hidden: 1, checked: true, onChange: () => {},
      }),
    );
    expect(html).toContain("Include 3 subtasks");
    expect(html).toContain("1 subtask is hidden from you");
  });

  it("the dialogs seed their box from the defaults", () => {
    for (const file of [
      "app/projects/components/MoveTasksDialog.tsx",
      "app/projects/components/PromoteFields.tsx",
      "app/tasks/components/SubtaskPromptHost.tsx",
    ]) {
      expect(read(file), file).toMatch(/useState<boolean>\(CASCADE_DEFAULTS\.(move|archive)\)/);
    }
    expect(read("app/projects/page.tsx")).toMatch(
      /setArchiveSubtasks\(CASCADE_DEFAULTS\.archive\)/,
    );
  });

  it("labels the box", () => {
    expect(includeSubtasksLabel(1)).toBe("Include 1 subtask");
    expect(includeSubtasksLabel(4)).toBe("Include 4 subtasks");
  });
});

describe("the bulk summary", () => {
  const rows = [
    { id: "a", subtasks: { done: 1, total: 3 } },
    { id: "b", subtasks: { done: 2, total: 2 } },
    { id: "c", subtasks: { done: 0, total: 3 } },
    { id: "d" },
  ];

  it("counts only the selected parents with open subtasks", () => {
    const got = bulkSubtaskSummary(rows, new Set(["a", "b", "c", "d"]));
    expect(got).toEqual({ parents: 2, open: 5 });
    expect(bulkSummaryLine(got)).toBe("includes 2 parents with 5 open subtasks");
  });

  it("says nothing when no selected task has an open subtask", () => {
    expect(bulkSummaryLine(bulkSubtaskSummary(rows, new Set(["b", "d"])))).toBeNull();
  });

  it("is drawn by both bulk bars", () => {
    expect(read("app/projects/page.tsx")).toMatch(
      /subtaskSummary=\{bulkSummaryLine\(selectionSubtasks\)\}/,
    );
    expect(read("app/tasks/components/ItemList.tsx")).toMatch(/bulkSummaryLine\(/);
  });
});

describe("the receipts", () => {
  it("name what the server did", () => {
    expect(completeReceipt(0)).toBe("Completed");
    expect(completeReceipt(3)).toBe("Completed · and 3 subtasks");
    expect(archiveReceipt(0)).toBe("Task archived");
    expect(archiveReceipt(1)).toBe("Archived · and 1 subtask");
    expect(moveReceipt(1, 3, "Website relaunch")).toBe("Moved 4 tasks to Website relaunch");
    expect(moveReceipt(1, 0)).toBe("Moved 1 task");
  });
});

describe("Undo covers the cascade (D79)", () => {
  const changes = [
    { task_id: "p", from_status_id: "todo", to_status_id: "done" },
    { task_id: "k1", from_status_id: "review", to_status_id: "done" },
    { task_id: "k2", from_status_id: "todo-b", to_status_id: "shipped" },
    { task_id: "k3", from_status_id: "todo", to_status_id: "done" },
  ];

  it("puts each task back to its exact prior status, with If-Match", async () => {
    const now: Record<string, { status_id: string; updated_at: string }> = {
      p: { status_id: "done", updated_at: "v1" },
      k1: { status_id: "done", updated_at: "v2" },
      k2: { status_id: "shipped", updated_at: "v3" },
      // A teammate moved k3 on after the complete: it is KEPT.
      k3: { status_id: "archived-lane", updated_at: "v4" },
    };
    const write = vi.fn(async () => ({}));
    const got = await revertCascade(changes, { read: async (id) => now[id], write });
    expect(write.mock.calls).toEqual([
      ["p", "todo", "v1"],
      ["k1", "review", "v2"],
      ["k2", "todo-b", "v3"],
    ]);
    expect(got.restored).toEqual(["p", "k1", "k2"]);
    expect(got.movedSince).toEqual(["k3"]);
    expect(revertSummary(got)).toMatch(/^1 task was changed by someone since/);
  });

  it("keeps a move that lands between the read and the write (412)", async () => {
    const refused = Object.assign(new Error("changed"), { status: 412 });
    const got = await revertCascade(changes.slice(0, 1), {
      read: async () => ({ status_id: "done", updated_at: "v1" }),
      write: async () => {
        throw refused;
      },
    });
    expect(got).toEqual({ restored: [], movedSince: ["p"], failed: [] });
  });

  it("names any other failure, and goes on with the rest", async () => {
    let calls = 0;
    const got = await revertCascade(changes.slice(0, 2), {
      read: async (id) => ({ status_id: id === "p" ? "done" : "done", updated_at: "v" }),
      write: async () => {
        calls += 1;
        if (calls === 1) throw new Error("gateway down");
        return {};
      },
    });
    expect(got.failed).toEqual([{ id: "p", reason: "gateway down" }]);
    expect(got.restored).toEqual(["k1"]);
  });

  it("the Projects complete records the cascade's Undo, parent first", () => {
    const hook = read("app/projects/components/useSubtaskComplete.tsx");
    expect(hook).toMatch(
      /\{ task_id: task\.id, from_status_id: task\.status_id, to_status_id: statusId \},\s*\.\.\.\(fresh\.subtask_changes \?\? \[\]\)/,
    );
    expect(hook).toMatch(/undoApi\.record\(/);
    expect(hook).toMatch(/action: \{ label: "Undo"/);
  });
});
