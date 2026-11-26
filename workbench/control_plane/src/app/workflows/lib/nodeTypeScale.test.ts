/**
 * The node and the edge of the workflow editor draw their text on the type
 * scale: `text-xs` (12), `text-[11px]` or `text-[10px]` (spec
 * `ai_studio_rows/E7c-1.md`). The card drew 8, 8.5, 9, 9.5, 10.5 and 12.5 px.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `text-[9px]` returns to WorkflowNode.tsx -> "WorkflowNode draws on the scale";
 * - `text-[8.5px]` returns to WorkflowEdge.tsx -> "WorkflowEdge draws on the scale".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const COMPONENTS = new URL("../components/", import.meta.url);

/** The px sizes of the scale that the arbitrary class may name. */
const ALLOWED_PX = new Set(["10", "11"]);

const ARBITRARY = /text-\[(\d+(?:\.\d+)?)px\]/g;
/** `fontSize: 12`, `fontSize: "12px"`, `font-size: 12px`. */
const INLINE_PX = /fontSize:\s*(?:\d|["'`]\s*\d+(?:\.\d+)?px)|font-size:\s*\d+(?:\.\d+)?px/;

/** The lines of a file that draw a size off the scale. */
function offScale(text: string): string[] {
  const hits: string[] = [];
  text.split(/\r?\n/).forEach((line, i) => {
    for (const m of line.matchAll(ARBITRARY)) {
      if (!ALLOWED_PX.has(m[1])) hits.push(`${i + 1}: text-[${m[1]}px]`);
    }
    if (INLINE_PX.test(line)) hits.push(`${i + 1}: ${line.trim()}`);
  });
  return hits;
}

function read(name: string): string {
  return readFileSync(fileURLToPath(new URL(name, COMPONENTS)), "utf8");
}

describe("the workflow node and edge text", () => {
  it("WorkflowNode draws on the scale", () => {
    expect(offScale(read("WorkflowNode.tsx"))).toEqual([]);
  });

  it("WorkflowEdge draws on the scale", () => {
    expect(offScale(read("WorkflowEdge.tsx"))).toEqual([]);
  });

  it("the check sees each off-scale form", () => {
    for (const bad of ["text-[8.5px]", "text-[9px]", "text-[12.5px]", "fontSize: 12,", 'fontSize: "9px"'])
      expect(offScale(bad), bad).not.toEqual([]);
    for (const good of ["text-[10px]", "text-[11px]", "text-xs", 'fontSize: "0.75rem"'])
      expect(offScale(good), good).toEqual([]);
  });
});
