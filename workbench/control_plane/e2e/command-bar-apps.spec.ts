import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The command bar finds every app the member holds, pinned or not (owner,
 * 2026-10-11). The sidebar shows only the pinned apps, so the bar is the one
 * door to an app the member did not pin.
 *
 * Here the member pinned NOTHING (`pins: []`), and each case is written so
 * the obvious wrong build fails it:
 *   1. ⌘K, "peo", Enter: the member lands on People.
 *   2. An admin finds an Admin app: "orga", Enter, Organisation.
 *   3. A member without the grant finds no People, no Approvals and no
 *      Organisation.
 *
 * Mutation, seen red first: build the bar's panes from
 * `pinnedPanes(shell.layout.pins, …)` in `ShellBar.tsx`, and cases 1 and 2
 * fail, because no pin means no app.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};

const person = (admin: boolean, features: string[]) => ({
  authenticated: true,
  email: "priya@example.com",
  is_active: true,
  is_admin: admin,
  features,
  permissions: admin ? ["admin:members:read"] : [],
  capabilities: [],
  roles: admin ? ["admin"] : ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
});

/** Answered, with no pins: the sidebar's "My apps" has nothing in it. */
const NO_PINS = { preset: "new-hire", answered: "answered", pins: [], cardOrder: null, newOrder: null };

async function stub(page: Page, me: unknown) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  // One router, on purpose (`org-branding.spec.ts` says why).
  await page.route("**/api/**", (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") return json(NO_PINS);
    if (path === "/api/auth/me") return json(me);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    if (path.startsWith("/api/projects/my/")) return json({ rows: [], total: 0 });
    return json([]);
  });
}

const commandBar = (page: Page) => page.getByRole("dialog", { name: "Search or ask" });
const field = (page: Page) => commandBar(page).getByRole("combobox", { name: "Search or ask anything" });
const mod = process.platform === "darwin" ? "Meta" : "Control";

async function openBar(page: Page) {
  await page.goto("/settings/appearance");
  // The shell has the member's access once the account menu can name them.
  await expect(page.locator("[data-shell-bar]")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('aside [data-nav-section="my-apps"] a')).toHaveCount(0);
  await page.keyboard.press(`${mod}+k`);
  await expect(commandBar(page)).toBeVisible();
}

test.use({ viewport: { width: 1440, height: 900 } });

test("a member who pinned nothing types “peo”, presses Enter and lands on People", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, person(false, ["tasks", "people", "chat"]));
  await openBar(page);
  await field(page).fill("peo");
  await expect(commandBar(page).getByRole("option", { name: /Open People/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => u.pathname === "/people", { timeout: 30_000 });
});

test("an admin who pinned nothing opens an Admin app: “orga”, Enter, Organisation", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, person(true, ["tasks", "people", "chat", "approvals"]));
  await openBar(page);
  await field(page).fill("appro");
  await expect(commandBar(page).getByRole("option", { name: /Open Approvals/ })).toBeVisible();
  await field(page).fill("orga");
  await expect(commandBar(page).getByRole("option").first()).toContainText("Open Organisation");
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => u.pathname === "/settings/organization", { timeout: 30_000 });
});

test("a member without the grant finds no People, no Approvals and no Organisation", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, person(false, ["tasks", "chat"]));
  await openBar(page);
  // A held app shows first, so an empty list below proves the access, not a
  // bar that is still loading.
  await field(page).fill("chat");
  await expect(commandBar(page).getByRole("option", { name: /Open Chat/ })).toBeVisible();
  for (const [words, app] of [
    ["peo", "People"],
    ["appro", "Approvals"],
    ["orga", "Organisation"],
  ]) {
    await field(page).fill(words);
    await expect(commandBar(page).getByRole("option", { name: new RegExp(`Open ${app}`) })).toHaveCount(0);
  }
});
