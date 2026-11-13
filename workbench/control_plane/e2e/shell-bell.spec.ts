import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * NS-6 slice 6a, the shell's one bell, in a browser (`navigation_shell.md`
 * §7.2, `src/lib/shell/ShellBell.tsx`).
 *
 * The flag is a build-time value, so a dev build lets one browser turn the
 * bell on for itself (`localStorage["cc-shell-dock"]`, `lib/shell/dockFlag.ts`).
 * That is how this one suite runs both sides.
 *
 * Each test is written so the obvious wrong build fails it:
 *   1. Flag off: no shell bell, and My Tasks still mounts its own bell.
 *   2. Flag on: the sidebar's Needs you row (the Home row) holds the
 *      feed's count, and neither My Tasks nor Projects mounts a bell.
 *   3. Phone: the Menu drawer holds the bell. The panel lists the same rows
 *      as My Day's "Needs you" card. GL-12 removed the desktop bell, so the
 *      desktop panel cases (Done and Undo, Mark read, Escape) are gone.
 */

const MEMBER = {
  authenticated: true,
  email: "member@example.com",
  name: "Asha Rao",
  is_admin: false,
  features: ["tasks", "email", "projects", "people", "chat"],
  permissions: [],
  roles: ["employee"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const hoursAgo = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString();

/** Three rows, one per kind of act: a task (Done), a notification (Mark read), a reply (none). */
const FEED = {
  count: 3,
  items: [
    {
      id: "tasks:t1",
      app: "tasks",
      kind: "overdue",
      title: "Send the quote",
      detail: "Website relaunch",
      href: "/projects?task=t1",
      at: hoursAgo(30),
      act: "done",
      act_ref: "t1",
    },
    {
      id: "projects:n1",
      app: "projects",
      kind: "notification",
      title: "Priya assigned you Draft the plan",
      detail: null,
      href: "/projects?task=t2",
      at: hoursAgo(2),
      act: "read",
      act_ref: "n1",
    },
    {
      id: "email:acc1:th1",
      app: "email",
      kind: "needs_reply",
      title: "Re: the supplier contract",
      detail: "Ravi Kumar",
      href: "/email?thread=th1",
      at: hoursAgo(5),
      act: null,
      act_ref: null,
    },
  ],
  sources: { tasks: "ok", projects: "ok", email: "ok" },
};

/** The one task the My Tasks store loads, so a Done from the feed finds it. */
const LENS_ROW = {
  id: "t1",
  title: "Send the quote",
  is_mine: true,
  project_id: "p1",
  disposition: "NEXT",
  due_at: hoursAgo(30),
  created_at: hoursAgo(48),
  updated_at: hoursAgo(48),
};

async function setup(page: Page, opts: { dock: boolean; myDay?: boolean }) {
  await page.addInitScript(
    ({ dock, myDay }) => {
      localStorage.setItem("cc-shell-bar", "1");
      localStorage.setItem("cc-shell-nav", "1");
      if (dock) localStorage.setItem("cc-shell-dock", "1");
      else localStorage.removeItem("cc-shell-dock");
      if (myDay) localStorage.setItem("cc-my-day", "1");
      else localStorage.removeItem("cc-my-day");
    },
    { dock: opts.dock, myDay: opts.myDay ?? false },
  );
  await stubApi(
    page,
    {
      "auth/me": MEMBER,
      health: { gateway: "up" },
      "shell/needs": FEED,
      "projects/notifications/read": { marked: 1 },
      "projects/notifications": { rows: [], total: 0, unread: { total: 0, mentions: 0 } },
      "projects/my/inbox": { rows: [LENS_ROW], total: 1 },
      "email/accounts": [],
    },
    { arrayPaths: /^(notes|chat|agent|apps|agents)\b/ },
  );
}

const shellBar = (page: Page) => page.locator("[data-shell-bar]");
const appBar = (page: Page) => page.locator('[data-app-bar="desktop"]');
const panel = (page: Page) => page.getByRole("dialog", { name: "Needs you" });
/** The row titles of a list, in order. */
const titles = (scope: ReturnType<Page["locator"]>) =>
  scope.locator("li a span.block.truncate.text-sm").allInnerTexts();

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("flag off: no shell bell, and My Tasks still mounts its own bell", async ({ page }) => {
    await setup(page, { dock: false });
    await page.goto("/tasks");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    await expect(appBar(page).getByRole("button", { name: /^Notifications/ })).toBeVisible();
    await expect(page.locator("[data-shell-bell]")).toHaveCount(0);
    await expect(shellBar(page).getByRole("button", { name: /^Needs you/ })).toHaveCount(0);
  });

  test("flag on: the sidebar's Needs you row holds the feed's count, and no app mounts its own bell", async ({ page }) => {
    await setup(page, { dock: true });
    await page.goto("/tasks");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    // GL-12 (30e72d332): no top bar. The row is the sidebar's Home row (`data-nav-my-day`), and it carries the count.
    await expect(shellBar(page)).toHaveCount(0);
    const row = page.locator("[data-nav-my-day]");
    await expect(row).toHaveAttribute("aria-label", "Home, 3 need you");
    await expect(row).toContainText("3");
    // Neither app mounts a bell of its own.
    await expect(page.getByRole("button", { name: /^Notifications/ })).toHaveCount(0);
    await page.goto("/projects");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
    await expect(page.locator("[data-nav-my-day]")).toBeVisible();
    await expect(page.getByRole("button", { name: /^Notifications/ })).toHaveCount(0);
  });

  // The panel cases (Escape and focus, Done and Undo, Mark read) opened the
  // panel from the desktop bell. GL-12 removed that bell, and the sidebar row is
  // a link to My Day, so the desktop has no trigger. The phone case below
  // still opens the panel, and `shellBell.test.ts` fences the rows.
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the Menu drawer holds the bell, and it opens the same list as a sheet", async ({ page }) => {
    await setup(page, { dock: true });
    await page.goto("/tasks");
    // No app bar bell on the phone either.
    await expect(page.getByRole("button", { name: /^Notifications/ })).toHaveCount(0);
    // `dispatchEvent`: on a phone the dev server's own badge sits over the
    // Menu tab, and a pointer click lands on it. A production build has none.
    await page.getByRole("button", { name: "Menu" }).dispatchEvent("click");
    const phoneBell = page.getByRole("button", { name: "Needs you, 3 items" });
    await expect(phoneBell).toBeVisible();
    await phoneBell.click();
    await expect(panel(page)).toBeVisible();
    expect(await titles(panel(page))).toEqual([
      "Send the quote",
      "Priya assigned you Draft the plan",
      "Re: the supplier contract",
    ]);
  });
});
