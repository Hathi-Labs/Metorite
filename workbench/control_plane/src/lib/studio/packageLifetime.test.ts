/**
 * PKG-10e-2 fence: the end-date bar reads the server clock, shows nothing on a failed read,
 * and maps Keep and Restore to one outcome.
 *
 * Mutation 1: read `Date.now()` in `daysLeft`. "uses the now of the answer" turns red.
 * Mutation 2: round down in `daysLeft`. "rounds up" turns red.
 * Mutation 3: return an error from `getLifetime` on a 500. "maps ... to none" turns red.
 * Mutation 4: let a second press send. "a second press while one runs" turns red.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, peek, put } from "@/lib/dataCache";
import {
  type LifetimeInstall, archivedText, barState, barText, createLifetimeFlow, daysLeft, endsText, getLifetime,
  keepPackage, lifetimeKey, restorePackage,
} from "@/lib/studio/packageLifetime";

beforeEach(() => clearAll());

const NOW = "2026-11-11T12:00:00Z";
const install = (over: Partial<LifetimeInstall> = {}): LifetimeInstall => ({
  state: "active", ends_at: "2026-12-11T12:00:00Z", archived_at: null, restore_until: null, now: NOW, ...over,
});
const reply = (status: number, body: unknown): typeof fetch =>
  (async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status })) as typeof fetch;

describe("daysLeft", () => {
  it("rounds up", () => {
    expect(daysLeft("2026-11-12T12:00:01Z", NOW)).toBe(2);
    expect(daysLeft("2026-12-11T12:00:00Z", NOW)).toBe(30);
  });
  it("uses the now of the answer, not the browser clock", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    try {
      expect(daysLeft("2026-11-14T12:00:00Z", NOW)).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });
  it("gives 1 for one day, 0 for the same moment and a negative number for the past", () => {
    expect(daysLeft("2026-11-12T12:00:00Z", NOW)).toBe(1);
    expect(daysLeft(NOW, NOW)).toBe(0);
    expect(daysLeft("2026-11-09T12:00:00Z", NOW)).toBe(-2);
  });
});

describe("the texts", () => {
  it("reads days, one day and today", () => {
    expect(endsText(5)).toBe("Ends in 5 days");
    expect(endsText(1)).toBe("Ends in 1 day");
    expect(endsText(0)).toBe("Ends today");
    expect(endsText(-3)).toBe("Ends today");
  });
  it("names the restore date of an archived install", () => {
    const t = archivedText("2026-12-05T10:00:00Z");
    expect(t.startsWith("Archived. You can restore it until ")).toBe(true);
    expect(t.endsWith("2026.")).toBe(true);
    expect(archivedText(null)).toBe("Archived.");
  });
  it("draws the text of the state", () => {
    expect(barText(install())).toBe("Ends in 30 days");
    expect(barText(install({ state: "paused" }))).toBeNull();
  });
});

describe("barState", () => {
  it("is ends only for an active install with an end date", () => {
    expect(barState(install())).toBe("ends");
    expect(barState(install({ state: "paused" }))).toBe("none");
    expect(barState(install({ ends_at: null }))).toBe("none");
    expect(barState(install({ state: "archived", ends_at: null }))).toBe("archived");
    expect(barState(null)).toBe("none");
  });
});

describe("getLifetime", () => {
  it("maps an install to a bar", async () => {
    const got = await getLifetime("p1", reply(200, { install: install() }));
    expect(got).toEqual({ kind: "bar", install: install() });
  });
  it.each([
    ["200 with no install", reply(200, { install: null })],
    ["404", reply(404, { code: "Not found" })],
    ["500", reply(500, { message: "boom" })],
    ["a bad body", reply(200, "not json")],
    ["a body with a bad state", reply(200, { install: { ...install(), state: "weird" } })],
    ["a network error", (async () => { throw new Error("down"); }) as typeof fetch],
  ])("maps %s to none", async (_name, f) => {
    expect(await getLifetime("p1", f)).toEqual({ kind: "none" });
  });
  it("reads the lifetime route of the package", async () => {
    const f = vi.fn(reply(200, { install: null }));
    await getLifetime("a b", f);
    expect(f.mock.calls[0][0]).toBe("/api/studio/packages/a%20b/lifetime");
  });
});

describe("keepPackage and restorePackage", () => {
  it("map 200 to ok and post to the verb", async () => {
    const f = vi.fn(reply(200, { install: {} }));
    expect(await keepPackage("p1", f)).toEqual({ kind: "ok" });
    expect(await restorePackage("p1", f)).toEqual({ kind: "ok" });
    expect(f.mock.calls[0]).toEqual(["/api/studio/packages/p1/keep", { method: "POST" }]);
    expect(f.mock.calls[1]).toEqual(["/api/studio/packages/p1/restore", { method: "POST" }]);
  });
  it("carries the message of a 409", async () => {
    const got = await keepPackage("p1", reply(409, { code: "private_only", message: "Only a private app ends." }));
    expect(got).toEqual({ kind: "error", status: 409, message: "Only a private app ends." });
  });
  it("uses a short sentence when the body has no message", async () => {
    const got = await restorePackage("p1", reply(500, "oops"));
    expect(got.kind).toBe("error");
    expect(got.kind === "error" && got.message.length > 0).toBe(true);
  });
  it("maps a network error to an error", async () => {
    const got = await keepPackage("p1", (async () => { throw new Error("down"); }) as typeof fetch);
    expect(got).toMatchObject({ kind: "error", status: 0 });
  });
});

describe("the flow", () => {
  it("Keep sends once, sets kept and drops the caches", async () => {
    put(lifetimeKey("p1"), { kind: "bar", install: install() });
    put("studio:package-preview:pkg:p1", { id: "p1" });
    const keep = vi.fn(async () => ({ kind: "ok" as const }));
    const onChange = vi.fn();
    const flow = createLifetimeFlow("p1", { keep, onChange });
    await Promise.all([flow.press("keep"), flow.press("keep")]);
    expect(keep).toHaveBeenCalledTimes(1);
    expect(flow.state()).toEqual({ busy: false, kept: true, problem: null });
    expect(peek(lifetimeKey("p1"))).toBeUndefined();
    expect(peek("studio:package-preview:pkg:p1")).toBeUndefined();
  });
  it("Restore reports success and does not set kept", async () => {
    const restore = vi.fn(async () => ({ kind: "ok" as const }));
    const flow = createLifetimeFlow("p1", { restore, onChange: vi.fn() });
    expect(await flow.press("restore")).toBe(true);
    expect(flow.state().kept).toBe(false);
  });
  it("a refusal keeps the bar and shows the message", async () => {
    const keep = vi.fn(async () => ({ kind: "error" as const, status: 409, message: "Not now." }));
    const flow = createLifetimeFlow("p1", { keep, onChange: vi.fn() });
    await flow.press("keep");
    expect(flow.state()).toEqual({ busy: false, kept: false, problem: "Not now." });
  });
  it("is busy while the call runs", async () => {
    let release: (v: { kind: "ok" }) => void = () => {};
    const keep = vi.fn(() => new Promise<{ kind: "ok" }>((r) => { release = r; }));
    const flow = createLifetimeFlow("p1", { keep, onChange: vi.fn() });
    const p = flow.press("keep");
    expect(flow.state().busy).toBe(true);
    release({ kind: "ok" });
    await p;
    expect(flow.state().busy).toBe(false);
  });
});
