import { describe, expect, it } from "vitest";
import { isInterruptedReply } from "./chatInterrupted";

describe("isInterruptedReply", () => {
  it("is true only for an assistant reply that was still streaming", () => {
    expect(isInterruptedReply({ role: "assistant", streaming: true })).toBe(true);
  });

  it.each([
    "Here are the tasks:\n\n| Task | Owner |\n|---|---|\n| Fix | Asha |",
    "- one\n- two",
    "```\ncode\n```",
    "Created **Plan review**",
    "",
  ])("a settled reply is complete whatever its last character (%#)", (content) => {
    expect(isInterruptedReply({ role: "assistant", streaming: false, ...{ content } })).toBe(false);
    expect(isInterruptedReply({ role: "assistant", ...{ content } })).toBe(false);
  });

  it("a user or system message is never interrupted", () => {
    expect(isInterruptedReply({ role: "user", streaming: true })).toBe(false);
    expect(isInterruptedReply({ role: "system", streaming: true })).toBe(false);
    expect(isInterruptedReply(undefined)).toBe(false);
  });
});
