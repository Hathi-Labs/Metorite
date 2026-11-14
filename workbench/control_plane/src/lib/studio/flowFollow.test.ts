/**
 * STU-D8 fence: following a live run. A fake stream and fake timers drive `followRun`.
 * Each test names the mutation it catches.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type RunRead, type StreamLike, followRun } from "./flowFollow";

class FakeStream implements StreamLike {
  onmessage: ((m: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  close() { this.closed = true; }
  send(frame: unknown) { this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) }); }
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(reads: RunRead[] = [], withStream = true) {
  const es = new FakeStream();
  const urls: string[] = [];
  const frames: unknown[] = [];
  const polled: RunRead[] = [];
  let i = 0;
  const read = vi.fn(async () => reads[Math.min(i++, reads.length - 1)]);
  const stop = followRun("r 1", {
    openStream: withStream ? (u) => { urls.push(u); return es; } : null,
    read, pollMs: 3000,
    onFrame: (f) => frames.push(f),
    onRead: (r) => polled.push(r),
  });
  return { es, urls, frames, polled, read, stop };
}

describe("followRun", () => {
  it("opens the stream of the run and passes each frame on", () => {
    const t = setup();
    expect(t.urls).toEqual(["/api/workflows/runs/r%201/stream"]);
    t.es.send({ event: "node", node_id: "a", status: "running" });
    t.es.send("not json");
    expect(t.frames).toEqual([{ event: "node", node_id: "a", status: "running" }]);
  });

  it("a run frame that ends the run closes the stream (mutation: keep it open)", () => {
    const t = setup();
    t.es.send({ event: "run", status: "running" });
    expect(t.es.closed).toBe(false);
    t.es.send({ event: "run", status: "succeeded" });
    expect(t.es.closed).toBe(true);
    t.es.send({ event: "node", node_id: "z", status: "ok" });
    expect(t.frames).toHaveLength(2);
  });

  it("a dropped stream falls back to a read every 3 s until the run ends", async () => {
    const t = setup([
      { status: "running", node_results: {} },
      { status: "running", node_results: { a: { status: "ok" } } },
      { status: "succeeded", node_results: { a: { status: "ok" } } },
    ]);
    t.es.onerror?.();
    expect(t.es.closed).toBe(true);
    expect(t.read).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(3000);
    expect(t.read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(t.polled.map((r) => r.status)).toEqual(["running", "running", "succeeded"]);
    await vi.advanceTimersByTimeAsync(9000);
    expect(t.read).toHaveBeenCalledTimes(3);
  });

  it("with no EventSource it polls from the start", async () => {
    const t = setup([{ status: "failed", node_results: {} }], false);
    expect(t.urls).toEqual([]);
    await vi.advanceTimersByTimeAsync(3000);
    expect(t.polled).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(9000);
    expect(t.read).toHaveBeenCalledTimes(1);
  });

  it("a failed read does not stop the poll", async () => {
    const t = setup([], false);
    t.read.mockRejectedValueOnce(new Error("x")).mockResolvedValue({ status: "succeeded", node_results: {} });
    await vi.advanceTimersByTimeAsync(3000);
    await vi.advanceTimersByTimeAsync(3000);
    expect(t.polled).toHaveLength(1);
  });

  it("stop closes the stream and ends the poll (mutation: leak the timer)", async () => {
    const t = setup([{ status: "running", node_results: {} }]);
    t.stop();
    expect(t.es.closed).toBe(true);
    t.es.onerror?.();
    await vi.advanceTimersByTimeAsync(9000);
    expect(t.read).not.toHaveBeenCalled();
    const p = setup([{ status: "running", node_results: {} }], false);
    await vi.advanceTimersByTimeAsync(3000);
    p.stop();
    await vi.advanceTimersByTimeAsync(9000);
    expect(p.read).toHaveBeenCalledTimes(1);
  });
});
