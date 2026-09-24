/**
 * WS-27bm S8 visual review — the board keeps BOARD_MIN_REM beside the panel.
 * The 1440 case is the one the review photographed: the tree (240), the
 * panel (480) and the dock (416) left the board about 50px.
 */
import { describe, expect, it } from "vitest";

import { BOARD_MIN_REM, sidePanelFits } from "./sidePanelFit";

const REM = 16;
const base = { navWidth: 240, dockWidth: 416, panelWidth: 480, boardMinPx: BOARD_MIN_REM * REM };

describe("sidePanelFits", () => {
  it("refuses the panel at 1440 with the tree and the dock open", () => {
    // 1440 minus the app sidebar (256) is the row.
    expect(sidePanelFits({ ...base, rowWidth: 1184 })).toBe(false);
  });

  it("allows the panel at 1440 when the tree and the dock are closed", () => {
    expect(sidePanelFits({ ...base, rowWidth: 1184, dockWidth: 0, navWidth: 0 })).toBe(true);
  });

  it("still refuses at 1440 with only the tree open, which leaves 464px", () => {
    expect(sidePanelFits({ ...base, rowWidth: 1184, dockWidth: 0 })).toBe(false);
  });

  it("allows it on a wide screen with everything open", () => {
    expect(sidePanelFits({ ...base, rowWidth: 1664 })).toBe(true);
  });

  it("holds exactly at the minimum", () => {
    const rowWidth = 240 + 416 + 480 + BOARD_MIN_REM * REM;
    expect(sidePanelFits({ ...base, rowWidth })).toBe(true);
    expect(sidePanelFits({ ...base, rowWidth: rowWidth - 1 })).toBe(false);
  });

  it("follows the density: a larger rem needs a wider row", () => {
    const row = 240 + 416 + 480 + 32 * 16;
    expect(sidePanelFits({ ...base, rowWidth: row, boardMinPx: 32 * 18 })).toBe(false);
  });

  it("says yes before the first measurement", () => {
    expect(sidePanelFits({ ...base, rowWidth: 0 })).toBe(true);
  });
});
