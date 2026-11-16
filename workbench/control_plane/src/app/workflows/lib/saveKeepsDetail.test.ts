/**
 * RUNG 0 (task 3382): a Save must not drop the fields the save answer lacks.
 * `PUT /workflows/{id}` answers with no `versions`, `hook_url` or `hook_path`, and the
 * page reads `detail.versions.length`, so `setDetail(<the save answer>)` crashed the page
 * ("This page did not load") on every Save, Validate, Test and Publish.
 * Fence: the save path merges the answer into the detail it already holds.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PAGE = readFileSync(join(__dirname, "../../studio/workflows/[id]/page.tsx"), "utf-8");

describe("the editor save keeps the detail it already holds", () => {
  it("merges the answer of updateWorkflow, and never sets it raw", () => {
    const at = PAGE.indexOf("await updateWorkflow(id");
    expect(at).toBeGreaterThan(0);
    const block = PAGE.slice(at, at + 600);
    expect(block).toMatch(/setDetail\(\(d\) => \(d \? \{ \.\.\.d, \.\.\.wf \} : wf\)\)/);
    expect(block).not.toMatch(/setDetail\(wf\)/);
  });
});
