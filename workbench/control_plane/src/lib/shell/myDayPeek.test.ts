/**
 * The panel on My Day (PK-1, task 2469, D204, `my_day_feed.md` §11.5).
 *
 * What this holds, rule by rule:
 *
 * 1. Which link opens in the panel: a plain task link, and nothing else.
 * 2. Which click the panel takes: a plain left click only. Ctrl, Cmd, Shift,
 *    Alt, a middle click, a `target` and a `download` keep the browser's act.
 * 3. The URL: `?peek=task:<id>` on `/`, set and cleared with every other key
 *    kept, and a close that walks back over the page's own entries.
 * 4. The flag: off by default, read in the one form Next inlines, and the
 *    server render reads the build-time value only.
 * 5. The seams, by source: the panel hosts Projects' `TaskPanel` and draws
 *    no field block of its own. It closes on a phone bar tap, with the hook
 *    above every return. The task body loads on demand. The feed's phone
 *    menu goes through `useMyDayGo`. My Tasks reads through `useTaskById`.
 * 6. With the flag off, the server render of `/` is the same markup.
 *
 * Mutation, run by hand (2026-11-09): make `peekTakesClick` ignore
 * `ctrlKey`, and the modifier case fails. Make `TASK_KIND` take any key,
 * and the `org` case fails. Move `useCloseOnBarTap` below the return of
 * `MyDayPeekPanel`, and the hook-order case fails.
 */
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";

import {
  PEEK_DEPTH_KEY,
  PEEK_KINDS,
  barTapCloses,
  closeMove,
  peekDepthOf,
  peekFromParam,
  peekHref,
  peekParamOf,
  peekTakesClick,
  peekTargetOf,
} from "./myDayPeek";

const ID = "6f0c1a52-3b9e-4c7d-9a10-2b5e8f4d1c33";
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
/** Comments out, so a comment that names a rule cannot pass for code. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:\\])\/\/[^\n]*/g, "$1");

describe("which link opens in the panel (peekTargetOf)", () => {
  it.each([
    [`/projects?task=${ID}`, { kind: "task", id: ID }],
    [`/tasks?task=${ID}`, { kind: "task", id: ID }],
    [`/tasks?item=${ID}`, { kind: "task", id: ID }],
  ])("%s opens the task", (href, target) => {
    expect(peekTargetOf(href)).toEqual(target);
  });

  it.each([
    // Another key: an org, an app, a tab. Not a plain task link.
    `/projects?task=${ID}&org=11111111-2222-3333-4444-555555555555`,
    `/projects?task=${ID}&app=reports`,
    `/projects?app=my-tasks&chip=today`,
    `/projects?project=${ID}`,
    `/projects?task=${ID}#comments`,
    "/projects?task=",
    "/projects?task=has%20space",
    `/projects/?task=${ID}`,
    // Another app. PK-2 and PK-3 add their own kinds.
    "/email?email=m1&account=a1",
    "/chat?reminder=r1",
    "/calendar?date=2026-11-09",
    "/approvals?action=x",
    // Not an in-app page at all.
    `//evil.example/projects?task=${ID}`,
    `https://evil.example/projects?task=${ID}`,
    `/api/projects?task=${ID}`,
    `/\\evil.example/projects?task=${ID}`,
    "",
    null,
    undefined,
  ])("%s opens its app", (href) => {
    expect(peekTargetOf(href)).toBeNull();
  });

  it("asks a registry of kinds, so a later kind is one more row", () => {
    expect(PEEK_KINDS.map((k) => k.kind)).toEqual(["task"]);
  });
});

describe("which click the panel takes (peekTakesClick)", () => {
  const plain = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
  const link = { target: "", download: false };

  it("takes a plain left click", () => {
    expect(peekTakesClick(plain, link)).toBe(true);
    expect(peekTakesClick(plain, { ...link, target: "_self" })).toBe(true);
  });

  it.each([
    ["Ctrl", { ctrlKey: true }],
    ["Cmd", { metaKey: true }],
    ["Shift", { shiftKey: true }],
    ["Alt", { altKey: true }],
    ["the middle button", { button: 1 }],
    ["the right button", { button: 2 }],
    ["a click something else took", { defaultPrevented: true }],
  ])("leaves %s to the browser", (_name, change) => {
    expect(peekTakesClick({ ...plain, ...change }, link)).toBe(false);
  });

  it("leaves a link with a target or a download to the browser", () => {
    expect(peekTakesClick(plain, { ...link, target: "_blank" })).toBe(false);
    expect(peekTakesClick(plain, { ...link, download: true })).toBe(false);
  });
});

describe("the URL carries the panel", () => {
  it("writes and reads `task:<id>`", () => {
    const target = { kind: "task" as const, id: ID };
    expect(peekParamOf(target)).toBe(`task:${ID}`);
    expect(peekFromParam(peekParamOf(target))).toEqual(target);
  });

  it.each(["", null, "task", "task:", `mail:${ID}`, ":abc", "task:a b", "task:../x"])(
    "reads no target from %s",
    (value) => {
      expect(peekFromParam(value)).toBeNull();
    },
  );

  it("sets and clears the key, and keeps every other key and the hash", () => {
    const at = { pathname: "/", search: "?x=1", hash: "#top" };
    const open = peekHref(at, { kind: "task", id: ID });
    expect(open).toBe(`/?x=1&peek=task%3A${ID}#top`);
    const search = open.slice(open.indexOf("?"), open.indexOf("#"));
    expect(peekHref({ pathname: "/", search, hash: "#top" }, null)).toBe("/?x=1#top");
    expect(peekHref({ pathname: "/", search: `?peek=task:${ID}`, hash: "" }, null)).toBe("/");
  });

  it("closes back over the page's own entries, or in place when it pushed none", () => {
    expect(peekDepthOf({ [PEEK_DEPTH_KEY]: 2, __NA: true })).toBe(2);
    for (const state of [null, undefined, {}, { [PEEK_DEPTH_KEY]: "2" }, { [PEEK_DEPTH_KEY]: -1 }, { [PEEK_DEPTH_KEY]: 1.5 }]) {
      expect(peekDepthOf(state)).toBe(0);
    }
    expect(closeMove(2)).toEqual({ go: -2 });
    expect(closeMove(0)).toEqual({ replace: true });
  });
});

describe("the flag (NEXT_PUBLIC_MY_DAY_PEEK)", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("is off by default, and on with the build value", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_PEEK", "");
    const { myDayPeekOn, myDayPeekOnServer } = await import("./myDayPeekFlag");
    expect(myDayPeekOn()).toBe(false);
    expect(myDayPeekOnServer()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_PEEK", "1");
    expect(myDayPeekOn()).toBe(true);
    expect(myDayPeekOnServer()).toBe(true);
  });

  it("lets a dev browser turn it on, and never the server render", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_PEEK", "");
    vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "cc-my-day-peek" ? "1" : null) });
    const { myDayPeekOn, myDayPeekOnServer } = await import("./myDayPeekFlag");
    expect(myDayPeekOn()).toBe(true);
    expect(myDayPeekOnServer()).toBe(false);
  });

  it("is read in the one form Next inlines, from a file with no imports", () => {
    const flag = read("./myDayPeekFlag.ts");
    expect(flag).toContain('process.env.NEXT_PUBLIC_MY_DAY_PEEK === "1"');
    expect(flag).not.toMatch(/^import /m);
    expect(read("./MyDayPage.tsx")).toMatch(/useSyncExternalStore\(noSubscribe, myDayPeekOn, myDayPeekOnServer\)/);
  });
});

describe("the seams (source)", () => {
  const panel = code(read("./MyDayPeekPanel.tsx"));
  const hook = code(read("./useMyDayPeek.tsx"));

  it("hosts Projects' own TaskPanel, and no second task view", () => {
    expect(panel).toMatch(/import \{ TaskPanel \} from "@\/app\/projects\/components\/TaskPanel"/);
    expect(panel).toContain("<TaskPanel");
    expect(panel).not.toContain("<TaskBody");
    // The blocks of the body (`itemDetail.test.ts`): none is drawn here.
    for (const block of ["AssigneePicker", "CustomFieldValues", "TagPicker", "RelationsBlock", "threadComments"]) {
      expect(panel, block).not.toContain(block);
    }
    // The one loader of a panel that starts from an id.
    expect(panel).toContain("useTaskById(id, { followMerge: true })");
  });

  it("closes on a phone bar tap, but Ask, with the hook above every return", () => {
    expect(barTapCloses("menu")).toBe(true);
    expect(barTapCloses(null)).toBe(true);
    expect(barTapCloses("ask")).toBe(false);
    const body = panel.slice(panel.indexOf("export default function MyDayPeekPanel"));
    const hookAt = body.indexOf("useBarTap((tab) => {");
    expect(body).toContain("if (barTapCloses(tab)) onLeave();");
    expect(hookAt).toBeGreaterThan(0);
    expect(hookAt).toBeLessThan(body.indexOf("return"));
    // The phone frame is the task card's own (`lib/layers.ts`), never a new layer.
    expect(panel).toContain("TASK_CARD_FRAME.phone");
    expect(panel).not.toMatch(/z-\[(5[1-9]|[6-9][0-9])\]/);
  });

  it("loads the panel on demand, so the task body is not in My Day's first load", () => {
    expect(hook).toMatch(/dynamic\(\(\) => import\("\.\/MyDayPeekPanel"\), \{ ssr: false \}\)/);
    expect(code(read("./MyDayPage.tsx"))).not.toMatch(/from "\.\/MyDayPeekPanel"/);
  });

  it("gives nothing with the flag off: no handler, no reader, no panel", () => {
    expect(hook).toMatch(
      /if \(!enabled\) \{\s*return \{ pageRef, onClickCapture: undefined, peek: null, reader: null, panel: null, docked: false \};/,
    );
  });

  it("keeps the count of its history entries when another part replaces the state", () => {
    // `useTaskTab` mirrors `?ttab=` with `replaceState(null, …)`, which drops
    // our key. The hook keeps the count in a ref and writes it back.
    expect(hook).toContain("const depth = useRef(0);");
    expect(hook).toMatch(/window\.history\.replaceState\(\{ \.\.\.state, \[PEEK_DEPTH_KEY\]: depth\.current \}, ""\)/);
    expect(hook).toContain("const move = closeMove(depth.current);");
  });

  it("draws My Day in one column while the panel is docked, and as before when it is not", () => {
    const page = code(read("./MyDayPage.tsx"));
    expect(page).toContain('const wide = (classes: string) => (peek.docked ? "" : classes);');
    expect(page.match(/\$\{wide\("xl:grid-cols-3"\)\}/g)).toHaveLength(2);
    expect(page).toContain('wide("xl:col-span-2 xl:col-start-1 xl:row-start-1")');
  });

  it("sends the feed's phone menu through useMyDayGo, not router.push", () => {
    const feed = code(read("./FeedCard.tsx"));
    expect(feed).toContain("const go = useMyDayGo();");
    expect(feed).not.toMatch(/router\.push\(/);
  });

  it("keeps My Tasks on the one loader", () => {
    const detail = code(read("../../app/tasks/components/ItemDetail.tsx"));
    expect(detail).toContain("useTaskById(item.id)");
    expect(detail).not.toContain("fetchMyTaskLanes(");
    expect(detail).not.toContain("projectsApi.task(item.id)");
  });
});

/**
 * Acceptance 4: with the flag off, nothing changes. The server render of `/`
 * is the one the member gets first, and the flag adds nothing to it.
 */
describe("/ with the panel flag off and on", () => {
  vi.mock("@/components/AccessProvider", () => ({
    useAccess: () => ({
      access: { ...NO_ACCESS, authenticated: true, features: ["tasks", "projects"], is_admin: false },
      loading: false,
    }),
  }));
  vi.mock("next-auth/react", () => ({
    useSession: () => ({ data: { user: { name: "Asha Rao", email: "asha@example.com" } } }),
  }));

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  async function renderHome(): Promise<string> {
    vi.resetModules();
    const { default: Home } = await import("@/app/page");
    return renderToStaticMarkup(createElement(Home));
  }

  it("draws the same page either way, and no panel before a click", async () => {
    vi.stubEnv("NEXT_PUBLIC_MY_DAY", "1");
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_PEEK", "");
    const off = await renderHome();
    vi.stubEnv("NEXT_PUBLIC_MY_DAY_PEEK", "1");
    const on = await renderHome();
    expect(off).toContain('data-testid="my-day"');
    expect(on).toBe(off);
    expect(on).not.toContain("data-my-day-peek");
    // Two cold imports of the whole page, as `myDayPage.test.ts` allows.
  }, 30_000);
});
