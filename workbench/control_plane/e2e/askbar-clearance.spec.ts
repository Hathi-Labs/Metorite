import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The floating Ask bar never hides the last line of a pane (task 2242, owner
 * 2026-11-07: "extra scroll down so that all the elements appear above the
 * chat box across all of the apps").
 *
 * ## The rule
 *
 * At 1440 x 900, every vertical scroller that the bar overlaps scrolls its
 * last line clear of the bar's top edge. `src/lib/shell/askBarClearance.ts`
 * decides which scroller gets room. `src/lib/shell/askBarClearance.test.ts`
 * fences the rules, and THIS spec measures the layout, which a unit test
 * cannot see: padding at the end of a flex-column scroller must be scrollable.
 *
 * Four cases: Projects with 40 tasks (a board scroller nested in the app),
 * People with 30 people, Appearance (a settings page), and a People
 * directory that does NOT overflow but ends under the bar, which must now
 * scroll. A fifth asserts that the phone layout is not touched.
 *
 * Start the server with `npx playwright test e2e/askbar-clearance.spec.ts`.
 * If :3101 is taken, run a throwaway config on a free port.
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  name: "Asha Rao",
  is_admin: true,
  features: ["tasks", "email", "projects", "people", "chat", "knowledge", "studio"],
  permissions: ["admin:members:read", "admin:members:manage"],
  capabilities: ["admin:members:read", "admin:members:manage"],
  roles: ["owner"],
  organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
};

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
const tasks = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
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

const people = (n: number) => ({
  rows: Array.from({ length: n }, (_, i) => ({
    id: `person-${i}`,
    name: `Person ${String(i + 1).padStart(2, "0")}`,
    email: `p${i}@acme.example`,
    title: "Engineer",
    department: "Engineering",
    team: "Firmware",
    status: "active",
    skills: ["c", "rust"],
  })),
  total: n,
  hr_visible: true,
  can_manage: false,
  self_person_id: null,
});

async function open(page: Page, path: string, handlers: Record<string, unknown>, size = { width: 1440, height: 900 }) {
  await page.setViewportSize(size);
  await page.addInitScript(() => {
    for (const k of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) window.localStorage.setItem(k, "1");
  });
  await stubApi(page, { "auth/me": ADMIN, ...handlers }, { arrayPaths: /^(notes|chat|agent|apps|agents|studio)\b/ });
  await page.goto(path);
  await page.waitForLoadState("domcontentloaded");
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
}

/**
 * Scroll every scroller under the bar to its end. Return, for each, the foot
 * of its last descendant, the bar's top, and whether it scrolled.
 */
async function endsUnderBar(page: Page) {
  return page.evaluate(() => {
    const field = document.querySelector("[data-ask-bar] [data-ask-form]") as HTMLElement;
    const barTop = field.getBoundingClientRect().top;
    const barLeft = field.getBoundingClientRect().left;
    const barRight = field.getBoundingClientRect().right;
    const out: Array<{ cls: string; last: number; barTop: number; scrolled: boolean; marked: boolean; natural: boolean }> = [];
    const under = Array.from(document.querySelectorAll<HTMLElement>("*")).filter((el) => {
      const cs = getComputedStyle(el);
      if (!/auto|scroll/.test(cs.overflowY) || el.clientHeight < 80) return false;
      const r = el.getBoundingClientRect();
      if (r.bottom <= barTop || r.right <= barLeft || r.left >= barRight) return false;
      // A box that grows past the window does not scroll: an ancestor does.
      return r.bottom <= window.innerHeight + 1;
    });
    // Only the innermost scroller ends the page's content under the bar.
    for (const el of under.filter((e) => !under.some((o) => o !== e && e.contains(o)))) {
      const extra = parseFloat(el.style.getPropertyValue("--askbar-extra")) || 0;
      const natural = el.scrollHeight - extra > el.clientHeight + 1;
      el.scrollTop = el.scrollHeight;
      let last = 0;
      for (const d of Array.from(el.querySelectorAll<HTMLElement>("*"))) {
        const dr = d.getBoundingClientRect();
        if (dr.height > 0 && dr.width > 0 && getComputedStyle(d).position !== "fixed") last = Math.max(last, dr.bottom);
      }
      out.push({
        cls: String(el.className).slice(0, 60),
        last: Math.round(last),
        barTop: Math.round(barTop),
        scrolled: el.scrollTop > 0,
        marked: el.hasAttribute("data-askbar-clear"),
        natural,
      });
    }
    return out;
  });
}

async function settled(page: Page) {
  await expect(page.locator("[data-ask-bar] [data-ask-form]")).toBeVisible({ timeout: 60_000 });
  // The hook marks once the page has content: wait for a mark, then for quiet.
  await expect(page.locator("[data-askbar-clear]").first()).toBeAttached({ timeout: 20_000 });
  await page.waitForTimeout(600);
}

function expectClear(rows: Awaited<ReturnType<typeof endsUnderBar>>, label: string) {
  expect(rows.length, `${label}: no scroller under the bar`).toBeGreaterThan(0);
  for (const r of rows) {
    expect(r.last, `${label}: "${r.cls}" ends under the bar (last ${r.last}, bar top ${r.barTop})`).toBeLessThanOrEqual(
      r.barTop,
    );
  }
}

test("Projects with 40 tasks scrolls the last card above the bar", async ({ page }) => {
  test.setTimeout(120_000);
  await open(page, "/projects?project=p2", {
    "projects/tree": { rows: TREE, total: 1 },
    "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
    "projects/tasks": { rows: tasks(40), total: 40 },
  });
  await expect(page.getByText("Task number 1", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await settled(page);
  expectClear(await endsUnderBar(page), "Projects");
});

test("People with 30 people scrolls the last row above the bar", async ({ page }) => {
  test.setTimeout(120_000);
  await open(page, "/people", { "people/facets": { departments: [], statuses: [] }, people: people(30) });
  await expect(page.getByText("Person 30")).toBeVisible({ timeout: 60_000 });
  await settled(page);
  const rows = await endsUnderBar(page);
  expectClear(rows, "People");
  expect(rows.some((r) => r.scrolled)).toBe(true);
});

test("a page parked at its end stays put through a re-scan, and rests with no scans", async ({ page }) => {
  // Review round 1: a scan lifts each mark, which clamps a parked page. It must
  // put every scroll position back. A scroll starts a scan, and the scan's own
  // restore must not start another, or a parked page scans for ever.
  test.setTimeout(120_000);
  await open(page, "/people", { "people/facets": { departments: [], statuses: [] }, people: people(30) });
  await expect(page.getByText("Person 30")).toBeVisible({ timeout: 60_000 });
  await settled(page);
  expectClear(await endsUnderBar(page), "People, at its end");
  await page.waitForTimeout(700);
  const pane = page.locator("[data-askbar-clear]").first();
  const parked = await pane.evaluate((el) => el.scrollTop);
  expect(parked).toBeGreaterThan(0);
  await page.evaluate(() => {
    const w = window as unknown as { __scans: number };
    w.__scans = 0;
    const real = Element.prototype.querySelectorAll;
    Element.prototype.querySelectorAll = function (this: Element, sel: string) {
      if (sel.includes('class*="overflow"')) w.__scans += 1;
      return real.call(this, sel);
    } as typeof Element.prototype.querySelectorAll;
  });
  const scans = () => page.evaluate(() => (window as unknown as { __scans: number }).__scans);
  // A node mounted in the column starts a scan, as the Ask bar's own rows do.
  await page.evaluate(() => document.querySelector("main")!.appendChild(document.createElement("span")));
  await expect.poll(scans, { timeout: 5_000 }).toBeGreaterThan(0);
  await page.waitForTimeout(700);
  expect(await pane.evaluate((el) => el.scrollTop), "the re-scan moved the page").toBe(parked);
  // At rest, parked at the end, nothing scans.
  const atRest = await scans();
  await page.waitForTimeout(1500);
  expect(await scans(), "a parked page keeps scanning").toBe(atRest);
  // A real scroll starts a scan once it stops.
  await pane.evaluate((el) => {
    el.scrollTop -= 120;
  });
  await expect.poll(scans, { timeout: 5_000 }).toBeGreaterThan(atRest);
  expectClear(await endsUnderBar(page), "People, after the re-scans");
});

test("a scan puts back a holder that only its nested pane makes scroll", async ({ page }) => {
  // Review round 2: lifting the mark shrinks the nested pane, so its unmarked
  // holder clamps. Only a restore of EVERY scroller puts the holder back.
  test.setTimeout(120_000);
  await open(page, "/people", { "people/facets": { departments: [], statuses: [] }, people: people(3) });
  await expect(page.locator("[data-ask-bar] [data-ask-form]")).toBeVisible({ timeout: 60_000 });
  await page.evaluate(() => {
    const holder = document.createElement("div");
    holder.id = "fx-holder";
    holder.className = "overflow-y-auto";
    holder.style.cssText = "position:fixed;left:30%;right:30%;top:500px;bottom:16px;z-index:5;background:gray";
    const nested = document.createElement("div");
    nested.id = "fx-nested";
    nested.className = "overflow-y-auto";
    holder.appendChild(nested);
    document.querySelector("main")!.appendChild(holder);
    const body = document.createElement("div");
    body.style.height = `${holder.clientHeight}px`;
    nested.appendChild(body);
  });
  await expect(page.locator("#fx-nested[data-askbar-clear]")).toBeAttached({ timeout: 10_000 });
  await page.waitForTimeout(500);
  const end = await page.locator("#fx-holder").evaluate((el) => {
    el.scrollTop = el.scrollHeight;
    return el.scrollTop;
  });
  expect(end, "the nested pane's room does not make the holder scroll").toBeGreaterThan(0);
  await page.waitForTimeout(700);
  await page.evaluate(() => document.querySelector("main")!.appendChild(document.createElement("span")));
  await page.waitForTimeout(700);
  expect(await page.locator("#fx-holder").evaluate((el) => el.scrollTop), "the scan left the holder clamped").toBe(end);
});

test("a settings page scrolls its last line above the bar", async ({ page }) => {
  test.setTimeout(120_000);
  await open(page, "/settings/appearance", {});
  await settled(page);
  expectClear(await endsUnderBar(page), "Appearance");
});

test("a pane that does not overflow but ends under the bar now scrolls", async ({ page }) => {
  test.setTimeout(180_000);
  // Find the count whose last row ends just under the bar's top edge, while
  // the directory's own height still holds it. One row is the same height.
  await open(page, "/people", { "people/facets": { departments: [], statuses: [] }, people: people(3) });
  await expect(page.getByText("Person 03")).toBeVisible({ timeout: 60_000 });
  const rowH = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll<HTMLElement>('a[href^="/people/person-"]'));
    return rows[1].getBoundingClientRect().top - rows[0].getBoundingClientRect().top;
  });
  const geo = await page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll<HTMLElement>('a[href^="/people/person-"]'));
    const field = document.querySelector("[data-ask-bar] [data-ask-form]") as HTMLElement;
    return { last: rows[rows.length - 1].getBoundingClientRect().bottom, barTop: field.getBoundingClientRect().top };
  });
  const n = 3 + Math.ceil((geo.barTop + 8 - geo.last) / rowH);
  await page.unroute("**/api/**");
  await stubApi(page, { "auth/me": ADMIN, "people/facets": { departments: [], statuses: [] }, people: people(n) });
  await page.reload();
  await expect(page.getByText(`Person ${String(n).padStart(2, "0")}`)).toBeVisible({ timeout: 60_000 });
  await settled(page);
  const rows = await endsUnderBar(page);
  expectClear(rows, "People (no natural overflow)");
  const pane = rows.find((r) => !r.natural && r.marked);
  expect(pane, "no pane that ends under the bar without overflowing").toBeTruthy();
  expect(pane!.scrolled, "the pane did not scroll").toBe(true);
});

test("the phone layout gets no clearance", async ({ page }) => {
  test.setTimeout(120_000);
  await open(page, "/people", { "people/facets": { departments: [], statuses: [] }, people: people(30) }, { width: 390, height: 844 });
  await expect(page.getByText("Person 30")).toBeAttached({ timeout: 60_000 });
  await page.waitForTimeout(1500);
  await expect(page.locator("[data-askbar-clear]")).toHaveCount(0);
});
