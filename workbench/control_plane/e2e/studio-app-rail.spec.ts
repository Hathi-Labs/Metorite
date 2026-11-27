import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The rail of a Studio app (STU-AF2b, `ai_studio.md` §5.5).
 *
 * The app frame is behind `NEXT_PUBLIC_STUDIO_APP_FRAME`, a build-time flag. The dev server of
 * this run must start with `NEXT_PUBLIC_STUDIO_APP_FRAME=1`, and this spec skips without it.
 * The API is stubbed (`e2e/visual/harness.ts`), so no seeded package is needed.
 */
const FRAME_ON = process.env.NEXT_PUBLIC_STUDIO_APP_FRAME === "1";

const PKG = "11111111-2222-3333-4444-555555555555";

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  name: "Asha Rao",
  is_admin: true,
  features: ["studio"],
  permissions: [],
  capabilities: [],
  roles: ["owner"],
  organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
};

const PACKAGE = {
  package: {
    name: "Shop floor",
    manifest: {
      views: [
        { id: "intro", block: "detail", title: "Intro" },
        { id: "more", block: "detail", title: "More" },
      ],
      pages: [
        { id: "home", title: "Home", cells: [{ view: "intro" }] },
        { id: "extra", title: "Extra", cells: [{ view: "more" }] },
      ],
      nav: [{ title: "Work", rows: [{ page: "home", icon: "Home" }, { page: "extra", label: "More work" }] }],
    },
  },
  objects: [],
};

async function open(page: Page): Promise<void> {
  await stubApi(page, {
    "auth/me": ADMIN,
    health: { gateway: "up" },
    [`studio/packages/${PKG}`]: PACKAGE,
  }, { arrayPaths: /^(notes|chat|agent|apps|agents)\b/ });
  await page.goto(`/studio/package-preview?pkg=${PKG}`);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Shop floor", { timeout: 30_000 });
}

test.describe("the rail of a Studio app", () => {
  test.skip(!FRAME_ON, "NEXT_PUBLIC_STUDIO_APP_FRAME is not 1 in this run");

  test.describe("at 1440 px", () => {
    test.use({ viewport: { width: 1440, height: 900 } });

    test("one h1, one app bar, a rail the toggle hides and shows", async ({ page }) => {
      await open(page);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(page.locator("[data-app-bar]")).toHaveCount(1);

      const rail = page.getByRole("navigation", { name: "Pages" });
      await expect(rail).toBeVisible();
      await expect(rail.getByRole("button", { name: "Home", exact: true })).toBeVisible();
      await expect(rail.getByRole("button", { name: "More work", exact: true })).toBeVisible();

      await page.getByRole("button", { name: "Hide the pages" }).click();
      await expect(rail).toHaveCount(0);
      await page.getByRole("button", { name: "Show the pages" }).click();
      await expect(rail).toBeVisible();

      await rail.getByRole("button", { name: "More work", exact: true }).click();
      await expect(page).toHaveURL(/page=extra/);
      await expect(rail.getByRole("button", { name: "More work", exact: true })).toHaveAttribute("aria-current", "page");
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    });
  });

  test.describe("at 390 px", () => {
    test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

    test("the rail opens in the drawer and closes on a row choice", async ({ page }) => {
      await open(page);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(page.locator("[data-app-bar]")).toHaveCount(1);
      await expect(page.getByRole("navigation", { name: "Pages" })).toHaveCount(0);

      await page.getByRole("button", { name: "Show pages" }).click();
      const drawer = page.getByRole("navigation", { name: "Pages" });
      await expect(drawer).toBeVisible();
      await drawer.getByRole("button", { name: "More work", exact: true }).click();
      await expect(page).toHaveURL(/page=extra/);
      await expect(page.getByRole("navigation", { name: "Pages" })).toHaveCount(0);
    });
  });
});
