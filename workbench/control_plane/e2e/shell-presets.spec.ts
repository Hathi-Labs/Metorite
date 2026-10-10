import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * NS-7 in a browser: presets, pins and the first sign-in question
 * (`navigation_shell.md` §8, the owner's table of 2026-10-10).
 *
 *   1. A first visit asks "What will you do most here?". An answer sets the
 *      preset, and the sidebar's "My apps" shows its pins.
 *   2. "Skip for now" is stored, so a reload does not ask again. The role's
 *      preset then fills "My apps".
 *   3. The star in All apps pins an app and unpins it.
 *   4. "Change my layout" in the account menu asks again.
 *   5. A founder's `?welcome=new-org` asks the question first, then shows the
 *      welcome, one dialog at a time.
 *
 * No gateway runs here, so `/api/auth/me/shell` is an in-memory store that
 * answers as the gateway does.
 *
 * Mutation, seen to fail first: make "Skip for now" close the dialog without
 * a write, and case 2 fails, because the reload asks again.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "priya@example.com",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const EMPTY = { preset: null, answered: null, pins: null, cardOrder: null, newOrder: null };

type Layout = typeof EMPTY | Record<string, unknown>;

interface Store {
  layout: Layout;
  puts: unknown[];
}

async function stub(page: Page, start: Layout = EMPTY, me: unknown = MEMBER): Promise<Store> {
  const store: Store = { layout: start, puts: [] };
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  });
  // One router, on purpose (`org-branding.spec.ts` says why).
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") {
      if (req.method() === "PUT") {
        const body = req.postDataJSON() as Record<string, unknown> | null;
        store.puts.push(body);
        store.layout = body === null ? EMPTY : { ...EMPTY, ...body };
      }
      return json(store.layout);
    }
    if (path === "/api/auth/me") return json(me);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    return json([]);
  });
  return store;
}

const question = (page: Page) => page.getByRole("dialog", { name: "What will you do most here?" });
const myApps = (page: Page) => page.locator('aside [data-nav-section="my-apps"] a');
const sidebar = (page: Page) => page.locator("aside[data-collapsed]");

test.use({ viewport: { width: 1280, height: 860 } });

test("a first visit asks the question, and the answer sets the pins", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  await question(page).getByRole("button", { name: /Build and ship the work/ }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toEqual([{ ...EMPTY, preset: "engineer", answered: "answered" }]);
  // The Engineer's pins, in the table's order, first in the sidebar.
  await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat"]);
  // A pinned app moves into My apps. It keeps one door.
  await expect(sidebar(page).getByRole("link", { name: "Projects" })).toHaveCount(1);
});

test("Skip for now is stored, and a reload does not ask again", async ({ page }) => {
  const store = await stub(page);
  await page.goto("/settings/appearance");
  await expect(question(page)).toBeVisible();
  await question(page).getByRole("button", { name: "Skip for now" }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toEqual([{ ...EMPTY, answered: "skipped" }]);

  const read = page.waitForResponse((r) => r.url().endsWith("/api/auth/me/shell"));
  await page.reload();
  await read;
  await expect(sidebar(page)).toBeVisible();
  // The role picks: a member gets New hire.
  await expect(myApps(page)).toHaveText(["My Tasks", "People", "Chat"]);
  await expect(question(page)).toHaveCount(0);
  expect(store.puts).toHaveLength(1);
});

test("the star in All apps pins an app, and unpins it", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered" });
  await page.goto("/settings/appearance");
  await expect(myApps(page)).toHaveCount(4);
  await expect(question(page)).toHaveCount(0);

  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  const launcher = page.getByRole("dialog", { name: "All apps" });
  const star = launcher.getByRole("button", { name: "Pin My Email to My apps" });
  await expect(star).toHaveAttribute("aria-pressed", "false");
  await star.click();
  const unstar = launcher.getByRole("button", { name: "Unpin My Email from My apps" });
  await expect(unstar).toHaveAttribute("aria-pressed", "true");
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/projects", "/calendar", "/chat", "/email"] });
  await page.keyboard.press("Escape");
  await expect(myApps(page)).toHaveText(["My Tasks", "Projects", "Calendar", "Chat", "My Email"]);

  await sidebar(page).getByRole("button", { name: "All apps" }).click();
  await launcher.getByRole("button", { name: "Unpin My Email from My apps" }).click();
  expect(store.puts.at(-1)).toMatchObject({ pins: ["/tasks", "/projects", "/calendar", "/chat"] });
  await page.keyboard.press("Escape");
  await expect(myApps(page)).toHaveCount(4);
});

test("Change my layout in the account menu asks again", async ({ page }) => {
  const store = await stub(page, { ...EMPTY, preset: "engineer", answered: "answered" });
  await page.goto("/settings/appearance");
  await expect(myApps(page)).toHaveCount(4);
  await expect(question(page)).toHaveCount(0);

  // From the keyboard: in the dev build, Next's own badge sits over the foot.
  const foot = page.getByRole("button", { name: /^Accounts: signed in as/ });
  await foot.focus();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Change my layout" }).click();
  await expect(question(page)).toBeVisible();
  // The way out keeps the layout as it is.
  await expect(question(page).getByRole("button", { name: "Keep my layout" })).toBeVisible();
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  await expect(question(page)).toHaveCount(0);
  expect(store.puts.at(-1)).toEqual({ ...EMPTY, preset: "founder", answered: "answered" });
  // Founder pins Approvals too. A member who holds it sees it.
  await expect(myApps(page)).toHaveText(["Projects", "Approvals", "My Email", "Chat"]);
});

test("a founder's welcome follows the question, one dialog at a time", async ({ page }) => {
  await stub(page, EMPTY, { ...MEMBER, is_admin: true, roles: ["owner"] });
  await page.goto("/settings/appearance?welcome=new-org");
  await expect(question(page)).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(question(page)).toContainText("Your organization is ready");
  await question(page).getByRole("button", { name: /Run the company/ }).click();
  const welcome = page.getByRole("dialog", { name: "Your organization is ready" });
  await expect(welcome).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await welcome.getByRole("button", { name: "Explore on my own first" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).toHaveURL(/\/settings\/appearance$/);
});
