import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Our comet at the right end of the shell bar (owner, 2026-10-11).
 *
 * The owner first put a "Metorite" signature at the foot of the sidebar, and
 * moved it to the top right of the bar on the same day. The bar's left end
 * shows the customer's logo alone. Each test fails on the obvious wrong build:
 *   1. With a logo: the comet is the last item of the bar's right group, about
 *      20px, centred in the bar, and no control (no link, no button, no
 *      focus stop). The shell-bar frame alone signs the bar the same way.
 *   2. With no logo: the left lockup is our comet, so the right end draws
 *      none. The bar shows one comet, not two.
 *   3. The sidebar, open or folded, and the phone drawer draw no comet.
 *   4. The phone has no shell row (`ShellFrame bar={false}`), so it shows no
 *      signature at 390px or at 360px. With no logo, the drawer's head shows
 *      our comet as the workspace badge, and still no signature.
 *   5. With the bell on, the comet never pushes the bell over the command
 *      bar. It hides below `xl` (1280px) and shows from there up.
 *   6. A member with a cached logo sees the right-end comet before the
 *      branding call answers, not after it.
 * The markup rules are `src/lib/shell/shellSignature.test.ts`.
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
  // A signed-in session, so the sidebar's account row and the drawer's
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
const aside = (page: Page) => page.locator("aside[data-collapsed]");
const signature = (page: Page) => bar(page).locator("[data-shell-signature]");
const box = async (l: ReturnType<Page["locator"]>) => (await l.boundingBox())!;

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("with a logo: the comet is the bar's last item, centred, and no control", async ({ page }) => {
    await flags(page);
    await stub(page, WITH_LOGO);
    await page.goto("/settings/appearance");
    await expect(bar(page).locator("[data-shell-brand] img").first()).toBeAttached();
    const sig = signature(page);
    await expect(sig).toBeVisible();
    await expect(sig).toHaveAttribute("title", "Metorite");
    await expect(bar(page).getByRole("img", { name: "Metorite" })).toHaveCount(1);
    // The last item of the bar's last group, after the activity control.
    const place = await sig.evaluate((el) => ({
      last: el.parentElement?.lastElementChild === el,
      groupIsLast: el.parentElement?.parentElement?.lastElementChild === el.parentElement,
      afterActivity: !!el.previousElementSibling?.querySelector("svg"),
    }));
    expect(place).toEqual({ last: true, groupIsLast: true, afterActivity: true });
    // About 20px, and on the bar's centre line.
    const comet = await box(sig.locator("svg[data-metorite-mark]"));
    expect(Math.round(comet.height)).toBe(20);
    const b = await box(bar(page));
    expect(Math.abs(comet.y + comet.height / 2 - (b.y + b.height / 2))).toBeLessThanOrEqual(1);
    // At the right end of the window, inside the bar's padding.
    expect(b.x + b.width - (comet.x + comet.width)).toBeLessThanOrEqual(16);
    // A divider parts it from the control before it.
    expect(await sig.evaluate((el) => getComputedStyle(el).borderLeftWidth)).toBe("1px");
    // A signature, never a control.
    expect(
      await sig.evaluate((el) => ({
        control: !!el.closest("a, button") || !!el.querySelector("a, button, [tabindex]"),
        tabindex: el.getAttribute("tabindex"),
      })),
    ).toEqual({ control: false, tabindex: null });
    await sig.click({ force: true });
    expect(await sig.evaluate((el) => el.contains(document.activeElement))).toBe(false);
  });

  test("the shell bar alone signs its right end the same way", async ({ page }) => {
    await flags(page, false);
    await stub(page, WITH_LOGO);
    await page.goto("/settings/appearance");
    await expect(signature(page)).toBeVisible();
    expect(await signature(page).evaluate((el) => el.parentElement?.lastElementChild === el)).toBe(true);
  });

  test("with no logo: the left lockup is our comet, and the right end draws none", async ({ page }) => {
    await flags(page);
    await stub(page, NO_LOGO);
    await page.goto("/settings/appearance");
    const brand = bar(page).locator("[data-shell-brand]");
    await expect(brand.locator("a[href='/']")).toContainText("Acme");
    await expect(brand.locator("svg[data-metorite-mark]")).toBeVisible();
    await expect(signature(page)).toHaveCount(0);
    await expect(bar(page).locator("svg[data-metorite-mark]")).toHaveCount(1);
  });

  for (const folded of [false, true]) {
    test(`the sidebar draws no comet (${folded ? "folded" : "open"})`, async ({ page }) => {
      await flags(page, true, folded ? { "cc-sidebar-collapsed": "1" } : {});
      await stub(page, WITH_LOGO);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", folded ? "true" : "false");
      await expect(aside(page).getByRole("button", { name: /^Accounts:/ })).toBeVisible();
      await expect(signature(page)).toBeVisible();
      await expect(aside(page).locator("[data-metorite-mark], [data-metorite-signature]")).toHaveCount(0);
    });
  }
});

test.describe("desktop, with the bell on", () => {
  for (const [width, shown] of [[1024, false], [1100, false], [1280, true]] as const) {
    test(`the comet never crowds the bell at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await flags(page, true, { "cc-shell-dock": "1" });
      await stub(page, WITH_LOGO);
      await page.goto("/settings/appearance");
      await expect(bar(page).locator("[data-shell-brand] img").first()).toBeAttached();
      const group = bar(page).locator(":scope > div").last();
      const bell = group.locator(":scope > *").first();
      await expect(bell).toBeVisible();
      // The bell, not the activity control, is the first of the group.
      await expect(group.locator(":scope > *")).toHaveCount(3);
      if (shown) await expect(signature(page)).toBeVisible();
      else await expect(signature(page)).toBeHidden();
      const cmd = await box(bar(page).getByRole("button", { name: /^Search or ask anything/ }));
      const b = await box(bell);
      expect(b.x, `the bell at ${b.x} vs the command bar's end at ${cmd.x + cmd.width}`).toBeGreaterThan(cmd.x + cmd.width);
    });
  }
});

test.describe("first paint", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a cached logo signs the bar before the branding call answers", async ({ page }) => {
    await flags(page);
    await page.addInitScript((b) => localStorage.setItem("cc-org-branding-v1", JSON.stringify(b)), WITH_LOGO);
    await stub(page, WITH_LOGO);
    let answered = false;
    // Registered last, so it wins over the stub's branding route.
    await page.route("**/api/settings/branding", async (r) => {
      await new Promise((res) => setTimeout(res, 3_000));
      answered = true;
      await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(WITH_LOGO) });
    });
    await page.goto("/settings/appearance");
    await expect(signature(page)).toBeVisible({ timeout: 2_000 });
    expect(answered, "the comet waited on the network").toBe(false);
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
