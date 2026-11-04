/**
 * STU-D2b-1 fence: the real data door.
 *
 * Four things bind here.
 * 1. THE READ GUARD. `door.read` sends only `object:<name>.list` and `.get`.
 *    The BFF signs EVERY same-origin POST to the action route, so it cannot
 *    tell a read from a write. This guard is the only line.
 *    Mutation: make `isReadSource` accept any `object:` source. The test
 *    "refuses every source that is not a list or a get" turns red.
 * 2. Mount, focus and refresh read only. They go through `door.read` and
 *    reach the network as a list or a get, never a create, update or delete.
 *    Mutation: let `read` fall back to `run` for a write verb. The test
 *    "a mount, a focus and a refresh send no write" turns red.
 * 3. The flat rows: `{records:[{id, data}]}` becomes `{id, ...data}`.
 *    Mutation: return `rec.data` without the id. "flattens" turns red.
 * 4. The refusal map. A 422 keeps `fieldErrors`, `rule` and `message`.
 *    Mutation: drop `fieldErrors` in `mapAnswer`. "a 422 keeps" turns red.
 */
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, invalidate, read as cacheRead } from "@/lib/dataCache";
import { sourceKey } from "./dataDoor";
import {
  APPROVAL_SENTENCE, DoorReadError, STUDIO_OFF, createRealDoor, flattenRecords, getPackage, isReadSource,
  listPackages, mapAnswer, verbMap,
} from "./realDoor";
import { readRefusal } from "./formModel";

beforeEach(() => clearAll());

const PKG = "123e4567-e89b-12d3-a456-426614174000";
const ACTIONS = [
  { id: "find", uses: "object:visit.list" },
  { id: "peek", uses: "object:visit.get" },
  { id: "add", uses: "object:visit.create" },
  { id: "change", uses: "object:visit.update" },
  { id: "remove", uses: "object:visit.delete" },
  { id: "close_visit", uses: "object:visit.update", set: { status: "done" } },
];

interface Call { url: string; body: unknown }
function fakeFetch(status = 200, body: unknown = { records: [], total: 0 }, headers: Record<string, string> = {}) {
  const calls: Call[] = [];
  const impl = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(body), { status, headers });
  });
  return { impl: impl as unknown as typeof fetch, calls, spy: impl };
}

describe("the read guard", () => {
  const FORBIDDEN = [
    "object:visit.create", "object:visit.update", "object:visit.delete",
    "find", "add", "close_visit", "knowledge.list_pages", "integration:zoho.push",
    "workflow:123e4567-e89b-12d3-a456-426614174000", "object:visit.list/../x",
    "object:Visit.list", "object:visit.list ", "", "object:visit.", "object:.list",
  ];
  it.each(FORBIDDEN)("refuses every source that is not a list or a get: %j", async (action) => {
    const { impl, spy } = fakeFetch();
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    await expect(door.read({ action })).rejects.toBeInstanceOf(DoorReadError);
    expect(spy).not.toHaveBeenCalled();
  });
  it("refuses a source with no action, with no fetch", async () => {
    const { impl, spy } = fakeFetch();
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    await expect(door.read(undefined as never)).rejects.toBeInstanceOf(DoorReadError);
    await expect(door.read({ action: 7 as never })).rejects.toBeInstanceOf(DoorReadError);
    expect(spy).not.toHaveBeenCalled();
  });
  it("accepts a list and a get, and posts the declared id of each", async () => {
    const { impl, calls } = fakeFetch(200, { records: [], record: { id: "x", data: { a: 1 } } });
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    await door.read({ action: "object:visit.list", args: { limit: 5 } });
    await expect(door.read({ action: "object:visit.get", args: { id: "x" } })).resolves.toEqual([{ id: "x", a: 1 }]);
    expect(calls[0]).toEqual({ url: `/api/studio/apps/${PKG}/actions/find`, body: { limit: 5 } });
    expect(calls[1].url).toBe(`/api/studio/apps/${PKG}/actions/peek`);
  });
  it("refuses a read whose verb the package does not declare, with no fetch", async () => {
    const { impl, spy } = fakeFetch();
    const door = createRealDoor({ packageId: PKG, actions: [], fetchImpl: impl });
    await expect(door.read({ action: "object:visit.list" })).rejects.toThrow(/does not declare/);
    expect(spy).not.toHaveBeenCalled();
  });
  it("tests the shape alone", () => {
    expect(isReadSource("object:visit.list")).toBe(true);
    expect(isReadSource("object:visit.get")).toBe(true);
    expect(isReadSource("object:visit.update")).toBe(false);
    expect(isReadSource(null)).toBe(false);
  });
  it("never picks an action that carries a set for a verb", () => {
    expect(verbMap(ACTIONS)["object:visit.update"]).toBe("change");
    expect(verbMap([ACTIONS[5]])).toEqual({});
  });
});

describe("a mount, a focus and a refresh send no write", () => {
  it("reach the network as a list only", async () => {
    const { impl, calls } = fakeFetch(200, { records: [{ id: "a", data: { title: "A" } }] });
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    const src = { action: "object:visit.list" };
    const key = sourceKey(door, src);
    // The three paths of `useCachedResource`: the mount read, the focus
    // read (stale entry) and `refresh` (forced). Each runs `door.read`.
    await cacheRead(key, () => door.read(src));
    invalidate(key);
    await cacheRead(key, () => door.read(src));
    await cacheRead(key, () => door.read(src), { force: true });
    expect(calls.length).toBeGreaterThanOrEqual(2);
    for (const c of calls) expect(c.url).toBe(`/api/studio/apps/${PKG}/actions/find`);
  });
  it("hold no write in the bound reader: BoundSource calls door.read and never door.run", () => {
    const src = readFileSync(new URL("../../components/GenerativeUINode.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
    const start = src.indexOf("function BoundSource(");
    const end = src.indexOf("function Node(", start);
    const body = src.slice(start, end);
    expect(body).toContain("door.read(");
    expect(body).not.toMatch(/\.run\(/);
  });
  it("hold no write in the Table component: only the edit store writes", () => {
    const src = readFileSync(new URL("../../components/studio/StudioTable.tsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
    expect(src).not.toMatch(/door\.run\(/);
  });
});

describe("the rows", () => {
  it("flattens a list answer to flat rows, and the record id wins", () => {
    const rows = flattenRecords({
      records: [
        { id: "r1", object_id: "o", data: { title: "A", qty: 2, id: "spoof" }, created_by: "x" },
        { id: "r2", data: {} },
        { nope: true },
      ],
      total: 2,
    });
    expect(rows).toEqual([{ id: "r1", title: "A", qty: 2 }, { id: "r2" }]);
  });
  it("gives null for an answer with no records list", () => {
    expect(flattenRecords({})).toBeNull();
    expect(flattenRecords("x")).toBeNull();
  });
  it("a read of a bad answer fails with a plain sentence", async () => {
    const { impl } = fakeFetch(200, { oops: 1 });
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    await expect(door.read({ action: "object:visit.list" })).rejects.toThrow("That did not work. Try again.");
  });
});

describe("the refusal map", () => {
  it("a 422 keeps fieldErrors, rule and message, and fieldErrors becomes a map", () => {
    const res = mapAnswer(422, { detail: {
      code: "rule_refused", message: "A day holds at most 2 visits.", rule: "visit_limit",
      fieldErrors: [{ field: "title", message: "Taken." }],
    } });
    expect(res.ok).toBe(false);
    expect(res.message).toBe("A day holds at most 2 visits.");
    expect(readRefusal(res.data)).toEqual({
      fieldErrors: { title: "Taken." }, rule: "visit_limit", message: "A day holds at most 2 visits.",
    });
  });
  it("a 422 of a query names its field", () => {
    const res = mapAnswer(422, { detail: { code: "query_value_invalid", message: "Bad.", field: "qty" } });
    expect(readRefusal(res.data).fieldErrors).toEqual({ qty: "Bad." });
  });
  it("a 422 whose detail is a plain string still gives a sentence", () => {
    const res = mapAnswer(422, { detail: "Send the record id as a UUID in the key id of a JSON object." });
    expect(res.ok).toBe(false);
    expect(res.message).toContain("record id");
  });
  it("a 503 with Retry-After becomes a busy sentence with the wait", async () => {
    const { impl } = fakeFetch(503, { detail: { code: "store_busy", message: "raw text" } }, { "Retry-After": "1" });
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    const res = await door.run("object:visit.create", { data: { title: "x" } });
    expect(res.ok).toBe(false);
    expect(res.message).toBe("This app is busy now. Try again in 1 second.");
    expect(res.message).not.toContain("raw text");
  });
  it("a 503 with no wait says in a moment", () => {
    expect(mapAnswer(503, {}).message).toBe("This app is busy now. Try again in a moment.");
  });
  it("a 403 approval_required becomes a plain sentence", () => {
    const res = mapAnswer(403, { detail: { code: "approval_required", message: "raw text" } });
    expect(res).toEqual({ ok: false, message: APPROVAL_SENTENCE });
  });
  it("a 403 of another code and a 500 never show the raw text", () => {
    expect(mapAnswer(403, { detail: { code: "class_x", message: "raw" } }).message).not.toContain("raw");
    expect(mapAnswer(500, { error: "Error: boom at line 4" }).message).toBe("That did not work. Try again.");
  });
  it("a 404 for an object that is not published says to publish", () => {
    expect(mapAnswer(404, { detail: { code: "object_missing", message: "raw" } }).message).toMatch(/Publish the package/);
  });
  it("a 2xx is ok and keeps the body", () => {
    expect(mapAnswer(200, { record: { id: "a" } })).toEqual({ ok: true, data: { record: { id: "a" } } });
  });
});

describe("a write", () => {
  it("posts the declared id with the body, and refuses a verb that is not declared", async () => {
    const { impl, calls } = fakeFetch(200, { record: { id: "a", data: {} } });
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    expect((await door.run("object:visit.update", { id: "a", data: { title: "B" } })).ok).toBe(true);
    expect(calls).toEqual([{ url: `/api/studio/apps/${PKG}/actions/change`, body: { id: "a", data: { title: "B" } } }]);
    const none = createRealDoor({ packageId: PKG, actions: [], fetchImpl: impl });
    const res = await none.run("object:visit.delete", { id: "a" });
    expect(res.ok).toBe(false);
    expect(calls.length).toBe(1);
  });
  it("answers a network failure with a plain sentence", async () => {
    const impl = (async () => { throw new TypeError("fetch failed: ECONNRESET"); }) as unknown as typeof fetch;
    const door = createRealDoor({ packageId: PKG, actions: ACTIONS, fetchImpl: impl });
    const res = await door.run("object:visit.create", { data: {} });
    expect(res).toEqual({ ok: false, message: "That did not work. Try again." });
  });
  it("scopes its cache keys by the package id and ':live'", () => {
    expect(createRealDoor({ packageId: PKG, actions: ACTIONS }).scope).toBe(`${PKG}:live`);
  });
});

describe("the package routes", () => {
  it("an org outside STUDIO_ORGS gets 404, and the page reads it as off, not as an error", async () => {
    const { impl } = fakeFetch(404, { detail: "Not found" });
    await expect(listPackages(impl)).rejects.toThrow(STUDIO_OFF);
    await expect(getPackage(PKG, impl)).rejects.toThrow(STUDIO_OFF);
  });
  it("lists the packages and reads one with its declared actions and objects", async () => {
    const list = fakeFetch(200, { packages: [{ id: PKG, name: "Visits", status: "published", live_version: 2 }] });
    await expect(listPackages(list.impl)).resolves.toEqual([{ id: PKG, name: "Visits", status: "published", live_version: 2 }]);
    expect(list.calls[0].url).toBe("/api/studio/packages");
    const one = fakeFetch(200, {
      package: { id: PKG, name: "Visits", manifest: { actions: ACTIONS } },
      objects: [{ id: "o", name: "visit", label: "Visit", fields: [], rules: [] }],
    });
    const got = await getPackage(PKG, one.impl);
    expect(got.actions).toEqual(ACTIONS);
    expect(got.objects.map((o) => o.name)).toEqual(["visit"]);
    expect(one.calls[0].url).toBe(`/api/studio/packages/${PKG}`);
  });
  it("a package id that is no uuid makes no call", async () => {
    const { impl, spy } = fakeFetch();
    await expect(getPackage("../x", impl)).rejects.toThrow(STUDIO_OFF);
    expect(spy).not.toHaveBeenCalled();
  });
});
