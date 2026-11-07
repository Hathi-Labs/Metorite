import { describe, expect, it } from "vitest";

import {
  PUBLISH_BUSY, PUBLISH_STALE, PUBLISH_UNKNOWN, getVersionManifest, publishBlock, publishDraft, publishedWords, syncProblems,
} from "./publish";
import { StudioOff } from "./realDoor";

const reply = (status: number, body: unknown): typeof fetch =>
  (async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })) as typeof fetch;
const HASH = "a".repeat(64);

describe("publishDraft", () => {
  it("sends the notes and the hash of the draft that was read", async () => {
    let seen: { url: string; init?: RequestInit } | null = null;
    const f = (async (url: string, init?: RequestInit) => {
      seen = { url, init };
      return new Response(JSON.stringify({ version: { version: 3 }, sync: { objects_created: ["a"], fields_added: [["a", "x"], ["a", "y"]] } }), { status: 201 });
    }) as unknown as typeof fetch;
    const r = await publishDraft("pkg", "  hi  ", HASH, f);
    expect(seen!.url).toBe("/api/studio/packages/pkg/versions");
    expect(JSON.parse(String(seen!.init?.body))).toEqual({ notes: "hi", based_on: HASH });
    expect(r).toEqual({ kind: "published", version: 3, created: ["a"], fieldsAdded: 2 });
    expect(publishedWords(r as never)).toBe("Published version 3. Studio added 1 new table and 2 new fields to your organization.");
  });
  it("says the draft changed on 409 draft_changed", async () => {
    expect(await publishDraft("p", "", HASH, reply(409, { detail: { code: "draft_changed", message: "x" } }))).toEqual({ kind: "stale", message: PUBLISH_STALE });
  });
  it("sends an out-of-sync refusal to the Problems", async () => {
    const r = await publishDraft("p", "", HASH, reply(409, { detail: { code: "objects_out_of_sync", problems: [{ object: "units", field: "site", message: "Type differs." }] } }));
    expect(r.kind).toBe("problems");
    expect((r as { problems: { path: string }[] }).problems[0].path).toBe("$.objects[units].fields[site]");
  });
  it("sends a 422 to the Problems at its path", async () => {
    const r = await publishDraft("p", "", HASH, reply(422, { detail: { message: "bad", errors: [{ path: "$.views[0]", message: "No object." }] } }));
    expect(r).toMatchObject({ kind: "problems", problems: [{ path: "$.views[0]", message: "No object." }] });
    expect((r as { message: string }).message).toMatch(/Nothing was published/);
  });
  it("says busy on 503, and may-have-worked on a network failure", async () => {
    expect(await publishDraft("p", "", HASH, reply(503, {}))).toEqual({ kind: "busy", message: PUBLISH_BUSY });
    const down = (async () => { throw new TypeError("x"); }) as unknown as typeof fetch;
    expect(await publishDraft("p", "", HASH, down)).toEqual({ kind: "unknown", message: PUBLISH_UNKNOWN });
  });
  it("throws StudioOff on 404", async () => {
    await expect(publishDraft("p", "", HASH, reply(404, {}))).rejects.toBeInstanceOf(StudioOff);
  });
  it("reads a 500 as a failure that published nothing", async () => {
    expect((await publishDraft("p", "", HASH, reply(500, {}))).kind).toBe("failed");
  });
});

describe("getVersionManifest and syncProblems", () => {
  it("reads the manifest of the version", async () => {
    expect(await getVersionManifest("p", 2, reply(200, { version: { manifest: { id: "a" } } }))).toEqual({ id: "a" });
  });
  it("fails with words", async () => {
    await expect(getVersionManifest("p", 2, reply(500, {}))).rejects.toThrow(/live version did not load/);
    await expect(getVersionManifest("p", 2, reply(404, {}))).rejects.toBeInstanceOf(StudioOff);
  });
  it("names a record with no field", () => {
    expect(syncProblems({ detail: { problems: [{ object: "u", message: "m" }] } })[0].path).toBe("$.objects[u]");
  });
});

describe("publishBlock", () => {
  const ok = { dirty: false, saving: false, fault: false, errors: 0, undoing: false, hash: HASH };
  it("is open when nothing blocks", () => expect(publishBlock(ok)).toBeNull());
  it("says why it is off", () => {
    expect(publishBlock({ ...ok, dirty: true })).toBe("Save your edits first. Publish sends the saved draft.");
    expect(publishBlock({ ...ok, saving: true })).toMatch(/save is running/);
    expect(publishBlock({ ...ok, undoing: true })).toMatch(/save is running/);
    expect(publishBlock({ ...ok, errors: 2 })).toMatch(/2 errors/);
    expect(publishBlock({ ...ok, fault: true })).toMatch(/does not parse/);
    expect(publishBlock({ ...ok, hash: null })).toMatch(/Reload/);
  });
});
