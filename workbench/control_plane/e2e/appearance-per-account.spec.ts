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
 *   3. Two tabs on two accounts in one browser (review round 2, P1). They
 *      used to move the one pointer back and forth and swap the two
 *      accounts' modes. Now each tab writes only its own scope, and a tab
 *      whose pointer another tab moved goes passive.
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

test("two tabs on two accounts keep both accounts' modes", async ({ context }) => {
  const read = (page: Page) =>
    page.evaluate(() => ({
      a: localStorage.getItem("theme:a@one.test|org-a"),
      b: localStorage.getItem("theme:b@two.test|org-b"),
      pointer: localStorage.getItem("cc-appearance-scope"),
    }));

  // Tab 1 records A as light, then B as dark.
  const tab1 = await context.newPage();
  await signedInAs(tab1, me("a@one.test", "org-a"));
  await tab1.goto("/settings/appearance");
  await expect.poll(() => pointer(tab1)).toBe("a@one.test|org-a");
  await tab1.getByRole("button", { name: "Light", exact: true }).click();
  await expect.poll(() => isLight(tab1)).toBe(true);
  await signedInAs(tab1, me("b@two.test", "org-b"));
  await tab1.goto("/settings/appearance");
  await expect.poll(() => pointer(tab1)).toBe("b@two.test|org-b");
  await expect.poll(() => isLight(tab1)).toBe(false);
  await tab1.getByRole("button", { name: "Dark", exact: true }).click();
  await expect.poll(() => read(tab1)).toEqual({ a: "light", b: "dark", pointer: "b@two.test|org-b" });

  // Tab 2 shows A. Then tab 1 loads as B again. Each load moves the pointer
  // under the other tab, and next-themes there hears the other mode.
  const tab2 = await context.newPage();
  await signedInAs(tab2, me("a@one.test", "org-a"));
  await tab2.goto("/settings/appearance");
  await expect.poll(() => pointer(tab2)).toBe("a@one.test|org-a");
  await tab1.reload();
  await expect.poll(() => pointer(tab1)).toBe("b@two.test|org-b");

  // Give a ping-pong the time it used to need, then read the record.
  await tab1.waitForTimeout(2500);
  expect(await read(tab1)).toEqual({ a: "light", b: "dark", pointer: "b@two.test|org-b" });
  await tab2.close();
  await tab1.close();
});
