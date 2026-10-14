import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The account at the sidebar's foot, with ONE menu (owner, 2026-10-14).
 *
 * The owner said: "move the account selection and the UI element that shows
 * the current account back to the bottom left where it was before. Combine
 * it with the pop-up settings … Remove the Metorite logo." Each test fails on
 * the obvious wrong build:
 *   1. The bar's right group holds no account control. Its controls never
 *      sit over the command bar: 1024, 1280 and 1440, with the bell on and
 *      off, at three densities.
 *   2. The foot is the account row: the avatar, the name and the email when
 *      open, and the avatar alone when folded, on the rail's icon column
 *      within 1px at three densities.
 *   3. Its menu holds, in order: the current account, marked, the others,
 *      Add, You, Settings, Sign out, and "Sign out of all accounts" with more
 *      than one account. A pick switches ITS slot. Escape closes it and
 *      gives the focus back.
 *   4. Sign-out fails closed: a refused switch, or a remove that fails
 *      twice, signs out of every account and says so. The cover survives
 *      Escape, Tab and a click.
 *   5. No Metorite logo in the sidebar or the bar, except the badge of a
 *      workspace with no logo, at the top left.
 *   6. The classic frame keeps its account foot, and the phone is unchanged.
 * The markup rules are `src/lib/shell/shellAccount.test.ts`.
 */

const MEMBER = {
  authenticated: true,
  email: "vjvarada@hathilabs.com",
  is_admin: false,
  features: ["tasks", "email", "projects", "people", "chat"],
  permissions: [],
  roles: ["employee"],
  organization: { id: "org1", slug: "hathi", display_name: "Hathi Labs LLP" },
};

const SESSION = {
  user: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" },
  expires: "2099-01-01T00:00:00.000Z",
};

const ONE = { enabled: true, active: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" }, others: [] };
const THREE = {
  enabled: true,
  active: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" },
  others: [
    { slot: 2, email: "vjvarada@fracktal.in", name: "Vijay Raghav Varada", organization: "Fracktal Works" },
    { slot: 0, email: "ops@hathilabs.com", name: null, organization: "Hathi Labs LLP" },
  ],
};
const OFF = { enabled: false };

const PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
const WITH_LOGO = {
  logo: { dataUri: `data:image/png;base64,${PIXEL}`, mime: "image/png", width: 600, height: 160, byteSize: 70 },
  updatedBy: "",
  updatedAt: "",
};
const NO_LOGO = { logo: null, updatedBy: "", updatedAt: "" };

/** One router, on purpose (`org-branding.spec.ts` says why). */
async function stub(
  page: Page,
  accounts: unknown = THREE,
  calls: string[] = [],
  refuse: Record<string, number> = {},
  branding: unknown = NO_LOGO,
) {
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json(accounts);
    if (path.startsWith("/api/accounts/")) {
      calls.push(`${path} ${req.postData() ?? ""}`);
      const refused = refuse[path.split("/").pop() ?? ""];
      if (refused) return route.fulfill({ status: refused, contentType: "application/json", body: "{}" });
      if (path.endsWith("/signout-all")) return json({ ok: true, emails: ["vjvarada@hathilabs.com"] });
      return json({ ok: true, email: "vjvarada@fracktal.in" });
    }
    if (path === "/api/auth/me/shell") {
      return json({ preset: null, answered: "skipped", pins: null, known: null, cardOrder: null, newOrder: null });
    }
    if (path === "/api/settings/branding") return json(branding);
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    return json([]);
  });
}

/** The shell flags, as production runs them, plus any extra keys. */
async function flags(page: Page, opts: { bar?: boolean; nav?: boolean; extra?: Record<string, string> } = {}) {
  await page.addInitScript(
    ({ bar, nav, extra }) => {
      if (bar) localStorage.setItem("cc-shell-bar", "1");
      if (nav) localStorage.setItem("cc-shell-nav", "1");
      for (const [k, v] of Object.entries(extra)) localStorage.setItem(k, v);
    },
    { bar: opts.bar ?? true, nav: opts.nav ?? true, extra: opts.extra ?? {} },
  );
}

/** The root font size each density gives, as `globals.css` computes it. */
const ROOT_PX: Record<string, string> = { "0.875": "14px", "1": "16px", "1.125": "18px" };
const rootPx = (page: Page) => page.evaluate(() => getComputedStyle(document.documentElement).fontSize);

/**
 * Set the density AFTER the page loads. ⚠️ An `addInitScript` value does not
 * hold: the appearance store writes `--ui-scale` again when it hydrates
 * (`lib/theme/store.ts`), so every "scale" of the first version measured
 * scale 1 (verifier, 2026-10-12). The poll proves the scale took.
 */
async function setScale(page: Page, scale: string) {
  await page.evaluate((v) => document.documentElement.style.setProperty("--ui-scale", v), scale);
  await expect.poll(() => rootPx(page)).toBe(ROOT_PX[scale]);
  // Let the layout settle at the new size.
  await page.waitForTimeout(150);
}

const bar = (page: Page) => page.locator("[data-shell-bar]");
const aside = (page: Page) => page.locator("aside[data-collapsed]");
const foot = (page: Page) => aside(page).locator("[data-account-foot]");
const trigger = (page: Page) => aside(page).getByRole("button", { name: /^Account and settings: / });
const menu = (page: Page) => page.getByRole("dialog", { name: "Account and settings" });
const box = async (l: ReturnType<Page["locator"]>) => (await l.boundingBox())!;
type Loc = ReturnType<Page["locator"]>;

/** The outline a focused element draws, as one comparable string. */
const ringOf = (l: Loc) =>
  l.evaluate((el) => {
    const s = getComputedStyle(el);
    return JSON.stringify({ style: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor, offset: s.outlineOffset });
  });

/**
 * The ring, once it has settled. Controls ease their outline in
 * (`tech-transition`, the control state layer), so a first frame reads a
 * value between two rings. Read it only once two reads 150ms apart agree.
 */
async function settledRing(page: Page, l: Loc) {
  let last = "";
  for (let i = 0; i < 30; i++) {
    const now = await ringOf(l);
    if (now === last) return JSON.parse(now) as Record<string, string>;
    last = now;
    await page.waitForTimeout(150);
  }
  throw new Error(`the ring never settled: ${last}`);
}

/** Focus from the keyboard, so `:focus-visible` matches. */
async function keyFocusOn(page: Page, l: Loc) {
  await l.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(l).toBeFocused();
  expect(await l.evaluate((el) => el.matches(":focus-visible"))).toBe(true);
}

/** Each group of the open menu, in DOM order. */
const groupsOf = (page: Page) => menu(page).locator("[data-account-group]").evaluateAll((els) => els.map((e) => e.getAttribute("data-account-group")));

/** The names of the menu's controls, in DOM order: each button and link. */
const controlsOf = (page: Page) =>
  menu(page)
    .locator("a, button")
    .evaluateAll((els) => els.map((e) => (e.getAttribute("aria-label") ?? e.textContent ?? "").trim()));

test.describe("the bar holds no account", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("its right group ends with the activity control, and holds no account control", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(trigger(page)).toBeVisible();
    await expect(bar(page).locator("[data-shell-account]")).toHaveCount(0);
    await expect(bar(page).getByRole("button", { name: /^Account/ })).toHaveCount(0);
    await expect(bar(page).getByText("vjvarada@hathilabs.com")).toHaveCount(0);
    const group = bar(page).locator(":scope > div").last();
    expect(await group.evaluate((el) => el.lastElementChild?.hasAttribute("data-activity-control") ?? false)).toBe(true);
  });
});

test.describe("the right group never crowds the command bar", () => {
  for (const width of [1024, 1280, 1440]) {
    for (const dock of [true, false]) {
      for (const scale of ["1", "0.875", "1.125"]) {
        test(`${width}px, bell ${dock ? "on" : "off"}, scale ${scale}`, async ({ page }) => {
          await page.setViewportSize({ width, height: 800 });
          await flags(page, { extra: dock ? { "cc-shell-dock": "1" } : {} });
          await stub(page);
          await page.goto("/settings/appearance");
          await expect(trigger(page)).toBeVisible();
          await expect(aside(page).locator("nav a").first()).toBeVisible();
          await setScale(page, scale);
          const group = bar(page).locator(":scope > div").last();
          // The bell (with the dock on) and the activity control. No account.
          await expect(group.locator(":scope > *")).toHaveCount(dock ? 2 : 1);
          const first = await box(group.locator(":scope > *").first());
          const cmd = await box(bar(page).getByRole("button", { name: /^Search or ask anything/ }));
          expect(first.x, `the first control at ${first.x} vs the command bar's end at ${cmd.x + cmd.width}`).toBeGreaterThan(cmd.x + cmd.width);
          // Each control clear of the next, and the last inside the window.
          const xs = await group.locator(":scope > *").evaluateAll((els) =>
            els.map((e) => {
              const r = e.getBoundingClientRect();
              return [r.left, r.right];
            }),
          );
          for (let i = 1; i < xs.length; i++) expect(xs[i][0]).toBeGreaterThanOrEqual(xs[i - 1][1] - 0.5);
          expect(xs[xs.length - 1][1]).toBeLessThanOrEqual(width);
          // The scale held for the whole measure.
          expect(await rootPx(page)).toBe(ROOT_PX[scale]);
        });
      }
    }
  }
});

test.describe("the sidebar foot is the account row", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("open: the avatar, the name, and the email muted under it, with the full tip", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(trigger(page)).toHaveAccessibleName("Account and settings: vjvarada@hathilabs.com");
    await expect(trigger(page)).toHaveAttribute("title", "Vijay Varada\nvjvarada@hathilabs.com\nHathi Labs LLP");
    await expect(trigger(page)).toContainText("Vijay Varada");
    const email = trigger(page).locator("[data-account-email]");
    await expect(email).toHaveText("vjvarada@hathilabs.com");
    // Muted and cut, never wrapped.
    const look = await email.evaluate((el) => {
      const s = getComputedStyle(el);
      return { overflow: s.textOverflow, wrap: s.whiteSpace };
    });
    expect(look).toEqual({ overflow: "ellipsis", wrap: "nowrap" });
    // Under the name.
    const n = await box(trigger(page).getByText("Vijay Varada", { exact: true }));
    expect((await box(email)).y).toBeGreaterThan(n.y);
    // The foot is the rail's last block, and inside the rail.
    expect(await foot(page).evaluate((el) => el.parentElement?.lastElementChild === el)).toBe(true);
    const t = await box(trigger(page));
    const a = await box(aside(page));
    expect(t.x).toBeGreaterThanOrEqual(a.x);
    expect(t.x + t.width).toBeLessThanOrEqual(a.x + a.width);
  });

  test("the foot wears the house focus ring, the same as All apps and Add apps", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await keyFocusOn(page, trigger(page));
    const house = await settledRing(page, trigger(page));
    expect(house.style).toBe("solid");
    expect(house.offset).toBe("2px");
    for (const l of [aside(page).getByRole("button", { name: "All apps" }), aside(page).locator("[data-nav-add-apps] button")]) {
      await keyFocusOn(page, l);
      expect(await settledRing(page, l)).toEqual(house);
    }
  });

  // Owner follow-up of #918 (2026-10-12): All apps and Add apps wear the
  // foot's ring in the phone drawer too.
  test("All apps and Add apps wear the foot's ring in the phone drawer", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await keyFocusOn(page, trigger(page));
    const house = await settledRing(page, trigger(page));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Menu" }).click();
    for (const l of [page.locator("button[data-nav-add-apps]"), page.locator("button[data-nav-all-apps]")]) {
      await expect(l).toBeVisible();
      await keyFocusOn(page, l);
      expect(await settledRing(page, l)).toEqual(house);
    }
  });

  for (const scale of ["1", "0.875", "1.125"]) {
    test(`folded: the avatar alone, on the rail's icon column (scale ${scale})`, async ({ page }) => {
      await flags(page, { extra: { "cc-sidebar-collapsed": "1" } });
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
      await expect(aside(page).locator("nav a").first()).toBeVisible();
      await setScale(page, scale);
      await expect(trigger(page)).toBeVisible();
      await expect(trigger(page)).not.toContainText("Vijay Varada");
      await expect(trigger(page).locator("[data-account-email]")).toHaveCount(0);
      await expect(trigger(page)).toHaveAttribute("title", "Vijay Varada\nvjvarada@hathilabs.com\nHathi Labs LLP");
      const centre = (r: { x: number; width: number }) => r.x + r.width / 2;
      const avatar = centre(await box(trigger(page).locator("span.rounded-full").first()));
      const icons = await aside(page)
        .locator("nav a svg, nav button svg")
        .evaluateAll((els) =>
          els
            .map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && r.height > 0)
            .map((r) => r.x + r.width / 2),
        );
      expect(icons.length).toBeGreaterThan(2);
      for (const c of icons) expect(Math.abs(c - avatar), `an icon at ${c} vs the avatar at ${avatar}`).toBeLessThanOrEqual(1);
      expect(await rootPx(page)).toBe(ROOT_PX[scale]);
      // The folded ring is the house ring too, the same as folded All apps.
      const allApps = aside(page).getByRole("button", { name: "All apps" });
      await keyFocusOn(page, allApps);
      const house = await settledRing(page, allApps);
      await keyFocusOn(page, trigger(page));
      expect(await settledRing(page, trigger(page))).toEqual(house);
      // The menu opens from the folded foot.
      await page.keyboard.press("Enter");
      await expect(menu(page).getByRole("link", { name: "My Profile" })).toBeVisible();
      await expect(menu(page).locator("[data-current-account]")).toContainText("vjvarada@hathilabs.com");
    });
  }
});

test.describe("the foot's one menu", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("with three accounts: current, others, Add, You, Settings, Sign out, Sign out of all, in that order", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page)).toBeVisible();
    expect(await groupsOf(page)).toEqual(["current", "accounts", "you", "settings", "sign-out"]);
    const current = menu(page).locator("[data-current-account]");
    await expect(current).toHaveAttribute("aria-current", "true");
    await expect(current).toContainText("Vijay Varada");
    await expect(current).toContainText("vjvarada@hathilabs.com");
    await expect(current).toContainText("Hathi Labs LLP");
    await expect(current).toContainText("Signed in");
    const list = menu(page).getByRole("list", { name: "Other accounts" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("Fracktal Works · vjvarada@fracktal.in");
    expect(await controlsOf(page)).toEqual([
      expect.stringMatching(/Vijay Raghav Varada\s*Fracktal Works · vjvarada@fracktal\.in$/),
      "Remove vjvarada@fracktal.in from this browser",
      expect.stringMatching(/ops@hathilabs\.com\s*Hathi Labs LLP$/),
      "Remove ops@hathilabs.com from this browser",
      "Add another account",
      "My Profile",
      "My access",
      "Appearance",
      expect.stringMatching(/^(Light|Dark) mode$/),
      "Change my layout",
      "Sign out",
      "Sign out of all accounts",
    ]);
    await expect(menu(page).getByRole("group", { name: "You" }).getByRole("link")).toHaveText(["My Profile", "My access"]);
    await expect(menu(page).getByRole("link", { name: "My access" })).toHaveAttribute("href", "/people/access");
    const settings = menu(page).getByRole("group", { name: "Settings" });
    await expect(settings.getByRole("link", { name: "Appearance" })).toBeVisible();
    await expect(settings.getByRole("button", { name: /^(Light|Dark) mode$/ })).toBeVisible();
    await expect(settings.getByRole("button", { name: "Change my layout" })).toBeVisible();
    // It opens upward, from the foot, inside the window.
    const m = await box(menu(page));
    const t = await box(trigger(page));
    expect(m.y + m.height).toBeLessThanOrEqual(t.y);
    expect(m.y).toBeGreaterThanOrEqual(0);
    expect(m.x + m.width).toBeLessThanOrEqual(1440);
  });

  test("with one account: Add, You, Settings and one Sign out", async ({ page }) => {
    await flags(page);
    await stub(page, ONE);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page).getByRole("list", { name: "Other accounts" })).toHaveCount(0);
    expect(await groupsOf(page)).toEqual(["current", "accounts", "you", "settings", "sign-out"]);
    await expect(menu(page).getByRole("button", { name: "Add another account" })).toBeVisible();
    await expect(menu(page).getByRole("button", { name: /^Sign out/ })).toHaveCount(1);
  });

  test("with the switcher flag off: the account, You, Settings and one Sign out", async ({ page }) => {
    await flags(page);
    await stub(page, OFF);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page).locator("[data-current-account]")).toContainText("vjvarada@hathilabs.com");
    expect(await groupsOf(page)).toEqual(["current", "you", "settings", "sign-out"]);
    await expect(menu(page).getByRole("button", { name: "Add another account" })).toHaveCount(0);
    await expect(menu(page).getByRole("button", { name: /^Sign out/ })).toHaveCount(1);
    await expect(menu(page).getByRole("link", { name: "My Profile" })).toBeVisible();
  });

  test("the colour mode keeps the menu open, so the change shows", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    const before = await page.evaluate(() => document.documentElement.classList.contains("light"));
    await menu(page).getByRole("button", { name: /^(Light|Dark) mode$/ }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("light"))).toBe(!before);
    await expect(menu(page)).toBeVisible();
  });

  test("Change my layout asks the first sign-in question again", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await menu(page).getByRole("button", { name: "Change my layout" }).click();
    await expect(page.getByRole("dialog", { name: "What will you do most here?" })).toBeVisible();
    await expect(menu(page)).toHaveCount(0);
  });

  test("a pick switches ITS slot, then loads /", async ({ page }) => {
    const calls: string[] = [];
    await flags(page);
    await stub(page, THREE, calls);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await menu(page).getByRole("button", { name: /Vijay Raghav Varada/ }).click();
    await page.waitForURL((u) => u.pathname === "/");
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
  });

  test("a pick the server refuses (409) stays here, takes the cover down and says so", async ({ page }) => {
    const calls: string[] = [];
    await flags(page);
    await stub(page, THREE, calls, { switch: 409 });
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await menu(page).getByRole("button", { name: /Vijay Raghav Varada/ }).click();
    await expect(menu(page)).toContainText("vjvarada@fracktal.in is no longer signed in here. Add it again.");
    await expect(page.locator("[data-switching-cover]")).toHaveCount(0);
    expect(new URL(page.url()).pathname).toBe("/settings/appearance");
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
  });

  test("a switch makes the leaving page read nothing under the new cookie (WS-58 MD-2b fix round 2)", async ({ page }) => {
    // The new document is held for 2.5 s, so the leaving page stays alive.
    // A cache clear before the reload would wake each reader of that page,
    // and each read would go out as the account the cookie now names.
    const calls: string[] = [];
    await flags(page);
    await stub(page, THREE, calls);
    let holding = false;
    const leaked: string[] = [];
    await page.route(
      (u) => u.pathname === "/",
      async (route) => {
        if (!route.request().isNavigationRequest()) return route.continue();
        holding = true;
        await new Promise((r) => setTimeout(r, 2_500));
        holding = false;
        return route.continue();
      },
    );
    page.on("request", (req) => {
      const path = new URL(req.url()).pathname;
      if (holding && ["/api/shell/needs", "/api/auth/me/shell", "/api/projects/tree"].includes(path)) leaked.push(path);
    });
    await page.goto("/projects");
    await expect(trigger(page)).toBeVisible();
    await page.waitForTimeout(1_000);
    await trigger(page).click();
    await menu(page).getByRole("button", { name: /Vijay Raghav Varada/ }).click();
    await page.waitForURL((u) => u.pathname === "/", { timeout: 15_000 });
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
    expect(leaked).toEqual([]);
  });

  test("Sign out leaves this account: a switch to the next, then a remove of that slot", async ({ page }) => {
    const calls: string[] = [];
    await flags(page);
    await stub(page, THREE, calls);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await menu(page).getByRole("button", { name: "Sign out", exact: true }).click();
    await page.waitForURL((u) => u.pathname === "/");
    expect(calls).toEqual(['/api/accounts/switch {"slot":2}', '/api/accounts/remove {"slot":2}']);
  });

  // ⚠️ Fails CLOSED (review of 2026-10-12, P1). A sign-out must not land on /
  // with the leaving account one click away.
  const NOTICE = "Couldn't sign out of that account alone, so we signed out of all accounts on this browser.";
  for (const [what, refuse, expected] of [
    [
      "a remove that fails twice",
      { remove: 500 },
      ['/api/accounts/switch {"slot":2}', '/api/accounts/remove {"slot":2}', '/api/accounts/remove {"slot":2}', "/api/accounts/signout-all "],
    ],
    ["a refused switch (409)", { switch: 409 }, ['/api/accounts/switch {"slot":2}', "/api/accounts/signout-all "]],
  ] as const) {
    test(`Sign out fails closed: ${what} signs out of every account, and says so`, async ({ page }) => {
      const calls: string[] = [];
      await flags(page);
      await stub(page, THREE, calls, refuse);
      await page.goto("/settings/appearance");
      await trigger(page).click();
      await menu(page).getByRole("button", { name: "Sign out", exact: true }).click();
      await page.waitForURL((u) => u.pathname === "/signin");
      expect(calls).toEqual(expected);
      // The one Toast channel. Addressed by structure: Base UI marks the
      // visible toast `aria-hidden`, so `getByRole` cannot see it
      // (`components/ui/Toast.tsx`), and an announcer copies the words.
      await expect(page.locator("h2[data-type='error']", { hasText: NOTICE })).toBeVisible();
      // Once: after it has been on screen, a reload shows no notice.
      await page.waitForTimeout(3_500);
      await page.reload();
      await expect(page.getByRole("button").first()).toBeVisible();
      await expect(page.locator("h2[data-type='error']", { hasText: "Couldn't sign out" })).toHaveCount(0);
    });
  }

  // Review of 2026-10-12, P2: Escape closed the menu, the cover was its
  // child, and the cover came down mid-switch. The cover is a Modal held by
  // AccountFoot: it takes the focus, keeps it, and nothing dismisses it.
  for (const [what, button] of [
    ["a switch", /Vijay Raghav Varada/],
    ["a sign-out", "Sign out"],
  ] as const) {
    test(`during ${what} held by the server, Escape, Tab and a click leave the cover up`, async ({ page }) => {
      await flags(page);
      await stub(page);
      let release = () => {};
      const held = new Promise<void>((r) => (release = r));
      // Registered last, so it wins over the stub's route.
      await page.route("**/api/accounts/switch", async (r) => {
        await held;
        await r.fulfill({ status: 409, contentType: "application/json", body: "{}" });
      });
      await page.goto("/settings/appearance");
      await trigger(page).click();
      const pick =
        typeof button === "string"
          ? menu(page).getByRole("button", { name: button, exact: true })
          : menu(page).getByRole("button", { name: button });
      await pick.click();
      const cover = page.locator("[data-switching-cover]");
      await expect(cover).toBeVisible();
      const inCover = () =>
        page.evaluate(() => {
          const popup = document.querySelector("[data-switching-cover]")?.closest("[role='dialog']");
          return !!popup && popup.contains(document.activeElement);
        });
      // The cover takes the focus.
      await expect.poll(inCover).toBe(true);
      await page.keyboard.press("Escape");
      await page.keyboard.press("Escape");
      await expect(cover).toBeVisible();
      // The menu under it stays open, so nothing unmounted. By structure:
      // the Modal marks the page under it `aria-hidden`, so `getByRole`
      // cannot see the menu.
      await expect(foot(page).locator("[role='dialog'][aria-label='Account and settings']")).toBeAttached();
      // Tab never reaches the page under it.
      for (let i = 0; i < 4; i++) {
        await page.keyboard.press("Tab");
        expect(await inCover()).toBe(true);
      }
      await page.mouse.click(700, 500);
      await expect(cover).toBeVisible();
      release();
    });
  }

  test("Escape closes it and gives the focus back; a click outside closes it", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(trigger(page)).toHaveAttribute("aria-expanded", "true");
    // Into the menu first, or the focus never left the trigger, and the
    // test could not tell a focus return from no move at all.
    await menu(page).getByRole("link", { name: "Appearance" }).focus();
    expect(await menu(page).evaluate((el) => el.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(menu(page)).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
    await trigger(page).click();
    await expect(menu(page)).toBeVisible();
    await page.locator("main").click({ position: { x: 400, y: 300 } });
    await expect(menu(page)).toHaveCount(0);
  });
});

test.describe("no Metorite logo in the shell", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const collapsed of [false, true]) {
    test(`with no logo, the comet is the top-left badge only${collapsed ? ", folded" : ""}`, async ({ page }) => {
      await flags(page, { extra: collapsed ? { "cc-sidebar-collapsed": "1" } : {} });
      await stub(page, THREE, [], {}, NO_LOGO);
      await page.goto("/settings/appearance");
      await expect(trigger(page)).toBeVisible();
      await expect(bar(page).locator("[data-shell-brand] svg[data-metorite-mark]")).toBeVisible();
      await expect(page.locator("svg[data-metorite-mark]")).toHaveCount(1);
      await expect(aside(page).locator("svg[data-metorite-mark], [data-metorite-foot]")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Metorite settings" })).toHaveCount(0);
    });
  }

  test("with a logo, no comet at all, and the menu draws none", async ({ page }) => {
    await flags(page);
    await stub(page, THREE, [], {}, WITH_LOGO);
    await page.goto("/settings/appearance");
    await expect(bar(page).locator("[data-shell-brand] img").first()).toBeAttached();
    await trigger(page).click();
    await expect(menu(page)).toBeVisible();
    await expect(page.locator("svg[data-metorite-mark]")).toHaveCount(0);
  });
});

test.describe("the other frames", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("classic (shell bar off): the account foot stays, and no new foot", async ({ page }) => {
    await flags(page, { bar: false, nav: true });
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(bar(page)).toHaveCount(0);
    await expect(aside(page).getByRole("button", { name: /^Accounts: signed in as/ })).toBeVisible();
    await expect(foot(page)).toHaveCount(0);
    await expect(page.locator("[data-shell-account]")).toHaveCount(0);
  });

  test("the shell bar alone: the account foot, with no You row and the colour mode only in Settings", async ({ page }) => {
    await flags(page, { bar: true, nav: false });
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(trigger(page)).toBeVisible();
    await expect(bar(page).locator("[data-shell-account]")).toHaveCount(0);
    await expect(aside(page).getByRole("button", { name: /^Accounts:/ })).toHaveCount(0);
    // No layout question without the shell nav, so no row for it. The rail
    // itself lists My Profile and Appearance then, so the menu repeats
    // neither, and keeps the colour mode.
    await expect(aside(page).getByRole("link", { name: "My Profile" })).toBeVisible();
    await trigger(page).click();
    expect(await groupsOf(page)).toEqual(["current", "accounts", "settings", "sign-out"]);
    await expect(menu(page).getByRole("button", { name: "Change my layout" })).toHaveCount(0);
    await expect(menu(page).getByRole("link")).toHaveCount(0);
    await expect(menu(page).getByRole("button", { name: /^(Light|Dark) mode$/ })).toBeVisible();
  });
});

test.describe("phone, unchanged", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("no shell row and no foot; the drawer keeps its account header and foot", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(page.locator("[data-shell-bar]")).toHaveCount(0);
    await expect(page.locator("[data-shell-account], [data-account-foot]")).toHaveCount(0);
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByTestId("drawer-org").getByRole("button", { name: /^Account, signed in as vjvarada@hathilabs.com/ })).toBeVisible();
    const drawerFoot = page.getByRole("region", { name: "Account and settings" });
    await expect(drawerFoot.getByRole("link", { name: "My Profile" })).toBeVisible();
    await expect(drawerFoot.getByRole("button", { name: "Sign out of all accounts" })).toBeVisible();
    await expect(page.getByRole("button", { name: /^Account and settings: / })).toHaveCount(0);
  });
});
