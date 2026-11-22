import type { Locator, Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * A shared rig for LOOKING at a surface, for any app in the control plane.
 *
 * ## Why this exists
 *
 * `DESIGN_SYSTEM.md` §0 says a hardcoded value "will render fine — and it will
 * still be wrong in light mode, at compact density, and under a changed
 * accent". It then asks a person to check those three by eye. Nothing in this
 * tree can: the conformance suite checks eight regexes, and the theme-switch
 * check that used to be the real gate went with the theming engine on
 * 2026-08-31.
 *
 * So this module makes the four contexts cheap. It does NOT assert anything by
 * itself — a capture rig produces evidence for a person or for a follow-up
 * assertion. Where you can write the assertion, write it: {@link readPaint} is
 * here so a colour claim becomes a test rather than a screenshot.
 *
 * ## What it is not
 *
 * Not a replacement for a real end-to-end spec. It stubs the API, so it says
 * nothing about latency, pagination or a slow network. It answers "does this
 * surface look right in the contexts a member can put it in".
 *
 * ## Use it like this
 *
 *     import { stubApi, gotoAndSettle, captureContexts } from "./visual/harness";
 *
 *     test("capture the invoices surface", async ({ page }) => {
 *       test.setTimeout(180_000);
 *       await stubApi(page, {
 *         "billing/invoices": { rows: [...], total: 3 },
 *       });
 *       await gotoAndSettle(page, "/billing");
 *       await captureContexts(page, "invoices", { out: "ux-shots/billing" });
 *     });
 */

// ---------------------------------------------------------------- contexts --

/**
 * The contexts a member can actually put a surface in.
 *
 * Colour mode, density and accent are the three axes Settings → Appearance
 * offers (`DESIGN_SYSTEM.md` §0). The widths are the three that change layout
 * decisions here: 1280 is the small laptop, 1440 is where wrapping starts to
 * bite, and 1920 is where a row that wraps at 1440 fits again — so a defect
 * visible only at 1440 hides from both of the others.
 */
export interface VisualContext {
  name: string;
  width: number;
  height: number;
  /** Applied to <html> before the capture, and undone after. */
  apply?: (page: Page) => Promise<void>;
  undo?: (page: Page) => Promise<void>;
}

const setVar = (name: string, value: string) => async (page: Page) => {
  await page.evaluate(([n, v]) => document.documentElement.style.setProperty(n, v), [name, value]);
};
const clearVar = (name: string) => async (page: Page) => {
  await page.evaluate((n) => document.documentElement.style.removeProperty(n), name);
};

export const CONTEXTS: VisualContext[] = [
  { name: "dark-1440", width: 1440, height: 900 },
  {
    name: "light-1440",
    width: 1440,
    height: 900,
    // next-themes writes `.light` on <html>; the stylesheet keys off that.
    apply: async (page) => page.evaluate(() => document.documentElement.classList.add("light")),
    undo: async (page) => page.evaluate(() => document.documentElement.classList.remove("light")),
  },
  {
    // Compact is where a pinned `px` font size stops matching its neighbours,
    // because globals.css sets html font-size from --ui-scale and a px value
    // does not follow it.
    name: "compact-1440",
    width: 1440,
    height: 900,
    apply: setVar("--ui-scale", "0.875"),
    undo: clearVar("--ui-scale"),
  },
  {
    name: "comfortable-1440",
    width: 1440,
    height: 900,
    apply: setVar("--ui-scale", "1.125"),
    undo: clearVar("--ui-scale"),
  },
  {
    // A changed accent. Settings writes --primary by setProperty, exactly so.
    // Anything that reads --primary to mean something OTHER than "selected"
    // shows up here and nowhere else.
    name: "accent-violet-1440",
    width: 1440,
    height: 900,
    apply: setVar("--primary", "hsl(280 70% 55%)"),
    undo: clearVar("--primary"),
  },
  { name: "wide-1920", width: 1920, height: 1000 },
  { name: "small-1280", width: 1280, height: 800 },
  { name: "mobile-390", width: 390, height: 844 },
];

// ------------------------------------------------------------------- stubs --

/**
 * The shape every unmatched endpoint answers with.
 *
 * ⚠️ THIS IS A SUPERSET ON PURPOSE, and the reason is a real defect rather
 * than tidiness. Several components in this tree read a response field behind
 * only a `!data` guard — `LiveDock` calls `.filter()` on the sessions body,
 * `RelationsBlock` reads `data.links` and `data.subtasks`, `NodeDashboard`
 * reads `summary.by_category`. An object without the key passes the guard and
 * throws, and the layout boundary then renders an EMPTY pane. A rig answering
 * `{}` spends its afternoon debugging the product's error handling instead of
 * looking at the surface.
 *
 * When those guards land, this can shrink. Until then, a superset keeps the
 * rig pointed at what it is for.
 */
export const SAFE_EMPTY = {
  rows: [],
  items: [],
  total: 0,
  links: [],
  subtasks: [],
  children: [],
  buckets: [],
};

export interface StubOptions {
  /** Endpoints that must answer as a bare ARRAY rather than an object. */
  arrayPaths?: RegExp;
  /** Recorded for you: every path the surface asked for. */
  seen?: Set<string>;
}

/**
 * Answer every `/api/**` call, so a surface renders with no gateway and no
 * database.
 *
 * `handlers` is keyed by a path FRAGMENT, matched against the path with the
 * `/api/` prefix removed. The longest matching key wins, so a specific entry
 * beats a general one and you do not have to think about declaration order —
 * which is the trap that cost the first version of this rig an afternoon:
 * `projects/tasks` also matches `projects/tasks/<id>/relations`.
 */
export async function stubApi(
  page: Page,
  handlers: Record<string, unknown> = {},
  options: StubOptions = {},
): Promise<void> {
  const keys = Object.keys(handlers).sort((a, b) => b.length - a.length);
  const arrayPaths = options.arrayPaths ?? /^(notes|chat|agent|apps)\//;

  await page.route("**/api/**", async (route) => {
    const p = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    options.seen?.add(`${route.request().method()} ${p}`);

    const hit = keys.find((k) => p.includes(k));
    if (hit) return route.fulfill({ json: handlers[hit] as object });

    // Some docks want a bare array and crash on an object. See SAFE_EMPTY.
    if (arrayPaths.test(p)) return route.fulfill({ json: [] });
    return route.fulfill({ json: SAFE_EMPTY });
  });
}

// ---------------------------------------------------------------- steering --

/**
 * Go to a route and wait for it to be usable.
 *
 * ⚠️ NOT `networkidle`. Several surfaces poll — `projects/notifications` is
 * one — so the page never goes idle and the wait burns the whole timeout.
 * ⚠️ NOT `127.0.0.1` either; `playwright.config.ts` explains that one at
 * length. Use a path, and let the config's `baseURL` supply the host.
 */
export async function gotoAndSettle(page: Page, route: string, settleMs = 4000): Promise<void> {
  await page.goto(route);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(settleMs);
}

/**
 * The first VISIBLE control with this accessible name, or null.
 *
 * Two traps, both measured, both of which read as "my selector is wrong":
 * `.first()` happily returns a HIDDEN duplicate from a mobile bar that carries
 * the same labels, and `getByText` misses a button whose label is a child span
 * beside an icon. Trying role first and then text, and walking for visibility,
 * answers both.
 */
export async function firstVisible(page: Page, label: string): Promise<Locator | null> {
  // Tenth trap. `exact: true` is WHITESPACE-STRICT, and JSX puts whitespace
  // everywhere. A tab whose markup wraps an icon and a label across lines has
  // the accessible name "Board" for a screen reader and still fails an exact
  // match here. Both forms are tried, exact first, so a short label cannot
  // match a longer neighbour by accident.
  for (const all of [
    page.getByRole("button", { name: label, exact: true }),
    page.getByRole("tab", { name: label, exact: true }),
    page.getByRole("link", { name: label, exact: true }),
    page.getByText(label, { exact: true }),
    page.getByRole("button", { name: label }),
    page.getByRole("tab", { name: label }),
    page.getByRole("link", { name: label }),
  ]) {
    const n = await all.count();
    for (let i = 0; i < n; i++) {
      const el = all.nth(i);
      if (await el.isVisible().catch(() => false)) return el;
    }
  }
  return null;
}

/**
 * Click something and wait for what it opens to mount.
 *
 * The wait is the point. A tab bar or a detail pane mounts with its data and
 * not with the click, and under about a second it is simply not there yet —
 * which, again, reads as a bad selector rather than as impatience.
 */
export async function clickAndWait(page: Page, label: string, mountMs = 1800): Promise<boolean> {
  const el = await firstVisible(page, label);
  if (!el) return false;
  await el.click();
  await page.waitForTimeout(mountMs);
  return true;
}

// ---------------------------------------------------------------- captures --

export interface CaptureOptions {
  out?: string;
  fullPage?: boolean;
  /** Contexts to capture. Defaults to all of {@link CONTEXTS}. */
  only?: string[];
}

export async function capture(page: Page, name: string, out = "ux-shots"): Promise<void> {
  fs.mkdirSync(out, { recursive: true });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: false });
}

/**
 * Capture one surface in every context.
 *
 * Restores the viewport and every property it set, so a caller can keep
 * driving the page afterwards.
 */
export async function captureContexts(
  page: Page,
  baseName: string,
  options: CaptureOptions = {},
): Promise<void> {
  const out = options.out ?? "ux-shots";
  const wanted = options.only
    ? CONTEXTS.filter((c) => options.only!.includes(c.name))
    : CONTEXTS;

  const start = page.viewportSize() ?? { width: 1440, height: 900 };
  for (const ctx of wanted) {
    await page.setViewportSize({ width: ctx.width, height: ctx.height });
    if (ctx.apply) await ctx.apply(page);
    await capture(page, `${baseName}--${ctx.name}`, out);
    if (ctx.undo) await ctx.undo(page);
  }
  await page.setViewportSize(start);
}

// ------------------------------------------------------------ assertable ----

/**
 * The COMPUTED paint of an element, which is the only honest way to ask what
 * colour something is.
 *
 * A unit test that asserts a hue NAME passes while every lane draws the same
 * grey — that has happened here, and `project-state.spec.ts` says so in its
 * own header. Read the pixel value instead.
 */
export async function readPaint(
  locator: Locator,
  property: "backgroundColor" | "color" | "borderLeftColor" = "backgroundColor",
): Promise<string> {
  return locator.evaluate(
    (el, prop) => getComputedStyle(el as Element)[prop as "backgroundColor"],
    property,
  );
}

/**
 * Run `body` once per accent and return what {@link readPaint} said each time.
 *
 * This is the shape of the assertion that catches a status colour wired to
 * `--primary`: a status hue must NOT move when the member's accent moves.
 */
export async function underAccents<T>(
  page: Page,
  accents: string[],
  body: () => Promise<T>,
): Promise<T[]> {
  const out: T[] = [];
  for (const accent of accents) {
    await page.evaluate((a) => document.documentElement.style.setProperty("--primary", a), accent);
    await page.waitForTimeout(200);
    out.push(await body());
  }
  await page.evaluate(() => document.documentElement.style.removeProperty("--primary"));
  return out;
}

/** Collect console and page errors. Call before the first navigation. */
export function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 240));
  });
  page.on("pageerror", (e) => errors.push("PAGEERROR " + String((e as Error).stack || e).slice(0, 600)));
  return errors;
}

/** Write the request log and the errors beside the captures. */
export function writeNotes(out: string, seen: Set<string>, errors: string[]): void {
  fs.mkdirSync(out, { recursive: true });
  fs.writeFileSync(
    path.join(out, "notes.txt"),
    [...seen].sort().join("\n") + "\n\n--- console + page errors ---\n" + (errors.join("\n") || "(none)"),
  );
}
