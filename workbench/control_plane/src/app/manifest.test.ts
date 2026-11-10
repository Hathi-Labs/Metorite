import { describe, expect, it } from "vitest";

import manifest from "./manifest";

// Chrome offers to install only when these hold, so a break here silently
// removes the Android install nudge. `proxy.test.ts` fences the public paths.
describe("web manifest installability", () => {
  const m = manifest();
  it("has the fields Chrome requires", () => {
    expect(m.name).toBeTruthy();
    expect(m.start_url).toBe("/");
    expect(m.display).toBe("standalone");
    expect(m.id).toBe("/");
    expect(m.scope).toBe("/");
  });
  it("has a 192 and a 512 icon and a maskable one", () => {
    const sizes = (m.icons ?? []).map((i) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    expect((m.icons ?? []).some((i) => i.purpose === "maskable")).toBe(true);
  });
});
