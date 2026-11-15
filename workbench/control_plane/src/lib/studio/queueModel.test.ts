/**
 * STU-LY-6b fence: Next and Previous stop at the ends, never wrap, and the position reads "3 of 12".
 *
 * Mutation 1: let `queueStep` wrap round at the end. "stops at the last item" turns red.
 * Mutation 2: let an unknown id count as the first item in `queueStep`. "gives null for an unknown id" turns red.
 */
import { describe, expect, it } from "vitest";

import { queuePosition, queueStep } from "./queueModel";

const ids = ["a", "b", "c"];

describe("queueStep", () => {
  it("moves to the next and the previous item", () => {
    expect(queueStep(ids, "a", 1)).toBe("b");
    expect(queueStep(ids, "b", 1)).toBe("c");
    expect(queueStep(ids, "c", -1)).toBe("b");
  });
  it("stops at the last item and at the first, and never wraps", () => {
    expect(queueStep(ids, "c", 1)).toBeNull();
    expect(queueStep(ids, "a", -1)).toBeNull();
  });
  it("reads no open item as the first, and gives null for an unknown id or no item", () => {
    expect(queueStep(ids, null, 1)).toBe("b");
    expect(queueStep(ids, undefined, -1)).toBeNull();
    expect(queueStep(ids, "zzz", 1)).toBeNull();
    expect(queueStep([], "a", 1)).toBeNull();
  });
});

describe("queuePosition", () => {
  it("reads the place of the open item", () => {
    expect(queuePosition(ids, "b")).toBe("2 of 3");
    expect(queuePosition(ids, null)).toBe("1 of 3");
    expect(queuePosition(ids, "zzz")).toBe("1 of 3");
    expect(queuePosition([], "a")).toBeNull();
  });
});
