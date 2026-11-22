import fs from "node:fs";
import path from "node:path";

import { test, type Page } from "@playwright/test";

// Throwaway rig for MOB-2. Not committed.
const OUT = process.env.UX_OUT || "ux-shots/mob2";
const TAG = process.env.UX_TAG || "after";

const STATUSES = ["Backlog", "To do", "Spec ready", "In progress", "Done"].map((name, i) => ({
  id: `s${i}`,
  project_id: "p2",
  name,
  color: ["gray", "blue", "violet", "amber", "green"][i],
  position: (i + 1) * 10,
  category: i === 4 ? "done" : i === 0 ? "backlog" : "todo",
  is_default: i === 0,
}));
const day = 86_400_000;
const T = (i: number, title: string, status: string, due: number | null, done = false) => ({
  id: `t${i}`,
  project_id: "p2",
  root_project_id: "p1",
  task_number: 140 + i,
  status_id: status,
  title,
  tags: [],
  assignees: i % 2 ? ["asha@example.com"] : [],
  custom_fields: {},
  subtasks: { done: 0, total: 0 },
  blocked_by_count: 0,
  due_at: due === null ? null : new Date(Date.now() + due * day).toISOString(),
  completed_at: done ? new Date().toISOString() : null,
});
const TASKS = [
  T(1, "Notification engine for projects", "s2", 2),
  T(2, "Send the revised quote to Fracktal for the printer order, with the new tax lines and the shipping terms", "s3", -1),
  T(3, "Review the Q4 hiring plan", "s1", 5),
  T(4, "Fix the bootloader checksum", "s1", null),
  T(5, "Write the release notes for the March firmware drop and post them in the channel", "s0", 12),
  T(6, "Ship the dock", "s4", -3, true),
];
const TREE = [
  {
    id: "p1",
    name: "Firmware",
    parent_project_id: null,
    kind: "project",
    children: [{ id: "p2", name: "Bootloader", parent_project_id: "p1", kind: "project", children: [] }],
  },
];
const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

async function setup(page: Page) {
  const fixtures: Record<string, unknown> = {
    "auth/me": { email: "asha@example.com", name: "Asha Rao" },
    "projects/tree": { rows: TREE, total: 1 },
    "nodes/p2/statuses": { rows: STATUSES, total: STATUSES.length },
    "projects/tasks": { rows: TASKS, total: TASKS.length },
  };
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^\/api\//, "");
    if (req.method() === "PATCH") return route.fulfill({ json: { ...TASKS[0], ...JSON.parse(req.postData() ?? "{}") } });
    const hit = Object.keys(fixtures)
      .sort((a, b) => b.length - a.length)
      .find((k) => p.includes(k));
    return route.fulfill({ json: hit ? (fixtures[hit] as object) : EMPTY });
  });
}

async function shoot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, `${TAG}-${name}.png`) });
}
const light = (page: Page) => page.evaluate(() => document.documentElement.classList.add("light"));
const dark = (page: Page) => page.evaluate(() => document.documentElement.classList.remove("light"));

async function open(page: Page) {
  await setup(page);
  await page.goto("/projects?project=p2");
  await page.getByText("Ship the dock").first().waitFor({ timeout: 90_000 });
  await page.waitForTimeout(1500);
}

test.describe("phone 390", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  test("390", async ({ page }) => {
    test.setTimeout(240_000);
    await open(page);
    await dark(page);
    await shoot(page, "dark-390");
    await light(page);
    await shoot(page, "light-390");
    if (TAG === "after") {
      await page.evaluate(() => document.documentElement.style.setProperty("--ui-scale", "0.875"));
      await shoot(page, "compact-light-390");
      await page.evaluate(() => document.documentElement.style.removeProperty("--ui-scale"));
      await page.evaluate(() => document.documentElement.style.setProperty("--primary", "hsl(280 70% 55%)"));
      await shoot(page, "accent-violet-light-390");
      await page.evaluate(() => document.documentElement.style.removeProperty("--primary"));
      // A finished row, after a tap on the first tick.
      await page.getByRole("button", { name: /^Mark Notification engine/ }).tap();
      await page.waitForTimeout(800);
      await shoot(page, "after-tick-light-390");
      // Selection mode, by a long press.
      const box = (await page.locator("[data-phone-row]").nth(2).boundingBox())!;
      const cdp = await page.context().newCDPSession(page);
      const pt = [{ x: box.x + 200, y: box.y + 20 }];
      await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: pt });
      await page.waitForTimeout(800);
      await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await page.waitForTimeout(300);
      await shoot(page, "select-mode-light-390");
    }
  });
});

test.describe("phone 360", () => {
  test.use({ viewport: { width: 360, height: 780 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  test("360", async ({ page }) => {
    test.setTimeout(240_000);
    await open(page);
    await light(page);
    await shoot(page, "light-360");
    await dark(page);
    await shoot(page, "dark-360");
  });
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 }, isMobile: false, hasTouch: false });
  test("1440", async ({ page }) => {
    test.setTimeout(240_000);
    await open(page);
    await page.getByRole("tab", { name: "List" }).or(page.getByRole("button", { name: "List", exact: true })).first().click();
    await page.waitForTimeout(800);
    await light(page);
    await shoot(page, "list-light-1440");
  });
});
