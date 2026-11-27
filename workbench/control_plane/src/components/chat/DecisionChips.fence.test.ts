/**
 * Source fence for the AgentChat wiring of the Undo chips (HRD-12b).
 * `settledKey` must be null while a reply streams. A read then would race the
 * turn that keeps or drops an item. Mutation: pass `messages.length` alone and
 * the first test goes red.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("AgentChat mounts DecisionChips", () => {
  const src = readFileSync(resolve(__dirname, "../AgentChat.tsx"), "utf-8");

  it("passes a settledKey that is null while a reply streams", () => {
    const m = src.match(/<DecisionChips\b[^>]*>/);
    expect(m).not.toBeNull();
    expect(m![0]).toMatch(/settledKey=\{isLoading \? null : [^}]+\}/);
  });

  it("mounts it for the member's own assistant chat only", () => {
    expect(src).toMatch(/<DecisionChips\b[^>]*enabled=\{ownAssistantChat\}/);
  });
});
