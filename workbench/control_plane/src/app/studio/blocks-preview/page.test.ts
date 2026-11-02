/**
 * STU-D2a fence: the block preview route is gated, and no pane lists it.
 * A route with no entry in `HREF_FEATURES` is unguarded (`access.ts`), so the
 * longest-prefix match of `/studio` has to reach it.
 */
import { describe, expect, it } from "vitest";

import { featureForPath } from "@/lib/access";
import { PANES } from "@/lib/nav";

describe("/studio/blocks-preview", () => {
  it("is gated by the feature of /studio", () => {
    const studio = featureForPath("/studio");
    expect(studio).toBeTruthy();
    expect(featureForPath("/studio/blocks-preview")).toBe(studio);
  });

  it("is in no nav pane", () => {
    expect(PANES.some((p) => p.href.startsWith("/studio/blocks-preview"))).toBe(false);
  });
});
