/**
 * The layout reader (NS-7). "Never asked" opens the first sign-in question,
 * so only a real layout may say it. A body that is not a layout is a failed
 * read, and the shell then asks nothing.
 *
 * Mutation: make `asLayout` accept any object, and the `{}` and `[]` cases
 * fail. In a browser, the catch-all stub of `e2e/sidebar-fold.spec.ts` then
 * opens the question over every test.
 */
import { describe, expect, it } from "vitest";

import { EMPTY_SHELL } from "./presets";
import { asLayout } from "./shellPrefs";

describe("asLayout", () => {
  it("reads the gateway's layout", () => {
    expect(asLayout({ ...EMPTY_SHELL, answered: "skipped" })).toEqual({ ...EMPTY_SHELL, answered: "skipped" });
    expect(asLayout(EMPTY_SHELL)).toEqual(EMPTY_SHELL);
  });

  it("refuses a body that does not say whether the member was asked", () => {
    expect(asLayout({})).toBeNull();
    expect(asLayout([])).toBeNull();
    expect(asLayout(null)).toBeNull();
    expect(asLayout("null")).toBeNull();
    // `/auth/me` itself, which a loose stub can answer for this path.
    expect(asLayout({ email: "a@example.com", features: [], roles: ["member"] })).toBeNull();
  });
});
