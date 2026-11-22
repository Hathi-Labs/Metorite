import fs from "node:fs";
import path from "node:path";

import { test, type Page, type Route } from "@playwright/test";

/** A throwaway rig for CC-4 captures. Not committed. */
const OUT = process.env.UX_OUT || "ux-shots/cc4";

const SESSION = { user: { email: "asha@example.com", name: "Asha Rao" }, expires: "2099-01-01T00:00:00.000Z" };
const MEMBER = {
  authenticated: true,
  email: "asha@example.com",
  name: "Asha Rao",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "calendar"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};
const KNOWN = ["/tasks", "/calendar", "/email", "/projects", "/people", "/chat"];
const LAYOUT = { preset: "engineer", answered: "answered", pins: KNOWN, known: KNOWN, cardOrder: null, newOrder: null };
const at = (mins: number) => new Date(Date.now() + mins * 60_000).toISOString();
const raw = (id: string, title: string, startMin: number, over: Record<string, unknown> = {}) => ({
  id,
  title,
  disposition: "NEXT",
  project_id: "p2",
  root_project_id: "p1",
  project_name: "Sales - Fracktal",
  status_id: "s-todo",
  status_category: "todo",
  workflow_stage: "To do",
  scheduled_start: at(startMin),
  scheduled_end: at(startMin + 60),
  is_mine: true,
  created_at: at(-1000),
  updated_at: at(-500),
  ...over,
});
const STATUSES = [
  { id: "s-todo", project_id: "p2", name: "To do", color: "blue", position: 20, category: "todo", is_default: false },
  { id: "s-prog", project_id: "p2", name: "In progress", color: "amber", position: 30, category: "in_progress", is_default: false },
];
const tasks: Record<string, ReturnType<typeof raw>> = {
  t1: raw("t1", "Send the revised quote to Fracktal for the printer order", -10, { due_at: at(300), subtask_count: 3, subtask_done: 2 }),
  t3: raw("t3", "Design review with Kim and Ravi", 70, { status_id: "s-prog", status_category: "in_progress" }),
  t4: raw("t4", "Write the investor update", 180, { status_id: "s-prog", status_category: "in_progress" }),
};
const SUBS = [
  { id: "s1", title: "Update the unit price sheet", disposition: "DONE" },
  { id: "s2", title: "Add the shipping terms", disposition: "DONE" },
  { id: "s3", title: "Write the cover note", disposition: "NEXT" },
];

async function setup(page: Page, extra: { accent?: boolean; compact?: boolean } = {}) {
  await page.addInitScript((x) => {
    for (const k of ["cc-shell-bar", "cc-shell-nav", "cc-shell-dock", "cc-my-day", "cc-my-day-feed"]) localStorage.setItem(k, "1");
    if (x.accent) {
      localStorage.setItem("cc-accent", "#8b5cf6");
      localStorage.setItem("cc-accent-ink", "#ffffff");
    }
    if (x.compact) localStorage.setItem("cc-density", "compact");
  }, extra);
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname.replace(/^\/api\//, "");
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "shell/feed") return json({ cards: [], groups: [], needs: { count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] }, sources: {}, capped: [] });
    if (p === "shell/needs") return json({ count: 0, total: 0, items: [], sources: {}, totals: {}, capped: [] });
    if (p === "auth/me/shell") return json(LAYOUT);
    if (p === "auth/me") return json(MEMBER);
    if (p === "auth/session") return json(SESSION);
    if (p === "health") return json({ gateway: "up" });
    if (p === "accounts") return json({ enabled: false });
    if (p === "settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (p === "projects/my/calendar") return json({ rows: Object.values(tasks), total: 3 });
    if (p === "projects/nodes/p2/statuses") return json({ rows: STATUSES, total: 2, counts: {}, owner_id: "p1" });
    if (p === "projects/tree") {
      return json({ rows: [{ id: "p1", name: "Hathi Labs", parent_project_id: null, kind: "project", children: [{ id: "p2", name: "Fracktal", parent_project_id: "p1", kind: "project", children: [] }] }], total: 1 });
    }
    let m = p.match(/^projects\/tasks\/([^/]+)\/personal$/);
    if (m) {
      Object.assign(tasks[m[1]] ?? {}, req.postDataJSON());
      return json(tasks[m[1]] ?? {});
    }
    m = p.match(/^projects\/tasks\/([^/]+)$/);
    if (m && req.method() === "PATCH") {
      const b = req.postDataJSON() as Record<string, unknown>;
      const lane = STATUSES.find((s) => s.id === b.status_id);
      if (lane && tasks[m[1]]) Object.assign(tasks[m[1]], { status_id: lane.id, status_category: lane.category, workflow_stage: lane.name });
      return json(tasks[m[1]] ?? {});
    }
    if (m && req.method() === "GET") {
      return json({ ...tasks[m[1]], tags: [], assignees: [], custom_fields: {}, subtasks: { done: 2, total: 3 }, blocked_by_count: 0 });
    }
    if (p === "projects/tasks") {
      const rows = SUBS.map((x) => ({ ...x, completed_at: x.disposition === "DONE" ? at(-5) : null }));
      return json({ rows, total: 3 });
    }
    m = p.match(/^projects\/my\/tasks\/([^/]+)$/);
    if (m && tasks[m[1]]) return json(tasks[m[1]]);
    if (p.startsWith("projects/notifications")) return json({ rows: [], total: 0, unread: { total: 0, mentions: 0 } });
    if (p === "chat/active-sessions") return json([]);
    return json({ rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] });
  });
}

async function shoot(page: Page, name: string) {
  fs.mkdirSync(OUT, { recursive: true });
  await page.waitForTimeout(700);
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
}

const VARIANTS: { key: string; w: number; h: number; mobile: boolean; light: boolean; accent?: boolean; compact?: boolean }[] = [
  { key: "dark-1440", w: 1440, h: 900, mobile: false, light: false },
  { key: "light-1440", w: 1440, h: 900, mobile: false, light: true },
  { key: "light-390", w: 390, h: 844, mobile: true, light: true },
  { key: "compact-light-1440", w: 1440, h: 900, mobile: false, light: true, compact: true },
  { key: "accent-light-1440", w: 1440, h: 900, mobile: false, light: true, accent: true },
];

for (const v of VARIANTS) {
  test.describe(v.key, () => {
    test.use({ viewport: { width: v.w, height: v.h }, isMobile: v.mobile, hasTouch: v.mobile, deviceScaleFactor: v.mobile ? 2 : 1 });
    test(`my day ${v.key}`, async ({ page }) => {
      test.setTimeout(180_000);
      Object.assign(tasks.t1, { status_id: "s-todo", status_category: "todo", workflow_stage: "To do" });
      await setup(page, { accent: v.accent, compact: v.compact });
      await page.goto("/");
      await page.getByTestId("now-card").waitFor({ timeout: 120_000 });
      if (v.light) await page.evaluate(() => document.documentElement.classList.add("light"));
      await shoot(page, `now-idle--${v.key}`);
      await page.getByTestId("now-start").click();
      await page.getByTestId("now-pause").waitFor({ timeout: 30_000 });
      await page.waitForTimeout(1500);
      await shoot(page, `now-running--${v.key}`);
      if (v.key === "light-1440") {
        await page.getByTestId("now-open-room").click();
        await shoot(page, `room--${v.key}`);
        await page.getByRole("button", { name: "Capture a thought" }).click();
        await shoot(page, `room-capture--${v.key}`);
      }
    });
  });
}

test.describe("panel", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  for (const after of [false, true]) {
    test(`projects task panel ${after ? "after" : "before"}`, async ({ page }) => {
      test.setTimeout(180_000);
      Object.assign(tasks.t1, { status_id: "s-todo", status_category: "todo", workflow_stage: "To do" });
      await setup(page);
      await page.goto("/projects?task=t1");
      await page.getByTestId("task-start-focus").waitFor({ timeout: 120_000 });
      if (!after) await page.addStyleTag({ content: "[data-testid=task-start-focus]{display:none!important}" });
      await page.evaluate(() => document.documentElement.classList.add("light"));
      await shoot(page, `panel-${after ? "after" : "before"}--light-1440`);
    });
  }
});
