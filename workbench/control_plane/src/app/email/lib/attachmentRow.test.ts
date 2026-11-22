// The closed file list of a message keeps to one row (owner, 2026-11-06).
//
// R7 fence named here:
//   * `email-files-one-row`: the closed list draws the chips that fit and a
//     "+N more" chip, so its height never changes when the files arrive, and
//     the widths in `AttachmentList.tsx` match the arithmetic here.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { CHIP_PX, GAP_PX, MORE_PX, chipsInRow } from "./attachmentRow";

describe("email-files-one-row", () => {
  it("draws every chip when they fit, and before the row is measured", () => {
    expect(chipsInRow(0, 6)).toBe(6);
    expect(chipsInRow(560, 2)).toBe(2);
    expect(chipsInRow(CHIP_PX, 1)).toBe(1);
    expect(chipsInRow(560, 0)).toBe(0);
  });

  it("keeps room for the more chip when some do not fit", () => {
    // A thread card on a phone: one chip and "+1 more".
    expect(chipsInRow(316, 2)).toBe(1);
    expect(chipsInRow(560, 6)).toBe(2);
    expect(chipsInRow(1000, 6)).toBe(4);
  });

  it("never draws a second row at any width", () => {
    for (let width = 300; width <= 2000; width += 7) {
      for (let total = 1; total <= 12; total++) {
        const n = chipsInRow(width, total);
        expect(n).toBeGreaterThanOrEqual(1);
        expect(n).toBeLessThanOrEqual(total);
        const used = n * CHIP_PX + (n - 1) * GAP_PX + (n < total ? GAP_PX + MORE_PX : 0);
        expect(used, `${width} px, ${total} files`).toBeLessThanOrEqual(width);
      }
    }
  });

  it("matches the widths that the list draws", () => {
    const src = readFileSync(join(__dirname, "..", "components", "AttachmentList.tsx"), "utf-8");
    expect(src).toContain(`w-[${CHIP_PX}px]`);
    expect(src).toContain(`w-[${MORE_PX}px]`);
    // gap-2 is 8 px.
    expect(GAP_PX).toBe(8);
    expect(src).toContain('"flex flex-nowrap gap-2 overflow-hidden"');
  });
});
