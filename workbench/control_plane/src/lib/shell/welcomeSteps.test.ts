/**
 * When the first sign-in dialog shows (`welcomeSteps.ts`).
 *
 * Mutation, seen to fail first: drop `!a.accessLoading`, and "never while
 * access resolves" fails.
 */
import { describe, expect, it } from "vitest";

import { layoutQuestionOpen } from "./welcomeSteps";

const BASE = { navOn: true, asking: true, reopened: false, step: "ask" as const, accessLoading: false };

describe("the first sign-in dialog", () => {
  it("shows when the member was never asked, or asks again, on step 1 or step 2", () => {
    expect(layoutQuestionOpen(BASE)).toBe(true);
    expect(layoutQuestionOpen({ ...BASE, step: "apps" })).toBe(true);
    expect(layoutQuestionOpen({ ...BASE, asking: false, reopened: true })).toBe(true);
  });

  it("never shows while access resolves, so step 2 never lists nothing", () => {
    expect(layoutQuestionOpen({ ...BASE, accessLoading: true })).toBe(false);
    expect(layoutQuestionOpen({ ...BASE, step: "apps", accessLoading: true })).toBe(false);
  });

  it("gives way to the invite step, and stays shut with the shell nav off", () => {
    expect(layoutQuestionOpen({ ...BASE, step: "invite" })).toBe(false);
    expect(layoutQuestionOpen({ ...BASE, navOn: false })).toBe(false);
    expect(layoutQuestionOpen({ ...BASE, asking: false })).toBe(false);
  });
});
