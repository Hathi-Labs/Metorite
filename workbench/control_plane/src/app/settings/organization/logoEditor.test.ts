/**
 * The crop box's drag rules (`LogoEditor.tsx` `moveBox`): it moves and
 * resizes, and it never leaves the image or shrinks to nothing.
 */
import { describe, expect, it } from "vitest";

import { moveBox } from "./LogoEditor";

const start = { x: 10, y: 10, w: 100, h: 40 };

describe("the crop box", () => {
  it("moves with the drag", () => {
    expect(moveBox(start, "move", 5, 3, 400, 200)).toEqual({ x: 15, y: 13, w: 100, h: 40 });
  });
  it("stays inside the image when dragged past its edge", () => {
    expect(moveBox(start, "move", 1000, 1000, 400, 200)).toEqual({ x: 300, y: 160, w: 100, h: 40 });
    expect(moveBox(start, "move", -1000, -1000, 400, 200)).toEqual({ x: 0, y: 0, w: 100, h: 40 });
  });
  it("grows from the bottom-right corner", () => {
    expect(moveBox(start, "se", 20, 10, 400, 200)).toEqual({ x: 10, y: 10, w: 120, h: 50 });
  });
  it("grows from the top-left corner and keeps the far edge still", () => {
    expect(moveBox(start, "nw", -5, -5, 400, 200)).toEqual({ x: 5, y: 5, w: 105, h: 45 });
  });
  it("never shrinks below 8px", () => {
    const b = moveBox(start, "se", -1000, -1000, 400, 200);
    expect([b.w, b.h]).toEqual([8, 8]);
    const c = moveBox(start, "nw", 1000, 1000, 400, 200);
    expect([c.w, c.h]).toEqual([8, 8]);
  });
});
