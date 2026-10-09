/**
 * The crop box's drag rules (`LogoEditor.tsx` `moveBox`): it moves and
 * resizes, and it never leaves the image or shrinks to nothing.
 */
import { describe, expect, it } from "vitest";

import { moveBox, savePayload, type OwnDark } from "./LogoEditor";
import type { Rendered } from "@/lib/logoCanvas";

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

const drawn = (base64: string) => ({ base64 }) as Rendered;
const ownFile = (storedBase64?: string) => ({ name: "dark.png", storedBase64 }) as OwnDark;
const base = { main: drawn("MAIN"), savedMain: null, white: drawn("WHITE"), own: null, ownRendered: null, ownRemoveBg: true };

describe("what Save sends", () => {
  it("sends the drawn logo and, for the white style, its white version", () => {
    expect(savePayload({ ...base, chosen: "white" })).toEqual({ logoBase64: "MAIN", logoDarkBase64: "WHITE", darkStyle: "white" });
    expect(savePayload({ ...base, chosen: "plate" })).toEqual({ logoBase64: "MAIN", logoDarkBase64: null, darkStyle: "plate" });
  });

  it("sends the admin's own dark version with the own style", () => {
    const p = savePayload({ ...base, chosen: "own", own: ownFile(), ownRendered: drawn("OWN") });
    expect(p).toEqual({ logoBase64: "MAIN", logoDarkBase64: "OWN", darkStyle: "own" });
  });

  it("cannot save the own style before its file is drawn", () => {
    expect(savePayload({ ...base, chosen: "own" })).toBeNull();
    expect(savePayload({ ...base, chosen: "own", own: ownFile() })).toBeNull();
  });

  // ⚠️ Review, 2026-10-09: "Change dark mode" re-trimmed the saved logo and
  // removed its background, so a logo saved with its background lost it.
  it("sends a reopened logo's SAVED bytes, not a redrawn copy", () => {
    const p = savePayload({ ...base, savedMain: "SAVED", chosen: "plate" });
    expect(p?.logoBase64).toBe("SAVED");
  });

  it("sends a saved own version as saved, unless its background is removed now", () => {
    const own = ownFile("SAVEDDARK");
    const kept = savePayload({ ...base, savedMain: "SAVED", chosen: "own", own, ownRendered: drawn("REDRAWN"), ownRemoveBg: false });
    expect(kept?.logoDarkBase64).toBe("SAVEDDARK");
    const changed = savePayload({ ...base, savedMain: "SAVED", chosen: "own", own, ownRendered: drawn("REDRAWN"), ownRemoveBg: true });
    expect(changed?.logoDarkBase64).toBe("REDRAWN");
  });
});
