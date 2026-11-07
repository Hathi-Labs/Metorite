/**
 * STU-G3a fence (refresh): the IDE redraws only when the manifest changed, and it reads again
 * when a chat run ends.
 *
 * Mutation 1: make `nextDraft` return `next` always. "keeps the same object for the same manifest" turns red.
 * Mutation 2: make `wentIdle` true for idle to idle. "fires only when a run ends" turns red.
 * Mutation 3: make `draftHash` depend on key order. "ignores the order of keys" turns red.
 * Mutation 4: drop the `getPackageDraft` guard on a bad id. "says off for a 404 and for a bad id" turns red.
 * Mutation 5: drop `clearInterval`, the session unsubscribe or the listener removal from `startWatch`. "cleans up on stop" turns red.
 * Mutation 6: drop the `isActive` check on the idle path. "reads again when a run ends, and only then" turns red.
 * Mutation 7: let a poll go on after a 404. "stops polling after a 404" turns red.
 * Mutation 8: start a second GET while one runs. "gives one GET for two asks" turns red.
 */
import { describe, expect, it, vi } from "vitest";

import { POLL_MS, fetchDraft, nextDraft, startWatch, wentIdle } from "./draftWatch";
import { draftHash, getPackageDraft, type PackageDraft } from "./packageDraft";

const ID = "123e4567-e89b-12d3-a456-426614174000";
const d = (manifest: unknown, over: Partial<PackageDraft> = {}): PackageDraft => ({
  id: ID, name: "A", manifest, liveVersion: null, manifestHash: null, objects: [], ...over,
});

describe("draftHash", () => {
  it("ignores the order of keys, and tells a change", () => {
    expect(draftHash({ a: 1, b: { c: 2, d: 3 } })).toBe(draftHash({ b: { d: 3, c: 2 }, a: 1 }));
    expect(draftHash({ a: 1 })).not.toBe(draftHash({ a: 2 }));
    expect(draftHash(null)).toBe(draftHash(undefined));
  });
});

describe("nextDraft", () => {
  it("keeps the same object for the same manifest, so nothing redraws", () => {
    const prev = d({ a: 1, b: 2 });
    expect(nextDraft(prev, d({ b: 2, a: 1 }))).toBe(prev);
  });
  it("takes the new draft when the manifest, the name or the live version changed", () => {
    const prev = d({ a: 1 });
    const next = d({ a: 2 });
    expect(nextDraft(prev, next)).toBe(next);
    const renamed = d({ a: 1 }, { name: "B" });
    expect(nextDraft(prev, renamed)).toBe(renamed);
    const live = d({ a: 1 }, { liveVersion: 2 });
    expect(nextDraft(prev, live)).toBe(live);
    expect(nextDraft(null, next)).toBe(next);
  });
});

describe("wentIdle", () => {
  it("fires only when a run ends", () => {
    expect(wentIdle(true, false)).toBe(true);
    expect(wentIdle(false, false)).toBe(false);
    expect(wentIdle(true, true)).toBe(false);
    expect(wentIdle(false, true)).toBe(false);
  });
});

describe("getPackageDraft", () => {
  const reply = (status: number, body: unknown = {}) =>
    (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it("reads the draft, and keeps the manifest", async () => {
    const out = await getPackageDraft(ID, reply(200, {
      package: { id: ID, name: "Train", manifest: { id: "t" }, live_version: 3 }, objects: [{ name: "m" }, 4],
    }));
    expect(out).toEqual({ id: ID, name: "Train", manifest: { id: "t" }, liveVersion: 3, manifestHash: null, objects: [{ name: "m" }] });
  });
  it("keeps manifest_hash from the answer", async () => {
    const out = await getPackageDraft(ID, reply(200, { package: { id: ID, name: "A", manifest: {}, manifest_hash: "h1" } }));
    expect(out.manifestHash).toBe("h1");
  });
  it("says off for a 404 and for a bad id, and fails with a sentence otherwise", async () => {
    await expect(getPackageDraft(ID, reply(404))).rejects.toThrow("studio_off");
    await expect(getPackageDraft("nope", reply(200))).rejects.toThrow("studio_off");
    await expect(getPackageDraft(ID, reply(500))).rejects.toThrow(/did not load/);
  });
});

describe("startWatch: the wiring of the poll, the visibility and the chat sessions", () => {
  function rig() {
    const listeners = new Set<() => void>();
    const doc = new EventTarget() as unknown as Document;
    let state: "visible" | "hidden" = "visible";
    Object.defineProperty(doc, "visibilityState", { get: () => state });
    let tick: (() => void) | null = null;
    const cleared: unknown[] = [];
    let active = false;
    const refresh = vi.fn();
    const unsub = vi.fn(() => { listeners.clear(); });
    const stop = startWatch({
      refresh, doc,
      setIntervalFn: (fn, ms) => { tick = fn; expect(ms).toBe(POLL_MS); return 7 as unknown as ReturnType<typeof setInterval>; },
      clearIntervalFn: (h) => { cleared.push(h); },
      subscribe: (l) => { listeners.add(l); return unsub; },
      isActive: () => active,
      isStopped: () => false,
    });
    return {
      stop, refresh, unsub, cleared, doc,
      setActive(a: boolean) { active = a; listeners.forEach((l) => l()); },
      hide() { state = "hidden"; },
      show() { state = "visible"; },
      fire() { tick?.(); },
    };
  }

  it("reads at once, then on each tick while the page shows, and not while it is hidden", () => {
    const r = rig();
    expect(r.refresh).toHaveBeenCalledTimes(1);
    r.fire();
    expect(r.refresh).toHaveBeenCalledTimes(2);
    r.hide();
    r.fire();
    expect(r.refresh).toHaveBeenCalledTimes(2);
    r.show();
    r.doc.dispatchEvent(new Event("visibilitychange"));
    expect(r.refresh).toHaveBeenCalledTimes(3);
  });

  it("reads again when a run ends, and only then", () => {
    const r = rig();
    r.refresh.mockClear();
    r.setActive(true);
    expect(r.refresh).not.toHaveBeenCalled();
    r.setActive(false);
    expect(r.refresh).toHaveBeenCalledTimes(1);
    r.setActive(false);
    expect(r.refresh).toHaveBeenCalledTimes(1);
  });

  it("cleans up on stop: the timer, the session subscription and the visibility listener", () => {
    const r = rig();
    r.stop();
    expect(r.cleared).toEqual([7]);
    expect(r.unsub).toHaveBeenCalledTimes(1);
    r.refresh.mockClear();
    r.doc.dispatchEvent(new Event("visibilitychange"));
    expect(r.refresh).not.toHaveBeenCalled();
  });

  it("stops polling after a 404", () => {
    const refresh = vi.fn();
    let off = false;
    let tick: (() => void) | null = null;
    const doc = new EventTarget() as unknown as Document;
    Object.defineProperty(doc, "visibilityState", { get: () => "visible" });
    startWatch({
      refresh, doc, setIntervalFn: (fn) => { tick = fn; return 1 as never; }, clearIntervalFn: () => {},
      subscribe: () => () => {}, isActive: () => false, isStopped: () => off,
    });
    refresh.mockClear();
    off = true;
    tick!();
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("fetchDraft: one GET for two asks (the double first read)", () => {
  it("gives one GET for two asks that overlap, and a new GET after it settles", async () => {
    let calls = 0;
    const f = (async () => {
      calls++;
      return new Response(JSON.stringify({ package: { id: ID, name: "A", manifest: {} } }), { status: 200 });
    }) as unknown as typeof fetch;
    const [a, b] = await Promise.all([fetchDraft(ID, f), fetchDraft(ID, f)]);
    expect(calls).toBe(1);
    expect(a).toBe(b);
    await fetchDraft(ID, f);
    expect(calls).toBe(2);
  });
});
