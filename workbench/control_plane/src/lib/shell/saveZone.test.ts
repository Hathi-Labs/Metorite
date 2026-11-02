/**
 * The shell saves the browser's zone once (RX-5, `saveZone.ts`).
 *
 * The rule that must not break: it replaces the default and nothing else.
 */

import { readFileSync } from "node:fs";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveBrowserZone, zoneToSave } from "./saveZone";

const KOLKATA = "Asia/Kolkata";

const shell = () =>
  readFileSync(new URL("../../components/AppShell.tsx", import.meta.url), "utf8");

describe("zoneToSave", () => {
  it.each([
    [undefined, KOLKATA, KOLKATA],
    [null, KOLKATA, KOLKATA],
    ["", KOLKATA, KOLKATA],
    ["UTC", KOLKATA, KOLKATA],
  ])("replaces the default: %s", (saved, browser, want) => {
    expect(zoneToSave(saved, browser)).toBe(want);
  });

  it.each([
    ["Europe/London", KOLKATA],
    ["America/Los_Angeles", KOLKATA],
    ["Etc/UTC", KOLKATA],
    ["Not/AZone", KOLKATA],
  ])("never replaces another zone: %s", (saved, browser) => {
    expect(zoneToSave(saved, browser)).toBeNull();
  });

  it.each([
    ["UTC", undefined],
    ["UTC", ""],
    ["UTC", "UTC"],
    ["UTC", "Etc/UTC"],
  ])("has nothing to save when the browser knows no better: %s %s", (saved, browser) => {
    expect(zoneToSave(saved, browser)).toBeNull();
  });
});

describe("saveBrowserZone", () => {
  let stored: Map<string, string>;
  let writes: Array<Record<string, unknown>>;
  let reads: number;

  function gateway(saved: string, status = 200) {
    return vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        writes.push(JSON.parse(String(init.body)));
        return new Response("{}", { status: 200 });
      }
      reads += 1;
      return new Response(JSON.stringify({ timezone: saved }), { status });
    });
  }

  beforeEach(() => {
    stored = new Map();
    writes = [];
    reads = 0;
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => stored.get(k) ?? null,
      setItem: (k: string, v: string) => void stored.set(k, v),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("writes the zone through the one route when the saved zone is the default", async () => {
    const fetcher = gateway("UTC");
    vi.stubGlobal("fetch", fetcher);
    await saveBrowserZone("m|o", KOLKATA);
    expect(writes).toEqual([{ timezone: KOLKATA }]);
    // RX-7: the route needs no app feature. `/api/tasks/settings` does.
    // Mutation: point `memberZone.ts` back at `/api/tasks/settings`, and this fails.
    expect(fetcher.mock.calls[0][0]).toBe("/api/auth/me/zone");
    expect(fetcher.mock.calls[1][0]).toBe("/api/auth/me/zone");
  });

  it("writes nothing over a zone that is not the default", async () => {
    vi.stubGlobal("fetch", gateway("Europe/London"));
    await saveBrowserZone("m|o", KOLKATA);
    expect(writes).toEqual([]);
  });

  it("checks once for the member and the org", async () => {
    vi.stubGlobal("fetch", gateway("Europe/London"));
    await saveBrowserZone("m|o", KOLKATA);
    await saveBrowserZone("m|o", KOLKATA);
    expect(reads).toBe(1);
    await saveBrowserZone("m|other", KOLKATA);
    expect(reads).toBe(2);
  });

  it("tries again on the next load when the read fails, and shows nothing", async () => {
    vi.stubGlobal("fetch", gateway("UTC", 503));
    await expect(saveBrowserZone("m|o", KOLKATA)).resolves.toBeUndefined();
    expect(writes).toEqual([]);
    await saveBrowserZone("m|o", KOLKATA);
    expect(reads).toBe(2);
  });
});

describe("the shell", () => {
  it("runs the save once for a signed-in member, with no feature test", () => {
    expect(shell()).toMatch(/useSaveZone\(inShell \? signedInAs : null, cacheOrg\)/);
    expect(shell()).not.toMatch(/useSaveZone\([^)]*canUseFeature/);
  });

  it("is the one rule that every client asks before it writes a zone (RX-7)", () => {
    const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
    // Mutation: write `browserTimeZone()` in either place again, and this fails.
    expect(read("../../app/tasks/lib/taskStore.ts")).toContain("zoneToSave(settings.timezone, browserTimeZone())");
    expect(read("../../app/calendar/CalendarView.tsx")).toContain("zoneToSave(settings.timezone, tz)");
  });
});
