// R7 fence for task 3340 (row E7b). It reads three files as text:
// globals.css, xyflow's style sheet and the editor page.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { CANVAS_STYLE } from "./canvasLook";

const ROOT = process.cwd();
const read = (...p: string[]) => readFileSync(path.join(ROOT, ...p), "utf-8");

const style = CANVAS_STYLE as unknown as Record<string, string>;

function lightBlockProperties(): string[] {
  const css = read("src", "app", "globals.css");
  const start = css.indexOf("\n.light {");
  expect(start).toBeGreaterThan(-1);
  const end = css.indexOf("\n}", start);
  const block = css.slice(start, end);
  return [...block.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)].map((m) => m[1]);
}

describe("CANVAS_STYLE", () => {
  it("sets inherit for each custom property of the .light block", () => {
    const names = lightBlockProperties();
    expect(names.length).toBeGreaterThan(20);
    for (const n of names) expect(style[n], n).toBe("inherit");
  });

  it("holds no colour literal", () => {
    for (const [k, v] of Object.entries(style)) {
      expect(v, k).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(v, k).not.toMatch(/\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/i);
    }
  });

  it("maps only real xyflow variables", () => {
    const xy = read("node_modules", "@xyflow", "react", "dist", "style.css");
    const keys = Object.keys(style).filter((k) => k.startsWith("--xy-"));
    expect(keys.length).toBeGreaterThan(10);
    for (const k of keys) expect(xy, k).toMatch(new RegExp(String.raw`var\(\s*${k},`));
  });

  it("makes the canvas background transparent", () => {
    expect(style["--xy-background-color"]).toBe("transparent");
  });
});

describe("the workflow editor page", () => {
  it("passes CANVAS_STYLE to ReactFlow", () => {
    const page = read("src", "app", "studio", "workflows", "[id]", "page.tsx");
    expect(page).toContain('from "@/app/workflows/lib/canvasLook"');
    expect(page).toMatch(/<ReactFlow[\s\S]*?style=\{CANVAS_STYLE\}[\s\S]*?>/);
  });
});
