/**
 * STU-AF5 fence: the record page draws with the shared parts. It imports no CRM component, builds no
 * control by hand, reuses `Tabs`, the Detail block, the Form block and the table block, and writes
 * through the one door.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf-8").replace(/\r\n/g, "\n");
const record = read("./StudioRecord.tsx");
const page = read("./StudioPage.tsx");
const frame = read("../../app/studio/package-preview/AppFrame.tsx");
const model = read("../../lib/studio/recordPageModel.ts");

describe("StudioRecord", () => {
  it("imports no CRM code", () => {
    for (const src of [record, page, frame, model]) {
      expect(src).not.toMatch(/from "@\/app\/crm/);
      expect(src).not.toMatch(/from "\.\.\/(\.\.\/)*app\/crm/);
    }
  });

  it("reuses Tabs, the Detail block, the Form block and the table block", () => {
    expect(record).toContain('from "@/components/Tabs"');
    expect(record).toContain('from "@/components/studio/StudioDetail"');
    expect(record).toContain('from "@/components/studio/StudioForm"');
    expect(record).toContain("tableBlock");
    expect(record).toContain("GenerativeUINode");
  });

  it("builds no control by hand", () => {
    for (const src of [record, page]) {
      expect(src).not.toMatch(/<(button|select|input|textarea|dialog)[\s>]/);
    }
    expect(page).toContain('from "@/components/ui/Modal"');
    expect(page).toContain('from "@/components/ui/Button"');
  });

  it("writes through the door only, with the link filled by linkedDoor", () => {
    expect(record).toContain("linkedDoor(door");
    expect(record).not.toMatch(/fetch\(|XMLHttpRequest/);
    expect(model).not.toMatch(/fetch\(/);
  });

  it("gives a viewer no New button, and a disabled New and its reason when the create is not declared", () => {
    expect(record).toMatch(/owner \? \(\s*<Button/);
    expect(record).toContain("does not declare a create action");
  });

  it("opens the panel from a record link with no page load, and keeps the full page one column", () => {
    expect(frame).toContain("parseRecordLink");
    expect(frame).toContain("onClickCapture");
    expect(page).toContain("max-w-3xl");
    expect(page).toContain('data-record-page="full"');
  });

  it("adds no flag: it hangs on the frame flag only", () => {
    for (const src of [record, page, frame, model]) {
      const flags = (src.match(/NEXT_PUBLIC_STUDIO_[A-Z_]+/g) ?? []).filter((f) => f !== "NEXT_PUBLIC_STUDIO_APP_FRAME");
      expect(flags).toEqual([]);
    }
  });
});
