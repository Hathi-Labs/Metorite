/** STU-AF3b fence: the hook of the rail counts reads through the door and shows no error. It is a source fence, because the suite has no DOM. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(resolve(__dirname, rel), "utf-8");

describe("useRailCounts", () => {
  const src = read("./useRailCounts.ts");
  it("reads through door.read only, and never runs an action", () => {
    expect(src).toContain("door.read(");
    expect(src).not.toMatch(/door\.run|fetch\(/);
  });
  it("keys each read with sourceKey, so a write that invalidates the door wakes it", () => {
    expect(src).toContain("sourceKey(door, source)");
    expect(src).toContain("useCachedResource");
  });
  it("reads at most the cap of counts, two at a time", () => {
    expect(src).toContain("MAX_NAV_COUNTS");
    expect(src).toMatch(/AT_ONCE = 2/);
  });
  it("gives a label and never an error text", () => {
    expect(src).toContain("countLabel(");
    expect(src).not.toMatch(/\.error\b.*label|label.*\.error\b/);
  });
  it("the frame draws the label as the muted meta of RailRow and holds no page model", () => {
    const frame = read("../../components/studio/StudioAppFrame.tsx");
    expect(frame).toContain("meta={rail.counts?.get(row.page)}");
    expect(frame).not.toMatch(/railCounts|pageModel|useRailCounts/);
  });
});
