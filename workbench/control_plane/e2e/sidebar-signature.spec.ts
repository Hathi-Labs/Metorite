import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The Metorite signature at the foot of the sidebar (owner, 2026-10-11).
 *
 * The owner moved our own mark out of the shell bar. The bar shows the
 * customer's logo alone, and the foot of the left sidebar and of the phone
 * drawer shows a quiet "Metorite" signature. Each test fails on the obvious
 * wrong build:
 *   1. Open sidebar: the signature shows, just over the account row, and it
 *      is no control (no link, no button, no focus stop).
 *   2. Folded rail: the comet sits alone, centred within 1px of the rail's
 *      icons and of the bar's fold control, at scales 1, 0.875 and 1.125.
 *   3. The shell bar holds no "powered by" text, with a logo or without one.
 *   4. Phone: the drawer shows the signature, over the account block.
 * The markup rules are `src/components/metoriteMark.test.ts`.
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

async function stub(page: Page, branding: unknown = NO_LOGO) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/auth/me", (r) => json(r, MEMBER));
  // A signed-in session, so the account row and the drawer's account block
  // draw (`account-switcher.spec.ts` stubs it the same way).
  await page.route("**/api/auth/session", (r) =>
    json(r, { user: { email: MEMBER.email, name: "Member" }, expires: "2099-01-01T00:00:00.000Z" }),
  );
  await page.route(/\/api\/accounts(\?.*)?$/, (r) => json(r, { enabled: false }));
  await page.route("**/api/settings/branding", (r) => json(r, branding));
}

/** Both shell flags: the full-width bar and the shell nav, as production runs. */
async function fullFrame(page: Page, extra: Record<string, string> = {}) {
  await page.addInitScript((more) => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    for (const [k, v] of Object.entries(more)) localStorage.setItem(k, v);
  }, extra);
}

const aside = (page: Page) => page.locator("aside[data-collapsed]");
const bar = (page: Page) => page.locator("[data-shell-bar]");
const box = async (l: ReturnType<Page["locator"]>) => (await l.boundingBox())!;

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("open: the signature sits just over the account row, and is no control", async ({ page }) => {
    await fullFrame(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(aside(page)).toHaveAttribute("data-collapsed", "false");
    const sig = aside(page).locator('[data-metorite-signature="full"]');
    await expect(sig).toBeVisible();
    await expect(sig).toHaveText("Metorite");
    await expect(sig.locator("svg[data-metorite-mark]")).toBeVisible();
    // A signature, never a control.
    await expect(sig.locator("a, button, [tabindex]")).toHaveCount(0);
    expect(await sig.evaluate((e) => e.closest("a, button") === null)).toBe(true);
    // Just over the account row, and nothing between them.
    const account = aside(page).getByRole("button", { name: /^Accounts:/ });
    await expect(account).toBeVisible();
    const s = await box(sig);
    const a = await box(account);
    expect(s.y + s.height).toBeLessThanOrEqual(a.y);
    expect(a.y - (s.y + s.height)).toBeLessThan(24);
    // Inside the rail, at its foot: below every nav item.
    const nav = await box(aside(page).locator("nav"));
    expect(s.y).toBeGreaterThanOrEqual(nav.y + nav.height - 1);
  });

  test("flags off: the classic sidebar shows the signature at its foot too", async ({ page }) => {
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(aside(page).locator('[data-metorite-signature="full"]')).toBeVisible();
  });

  for (const scale of ["1", "0.875", "1.125"]) {
    test(`folded: the comet is centred in the rail's icon column (scale ${scale})`, async ({ page }) => {
      await fullFrame(page, { "cc-sidebar-collapsed": "1" });
      await page.addInitScript((s) => document.documentElement.style.setProperty("--ui-scale", s), scale);
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
      const sig = aside(page).locator('[data-metorite-signature="rail"]');
      await expect(sig).toBeVisible();
      await expect(sig).toHaveAttribute("title", "Metorite");
      // The comet alone: no word beside it.
      expect((await sig.textContent())?.trim()).toBe("");
      const comet = await box(sig.locator("svg[data-metorite-mark]"));
      const cometCentre = comet.x + comet.width / 2;
      const fold = await box(bar(page).locator("[data-shell-brand]").getByRole("button", { name: "Expand sidebar" }));
      const foldCentre = fold.x + fold.width / 2;
      expect(Math.abs(cometCentre - foldCentre), `the comet at ${cometCentre} vs the control at ${foldCentre}`).toBeLessThanOrEqual(1);
      const centres = await aside(page)
        .locator("nav a svg, nav button svg")
        .evaluateAll((els) =>
          els
            .map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && r.height > 0)
            .map((r) => r.x + r.width / 2),
        );
      expect(centres.length).toBeGreaterThan(2);
      for (const c of centres) expect(Math.abs(c - cometCentre), `an icon at ${c} vs the comet at ${cometCentre}`).toBeLessThanOrEqual(1);
    });
  }

  for (const [name, branding] of [
    ["with a logo", WITH_LOGO],
    ["without a logo", NO_LOGO],
  ] as const) {
    test(`the shell bar holds no "powered by" text, ${name}`, async ({ page }) => {
      await fullFrame(page);
      await stub(page, branding);
      await page.goto("/settings/appearance");
      const brand = bar(page).locator("[data-shell-brand]");
      if (branding.logo) await expect(brand.locator("img").first()).toBeAttached();
      else await expect(brand.locator("a[href='/']")).toContainText("Acme");
      expect(await bar(page).textContent()).not.toMatch(/powered by/i);
      // The signature still shows at the foot, logo or not: one product mark
      // in one fixed place.
      await expect(aside(page).locator('[data-metorite-signature="full"]')).toBeVisible();
    });
  }
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the drawer shows the signature over the account block", async ({ page }) => {
    await fullFrame(page);
    await stub(page, WITH_LOGO);
    await page.goto("/settings/appearance");
    await page.getByRole("button", { name: "Menu" }).click();
    const sig = page.locator('[data-metorite-signature="full"]');
    await sig.scrollIntoViewIfNeeded();
    await expect(sig).toBeVisible();
    await expect(sig).toHaveText("Metorite");
    await expect(sig.locator("a, button")).toHaveCount(0);
    const account = page.getByRole("region", { name: "Account and settings" });
    await expect(account).toBeAttached();
    const s = await box(sig);
    const a = await box(account);
    expect(s.y + s.height).toBeLessThanOrEqual(a.y);
    // The drawer's head shows the customer's logo with nothing of ours.
    expect(await page.getByTestId("drawer-org").textContent()).not.toMatch(/powered by/i);
  });
});
