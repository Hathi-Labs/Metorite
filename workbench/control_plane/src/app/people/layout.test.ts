/**
 * The People app shell — which tab a route lights, and which tabs exist.
 *
 * Spec: `project-docs/specs/people_center_app.md` §5.
 *
 * Two claims worth a test and not a comment:
 *
 * 1. **`/people` must match EXACTLY.** As a prefix it matches every sibling
 *    route in the app, so without the exact rule the directory tab lights on
 *    the org chart, the workload dashboard and every other page — two tabs
 *    selected at once, everywhere.
 * 2. **The HR tab set is the SERVER's, not a guess.** Four surfaces refuse
 *    without `admin:members:read`. A tab bar that offers them anyway hands a
 *    plain member four links that answer 403, which reads as a broken app
 *    rather than as a permission they do not hold.
 */

import { describe, expect, it } from "vitest";

import { activeTabFor } from "./layout";

describe("which tab a People route lights", () => {
  it("lights the directory only on the directory itself", () => {
    expect(activeTabFor("/people")).toBe("directory");
  });

  it("does NOT light the directory on its sibling routes", () => {
    // The prefix trap. `/people/chart`.startsWith("/people") is true, so a
    // naive prefix match selects the directory on every page in the app.
    for (const path of [
      "/people/chart",
      "/people/search",
      "/people/dashboard",
      "/people/schedule",
      "/people/seats",
      "/people/me",
    ]) {
      expect(activeTabFor(path), path).not.toBe("directory");
    }
  });

  it("lights each surface on its own route", () => {
    expect(activeTabFor("/people/chart")).toBe("chart");
    expect(activeTabFor("/people/search")).toBe("search");
    expect(activeTabFor("/people/dashboard")).toBe("workload");
    expect(activeTabFor("/people/schedule")).toBe("schedule");
    expect(activeTabFor("/people/seats")).toBe("seats");
    expect(activeTabFor("/people/me")).toBe("me");
  });

  it("keeps a sub-route on its parent tab", () => {
    // `/people/dashboard/suggestions` is the rebalancing view, reached from
    // the workload page. It is that page's, not nobody's.
    expect(activeTabFor("/people/dashboard/suggestions")).toBe("workload");
  });

  it("lights NOTHING on a People route with no tab", () => {
    // `/people/quality` is linked from the workload page, and has no tab of
    // its own. Claiming "you are here" about the directory would be a lie.
    expect(activeTabFor("/people/quality")).toBe("");
  });

  /**
   * ⚠️ CHANGED 2026-09-23 (H-167). This read `activeTabFor("/people/overview")`
   * is `""`, and called the route "a D49 Center landing — kept routable and
   * unlinked".
   *
   * "Unlinked" was accurate. Nobody decided it. D49 withdrew Centers on
   * 2026-08-24 and this page's only door went with them, so a built, tested,
   * working surface sat unreachable for a month while this assertion held it
   * there. The 2026-09-20 pass that built this bar reached six surfaces and
   * missed this one, because its door was a withdrawn Center and not a
   * missing tab.
   */
  it("puts Overview on a tab of its own", () => {
    expect(activeTabFor("/people/overview")).toBe("overview");
  });

  it("lights nothing outside the app", () => {
    expect(activeTabFor("/projects")).toBe("");
    // A route that merely starts with the same letters is not this app.
    expect(activeTabFor("/people-analytics")).toBe("");
  });
});
