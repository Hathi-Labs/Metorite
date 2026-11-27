import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** H-120 (#548): "Move to…" opened nothing on a phone, because MoveDialog was
 *  mounted in the desktop return alone. `overlays` is the one fragment that
 *  BOTH page returns render, so the dialog must live there. */
const src = readFileSync(join(__dirname, "page.tsx"), "utf-8").replace(/\r\n/g, "\n");
const start = src.indexOf("const overlays = (");
const end = src.indexOf("\n  );\n", start);
const overlays = src.slice(start, end);
const rest = src.slice(0, start) + src.slice(end);

describe("MoveDialog on a phone", () => {
  it("finds the overlays fragment", () => {
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
  });

  it("mounts MoveDialog inside overlays, which both layouts render", () => {
    expect(overlays).toContain("<MoveDialog");
  });

  it("wires it to the same open state the Move to entry sets", () => {
    expect(overlays).toMatch(/movingNode \? \(\s*<MoveDialog/);
    expect(overlays).toContain("onClose={() => setMovingNode(null)}");
    expect(overlays).toContain("moveNodeTo(movingNode, parentId)");
  });

  it("mounts no second MoveDialog outside overlays", () => {
    expect(rest).not.toContain("<MoveDialog");
  });

  it("renders overlays in both the phone and the desktop return", () => {
    expect(src.match(/\{overlays\}/g)?.length).toBeGreaterThanOrEqual(2);
  });
});
