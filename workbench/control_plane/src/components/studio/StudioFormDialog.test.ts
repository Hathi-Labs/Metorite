/**
 * STU-AF6 fence: the add, edit and delete dialogs use the shared parts, hold no control of their own,
 * import no CRM code, write through the one door, and add no flag.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf-8").replace(/\r\n/g, "\n");
const dialog = read("./StudioFormDialog.tsx");
const button = read("./StudioNewButton.tsx");
const record = read("./StudioRecord.tsx");
const frame = read("../../app/studio/package-preview/AppFrame.tsx");
const model = read("../../lib/studio/dialogModel.ts");
const all = { dialog, button, record, frame, model };

describe("the Studio dialogs", () => {
  it("open the Form block in the shared Modal, as a sheet on a phone", () => {
    expect(dialog).toContain('from "@/components/ui/Modal"');
    expect(dialog).toContain('from "@/components/studio/StudioForm"');
    expect(dialog).toContain('"sheet"');
    expect(button).toContain("StudioFormDialog");
  });

  it("ask once through the shared ConfirmDialog before a delete", () => {
    expect(record).toContain("DeleteRecordButton");
    expect(read("./DeleteRecordButton.tsx")).toContain('from "@/components/ui/ConfirmDialog"');
  });

  it("build no control by hand and import no CRM code", () => {
    for (const src of Object.values(all)) {
      expect(src).not.toMatch(/<(button|select|input|textarea|dialog)[\s>]/);
      expect(src).not.toMatch(/from "@\/app\/crm/);
    }
  });

  it("write through door.run only: no fetch and no second writer", () => {
    for (const src of [dialog, button, model]) expect(src).not.toMatch(/fetch\(|XMLHttpRequest|\.run\(/);
    expect(read("./StudioForm.tsx")).toContain("sendRecord(door");
  });

  it("reuse the same dialog for New on a related table, with the link filled", () => {
    expect(record).toContain("StudioFormDialog");
    expect(record).toContain("linkedDoor(door");
    expect(record).not.toContain("<StudioForm ");
  });

  it("show a viewer no button, by the model", () => {
    expect(frame).toContain("newButtons(");
    expect(record).toContain("recordButtons(");
  });

  it("add no flag", () => {
    for (const src of Object.values(all)) {
      const flags = (src.match(/NEXT_PUBLIC_STUDIO_[A-Z_]+/g) ?? []).filter((f) => f !== "NEXT_PUBLIC_STUDIO_APP_FRAME");
      expect(flags).toEqual([]);
    }
  });
});
