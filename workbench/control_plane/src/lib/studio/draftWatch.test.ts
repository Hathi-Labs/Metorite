/**
 * STU-G3a fence (refresh): the IDE redraws only when the manifest changed, and it reads again
 * when a chat run ends.
 *
 * Mutation 1: make `nextDraft` return `next` always. "keeps the same object for the same manifest" turns red.
 * Mutation 2: make `wentIdle` true for idle to idle. "fires only when a run ends" turns red.
 * Mutation 3: make `draftHash` depend on key order. "ignores the order of keys" turns red.
 * Mutation 4: drop the `getPackageDraft` guard on a bad id. "reads the draft, and keeps the manifest" turns red.
 */
import { describe, expect, it } from "vitest";

import { nextDraft, wentIdle } from "./draftWatch";
import { draftHash, getPackageDraft, type PackageDraft } from "./packageDraft";

const ID = "123e4567-e89b-12d3-a456-426614174000";
const d = (manifest: unknown, over: Partial<PackageDraft> = {}): PackageDraft => ({
  id: ID, name: "A", manifest, liveVersion: null, objects: [], ...over,
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
    expect(out).toEqual({ id: ID, name: "Train", manifest: { id: "t" }, liveVersion: 3, objects: [{ name: "m" }] });
  });
  it("says off for a 404 and for a bad id, and fails with a sentence otherwise", async () => {
    await expect(getPackageDraft(ID, reply(404))).rejects.toThrow("studio_off");
    await expect(getPackageDraft("nope", reply(200))).rejects.toThrow("studio_off");
    await expect(getPackageDraft(ID, reply(500))).rejects.toThrow(/did not load/);
  });
});
