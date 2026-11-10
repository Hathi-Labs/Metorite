/**
 * `accountLeave.ts`, the one signal that a page leaves its account (WS-58
 * MD-2b fix round 2).
 *
 * Mutation, run by hand (2026-10-14): drop the `try` around a listener, and
 * "a throw never blocks" fails.
 */
import { describe, expect, it } from "vitest";

import { leaveAccount, onAccountLeave } from "./accountLeave";

describe("leaveAccount", () => {
  it("calls each listener once, and an unsubscribed one not at all", () => {
    const heard: string[] = [];
    const offA = onAccountLeave(() => heard.push("a"));
    const offB = onAccountLeave(() => heard.push("b"));
    offB();
    leaveAccount();
    offA();
    leaveAccount();
    expect(heard).toEqual(["a"]);
  });

  it("a throw never blocks the next listener or the caller", () => {
    const heard: string[] = [];
    const offA = onAccountLeave(() => {
      throw new Error("boom");
    });
    const offB = onAccountLeave(() => heard.push("b"));
    expect(() => leaveAccount()).not.toThrow();
    expect(heard).toEqual(["b"]);
    offA();
    offB();
  });
});
