/**
 * D-OP-10 (OP-7) — the delete dialog is drawn from the house primitives.
 *
 * Source tests: `vitest.config.ts` runs in `node`, with no DOM. They pin the
 * parts of the contract that a render would show: the sheet on a phone, the
 * whole row as the tap target, the two bulk buttons, the counts on the button,
 * and that the page sends the ids through the repeated query value.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const dialog = read("app/projects/components/DeleteTaskDialog.tsx");
const page = read("app/projects/page.tsx");
const api = read("app/projects/lib/api.ts");

describe("DeleteTaskDialog", () => {
  it("is a Modal that is a bottom sheet on a phone", () => {
    expect(dialog).toContain('import Modal from "@/components/ui/Modal"');
    expect(dialog).toContain('placement={isMobile ? "sheet" : "center"}');
  });
  it("uses the house Checkbox and Button, not a raw input", () => {
    expect(dialog).toContain('import Checkbox from "@/components/ui/Checkbox"');
    expect(dialog).toContain('import Button from "@/components/ui/Button"');
    expect(dialog).not.toContain('type="checkbox"');
  });
  it("makes the whole row the tap target", () => {
    expect(dialog).toMatch(/<label\s+className="flex min-h-9 cursor-pointer/);
  });
  it("has Keep all, Delete all, the summary and the counted button", () => {
    expect(dialog).toContain("Keep all");
    expect(dialog).toContain("Delete all");
    expect(dialog).toContain("summaryLine(counts)");
    expect(dialog).toContain("{confirmLabel(counts)}");
    expect(dialog).toContain("→ moves up");
  });
  it("starts with every box clear", () => {
    expect(dialog).toContain("useState<Set<string>>(new Set())");
  });
});

describe("the page and the client", () => {
  it("always draws the dialog for one task, so archived and hidden subtasks show", () => {
    expect(page).toContain("<DeleteTaskDialog");
    expect(page).toContain('{confirmingDelete?.kind === "one" ? (');
    expect(page).not.toContain("(doomedTask?.subtasks?.total ?? 0) > 0");
    expect(page).toContain('confirmingDelete?.kind !== "one"');
  });
  it("sends one flag, not a list, when every visible box is ticked", () => {
    expect(api).toContain("?delete_all_subtasks=true");
    expect(dialog).toContain("isAllTicked(ticked, list)");
    expect(page).toContain("{ deleteSubtaskIds, deleteAllSubtasks }");
  });
  it("lets the tag wrap under the title on a phone", () => {
    expect(dialog).toContain("basis-full pl-7");
    expect(dialog).toContain("flex-wrap");
  });
  it("sends the ticked ids as a repeated delete_subtask_ids value", () => {
    expect(api).toContain("delete_subtask_ids=${encodeURIComponent(id)}");
    expect(page).toContain("projectsApi.deleteTask(taskId, { deleteSubtaskIds, deleteAllSubtasks })");
  });
  it("tells the member how many hidden subtasks were kept", () => {
    expect(page).toContain("afterDeleteTitle(done.cascaded)");
  });
});
