/**
 * STU-D17a fence: the sheet of line items draws with the shared parts. It imports no CRM code,
 * builds no control by hand, writes through the one path (`saveSheet`, which calls `door.run` once),
 * shares the cell editors with the Table, and adds no flag.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (p: string) => readFileSync(resolve(__dirname, p), "utf-8").replace(/\r\n/g, "\n");
const sheet = read("./StudioSheet.tsx");
const editors = read("./StudioCellEditors.tsx");
const table = read("./StudioTable.tsx");
const record = read("./StudioRecord.tsx");
const model = read("../../lib/studio/sheetModel.ts");
const paste = read("../../lib/studio/sheetPaste.ts");

describe("StudioSheet", () => {
  it("imports no CRM code", () => {
    for (const src of [sheet, editors, model, paste]) {
      expect(src).not.toMatch(/from "@\/app\/crm/);
      expect(src).not.toMatch(/from "\.\.\/(\.\.\/)*app\/crm/);
    }
  });

  it("uses the shared grid, Button and the one alert line, and builds no control by hand", () => {
    expect(sheet).toContain('from "@/components/genUITemplates"');
    expect(sheet).toContain('from "@/components/ui/Button"');
    expect(sheet).toContain('role="alert"');
    for (const src of [sheet, editors]) {
      expect(src).not.toMatch(/<(button|select|input|textarea|dialog)[\s>]/);
    }
    expect(editors).toContain('from "@/components/ui/Input"');
    expect(editors).toContain('from "@/components/ui/Checkbox"');
    expect(editors).toContain('from "@/components/ui/SelectButton"');
  });

  it("shares the cell editors with the Table, and keeps no second copy of TextEditor", () => {
    expect(sheet).toContain('from "@/components/studio/StudioCellEditors"');
    expect(table).toContain('from "@/components/studio/StudioCellEditors"');
    expect(table).not.toContain("function TextEditor");
    expect(sheet).not.toContain("function TextEditor");
  });

  it("has one write path: saveSheet, with one door.run, and no call in the component", () => {
    expect(sheet).toContain("saveSheet(door");
    expect(sheet).not.toContain("door.run");
    expect(sheet).not.toMatch(/fetch\(|XMLHttpRequest/);
    expect(model.match(/await door\.run\(/g)).toHaveLength(1);
    expect(paste).not.toMatch(/door\.|StudioDataDoor|dataDoor/);
  });

  it("judges no rule in TypeScript", () => {
    for (const src of [model, paste]) {
      expect(src).not.toMatch(/total|sum\(|reduce\(/);
    }
  });

  it("guards unsaved edits and gives a viewer no Save, no paste and no new row", () => {
    expect(sheet).toContain("useLeaveGuard(dirty");
    expect(sheet).toContain("canWrite ? (");
    expect(sheet).toContain("if (!canWrite || saving) return;");
    expect(model).toContain("if (!opts.canWrite) return");
  });

  it("is reached from the Lines tab of the record page only, and adds no flag", () => {
    expect(record).toContain("<StudioSheet");
    for (const src of [sheet, editors, model, paste]) {
      expect(src).not.toMatch(/NEXT_PUBLIC_STUDIO_[A-Z_]+/);
    }
  });
});
