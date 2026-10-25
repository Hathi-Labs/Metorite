/**
 * The sidebar's row counts (owner, 2026-10-11): each app row shows what
 * needs the member there, from the one feed of My Day and the bell.
 *
 * Six claims, each one a way the feature breaks:
 *
 *   1. ONE map from a source to its app's row (`NEEDS_APP_HREF`), and it
 *      names a live pane for every source the feed has.
 *   2. The count is `total` semantics: the source's rows before the cut,
 *      less the rows an act took off. A FAILED source has no number, never a
 *      0 or a stale one. An ok source with 0 shows nothing.
 *   3. One slot: a run badge wins it, else the needs count, else nothing.
 *      The count stays in the spoken name when the run badge wins.
 *   4. A hidden app's count adds to "Add apps" (`strayCount`).
 *   5. "99+" past 99, the bell's own cap (`bellBadge`).
 *   6. One feed, one poll: the sidebar reads the feed through `useNeedsYou`,
 *      and no file but `needs.ts` fetches `/api/shell/needs`.
 *
 * The browser half is `e2e/sidebar-row-counts.spec.ts`.
 *
 * Mutations, run by hand (2026-10-11):
 *   - count a failed source as 0 in `countsByApp`: "a failed source has no number" fails;
 *   - let the needs count win over the run badge in `rowMeta`: "a run badge wins" fails;
 *   - count from `items` and not `totals`: "counts the rows before the cut" fails;
 *   - start a second interval in `joinNeedsPoll`: "two readers share one poll" fails;
 *   - fetch the feed in `useNavNeeds`: "no second fetch" fails;
 *   - print the number with no cap: "caps the number as the bell does" fails.
 * Mutations of the fix round (2026-10-12) are listed in the round's report.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { clearAll, invalidate, peek, put, subscribe } from "@/lib/dataCache";
import { NAV_SECTIONS } from "@/lib/nav";
import { coalesce, throttle } from "@/lib/useCachedResource";

import { APP_NAMES } from "./myDay";
import {
  NEEDS_APP_HREF,
  NEEDS_CACHE,
  type NeedsFeed,
  type NeedsItem,
  countsByApp,
  floorApps,
  floorHrefs,
  needsByHref,
  needsKey,
  readFeed,
} from "./needs";
import { addAppsName, needsLabel, needsSuffix, needsText, rowMeta, rowName, strayNeedsLabel } from "./navNeeds";
import { NAV_WAKE_MS } from "./useNavNeeds";
import { BELL_POLL_MS, joinNeedsPoll } from "./ShellBell";
import { strayCount } from "./shellNav";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
/** Source with comments removed, so prose about a fetch is not a fetch. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

const row = (id: string, app: NeedsItem["app"]): NeedsItem => ({
  id: `${app}:${id}`,
  app,
  kind: app === "email" ? "needs_reply" : app === "projects" ? "notification" : app === "approvals" ? "approval" : "overdue",
  title: id,
  detail: null,
  href: "/x",
  at: null,
  act: null,
  act_ref: null,
});

const feed = (over: Partial<NeedsFeed>): NeedsFeed => ({
  count: 0,
  total: 0,
  items: [],
  sources: {},
  totals: {},
  capped: [],
  ...over,
});

describe("the one map from a source to its app's row", () => {
  it("names a row for every source the feed has", () => {
    expect(Object.keys(NEEDS_APP_HREF).sort()).toEqual(Object.keys(APP_NAMES).sort());
  });

  it("maps each source to the app the owner named", () => {
    expect(NEEDS_APP_HREF).toEqual({ tasks: "/projects", approvals: "/approvals", projects: "/projects", email: "/email" });
  });

  it("names live panes only", () => {
    const live = new Set(NAV_SECTIONS.flatMap((s) => s.items.filter((p) => p.launch === "live").map((p) => p.href)));
    for (const href of Object.values(NEEDS_APP_HREF)) expect(live.has(href), href).toBe(true);
  });
});

describe("the count per app", () => {
  it("counts the rows before the cut, not the rows sent", () => {
    // The cut to 30 dropped every reply: `items` holds none. `totals` holds
    // the digest's own count, 17, which is past the list's cap of 15.
    const f = feed({ items: [row("t1", "tasks")], sources: { tasks: "ok", email: "ok" }, totals: { tasks: 2, email: 17 } });
    expect(countsByApp(f, f.items, false)).toEqual({ tasks: 2, email: 17 });
    expect(floorApps(f, false).size).toBe(0);
  });

  it("a capped source is a floor: 15 is 15 or more, never exactly 15", () => {
    const f = feed({ sources: { tasks: "ok", email: "ok" }, totals: { tasks: 15, email: 3 }, capped: ["tasks"] });
    expect(countsByApp(f, [], false)).toEqual({ tasks: 15, email: 3 });
    expect([...floorApps(f, false)]).toEqual(["tasks"]);
    expect([...floorHrefs(floorApps(f, false))]).toEqual(["/projects"]);
    expect(rowMeta(undefined, 15, true)).toEqual({ kind: "needs", text: "15+", label: "15 or more need you" });
    expect(rowName("My Tasks", 15, undefined, true)).toBe("My Tasks, 15 or more need you");
  });

  it("a floor needs an ok source, a held app and, for approvals, the right member", () => {
    const f = feed({ sources: { email: "failed", approvals: "ok" }, totals: { approvals: 15 }, capped: ["email", "approvals"] });
    expect([...floorApps(f, false)]).toEqual([]);
    expect([...floorApps(f, true)]).toEqual(["approvals"]);
    expect([...floorHrefs(new Set(["approvals"] as const), new Set(["/projects"]))]).toEqual([]);
    expect(readFeed({ items: [], sources: {} }).capped).toEqual([]);
  });

  it("a failed source has no number, never a 0 or a stale one", () => {
    const f = feed({
      items: [row("e1", "email"), row("t1", "tasks")],
      sources: { email: "failed", tasks: "ok" },
      totals: { email: 3, tasks: 1 },
    });
    const counts = countsByApp(f, f.items, false);
    expect(counts).not.toHaveProperty("email");
    expect(needsByHref(counts)).toEqual({ "/projects": 1 });
  });

  it("an absent source has no number either", () => {
    const f = feed({ sources: { email: "absent", tasks: "ok" }, totals: { tasks: 2 } });
    expect(countsByApp(f, [], false)).toEqual({ tasks: 2 });
  });

  it("an ok source with nothing shows nothing", () => {
    const f = feed({ sources: { email: "ok", tasks: "ok" }, totals: { email: 0, tasks: 0 } });
    expect(countsByApp(f, [], false)).toEqual({ email: 0, tasks: 0 });
    expect(needsByHref(countsByApp(f, [], false))).toEqual({});
  });

  it("takes off the rows an act removed, so a done task stops counting", () => {
    const items = [row("t1", "tasks"), row("t2", "tasks"), row("e1", "email")];
    const f = feed({ items, sources: { tasks: "ok", email: "ok" }, totals: { tasks: 5, email: 1 } });
    // `useNeedsYou` hands in the rows still on screen: t1 was just done.
    const shown = items.filter((i) => i.id !== "tasks:t1");
    expect(countsByApp(f, shown, false)).toEqual({ tasks: 4, email: 1 });
  });

  it("reads an older gateway, with no totals, from the rows sent", () => {
    const f = readFeed({ count: 2, items: [row("e1", "email"), row("e2", "email")], sources: { email: "ok" } });
    expect(f.totals).toEqual({});
    expect(countsByApp(f, f.items, false)).toEqual({ email: 2 });
  });

  it("never counts below the rows sent, whatever totals says", () => {
    const f = feed({ items: [row("e1", "email"), row("e2", "email")], sources: { email: "ok" }, totals: { email: 1 } });
    expect(countsByApp(f, f.items, false)).toEqual({ email: 2 });
  });

  it("counts approvals only for a member who may see them", () => {
    const f = feed({ sources: { approvals: "ok" }, totals: { approvals: 4 } });
    expect(countsByApp(f, [], false)).toEqual({});
    expect(countsByApp(f, [], true)).toEqual({ approvals: 4 });
  });

  it("puts a count on the app's row, and none on an app the member cannot open", () => {
    const counts = { tasks: 2, email: 3, approvals: 1 };
    expect(needsByHref(counts)).toEqual({ "/projects": 2, "/email": 3, "/approvals": 1 });
    expect(needsByHref(counts, new Set(["/projects", "/email"]))).toEqual({ "/projects": 2, "/email": 3 });
  });
});

describe("one slot, one rule", () => {
  it("a run badge wins the slot", () => {
    expect(rowMeta(2, 5)).toEqual({ kind: "run" });
  });

  it("else the needs count, in words for a screen reader", () => {
    expect(rowMeta(undefined, 5)).toEqual({ kind: "needs", text: "5", label: "5 need you" });
    expect(rowMeta(0, 1)).toEqual({ kind: "needs", text: "1", label: "1 needs you" });
  });

  it("else nothing", () => {
    expect(rowMeta(undefined, undefined)).toBeNull();
    expect(rowMeta(0, 0)).toBeNull();
  });

  it("the count stays in the spoken name when the run badge wins", () => {
    expect(needsSuffix(5)).toBe(", 5 need you");
    expect(needsSuffix(1)).toBe(", 1 needs you");
    expect(needsSuffix(0)).toBe("");
    expect(needsSuffix(undefined)).toBe("");
  });

  it("names the row link in one string, with the run badge's words between", () => {
    expect(rowName("My Email", 3)).toBe("My Email, 3 need you");
    expect(rowName("My Email", 3, "1 assistant running")).toBe("My Email, 1 assistant running, 3 need you");
    // No count: the link keeps the name its own text gives.
    expect(rowName("My Email", 0, "1 assistant running")).toBeUndefined();
    expect(rowName("My Email", undefined)).toBeUndefined();
  });

  it("each row sets that name, open, folded and on the phone", () => {
    const sidebar = code(read("../../components/Sidebar.tsx"));
    expect(sidebar.match(/aria-label=\{name\}/g)?.length).toBe(3);
    expect(sidebar).toMatch(/const name = rowName\(\s*pane\.label,\s*needs,/);
    expect(code(read("../../components/AppShell.tsx"))).toMatch(/aria-label=\{rowName\(p\.label, appNeeds\[p\.href\], b\?\.label, floor\)\}/);
  });

  it("names Add apps with the run badge's words and the needs, open and folded", () => {
    expect(addAppsName(0)).toBe("Add apps");
    expect(addAppsName(2, "1 assistant running")).toBe("Add apps, 1 assistant running, 2 need you in hidden apps");
    expect(addAppsName(0, "1 assistant running")).toBe("Add apps, 1 assistant running");
    expect(addAppsName(15, undefined, true)).toBe("Add apps, 15 or more need you in hidden apps");
    const sidebar = code(read("../../components/Sidebar.tsx"));
    expect(sidebar).toMatch(/const name = addAppsName\(needs, badge\?\.label, needsFloor\);/);
    expect(sidebar).toMatch(/aria-label=\{name === "Add apps" \? undefined : name\}/);
  });

  it("caps the number as the bell does, and says the real number", () => {
    expect(rowMeta(undefined, 99)).toMatchObject({ text: "99" });
    expect(rowMeta(undefined, 100)).toMatchObject({ text: "99+", label: "100 need you" });
    expect(needsLabel(240)).toBe("240 need you");
    // A floor past 99 keeps the bell's "99+", with no second "+".
    expect(needsText(120, true)).toBe("99+");
    expect(needsText(15, true)).toBe("15+");
  });
});

describe("a hidden app's count goes to Add apps", () => {
  it("adds the count of each app the sidebar does not show", () => {
    const byHref = needsByHref({ tasks: 2, email: 3 });
    const rail = new Set(["/email"]);
    expect(strayCount(byHref, rail)).toBe(2);
    expect(strayCount(byHref, new Set(["/email", "/projects"]))).toBe(0);
    expect(strayNeedsLabel(2)).toBe("2 need you in hidden apps");
  });

  it("the sidebar and the drawer both reuse that path", () => {
    expect(code(read("../../components/Sidebar.tsx"))).toMatch(/strayCount\(appNeeds, railHrefs\)/);
    expect(code(read("../../components/AppShell.tsx"))).toMatch(/strayCount\(appNeeds, drawerHrefs\)/);
  });
});

describe("one feed, one poll", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("two readers share one poll", () => {
    vi.useFakeTimers();
    const a = vi.fn();
    const b = vi.fn();
    const leaveA = joinNeedsPoll(a);
    const leaveB = joinNeedsPoll(b);
    vi.advanceTimersByTime(BELL_POLL_MS);
    expect(a.mock.calls.length + b.mock.calls.length).toBe(1);
    leaveA();
    vi.advanceTimersByTime(BELL_POLL_MS);
    expect(b).toHaveBeenCalledTimes(1);
    leaveB();
    vi.advanceTimersByTime(BELL_POLL_MS * 3);
    expect(a.mock.calls.length + b.mock.calls.length).toBe(2);
  });

  it("the sidebar and the drawer read the counts through the one hook", () => {
    expect(code(read("../../components/Sidebar.tsx"))).toMatch(/useNavNeeds\(shellNav && canPoll, heldHrefs\)/);
    // The drawer has the sidebar's gate: no workspace, no read.
    expect(code(read("../../components/AppShell.tsx"))).toMatch(
      /useNavNeeds\(shellNav && shouldPollWorkspace\(access, accessLoading\), heldHrefs\)/,
    );
    const hook = code(read("./useNavNeeds.ts"));
    expect(hook).toMatch(/useNeedsYou\(on && allowed, approvals, NAV_WAKE_MS\)/);
    expect(hook).toMatch(/useNeedsPoll\(on && allowed, needs\.refresh\)/);
  });

  it("a hidden tab reads nothing, and reads once when it shows again", () => {
    vi.useFakeTimers();
    const doc = Object.assign(new EventTarget(), { visibilityState: "hidden" as DocumentVisibilityState });
    vi.stubGlobal("document", doc);
    try {
      const refresh = vi.fn();
      const leave = joinNeedsPoll(refresh);
      vi.advanceTimersByTime(BELL_POLL_MS * 3);
      expect(refresh).not.toHaveBeenCalled();
      doc.visibilityState = "visible";
      doc.dispatchEvent(new Event("visibilitychange"));
      expect(refresh).toHaveBeenCalledTimes(1);
      // A second show with no missed tick reads nothing more.
      doc.dispatchEvent(new Event("visibilitychange"));
      expect(refresh).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(BELL_POLL_MS);
      expect(refresh).toHaveBeenCalledTimes(2);
      leave();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("writes elsewhere read the feed at most once in 15 s, with the hook's own wiring", () => {
    vi.useFakeTimers();
    expect(NAV_WAKE_MS).toBe(15_000);
    // The effect of `useCachedResource`, with `wakeEveryMs`: the subscribe
    // path coalesces a burst, then the throttle lets one read through.
    // A network read stores the answer, as `read` does, and that `put` wakes
    // the watchers again. A wake with the key still held is a cache hit.
    const load = vi.fn(() => put(needsKey(), readFeed({})));
    const hit = vi.fn();
    put(needsKey(), readFeed({}));
    const wake = throttle(load, NAV_WAKE_MS);
    const settle = coalesce(() => (!peek(needsKey()) ? wake.trigger() : hit()));
    const off = subscribe(needsKey(), settle.trigger);
    try {
      // Twenty Projects writes in 2 s. Each drops the whole family.
      for (let i = 0; i < 20; i += 1) {
        invalidate(NEEDS_CACHE.slice(0, NEEDS_CACHE.indexOf("shell/needs")));
        vi.advanceTimersByTime(100);
      }
      vi.advanceTimersByTime(500);
      expect(load).toHaveBeenCalledTimes(1);
      // One trailing read carries the state after the burst, at the window's end.
      vi.advanceTimersByTime(NAV_WAKE_MS);
      expect(load).toHaveBeenCalledTimes(2);
      // And it stops there. A read's own `put` is a hit, never a new read,
      // so the throttle never turns into a poll every 15 s.
      vi.advanceTimersByTime(NAV_WAKE_MS * 4);
      expect(load).toHaveBeenCalledTimes(2);
      expect(hit).toHaveBeenCalled();
    } finally {
      off();
      wake.cancel();
      settle.cancel();
      clearAll();
    }
  });

  it("the sidebar's read is throttled, and My Day and the bell keep theirs", () => {
    const hook = code(read("../useCachedResource.ts"));
    expect(hook).toMatch(/const wake = wakeEveryMs \? throttle\(\(\) => load\(false\), wakeEveryMs\) : null;/);
    expect(hook).toMatch(/const settle = coalesce\(\(\) => \(wake && !peek\(key\) \? wake\.trigger\(\) : load\(false\)\)\);/);
    expect(code(read("./NeedsYouCard.tsx"))).toMatch(/\(\) => fetchNeeds\(\), \{ wakeEveryMs \}\)/);
    expect(code(read("./ShellBell.tsx"))).toMatch(/useNeedsYou\(allowed, approvals\);/);
  });

  it("a feed read that failed as a whole shows no number on any row", () => {
    const hook = code(read("./useNavNeeds.ts"));
    expect(hook).toMatch(/const failed = needs\.error !== null;/);
    expect(hook).toMatch(/if \(!on \|\| !allowed \|\| failed\) return NONE;/);
  });

  it("the bell's host joins the same poll, and keeps no timer of its own", () => {
    const bell = code(read("./ShellBell.tsx"));
    const host = bell.slice(bell.indexOf("export function ShellBellHost"));
    expect(host).toMatch(/useNeedsPoll\(allowed, refresh\)/);
    expect(host).not.toMatch(/setInterval\(/);
  });

  it("no second fetch: only needs.ts fetches the feed", () => {
    const root = fileURLToPath(new URL("../../", import.meta.url));
    const hits: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
          walk(path);
          continue;
        }
        if (!/\.(ts|tsx)$/.test(name) || /\.test\.tsx?$/.test(name)) continue;
        const rel = relative(root, path).replace(/\\/g, "/");
        // The BFF route itself is the proxy, not a reader.
        if (rel.startsWith("app/api/shell/needs/")) continue;
        if (/["'`]\/api\/shell\/needs/.test(code(readFileSync(path, "utf8")))) hits.push(rel);
      }
    };
    walk(root);
    expect(hits).toEqual(["lib/shell/needs.ts"]);
  });
});
