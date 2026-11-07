/**
 * The Waiting row's count (WS-AP AP-2a, `waiting_on_me.md` §5.1).
 *
 * Each claim is a way the row breaks:
 *
 *   1. The number IS the seam's count. A 0, the OFF state (404) and a failed
 *      read put no number on the row.
 *   2. A capped source makes the count a floor: "15+".
 *   3. One count path (§7.3): the hook adds nothing to the seam's count,
 *      and the agent questions never reach it.
 *   4. One poll: the hook joins `joinNeedsPoll` under its own key, and
 *      starts no timer of its own.
 *
 * Mutations, run by hand (2026-11-07):
 *   - put a failed read's last number on the row: "a failed read" fails;
 *   - add `agentRunsApi` to the hook: "one count path" fails;
 *   - read the count with a `setInterval` in the hook: "one poll" fails.
 */
import { readFileSync } from "node:fs";

import { afterEach, describe, expect, it, vi } from "vitest";

import { rowMeta, rowName } from "./navNeeds";
import {
  WAITING_COUNT_KEY,
  WAITING_HREF,
  fetchWaitingCount,
  readWaitingCount,
  withWaitingCount,
} from "./waitingCount";
import { NAV_SECTIONS } from "@/lib/nav";
import { PROJECTS_CACHE } from "@/app/projects/lib/api";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

describe("the count on the Waiting row", () => {
  it("names the pane of nav.ts", () => {
    const pane = NAV_SECTIONS.flatMap((s) => s.items).find((p) => p.href === WAITING_HREF);
    expect(pane?.label).toBe("Waiting");
  });

  it("reads the seam's count, and a capped source as a floor", () => {
    expect(readWaitingCount({ count: 4, capped: [], sources: {} })).toEqual({ count: 4, floor: false });
    expect(readWaitingCount({ count: 15, capped: ["decision"] })).toEqual({ count: 15, floor: true });
    expect(readWaitingCount({})).toEqual({ count: 0, floor: false });
    expect(readWaitingCount({ count: -3 })).toEqual({ count: 0, floor: false });
  });

  it("puts the number on the row, and draws it as the needs pill does", () => {
    const out = withWaitingCount({ "/email": 2 }, new Set(), { count: 4, floor: false }, false);
    expect(out.counts).toEqual({ "/email": 2, "/waiting": 4 });
    expect(rowMeta(undefined, out.counts[WAITING_HREF])).toEqual({ kind: "needs", text: "4", label: "4 need you" });
    expect(rowMeta(undefined, 140)).toMatchObject({ text: "99+" });
  });

  it("a floor prints 15+", () => {
    const out = withWaitingCount({}, new Set(), { count: 15, floor: true }, false);
    expect([...out.floors]).toEqual([WAITING_HREF]);
    expect(rowName("Waiting", 15, undefined, true)).toBe("Waiting, 15 or more need you");
  });

  it("shows no number for 0, for the OFF state and for a failed read", () => {
    const counts = { "/email": 2 };
    const floors = new Set<string>();
    expect(withWaitingCount(counts, floors, { count: 0, floor: false }, false).counts).toBe(counts);
    expect(withWaitingCount(counts, floors, null, false).counts).toBe(counts);
    expect(withWaitingCount(counts, floors, undefined, false).counts).toBe(counts);
    // A failed read keeps no stale number, even with an old answer in hand.
    expect(withWaitingCount(counts, floors, { count: 4, floor: false }, true).counts).toBe(counts);
  });

  it("a Projects write drops the count, as it drops the needs feed", () => {
    expect(WAITING_COUNT_KEY.startsWith(PROJECTS_CACHE)).toBe(true);
  });
});

describe("fetchWaitingCount", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads a 404 as the OFF state, and throws on a failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    expect(await fetchWaitingCount()).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 502 })));
    await expect(fetchWaitingCount()).rejects.toThrow();
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"count":3,"capped":[]}', { status: 200 })));
    expect(await fetchWaitingCount()).toEqual({ count: 3, floor: false });
  });
});

describe("one count path, one poll", () => {
  const hook = code(read("./useNavNeeds.ts"));

  it("the hook reads the count through the cache, only while the row is held", () => {
    expect(hook).toMatch(/const waitingHeld = on && held\.includes\(WAITING_HREF\);/);
    expect(hook).toMatch(/waitingHeld \? WAITING_COUNT_KEY : null,\s*\(\) => fetchWaitingCount\(\)/);
  });

  it("the hook joins the one poll under the count's key, and keeps no timer", () => {
    expect(hook).toMatch(/joinNeedsPoll\(waitingRefresh, WAITING_COUNT_KEY\)/);
    expect(hook).not.toMatch(/setInterval|setTimeout/);
  });

  it("the hook adds nothing to the seam's count: no agent questions", () => {
    expect(hook).not.toMatch(/agentRunsApi|agent-runs/);
    expect(code(read("./waitingCount.ts"))).not.toMatch(/agentRunsApi|agent-runs/);
  });

  it("only waitingCount.ts fetches the count", () => {
    expect(hook).not.toMatch(/\/api\/shell\/waiting/);
  });
});
