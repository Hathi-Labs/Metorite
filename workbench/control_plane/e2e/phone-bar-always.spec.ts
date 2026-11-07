import { expect, test, type Page } from "@playwright/test";

/**
 * The phone's bottom bar always shows (MOB-9, task 2002).
 *
 * ## The rule
 *
 * At 390 px the bar's tabs stay in reach while an overlay is open. Three
 * overlay kinds stand for the rest: the shell's Menu drawer, a `Modal`, and
 * the assistant's phone sheet. For each one this spec asserts that
 * `document.elementFromPoint` at the centre of every tab lands inside the bar
 * (`[data-phone-nav-bar]`), and that the overlay ends at or above the bar's
 * top edge. Then it taps a tab and asserts that the tab did its job.
 *
 * A vitest test cannot see paint order or layout, so this measures both.
 * `src/lib/layers.test.ts` fences the layer numbers and the CSS rule.
 *
 * ## The harness
 *
 * `/projects` with stubbed API reads (the style of `projects-compact-chrome`).
 * The one assistant needs its dev flags, set through `localStorage`.
 * The Projects bar reads Menu, Projects, Capture, Search, Ask with the flag
 * `NEXT_PUBLIC_PROJECTS_MY_WORK` on, and Menu, Projects, Views, Search, Ask
 * with it off. The spec taps only tabs that both lists have. The Next.js
 * dev-tools badge sits on the Menu tab in a dev build, so the spec hides it.
 */

const DRAWER = "div.fixed.inset-0.z-\\[70\\]";
const BAR_TABS = "[data-phone-nav-bar] nav";

const TREE = [
  {
    id: "p1",
    name: "Firmware",
    parent_project_id: null,
    kind: "project",
    children: [{ id: "p2", name: "Bootloader", parent_project_id: "p1", kind: "project", children: [] }],
  },
];
const STATUSES = ["Backlog", "To do", "Doing", "Done"].map((name, i) => ({
  id: `s${i}`,
  project_id: "p2",
  name,
  color: "gray",
  position: (i + 1) * 10,
  category: i === 3 ? "done" : i === 0 ? "backlog" : "todo",
  is_default: i === 0,
}));
const TASKS = Array.from({ length: 6 }, (_, i) => ({
  id: `t${i}`,
  project_id: "p2",
  root_project_id: "p1",
  task_number: i + 1,
  status_id: "s0",
  title: `Task number ${i + 1}`,
  tags: [],
  assignees: [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
}));
const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };
const SHELL = {
  preset: null,
  answered: "skipped",
  pins: null,
  known: null,
  cardOrder: null,
  newOrder: null,
  chat_first_message_at: "2026-10-20T09:00:00.000Z",
};
const FIXTURES: Record<string, unknown> = {
  "auth/me/shell": SHELL,
  "auth/me": { email: "you@example.com", name: "You" },
  "projects/tree": { rows: TREE, total: 1 },
  "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
  "projects/tasks": { rows: TASKS, total: TASKS.length },
};

async function openPhone(page: Page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    window.localStorage.clear();
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api\//, "");
    const hit = Object.keys(FIXTURES)
      .sort((a, b) => b.length - a.length)
      .find((key) => path.includes(key));
    return route.fulfill({ json: hit ? (FIXTURES[hit] as object) : EMPTY });
  });
  await page.goto("/projects?project=p2");
  await page.waitForLoadState("domcontentloaded");
  await expect(page.locator("[data-phone-nav-bar]")).toBeVisible({ timeout: 60_000 });
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  const skip = page.getByRole("button", { name: "Skip for now" });
  if (await skip.isVisible().catch(() => false)) await skip.click();
}

/** Every bar tab's centre lands inside the bar, and the overlay ends above it. */
async function expectBarReachable(page: Page, overlay: string) {
  const r = await page.evaluate((sel) => {
    const bar = document.querySelector("[data-phone-nav-bar]") as HTMLElement;
    const barBox = bar.getBoundingClientRect();
    const tabs = Array.from(bar.querySelectorAll("nav > button"));
    const hits = tabs.map((t) => {
      const b = t.getBoundingClientRect();
      const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      return { label: (t.textContent ?? "").trim(), inside: !!el && bar.contains(el) };
    });
    const o = document.querySelector(sel);
    return { barTop: barBox.top, barHeight: barBox.height, hits, bottom: o ? o.getBoundingClientRect().bottom : null };
  }, overlay);
  expect(r.hits.length, "the bar drew no tabs").toBeGreaterThanOrEqual(3);
  for (const h of r.hits) expect(h.inside, `the "${h.label}" tab is covered`).toBe(true);
  expect(r.bottom, `${overlay} did not draw`).not.toBeNull();
  expect(r.bottom!, `${overlay} runs under the bar`).toBeLessThanOrEqual(r.barTop + 0.5);
  expect(r.barHeight).toBeCloseTo(56, 0);
}

test("the Menu drawer ends above the bar, and a tab tap closes it first", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
  await expectBarReachable(page, `${DRAWER}`);
  await expectBarReachable(page, `${DRAWER} > aside`);
  // A second tap on Menu closes it.
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(page.locator(DRAWER)).toHaveCount(0);
  // Menu open, then Search: the drawer closes first, then the search opens.
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(page.locator(DRAWER)).toHaveCount(1);
  await page.locator(BAR_TABS).getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.locator(DRAWER)).toHaveCount(0);
  await expect(page.getByRole("dialog").first()).toBeVisible();
});

test("the Projects sheet toggles on a second tap, and the Menu replaces it", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  const tab = page.locator(BAR_TABS).getByRole("button", { name: "Projects", exact: true });
  await tab.click();
  await expect(page.locator(DRAWER)).toHaveCount(1);
  await expectBarReachable(page, `${DRAWER} > aside`);
  await tab.click();
  await expect(page.locator(DRAWER)).toHaveCount(0);
  await tab.click();
  await expect(page.locator(DRAWER)).toHaveCount(1);
  await page.getByRole("button", { name: "Menu", exact: true }).click();
  await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
});

test("the selected place, the accent and an idle tab paint three different looks", async ({ page }) => {
  // MOB-12: a selected place, the accent and Ask once drew in one blue. The
  // paint is the claim, so this reads computed colours, not class names.
  test.setTimeout(120_000);
  await openPhone(page);
  const bar = page.locator(BAR_TABS);
  await bar.getByRole("button", { name: "Projects", exact: true }).click();
  await expect(page.locator(DRAWER)).toHaveCount(1);
  const well = (name: string) => bar.getByRole("button", { name, exact: true }).locator("[data-bar-look]");
  await expect(well("Projects")).toHaveAttribute("data-bar-look", "selected");
  await expect(well("Capture")).toHaveAttribute("data-bar-look", "accent");
  await expect(well("Search")).toHaveAttribute("data-bar-look", "idle");
  const paint = await page.evaluate(() => {
    const pick = (look: string) =>
      document.querySelector(`[data-phone-nav-bar] [data-bar-look="${look}"]`) as HTMLElement;
    const bg = (el: Element | null) => (el ? getComputedStyle(el).backgroundColor : "none");
    return {
      selected: bg(pick("selected")),
      accent: bg(pick("accent").firstElementChild),
      idle: bg(pick("idle")),
    };
  });
  const clear = (c: string) => c === "transparent" || /rgba\(0, 0, 0, 0\)/.test(c);
  expect(clear(paint.selected), "the selected place has no pill").toBe(false);
  expect(clear(paint.accent), "the accent has no circle").toBe(false);
  expect(paint.selected, "the pill and the circle paint the same").not.toBe(paint.accent);
  expect(clear(paint.idle), "an idle tab paints a well").toBe(true);
});

test("a Modal ends above the bar, and every tab stays in reach", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  await page.locator("main").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await page.waitForTimeout(250);
  await expectBarReachable(page, '[role="dialog"]');
  // A tap on a tab is a tap on the tab: the Projects sheet opens, and the
  // Modal closes on the outside press. The Modal marks the bar `aria-hidden`
  // while it is open, so the tab is found by its text and not by its role.
  await page.locator(`${BAR_TABS} > button`, { hasText: "Projects" }).click();
  await expect(page.locator(DRAWER)).toHaveCount(1);
  await expect(page.getByRole("dialog", { name: /Keyboard shortcuts/ })).toHaveCount(0);
});

test("the assistant sheet ends above the bar, and Ask closes it", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  const ask = page.locator("[data-ask-tab]");
  await expect(ask).toBeVisible();
  await ask.click();
  await expect(ask).toHaveAttribute("aria-pressed", "true");
  const sheet = '.fixed.inset-0.z-50.flex-col.pt-safe';
  await expect(page.locator(sheet)).toBeVisible();
  await expectBarReachable(page, sheet);
  // A tap on a page tab closes the sheet first, then does its job.
  await page.locator(BAR_TABS).getByRole("button", { name: "Projects", exact: true }).click();
  await expect(ask).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(DRAWER)).toHaveCount(1);
  // Ask opens it again (and the drawer gives way), and a second tap closes it.
  await ask.click();
  await expect(ask).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(DRAWER)).toHaveCount(0);
  await ask.click();
  await expect(ask).toHaveAttribute("aria-pressed", "false");
});

// MOB-9 fix round (task 2002): one bar-tap signal, so a hand-rolled overlay gives way.
test("a hand-rolled overlay gives way to Ask, and the assistant sheet is the top layer", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  await page.locator(`${BAR_TABS} > button`, { hasText: "Capture" }).click();
  const capture = page.locator(".fixed.inset-x-0.z-\\[80\\]");
  await expect(capture).toHaveCount(1);
  const ask = page.locator("[data-ask-tab]");
  await ask.click();
  await expect(capture).toHaveCount(0);
  const sheet = ".fixed.inset-0.z-50.flex-col.pt-safe";
  await expect(page.locator(sheet)).toBeVisible();
  const top = await page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement;
    const b = el.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return !!hit && el.contains(hit);
  }, sheet);
  expect(top, "the assistant sheet is not the top layer").toBe(true);
});

test("a second tap on Search closes it", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  const search = page.locator(`${BAR_TABS} > button`, { hasText: "Search" });
  await search.click();
  await expect(page.getByRole("dialog").first()).toBeVisible();
  await search.click();
  await page.waitForTimeout(500);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

// MOB-11 (task 2004): on the phone Board, the "Manage this project" menu sat
// under the board's sticky status strip, so a tap on Statuses hit the strip.
test("the Board's Manage menu paints above the status strip, and Statuses opens", async ({ page }) => {
  test.setTimeout(120_000);
  await openPhone(page);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("cc-mobile-nav", { detail: "projects-views" })));
  await page.getByText("Board", { exact: true }).first().click({ timeout: 20_000 });
  await page.keyboard.press("Escape");
  await expect(page.locator(".sticky.top-0")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Manage this project" }).click();
  const rows = await page.evaluate(() =>
    Array.from(document.querySelectorAll('[role="menu"] [role="menuitem"]')).map((it) => {
      const b = it.getBoundingClientRect();
      const el = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
      return { label: (it.textContent ?? "").trim(), hit: !!el && it.contains(el) };
    }),
  );
  expect(rows.length).toBeGreaterThanOrEqual(4);
  for (const r of rows) expect(r.hit, `the "${r.label}" row is covered`).toBe(true);
  await page.getByRole("menuitem", { name: "Statuses" }).click();
  await expect(page.getByRole("dialog").first()).toBeVisible();
});
