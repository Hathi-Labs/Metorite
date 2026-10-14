import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Our comet in the shell bar (owner, 2026-10-11), and what is left of it
 * after 2026-10-12.
 *
 * ⚠️ **The right-end signature is retired** (owner, 2026-10-12), and the
 * Metorite logo left the shell on 2026-10-14. The bar's right end is the
 * assistant, the bell and the activity control, and the sidebar's foot is the
 * account (`e2e/shell-account.spec.ts`). This file keeps the cases that
 * still hold:
 *   1. With a logo, the bar draws no comet at all. The left end is the
 *      customer's logo alone, and the bar holds no account.
 *   2. With no logo, the left lockup is our comet, once.
 *   3. The phone has no shell row (`ShellFrame bar={false}`) and no sidebar,
 *      so it shows no signature at 390px or at 360px. With no logo, the
 *      drawer's head shows our comet as the workspace badge, once.
 * The markup rules are `src/lib/shell/shellAccount.test.ts`.
 */

const MEMBER = {
  authenticated: true,
  email: "member@example.com",
  is_admin: false,
  features: ["tasks", "email", "projects", "people", "chat"],
  permissions: [],
  roles: ["employee"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

const PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const WITH_LOGO = {
  logo: { dataUri: `data:image/png;base64,${PIXEL}`, mime: "image/png", width: 600, height: 160, byteSize: 70 },
  updatedBy: "",
  updatedAt: "",
};
const NO_LOGO = { logo: null, updatedBy: "", updatedAt: "" };

async function stub(page: Page, branding: unknown) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/auth/me", (r) => json(r, MEMBER));
  await page.route("**/api/settings/branding", (r) => json(r, branding));
  // A signed-in session, so the sidebar's account foot and the drawer's
  // account block draw (`account-switcher.spec.ts` stubs it the same way).
  await page.route("**/api/auth/session", (r) =>
    json(r, { user: { email: MEMBER.email, name: "Member" }, expires: "2099-01-01T00:00:00.000Z" }),
  );
  await page.route(/\/api\/accounts(\?.*)?$/, (r) => json(r, { enabled: false }));
}

/** The shell flags. Both is the full-width bar production runs. */
async function flags(page: Page, nav = true, extra: Record<string, string> = {}) {
  await page.addInitScript(
    ([withNav, more]) => {
      localStorage.setItem("cc-shell-bar", "1");
      if (withNav) localStorage.setItem("cc-shell-nav", "1");
      for (const [k, v] of Object.entries(more as Record<string, string>)) localStorage.setItem(k, v);
    },
    [nav, extra] as const,
  );
}

const bar = (page: Page) => page.locator("[data-shell-bar]");
const signature = (page: Page) => bar(page).locator("[data-shell-signature]");

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("with a logo: the bar draws no comet, and the signature is gone", async ({ page }) => {
    await flags(page);
    await stub(page, WITH_LOGO);
    await page.goto("/settings/appearance");
    await expect(bar(page).locator("[data-shell-brand] img").first()).toBeAttached();
    await expect(page.locator("aside [data-account-foot]")).toBeVisible();
    await expect(bar(page).locator("[data-shell-account]")).toHaveCount(0);
    await expect(signature(page)).toHaveCount(0);
    await expect(page.locator("svg[data-metorite-mark]")).toHaveCount(0);
  });

  test("with no logo: the left lockup is our comet, once", async ({ page }) => {
    await flags(page);
    await stub(page, NO_LOGO);
    await page.goto("/settings/appearance");
    const brand = bar(page).locator("[data-shell-brand]");
    await expect(brand.locator("a[href='/']")).toContainText("Acme");
    await expect(brand.locator("svg[data-metorite-mark]")).toBeVisible();
    await expect(signature(page)).toHaveCount(0);
    await expect(bar(page).locator("svg[data-metorite-mark]")).toHaveCount(1);
    // The badge is the one comet on the page: the foot draws none.
    await expect(page.locator("svg[data-metorite-mark]")).toHaveCount(1);
  });
});

for (const width of [390, 360]) {
  test.describe(`phone at ${width}px`, () => {
    test.use({ viewport: { width, height: 800 }, hasTouch: true, isMobile: true });

    test("no shell row, so no signature, and the drawer draws no comet", async ({ page }) => {
      await flags(page);
      await stub(page, WITH_LOGO);
      await page.goto("/settings/appearance");
      await expect(page.locator("[data-shell-bar]")).toHaveCount(0);
      await page.getByRole("button", { name: "Menu" }).click();
      await expect(page.getByRole("region", { name: "Account and settings" })).toBeAttached();
      await expect(page.locator("[data-metorite-mark], [data-metorite-signature], [data-shell-signature]")).toHaveCount(0);
    });

    test("with no logo, the drawer's head shows our comet as the badge, and no signature", async ({ page }) => {
      await flags(page);
      await stub(page, NO_LOGO);
      await page.goto("/settings/appearance");
      await page.getByRole("button", { name: "Menu" }).click();
      const head = page.getByTestId("drawer-org");
      await expect(head.locator("[data-our-mark] svg[data-metorite-mark]")).toBeVisible();
      await expect(head).toContainText("Acme");
      await expect(page.locator("[data-metorite-mark]")).toHaveCount(1);
      await expect(page.locator("[data-shell-signature], [data-metorite-signature]")).toHaveCount(0);
    });
  });
}
