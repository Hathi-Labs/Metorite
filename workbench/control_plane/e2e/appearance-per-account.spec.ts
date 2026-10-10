import { expect, test, type Page } from "@playwright/test";

/**
 * Appearance is per account on this device (owner bug, 2026-10-11): "setting
 * the appearance in one organization carries over to others."
 *
 * The unit fences live in `src/lib/theme/scope.test.ts`. This spec reads the
 * computed style in a real browser, so it sees the paint a member sees.
 *
 *   1. An accent and a mode set in account A do not paint account B, and
 *      they come back for A. The B and A loads get no `/api/auth/me` answer,
 *      so only the boot script and the pointer paint them.
 *   2. A pointer that names the wrong account corrects itself once the
 *      session answers.
 */

const ROSE = "hsl(347 77% 50%)";
const SCOPE_KEY = "cc-appearance-scope";

function me(email: string, org: string) {
  return {
    authenticated: true,
    email,
    is_admin: false,
    features: ["tasks", "email", "chat"],
    permissions: [],
    roles: ["employee"],
    organization: { id: org, slug: org, display_name: org },
  };
}

/** Answer `/api/auth/me` as `who`, or abort it, so no account binds. */
async function signedInAs(page: Page, who: ReturnType<typeof me> | null) {
  await page.unroute("**/api/auth/me");
  await page.route("**/api/auth/me", (r) =>
    who
      ? r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(who) })
      : r.abort(),
  );
}

const primary = (page: Page) =>
  page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--primary").trim());
const isLight = (page: Page) => page.evaluate(() => document.documentElement.classList.contains("light"));
const pointer = (page: Page) => page.evaluate((k) => localStorage.getItem(k), SCOPE_KEY);

test("an accent and a mode set in account A do not paint account B", async ({ page }) => {
  await signedInAs(page, me("a@one.test", "org-a"));
  await page.goto("/settings/appearance");
  await expect.poll(() => pointer(page)).toBe("a@one.test|org-a");
  // The look of an account that chose nothing, read here rather than from
  // `themes.ts`, so this spec tests the scope and not the palette.
  const plain = await primary(page);
  expect(plain).not.toBe(ROSE);
  await page.getByRole("button", { name: "Rose", exact: true }).click();
  await page.getByRole("button", { name: "Light", exact: true }).click();
  await expect.poll(() => primary(page)).toBe(ROSE);
  await expect.poll(() => isLight(page)).toBe(true);

  // What `switchTo` does before its reload: point at the target.
  await page.evaluate((k) => localStorage.setItem(k, "b@two.test|org-b"), SCOPE_KEY);
  await signedInAs(page, null);
  await page.goto("/settings/appearance");
  expect(await primary(page)).toBe(plain);
  expect(await isLight(page)).toBe(false);

  await page.evaluate((k) => localStorage.setItem(k, "a@one.test|org-a"), SCOPE_KEY);
  await page.goto("/settings/appearance");
  expect(await primary(page)).toBe(ROSE);
  expect(await isLight(page)).toBe(true);
});

test("a pointer that names the wrong account corrects itself", async ({ page }) => {
  await signedInAs(page, me("a@one.test", "org-a"));
  await page.goto("/settings/appearance");
  await expect.poll(() => pointer(page)).toBe("a@one.test|org-a");
  const plain = await primary(page);
  await page.getByRole("button", { name: "Rose", exact: true }).click();
  await expect.poll(() => primary(page)).toBe(ROSE);

  // A sign-in as B that no switch announced: the pointer still names A.
  await signedInAs(page, me("b@two.test", "org-b"));
  await page.goto("/settings/appearance");
  await expect.poll(() => pointer(page)).toBe("b@two.test|org-b");
  await expect.poll(() => primary(page)).toBe(plain);
});
