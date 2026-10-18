/**
 * When the editor writes its Markdown (WS-61 KB-2b fix round). A key marks
 * the body pending, the pause or a flush sends it, and a silent flush sends
 * nothing and keeps it pending.
 */
import { describe, expect, it, vi } from "vitest";

import { createBodyWriter } from "./bodyWriter";

function setup(body = "typed") {
  const timers: Array<() => void> = [];
  const onChange = vi.fn();
  const onTouched = vi.fn();
  const serialize = vi.fn(() => body);
  const writer = createBodyWriter({
    serialize,
    onChange,
    onTouched,
    delayMs: 300,
    setTimer: (fn) => {
      timers.push(fn);
      return timers.length;
    },
    clearTimer: () => undefined,
  });
  return { writer, onChange, onTouched, serialize, timers };
}

describe("createBodyWriter", () => {
  it("does no write on a key, and sends the body after the pause", () => {
    const { writer, onChange, onTouched, serialize, timers } = setup();
    writer.touch();
    writer.touch();
    expect(serialize).not.toHaveBeenCalled();
    expect(onTouched).toHaveBeenCalledTimes(1);
    timers[timers.length - 1]();
    expect(onChange).toHaveBeenCalledWith("typed");
    expect(writer.pending()).toBe(false);
  });

  it("a flush sends at once, and a second flush has nothing to send", () => {
    const { writer, onChange } = setup();
    writer.touch();
    expect(writer.flush()).toBe("typed");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(writer.flush()).toBeNull();
  });

  it("a silent flush sends nothing and keeps the keys pending for a later Save", () => {
    const { writer, onChange } = setup();
    writer.touch();
    // A new view of the page stores the keys: the old view stays on screen.
    expect(writer.flush({ silent: true })).toBe("typed");
    expect(onChange).not.toHaveBeenCalled();
    expect(writer.pending()).toBe(true);
    // Save on the old view still gets the keys.
    expect(writer.flush()).toBe("typed");
    expect(onChange).toHaveBeenCalledWith("typed");
  });

  it("a flush with nothing pending writes nothing", () => {
    const { writer, serialize } = setup();
    expect(writer.flush()).toBeNull();
    expect(writer.flush({ silent: true })).toBeNull();
    expect(serialize).not.toHaveBeenCalled();
  });
});
