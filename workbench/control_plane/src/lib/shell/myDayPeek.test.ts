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
 * 5. The seams, by source: the frame is the record card (`CardPanel`, D181)
 *    with My Day's page minimum, and the panel builds no frame and no Escape
 *    of its own. It hosts Projects' `TaskPanel` and draws no field block. It
 *    closes on a phone bar tab but Ask (`useBarTap`), with the hook above
 *    every return. The task body loads on demand. The feed's phone menu goes
 *    through `useMyDayGo`. My Tasks reads through `useTaskById`.
 * 6. With the flag off, the server render of `/` is the same markup.
 * 7. The capture handler of the hook asks the click guard: a Ctrl-click
 *    stays with the browser, and a plain click opens the panel.
 *
 * Mutation, run by hand (2026-11-09): make `peekTakesClick` ignore
 * `ctrlKey`, and the modifier case fails. Make `TASK_KIND` take any key,
 * and the `org` case fails. Move `useBarTap` below the return of
 * `MyDayPeekPanel`, and the hook-order case fails. Make the hook's handler
 * call `peekTargetOf` in place of `peekOfClick`, and the Ctrl-click render
 * case fails.
 */
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";

import {
  MY_DAY_PAGE_MIN_REM,
  PEEK_DEPTH_KEY,
  PEEK_KINDS,
  PEEK_ROW,
  barTapCloses,
  clickedLinkOf,
  closeMove,
  isDocked,
  landingClears,
  panelModeFor,
  peekOfClick,
  sentenceOf,
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

describe("the click, the card stop, the landing and the words", () => {
  const plain = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
  /** A link as `clickedLinkOf` reads it, inside the element with the handler. */
  function clickOn(href: string | null, attrs: Record<string, string> = {}, inside = true) {
    const link = {
      getAttribute: (n: string) => (n === "href" ? href : (attrs[n] ?? null)),
      hasAttribute: (n: string) => n in attrs,
      closest: (sel: string): unknown => (sel === "[data-peek-open-app]" && "data-peek-open-app" in attrs ? link : null),
    };
    return { target: { closest: (sel: string) => (sel === "a[href]" ? link : null) }, currentTarget: { contains: () => inside } };
  }

  it("opens a task link on a plain click, and never the way out or a link outside", () => {
    expect(peekOfClick(plain, clickedLinkOf(clickOn(`/projects?task=${ID}`)))).toEqual({ kind: "task", id: ID });
    expect(peekOfClick(plain, clickedLinkOf(clickOn(`/projects?task=${ID}`, { "data-peek-open-app": "" })))).toBeNull();
    expect(peekOfClick(plain, clickedLinkOf(clickOn(`/projects?task=${ID}`, {}, false)))).toBeNull();
    expect(peekOfClick({ ...plain, ctrlKey: true }, clickedLinkOf(clickOn(`/projects?task=${ID}`)))).toBeNull();
    expect(peekOfClick(plain, clickedLinkOf(clickOn(`/projects?task=${ID}`, { target: "_blank" })))).toBeNull();
    expect(peekOfClick(plain, clickedLinkOf(clickOn("/email?email=a&account=b")))).toBeNull();
    expect(peekOfClick(plain, null)).toBeNull();
  });

  it("draws one column only while the card is docked beside the page", () => {
    expect(isDocked(true, "side")).toBe(true);
    expect(isDocked(true, "full")).toBe(false);
    expect(isDocked(true, "phone")).toBe(false);
    expect(isDocked(true, null)).toBe(false);
    expect(isDocked(false, "side")).toBe(false);
  });

  it("keeps 36 rem of page beside a docked card", () => {
    expect(MY_DAY_PAGE_MIN_REM).toBe(36);
  });

  it("draws two columns only in a card over the page that has room for them", () => {
    // At 768 the card over the page was 430 px, and two columns cut each field.
    expect(panelModeFor("full", 430, 16)).toBe("side");
    expect(panelModeFor("full", 640, 16)).toBe("full");
    expect(panelModeFor("full", 686, 16)).toBe("full");
    expect(panelModeFor("side", 900, 16)).toBe("side");
    expect(panelModeFor("phone", 900, 16)).toBe("side");
  });

  it("clears a panel the page did not push when a close lands on it", () => {
    expect(landingClears(true, 0, true)).toBe(true);
    expect(landingClears(false, 0, true)).toBe(false);
    expect(landingClears(true, 1, true)).toBe(false);
    expect(landingClears(true, 0, false)).toBe(false);
  });

  it("never prints two stops after a server message", () => {
    expect(sentenceOf("No such task.")).toBe("No such task");
    expect(sentenceOf("No such task")).toBe("No such task");
    expect(sentenceOf(" The server answered 502. ")).toBe("The server answered 502");
  });

  it("marks the nearest row of a link with the house selected tint", () => {
    expect(PEEK_ROW.split(", ")).toEqual(["[data-feed-row]", "[data-home-card]", "li > div", "li"]);
    const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
    expect(css).toMatch(/\[data-peek-open\] \{\s*background-color: color-mix\(in srgb, var\(--primary\) 10%, transparent\);/);
  });
});

/**
 * Item 7 of the PK-1 fix round: the HOOK must ask the click guard. The hook
 * runs in a server render, which keeps its handler. The test calls the
 * handler with a click on a row link.
 */
describe("the capture handler of the hook", () => {
  async function handler() {
    const pushed: string[] = [];
    vi.stubGlobal("window", {
      location: { pathname: "/", search: "", hash: "" },
      history: { state: null, pushState: (_s: unknown, _t: string, url: string) => pushed.push(url) },
    });
    const { useMyDayPeek } = await import("./useMyDayPeek");
    const got: { hook: ReturnType<typeof useMyDayPeek> | null } = { hook: null };
    function Probe() {
      got.hook = useMyDayPeek(true, { onSettled: () => {} });
      return null;
    }
    renderToStaticMarkup(createElement(Probe));
    const onClickCapture = got.hook?.onClickCapture;
    if (!onClickCapture) throw new Error("the hook gave no handler with the flag on");
    return { onClickCapture, pushed };
  }
  function event(over: Record<string, unknown>) {
    const link = {
      getAttribute: (n: string) => (n === "href" ? `/projects?task=${ID}` : null),
      hasAttribute: () => false,
      closest: () => null,
    };
    const prevented = { yes: false };
    return {
      prevented,
      e: {
        button: 0,
        metaKey: false,
        ctrlKey: false,
        shiftKey: false,
        altKey: false,
        defaultPrevented: false,
        target: { closest: () => link },
        currentTarget: { contains: () => true },
        preventDefault: () => {
          prevented.yes = true;
        },
        ...over,
      } as never,
    };
  }
  afterEach(() => vi.unstubAllGlobals());

  it("leaves a Ctrl-click to the browser", async () => {
    const { onClickCapture, pushed } = await handler();
    const { e, prevented } = event({ ctrlKey: true });
    onClickCapture(e);
    expect(prevented.yes).toBe(false);
    expect(pushed).toEqual([]);
  });

  it("opens the panel on a plain click, and the path stays /", async () => {
    const { onClickCapture, pushed } = await handler();
    const { e, prevented } = event({});
    onClickCapture(e);
    expect(prevented.yes).toBe(true);
    expect(pushed).toEqual([`/?peek=task%3A${ID}`]);
  });
});

describe("the seams (source)", () => {
  const panel = code(read("./MyDayPeekPanel.tsx"));
  const hook = code(read("./useMyDayPeek.tsx"));

  it("stands in the record card, and builds no frame and no Escape of its own", () => {
    expect(panel).toMatch(/import CardPanel, \{ type CardStop, useCardStop \} from "@\/components\/CardPanel"/);
    expect(panel).toContain("useCardStop(isMobile, MY_DAY_PAGE_MIN_REM)");
    expect(panel).toContain("<CardPanel stop={stop}");
    // No copied frame: the phone frame and the frame over the page are the card's.
    expect(panel).not.toContain("TASK_CARD_FRAME");
    expect(panel).not.toContain("PANEL_WIDTH_CLASS");
    expect(panel).not.toMatch(/z-\[(5[1-9]|[6-9][0-9])\]/);
    // No Escape of its own: the card's `cardEscape` serves every state.
    for (const src of [panel, hook]) {
      expect(src).not.toContain("cardEscape");
      expect(src).not.toMatch(/addEventListener\("keydown"/);
    }
  });

  it("hosts Projects' own TaskPanel at the card's stop, and no second task view", () => {
    expect(panel).toMatch(/import \{ TaskPanel \} from "@\/app\/projects\/components\/TaskPanel"/);
    expect(panel).toContain("<TaskPanel");
    expect(panel).not.toContain("<TaskBody");
    // The blocks of the body (`itemDetail.test.ts`): none is drawn here.
    for (const block of ["AssigneePicker", "CustomFieldValues", "TagPicker", "RelationsBlock", "threadComments"]) {
      expect(panel, block).not.toContain(block);
    }
    // The one loader of a panel that starts from an id.
    expect(panel).toContain('useTaskById(target.kind === "task" ? target.id : null, { followMerge: true })');
    expect(panel).toContain("mode={panelModeFor(stop, card.width, card.rem)}");
    expect(panel).toContain("mode={mode}");
    expect(panel).toContain("onMode={expand ? () => expand.onToggle() : undefined}");
  });

  it("moves the focus into the card on open, and takes a pill click inside it", () => {
    expect(panel).toContain("aside.focus({ preventScroll: true })");
    expect(panel).toContain("const next = peekOfClick(event, clickedLinkOf(event));");
    expect(panel).toContain("onClickCapture={onClickCapture}");
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
  });

  it("loads the panel on demand, so the task body is not in My Day's first load", () => {
    expect(hook).toMatch(/dynamic\(\(\) => import\("\.\/MyDayPeekPanel"\), \{ ssr: false \}\)/);
    expect(code(read("./MyDayPage.tsx"))).not.toMatch(/from "\.\/MyDayPeekPanel"/);
  });

  it("gives nothing with the flag off: no handler, no reader, no panel", () => {
    expect(hook).toMatch(
      /if \(!enabled\) \{\s*return \{ columnRef, onClickCapture: undefined, peek: null, reader: null, panel: null, docked: false \};/,
    );
  });

  it("keeps the count of its history entries, and clears a landing it did not push", () => {
    // `useTaskTab` mirrors `?ttab=` with `replaceState(null, …)`, which drops
    // our key. The hook keeps the count in a ref and writes it back.
    expect(hook).toContain("const depth = useRef(0);");
    expect(hook).toMatch(/window\.history\.replaceState\(\{ \.\.\.state, \[PEEK_DEPTH_KEY\]: depth\.current \}, ""\)/);
    expect(hook).toContain("const move = closeMove(depth.current);");
    expect(hook).toContain("if (!landingClears(wasClosing, depth.current, peekInAddress() !== null)) return;");
  });

  it("marks the open row from the hook, and watches the column for a re-read", () => {
    expect(hook).toContain("rows.add(link.closest(PEEK_ROW) ?? link);");
    expect(hook).toContain("new MutationObserver(mark)");
    expect(hook).toContain("watch.observe(column, { childList: true, subtree: true });");
  });

  it("draws one column only while the card is docked, and as before otherwise", () => {
    const page = code(read("./MyDayPage.tsx"));
    expect(hook).toContain("docked: isDocked(!!target, stop),");
    expect(page).toContain('const wide = (classes: string) => (peek.docked ? "" : classes);');
    expect(page.match(/\$\{wide\("xl:grid-cols-3"\)\}/g)).toHaveLength(2);
    expect(page).toContain('wide("xl:col-span-2 xl:col-start-1 xl:row-start-1")');
    // The page root and the page column of the card. The column carries the
    // column mark, and the page draws no `<main>` of its own: the shell's
    // `<main>` holds My Day, so a second one gives the page two main landmarks.
    expect(page).toContain('{...{ [CARD_PAGE_ATTR]: "" }}');
    expect(page).toContain('<div ref={peek.columnRef} {...{ [CARD_PAGE_COLUMN_ATTR]: "" }}');
    expect(page).not.toMatch(/<main\b/);
    const card = code(read("../../components/CardPanel.tsx"));
    expect(card).toContain("page?.querySelector(`[${CARD_PAGE_COLUMN_ATTR}]`) ?? page?.querySelector(\"main\")");
  });

  it("drops the My Day keys on a close after a write, so no read in flight answers", () => {
    const page = code(read("./MyDayPage.tsx"));
    const from = page.indexOf("onSettled: () => {");
    const settled = page.slice(from, page.indexOf("},", from));
    expect(settled).toContain("rereadFeed();");
    expect(settled).toContain("invalidate(needsKey());");
    expect(settled).not.toContain("feed.refresh()");
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
