/**
 * When the first sign-in dialog shows (`welcomeSteps.ts`), when its step 2
 * is skipped (WS-62 CF-2), and the store that says it is open now.
 *
 * Mutation, seen to fail first: drop `!a.accessLoading`, and "never while
 * access resolves" fails. Return `a.chatFirst` from `skipsAppsStep`, and
 * "a desktop keeps step 2" fails.
 */
import { afterEach, describe, expect, it } from "vitest";

import { __resetWelcomeForTests, layoutQuestionOpen, setWelcomeOpen, skipsAppsStep, welcomeOpen } from "./welcomeSteps";

const BASE = { navOn: true, asking: true, reopened: false, step: "ask" as const, accessLoading: false };

afterEach(() => __resetWelcomeForTests());

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

describe("step 2 on a phone (WS-62 CF-2, chat_first_surface.md §4.7)", () => {
  it("a phone with the chat-first flag on skips the apps step", () => {
    expect(skipsAppsStep({ chatFirst: true, phone: true })).toBe(true);
  });

  it("a desktop keeps step 2, and so does a phone with the flag off", () => {
    expect(skipsAppsStep({ chatFirst: true, phone: false })).toBe(false);
    expect(skipsAppsStep({ chatFirst: false, phone: true })).toBe(false);
    expect(skipsAppsStep({ chatFirst: false, phone: false })).toBe(false);
  });
});

describe("the dialog says when it is open", () => {
  it("starts shut, and reports each change", () => {
    expect(welcomeOpen()).toBe(false);
    setWelcomeOpen(true);
    expect(welcomeOpen()).toBe(true);
    setWelcomeOpen(false);
    expect(welcomeOpen()).toBe(false);
  });
});
