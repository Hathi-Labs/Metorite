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
    expect(activeTabFor("/people/me")).toBe("me");
  });

  it("keeps a sub-route on its parent tab", () => {
    // `/people/dashboard/suggestions` is the rebalancing view, reached from
    // the workload page. It is that page's, not nobody's.
    expect(activeTabFor("/people/dashboard/suggestions")).toBe("workload");
  });

  it("lights NOTHING on a People route with no tab", () => {
    // `/people/quality` is linked from the workload page and `/people/overview`
    // is a D49 Center landing — kept routable and unlinked. Neither has a tab,
    // and claiming "you are here" about the directory would be a lie.
    expect(activeTabFor("/people/quality")).toBe("");
    expect(activeTabFor("/people/overview")).toBe("");
  });

  it("lights nothing outside the app", () => {
    expect(activeTabFor("/projects")).toBe("");
    // A route that merely starts with the same letters is not this app.
    expect(activeTabFor("/people-analytics")).toBe("");
  });
});
