/**
 * Fail-open pin for the two access maps (WS-PKG, PKG-3b).
 *
 * `HREF_FEATURES` and `ALWAYS_ALLOWED` now come from the shell manifests. A
 * route with no entry is UNGUARDED, and a wrong always-allowed entry widens
 * access. So this file holds a FROZEN copy of both lists as they stood in
 * `access.ts` before the slice (commit acc5188d9). Change it only on purpose.
 * The Python twin is `tests/unit/test_shell_manifests.py`.
 */
import { describe, expect, it } from "vitest";

import { featureForPath, isAlwaysAllowed } from "./access";

const FROZEN_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ["/chat", "chat"],
  ["/email", "email"],
  ["/inbox", "email"],
  ["/whatsapp", "whatsapp"],
  ["/memory", "memory"],
  ["/tasks", "tasks"],
  ["/calendar", "tasks"],
  ["/notes", "notes"],
  ["/crm", "crm"],
  ["/projects", "projects"],
  ["/people", "people"],
  ["/knowledge", "knowledge"],
  ["/dashboard", "dashboard"],
  ["/observability", "observability"],
  ["/artifacts", "artifacts"],
  ["/workflows", "workflows"],
  ["/settings/models", "models"],
  ["/agents", "agents"],
  ["/approvals", "approvals"],
  ["/integrations", "integrations"],
  ["/apis", "integrations"],
  ["/build/agents", "build.agents"],
  ["/build/apps", "build.apps"],
];

const FROZEN_ALWAYS_ALLOWED = [
  "/",
  "/signin",
  "/settings",
  "/settings/profile",
  "/people/access",
  "/access",
  "/people/me",
  "/integrations",
];

describe("the manifest-fed access maps equal the pre-slice lists", () => {
  it("there are 23 pairs and 8 always-allowed routes", () => {
    expect(FROZEN_PAIRS).toHaveLength(23);
    expect(FROZEN_ALWAYS_ALLOWED).toHaveLength(8);
  });

  it("featureForPath gives the frozen feature for every href and for a deep path", () => {
    const wrong: string[] = [];
    for (const [href, feature] of FROZEN_PAIRS) {
      // An always-allowed exact route answers null on purpose.
      const want = FROZEN_ALWAYS_ALLOWED.includes(href) ? null : feature;
      if (featureForPath(href) !== want) wrong.push(href);
      // A deep path is never always-allowed, so the prefix feature applies.
      if (featureForPath(`${href}/x/y`) !== feature) wrong.push(`${href}/x/y`);
    }
    expect(wrong).toEqual([]);
  });

  it("/settings/models keeps its own slug under the shorter /settings entry", () => {
    expect(featureForPath("/settings/models")).toBe("models");
    expect(featureForPath("/settings/models/x")).toBe("models");
  });

  it("isAlwaysAllowed is true for each exact entry", () => {
    for (const href of FROZEN_ALWAYS_ALLOWED) expect(isAlwaysAllowed(href)).toBe(true);
  });

  it("isAlwaysAllowed matches EXACTLY, never a prefix", () => {
    for (const href of FROZEN_ALWAYS_ALLOWED) {
      if (href === "/") continue;
      expect(isAlwaysAllowed(`${href}/anything`), href).toBe(false);
    }
    expect(isAlwaysAllowed("/integrations/anything")).toBe(false);
    expect(isAlwaysAllowed("/crm")).toBe(false);
    expect(isAlwaysAllowed("/anything")).toBe(false);
  });

  it("the deep path under an always-allowed route stays gated", () => {
    expect(featureForPath("/integrations/anything")).toBe("integrations");
    expect(featureForPath("/people/anything")).toBe("people");
    expect(featureForPath("/people/me")).toBeNull();
  });
});
