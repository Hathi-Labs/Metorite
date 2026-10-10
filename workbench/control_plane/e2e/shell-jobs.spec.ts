import { expect, test, type Page, type Route } from "@playwright/test";
import { SAFE_EMPTY } from "./visual/harness";

/**
 * NS-2 slice 2, the three jobs, in a browser (`navigation_shell.md` §5.3).
 *
 * A job is a link, `?do=<id>`, and the app opens its form on arrival
 * (`lib/shell/doJob.tsx`). Each case is written so the obvious wrong build
 * fails it:
 *   1. Phone, Projects already cached in the tab: "New space" shows its draft
 *      row. ⚠️ A child's effect runs before its parent's, and the page's own
 *      reset closed the tree sheet in the same commit (verifier, 2026-10-09).
 *   2. Chat with a saved conversation: a plain visit restores it, and the job
 *      opens the new-session picker.
 *   3. An admin's "invite" link opens the invite form. A member never gets it.
 */

const member = (admin: boolean) => ({
  authenticated: true,
  email: "member@example.com",
  is_admin: admin,
  features: ["tasks", "projects", "chat", "people"],
  permissions: admin ? ["admin:members:read"] : [],
  roles: admin ? ["admin"] : ["employee"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
});

async function stub(page: Page, admin = false) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/**", (r) => json(r, SAFE_EMPTY));
  await page.route("**/api/auth/me", (r) => json(r, member(admin)));
  await page.route(/\/api\/admin\/(members|roles|members\/requests)(\?.*)?$/, (r) => json(r, []));
  await page.route(/\/api\/agent\/list(\?.*)?$/, (r) =>
    json(r, [{ name: "task-manager", description: "My Tasks", tags: ["tasks"], status: "live", agent_runtime: "maf" }]),
  );
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
}

/** A client-side visit, so the page's cache from an earlier visit is kept. */
const push = (page: Page, url: string) =>
  page.evaluate((u) => (window as unknown as { next: { router: { push(s: string): void } } }).next.router.push(u), url);

test("phone: New space shows its draft row when Projects is already cached", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await stub(page);
  await page.goto("/projects");
  await page.waitForTimeout(4000);
  await push(page, "/tasks");
  await page.waitForURL("**/tasks");
  await push(page, "/projects?do=new-project");
  await expect(page.getByRole("textbox", { name: "New space" })).toBeVisible({ timeout: 15_000 });
  await expect(page).toHaveURL(/\/projects$/);
});

test("chat: a visit restores the conversation, and New chat opens the picker", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page);
  const picker = page.getByText("Choose an agent to chat with");
  await page.goto("/chat");
  await expect(picker).toBeVisible({ timeout: 15_000 });
  await page.getByText("Metorite", { exact: true }).last().click();
  await expect(picker).toBeHidden();
  await page.goto("/chat");
  await page.waitForTimeout(3000);
  await expect(picker).toBeHidden();
  await page.goto("/chat?do=new-chat");
  await expect(picker).toBeVisible({ timeout: 15_000 });
});

test("an admin's invite link opens the invite form", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, true);
  await page.goto("/settings/organization?do=invite");
  await expect(page.getByText("Work email", { exact: true })).toBeVisible({ timeout: 15_000 });
});

// Organisation moved from the account menu to the sidebar (owner,
// 2026-10-09). The job reads the held panes, not the door, so it must still
// reach the form from the command bar.
test("an admin's “invite” in the command bar opens the invite form", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await stub(page, true);
  await page.goto("/tasks");
  await page.locator("[data-shell-bar]").getByRole("button", { name: /Search or ask anything/ }).click();
  const bar = page.getByRole("dialog", { name: "Search or ask" });
  await bar.getByRole("combobox", { name: "Search or ask anything" }).fill("invite");
  await expect(bar.getByRole("option").first()).toContainText("Invite a member");
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => u.pathname === "/settings/organization");
  await expect(page.getByText("Work email", { exact: true })).toBeVisible({ timeout: 15_000 });
});

test("a member's invite link opens nothing", async ({ page }) => {
  test.setTimeout(120_000);
  await stub(page, false);
  await page.goto("/settings/organization?do=invite");
  await page.waitForTimeout(4000);
  await expect(page.getByText("Work email", { exact: true })).toHaveCount(0);
});
