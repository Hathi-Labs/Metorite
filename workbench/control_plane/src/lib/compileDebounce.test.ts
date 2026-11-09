// The wait before a React artifact compiles (task 2432, F1). The rate limit
// of the compile route counts each compile, so a live preview must send one
// compile for each pause in the typing, not one for each key press.
//
// Mutations, each turns a named case red:
//   - a debounce of 0 -> "waits for a pause".
//   - a first source that waits -> "compiles the first source at once".
//   - SandboxedReact calls setTimeout itself, or compiles with no schedule
//     -> "SandboxedReact schedules every compile through scheduleCompile".
import { readFileSync } from "node:fs";
import path from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { COMPILE_DEBOUNCE_MS, compileDelay, scheduleCompile } from "./compileDebounce";

describe("compileDelay", () => {
  it("compiles the first source at once, so a chat card shows with no wait", () => {
    expect(compileDelay(true)).toBe(0);
  });

  it("waits for a pause before it compiles a changed source", () => {
    expect(COMPILE_DEBOUNCE_MS).toBeGreaterThanOrEqual(300);
    expect(compileDelay(false)).toBe(COMPILE_DEBOUNCE_MS);
  });
});

describe("scheduleCompile", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs a changed source only after the pause, and a cancel drops it", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    scheduleCompile(false, run);
    vi.advanceTimersByTime(COMPILE_DEBOUNCE_MS - 1);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(run).toHaveBeenCalledTimes(1);

    const dropped = vi.fn();
    const cancel = scheduleCompile(false, dropped);
    cancel();
    vi.advanceTimersByTime(COMPILE_DEBOUNCE_MS * 2);
    expect(dropped).not.toHaveBeenCalled();
  });

  it("runs the first source on the next tick", () => {
    vi.useFakeTimers();
    const run = vi.fn();
    scheduleCompile(true, run);
    vi.advanceTimersByTime(0);
    expect(run).toHaveBeenCalledTimes(1);
  });
});

describe("SandboxedReact schedules every compile through scheduleCompile", () => {
  // vitest runs in node here, with no DOM, so this half reads the component.
  const src = readFileSync(
    path.join(process.cwd(), "src", "components", "SandboxedReact.tsx"),
    "utf8",
  );

  it("imports scheduleCompile and calls it in the compile effect", () => {
    expect(src).toMatch(/import \{ scheduleCompile \} from "@\/lib\/compileDebounce";/);
    expect(src).toMatch(/scheduleCompile\(!compiledOnce\.current, \(\) => \{[\s\S]*?requestCompile\(code\)/);
  });

  it("sends a compile from nowhere else, and sets no timer of its own", () => {
    expect(src.match(/requestCompile\(/g)?.length).toBe(1);
    expect(src).not.toMatch(/setTimeout\(/);
  });
});
