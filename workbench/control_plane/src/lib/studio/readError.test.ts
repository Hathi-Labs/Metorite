/**
 * STU-LY-7 fence: a Retry is offered only for a read that a second try may fix.
 */
import { describe, expect, it } from "vitest";

import { retryableRead } from "./readError";
import { ACTION_FAILED } from "./TypedActionButton";
import { GONE_SENTENCE, NOT_DECLARED_SENTENCE, NOT_OPEN_SENTENCE, SIGN_IN_SENTENCE, busySentence } from "./realDoor";

describe("retryableRead", () => {
  it("retries a busy app, a lost network and the plain did-not-work answer", () => {
    expect(retryableRead(busySentence("5"))).toBe(true);
    expect(retryableRead(busySentence(null))).toBe(true);
    expect(retryableRead("Failed to fetch")).toBe(true);
    expect(retryableRead("NetworkError when attempting to fetch resource.")).toBe(true);
    expect(retryableRead(ACTION_FAILED)).toBe(true);
  });
  it("does not retry a refusal that would fail the same way", () => {
    for (const s of [GONE_SENTENCE, NOT_DECLARED_SENTENCE, NOT_OPEN_SENTENCE, SIGN_IN_SENTENCE,
      "The filter field is not known.", "Refused by max_len."]) expect(retryableRead(s), s).toBe(false);
  });
  it("does not retry an empty or missing message", () => {
    expect(retryableRead(null)).toBe(false);
    expect(retryableRead(undefined)).toBe(false);
    expect(retryableRead("")).toBe(false);
  });
});
