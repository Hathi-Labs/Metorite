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
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { NAV_SECTIONS } from "@/lib/nav";

import { APP_NAMES } from "./myDay";
import { NEEDS_APP_HREF, type NeedsFeed, type NeedsItem, countsByApp, needsByHref, readFeed } from "./needs";
import { needsLabel, needsSuffix, rowMeta, rowName, strayNeedsLabel } from "./navNeeds";
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
  ...over,
});

describe("the one map from a source to its app's row", () => {
  it("names a row for every source the feed has", () => {
    expect(Object.keys(NEEDS_APP_HREF).sort()).toEqual(Object.keys(APP_NAMES).sort());
  });

  it("maps each source to the app the owner named", () => {
    expect(NEEDS_APP_HREF).toEqual({ tasks: "/tasks", approvals: "/approvals", projects: "/projects", email: "/email" });
  });

  it("names live panes only", () => {
    const live = new Set(NAV_SECTIONS.flatMap((s) => s.items.filter((p) => p.launch === "live").map((p) => p.href)));
    for (const href of Object.values(NEEDS_APP_HREF)) expect(live.has(href), href).toBe(true);
  });
});

describe("the count per app", () => {
  it("counts the rows before the cut, not the rows sent", () => {
    // The cut to 30 dropped every reply: `items` holds none, `totals` holds 15.
    const f = feed({ items: [row("t1", "tasks")], sources: { tasks: "ok", email: "ok" }, totals: { tasks: 15, email: 15 } });
    expect(countsByApp(f, f.items, false)).toEqual({ tasks: 15, email: 15 });
  });

  it("a failed source has no number, never a 0 or a stale one", () => {
    const f = feed({
      items: [row("e1", "email"), row("t1", "tasks")],
      sources: { email: "failed", tasks: "ok" },
      totals: { email: 3, tasks: 1 },
    });
    const counts = countsByApp(f, f.items, false);
    expect(counts).not.toHaveProperty("email");
    expect(needsByHref(counts)).toEqual({ "/tasks": 1 });
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
    expect(needsByHref(counts)).toEqual({ "/tasks": 2, "/email": 3, "/approvals": 1 });
    expect(needsByHref(counts, new Set(["/tasks", "/email"]))).toEqual({ "/tasks": 2, "/email": 3 });
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
    expect(sidebar).toMatch(/const name = rowName\(pane\.label, needs,/);
    expect(code(read("../../components/AppShell.tsx"))).toMatch(/aria-label=\{rowName\(p\.label, appNeeds\[p\.href\], b\?\.label\)\}/);
  });

  it("caps the number as the bell does, and says the real number", () => {
    expect(rowMeta(undefined, 99)).toMatchObject({ text: "99" });
    expect(rowMeta(undefined, 100)).toMatchObject({ text: "99+", label: "100 need you" });
    expect(needsLabel(240)).toBe("240 need you");
  });
});

describe("a hidden app's count goes to Add apps", () => {
  it("adds the count of each app the sidebar does not show", () => {
    const byHref = needsByHref({ tasks: 2, email: 3 });
    const rail = new Set(["/email", "/projects"]);
    expect(strayCount(byHref, rail)).toBe(2);
    expect(strayCount(byHref, new Set(["/email", "/tasks"]))).toBe(0);
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
    expect(code(read("../../components/AppShell.tsx"))).toMatch(/useNavNeeds\(shellNav, heldHrefs\)/);
    const hook = code(read("./useNavNeeds.ts"));
    expect(hook).toMatch(/useNeedsYou\(on && allowed, approvals\)/);
    expect(hook).toMatch(/useNeedsPoll\(on && allowed, needs\.refresh\)/);
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
