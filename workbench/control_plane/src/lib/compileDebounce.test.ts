// The wait before a React artifact compiles (task 2432, F1). The rate limit
// of the compile route counts each compile, so a live preview must send one
// compile for each pause in the typing, not one for each key press.
//
// Mutation: a debounce of 0 -> red. A first source that waits -> red.
import { describe, expect, it } from "vitest";

import { COMPILE_DEBOUNCE_MS, compileDelay } from "./compileDebounce";

describe("compileDelay", () => {
  it("compiles the first source at once, so a chat card shows with no wait", () => {
    expect(compileDelay(true)).toBe(0);
  });

  it("waits for a pause before it compiles a changed source", () => {
    expect(COMPILE_DEBOUNCE_MS).toBeGreaterThanOrEqual(300);
    expect(compileDelay(false)).toBe(COMPILE_DEBOUNCE_MS);
  });
});
