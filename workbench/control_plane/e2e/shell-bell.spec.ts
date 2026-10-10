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
 *   2. Flag on: the bell is in the shell bar with the feed's count, and
 *      neither My Tasks nor Projects mounts a bell of its own.
 *   3. The panel lists the same rows as My Day's "Needs you" card.
 *   4. Done in the panel takes the row out of the panel AND the card, and
 *      the store's Undo shows.
 *   5. Escape closes the panel, and focus goes back to the bell.
 *   6. Phone: the Menu drawer holds the bell, and it opens the same list.
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
/**
 * The shell bar's bell. By its marker, not its role: while the panel is up,
 * the dialog marks the page behind it `aria-hidden`, and a role query
 * finds nothing there.
 */
const bell = (page: Page) => shellBar(page).locator("[data-shell-bell]");
const panel = (page: Page) => page.getByRole("dialog", { name: "Needs you" });
const card = (page: Page) => page.getByTestId("needs-you");
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

  test("flag on: the bell is in the shell bar with the feed's count, and no app mounts its own", async ({ page }) => {
    await setup(page, { dock: true });
    await page.goto("/tasks");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 3 items");
    await expect(bell(page).locator("[data-nav-badge]")).toHaveText("3");
    // The bell sits before the activity control, at the bar's right end.
    const end = shellBar(page).locator("div.justify-end").last();
    await expect(end.locator("button").first()).toHaveAttribute("data-shell-bell", "count");
    // Neither app mounts a bell of its own.
    await expect(page.getByRole("button", { name: /^Notifications/ })).toHaveCount(0);
    await page.goto("/projects");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
    await expect(bell(page)).toBeVisible();
    await expect(page.getByRole("button", { name: /^Notifications/ })).toHaveCount(0);
  });

  test("the panel lists My Day's rows, Done takes a row out of both, and Escape returns focus", async ({ page }) => {
    await setup(page, { dock: true, myDay: true });
    await page.goto("/");
    await expect(card(page).getByText("Send the quote")).toBeVisible();
    const cardRows = await titles(card(page));
    expect(cardRows).toEqual(["Send the quote", "Priya assigned you Draft the plan", "Re: the supplier contract"]);

    await bell(page).click();
    await expect(panel(page)).toBeVisible();
    // The same groups, in the same order, with the same rows.
    for (const group of ["Overdue", "From your projects", "Waiting for your reply"]) {
      await expect(panel(page).getByRole("group", { name: group })).toBeVisible();
    }
    expect(await titles(panel(page))).toEqual(cardRows);
    await expect(panel(page).getByRole("link", { name: /Open My Day/ })).toBeVisible();

    // Done, through the My Tasks store's own gesture.
    await panel(page).getByRole("button", { name: "Mark Send the quote done" }).click();
    await expect(panel(page).getByText("Send the quote")).toHaveCount(0);
    await expect(bell(page)).toHaveAttribute("aria-label", "Needs you, 2 items");
    // The store's Undo, as on My Day.
    await expect(page.getByRole("button", { name: "Undo" })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(panel(page)).toHaveCount(0);
    await expect(bell(page)).toBeFocused();
    // The card is the same list, so the row left it too.
    await expect(card(page).getByText("Send the quote")).toHaveCount(0);
    await expect(card(page).getByText("Priya assigned you Draft the plan")).toBeVisible();
  });

  test("Mark read takes a notification out of both, through the Projects route", async ({ page }) => {
    await setup(page, { dock: true, myDay: true });
    const reads: string[] = [];
    page.on("request", (req) => {
      if (req.method() === "POST" && req.url().includes("/api/projects/notifications/read")) reads.push(req.postData() ?? "");
    });
    await page.goto("/");
    await expect(card(page).getByText("Priya assigned you Draft the plan")).toBeVisible();
    await bell(page).click();
    await panel(page).getByRole("button", { name: "Mark Priya assigned you Draft the plan read" }).click();
    await expect(panel(page).getByText("Priya assigned you Draft the plan")).toHaveCount(0);
    await expect(card(page).getByText("Priya assigned you Draft the plan")).toHaveCount(0);
    expect(reads).toEqual([JSON.stringify({ ids: ["n1"] })]);
  });
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
