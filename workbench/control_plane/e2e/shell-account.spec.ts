import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The account at the shell bar's top right, and the Metorite menu at the
 * sidebar's foot (owner, 2026-10-12).
 *
 * The owner said: "place the account selector in the top-right corner …
 * Move the previous account selector from the bottom-left sidebar to the
 * Metorite logo and text." Each test fails on the obvious wrong build:
 *   1. The widget is the last item of the bar's right group. It shows the
 *      avatar and the email at 1440, and the avatar alone at 1280 and below.
 *   2. Its menu marks the current account, lists the others, and offers Add
 *      and sign-out. A pick switches ITS slot. Escape closes it and gives the
 *      focus back. With the switcher off, it is the account and Sign out.
 *   3. The bell, or the activity control, never sits over the command bar:
 *      1024, 1280 and 1440, with the bell on and off, at three densities.
 *   4. The sidebar foot is our comet and "Metorite" when open, and the comet
 *      alone when folded, on the rail's icon column within 1px.
 *   5. Its menu holds My Profile, My access, Appearance, the colour mode and
 *      Change my layout, and no account act. The folded rail has no
 *      account foot.
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

/** One router, on purpose (`org-branding.spec.ts` says why). */
async function stub(page: Page, accounts: unknown = THREE, calls: string[] = [], refuse: Record<string, number> = {}) {
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
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
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
const widget = (page: Page) => bar(page).locator("[data-shell-account]");
const trigger = (page: Page) => bar(page).getByRole("button", { name: /^Account: / });
const menu = (page: Page) => page.getByRole("dialog", { name: "Account" });
const aside = (page: Page) => page.locator("aside[data-collapsed]");
const foot = (page: Page) => aside(page).locator("[data-metorite-foot]");
const metorite = (page: Page) => aside(page).getByRole("button", { name: "Metorite settings" });
const metoriteMenu = (page: Page) => page.getByRole("dialog", { name: "Metorite" });
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

test.describe("the top-right account widget", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("is the last item of the bar's right group, with the avatar and the email at 1440", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(trigger(page)).toHaveAccessibleName("Account: vjvarada@hathilabs.com");
    const place = await widget(page).evaluate((el) => ({
      last: el.parentElement?.lastElementChild === el,
      groupIsLast: el.parentElement?.parentElement?.lastElementChild === el.parentElement,
      afterActivity: el.previousElementSibling?.hasAttribute("data-activity-control") ?? false,
    }));
    expect(place).toEqual({ last: true, groupIsLast: true, afterActivity: true });
    await expect(widget(page).locator("[data-account-email]")).toBeVisible();
    await expect(widget(page).locator("[data-account-email]")).toHaveText("vjvarada@hathilabs.com");
    await expect(trigger(page)).toHaveAttribute("title", "Vijay Varada\nvjvarada@hathilabs.com\nHathi Labs LLP");
    // The height of the bar's other controls, and centred in the bar.
    const t = await box(trigger(page));
    const a = await box(bar(page).locator("[data-activity-control]"));
    expect(Math.abs(t.height - a.height), `trigger ${t.height} vs activity ${a.height}`).toBeLessThanOrEqual(2);
    const b = await box(bar(page));
    expect(Math.abs(t.y + t.height / 2 - (b.y + b.height / 2))).toBeLessThanOrEqual(1);
    expect(b.x + b.width - (t.x + t.width)).toBeLessThanOrEqual(16);
    // The bar's focus ring, from the keyboard.
    await trigger(page).focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(trigger(page)).toBeFocused();
    const ring = await trigger(page).evaluate((el) => [getComputedStyle(el).outlineStyle, parseFloat(getComputedStyle(el).outlineWidth)]);
    expect(ring[0]).toBe("solid");
    expect(ring[1]).toBeGreaterThan(0);
  });

  for (const width of [1280, 1024]) {
    test(`shows the avatar alone at ${width}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await flags(page);
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(trigger(page)).toBeVisible();
      await expect(widget(page).locator("[data-account-email]")).toBeHidden();
      const t = await box(trigger(page));
      expect(t.width).toBeLessThanOrEqual(t.height + 1);
    });
  }

  test("the menu marks the current account, lists the others, then Add and sign-out", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page)).toBeVisible();
    const current = menu(page).locator("[data-current-account]");
    await expect(current).toHaveAttribute("aria-current", "true");
    await expect(current).toContainText("Vijay Varada");
    await expect(current).toContainText("vjvarada@hathilabs.com");
    await expect(current).toContainText("Hathi Labs LLP");
    await expect(current).toContainText("Signed in");
    const list = menu(page).getByRole("list", { name: "Other accounts" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("Fracktal Works · vjvarada@fracktal.in");
    await expect(menu(page).getByRole("button", { name: "Add another account" })).toBeVisible();
    await expect(menu(page).getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
    await expect(menu(page).getByRole("button", { name: "Sign out of all accounts" })).toBeVisible();
    // Account acts only.
    await expect(menu(page).getByRole("link")).toHaveCount(0);
    await expect(menu(page)).not.toContainText("Change my layout");
    // Inside the window, under the trigger, at the right edge.
    const m = await box(menu(page));
    expect(m.x + m.width).toBeLessThanOrEqual(1440);
    expect(m.y).toBeGreaterThanOrEqual((await box(trigger(page))).y);
  });

  test("with one account: Add and one Sign out", async ({ page }) => {
    await flags(page);
    await stub(page, ONE);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page).getByRole("list", { name: "Other accounts" })).toHaveCount(0);
    await expect(menu(page).getByRole("button", { name: "Add another account" })).toBeVisible();
    await expect(menu(page).getByRole("button", { name: /^Sign out/ })).toHaveCount(1);
  });

  test("with the switcher flag off: the account and Sign out, nothing else", async ({ page }) => {
    await flags(page);
    await stub(page, OFF);
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await expect(menu(page).locator("[data-current-account]")).toContainText("vjvarada@hathilabs.com");
    await expect(menu(page).getByRole("button")).toHaveCount(1);
    await expect(menu(page).getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
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

  // ⚠️ Fails CLOSED (review of 2026-10-12, P1). A remove that fails twice
  // must not land on / with the leaving account one click away.
  test("Sign out fails closed: a remove that fails twice signs out of every account, and says so", async ({ page }) => {
    const calls: string[] = [];
    await flags(page);
    await stub(page, THREE, calls, { remove: 500 });
    await page.goto("/settings/appearance");
    await trigger(page).click();
    await menu(page).getByRole("button", { name: "Sign out", exact: true }).click();
    await page.waitForURL((u) => u.pathname === "/signin");
    expect(calls).toEqual([
      '/api/accounts/switch {"slot":2}',
      '/api/accounts/remove {"slot":2}',
      '/api/accounts/remove {"slot":2}',
      "/api/accounts/signout-all ",
    ]);
    // The one Toast channel. Addressed by structure: Base UI marks the
    // visible toast `aria-hidden`, so `getByRole` cannot see it
    // (`components/ui/Toast.tsx`), and an announcer copies the words.
    const notice = page.locator("h2[data-type='error']", {
      hasText: "Couldn't sign out of that account alone, so we signed out of all accounts on this browser.",
    });
    await expect(notice).toBeVisible();
    // Once: after it has been on screen, a reload shows no notice.
    await page.waitForTimeout(3_500);
    await page.reload();
    await expect(page.getByRole("button").first()).toBeVisible();
    await expect(page.locator("h2[data-type='error']", { hasText: "Couldn't sign out" })).toHaveCount(0);
  });

  // Review of 2026-10-12, P2: Escape closed the menu, the cover was its
  // child, and the cover came down mid-switch. The cover is a Modal held by
  // BarAccount now: it takes the focus, keeps it, and nothing dismisses it.
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
      const pick = typeof button === "string" ? menu(page).getByRole("button", { name: button, exact: true }) : menu(page).getByRole("button", { name: button });
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
      await expect(widget(page).locator("[role='dialog'][aria-label='Account']")).toBeAttached();
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
    await page.keyboard.press("Tab");
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

test.describe("the widget never crowds the bell", () => {
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
          await expect(group.locator(":scope > *")).toHaveCount(dock ? 3 : 2);
          const first = await box(group.locator(":scope > *").first());
          const cmd = await box(bar(page).getByRole("button", { name: /^Search or ask anything/ }));
          expect(first.x, `the first control at ${first.x} vs the command bar's end at ${cmd.x + cmd.width}`).toBeGreaterThan(cmd.x + cmd.width);
          // Each control clear of the next, and the widget inside the window.
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

test.describe("the sidebar foot is the Metorite menu", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("open: the comet and the word Metorite, and no account foot", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(metorite(page)).toBeVisible();
    // Open, the word is in view, so there is no tip to repeat it.
    await expect(metorite(page)).not.toHaveAttribute("title");
    await expect(metorite(page)).toContainText("Metorite");
    const comet = await box(metorite(page).locator("svg[data-metorite-mark]"));
    expect(Math.round(comet.height)).toBe(20);
    // The foot is the rail's last block.
    expect(await foot(page).evaluate((el) => el.parentElement?.lastElementChild === el)).toBe(true);
    await expect(aside(page).getByRole("button", { name: /^Accounts:/ })).toHaveCount(0);
    await expect(aside(page).getByText("vjvarada@hathilabs.com")).toHaveCount(0);
    await expect(aside(page).getByRole("button", { name: /Switch to (light|dark) mode/ })).toHaveCount(0);
  });

  test("the foot wears the house focus ring, the same as the bar's Button", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    const settled = (l: Loc) => settledRing(page, l);
    const keyFocus = (l: Loc) => keyFocusOn(page, l);
    await keyFocus(trigger(page));
    const house = await settled(trigger(page));
    expect(house.style).toBe("solid");
    expect(house.offset).toBe("2px");
    await keyFocus(metorite(page));
    expect(await settled(metorite(page))).toEqual(house);
    // The word in the rows' type: the same size as "All apps".
    const size = (l: ReturnType<Page["locator"]>) => l.evaluate((el) => getComputedStyle(el).fontSize);
    expect(await size(metorite(page).getByText("Metorite"))).toBe(await size(aside(page).getByText("All apps")));
  });

  test("its menu holds You and Settings, and no account act", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await metorite(page).click();
    const m = metoriteMenu(page);
    await expect(m).toBeVisible();
    await expect(m.getByRole("group", { name: "You" }).getByRole("link")).toHaveText(["My Profile", "My access"]);
    const settings = m.getByRole("group", { name: "Settings" });
    await expect(settings.getByRole("link", { name: "Appearance" })).toBeVisible();
    await expect(settings.getByRole("button", { name: /^(Light|Dark) mode$/ })).toBeVisible();
    await expect(settings.getByRole("button", { name: "Change my layout" })).toBeVisible();
    await expect(m.getByRole("link", { name: "My access" })).toHaveAttribute("href", "/people/access");
    // No account act.
    for (const name of [/Sign out/, /Add another account/, /Switch/, /Remove/]) {
      await expect(m.getByRole("button", { name })).toHaveCount(0);
    }
    await expect(m).not.toContainText("vjvarada@hathilabs.com");
    // It opens upward, from the foot.
    const mb = await box(m);
    const fb = await box(metorite(page));
    expect(mb.y + mb.height).toBeLessThanOrEqual(fb.y);
    // The colour mode keeps the menu open, so the change shows.
    const before = await page.evaluate(() => document.documentElement.classList.contains("light"));
    await settings.getByRole("button", { name: /^(Light|Dark) mode$/ }).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("light"))).toBe(!before);
    await expect(m).toBeVisible();
    // Escape closes it and gives the focus back, from inside the menu.
    await settings.getByRole("link", { name: "Appearance" }).focus();
    await page.keyboard.press("Escape");
    await expect(m).toHaveCount(0);
    await expect(metorite(page)).toBeFocused();
  });

  // Owner follow-up of #918 (2026-10-12): "All apps" and "Add apps" wear
  // the same ring as the Metorite foot, in the open rail, in the folded rail
  // and in the phone drawer. Each button gets `cc-link`. Without it, the
  // button shows the browser's own ring, and this fails.
  test("All apps and Add apps wear the foot's focus ring, open and in the phone drawer", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await keyFocusOn(page, metorite(page));
    const house = await settledRing(page, metorite(page));
    expect(house.style).toBe("solid");
    for (const l of [aside(page).getByRole("button", { name: "All apps" }), aside(page).locator("[data-nav-add-apps] button")]) {
      await keyFocusOn(page, l);
      expect(await settledRing(page, l)).toEqual(house);
    }
    // The phone drawer, at the same page: the frame follows the width.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Menu" }).click();
    for (const l of [page.locator("button[data-nav-add-apps]"), page.locator("button[data-nav-all-apps]")]) {
      await expect(l).toBeVisible();
      await keyFocusOn(page, l);
      expect(await settledRing(page, l)).toEqual(house);
    }
  });

  test("folded: All apps and Add apps wear the foot's focus ring", async ({ page }) => {
    await flags(page, { extra: { "cc-sidebar-collapsed": "1" } });
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
    await keyFocusOn(page, metorite(page));
    const house = await settledRing(page, metorite(page));
    for (const l of [aside(page).getByRole("button", { name: "All apps" }), aside(page).locator("[data-nav-add-apps] button")]) {
      await keyFocusOn(page, l);
      expect(await settledRing(page, l)).toEqual(house);
    }
  });

  test("Change my layout asks the first sign-in question again", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await metorite(page).click();
    await metoriteMenu(page).getByRole("button", { name: "Change my layout" }).click();
    await expect(page.getByRole("dialog", { name: "What will you do most here?" })).toBeVisible();
    await expect(metoriteMenu(page)).toHaveCount(0);
  });

  for (const scale of ["1", "0.875", "1.125"]) {
    test(`folded: the comet alone, on the rail's icon column (scale ${scale})`, async ({ page }) => {
      await flags(page, { extra: { "cc-sidebar-collapsed": "1" } });
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
      await expect(aside(page).locator("nav a").first()).toBeVisible();
      await setScale(page, scale);
      await expect(metorite(page)).toBeVisible();
      await expect(metorite(page)).toHaveText("");
      await expect(metorite(page)).toHaveAttribute("title", "Metorite");
      const centre = (r: { x: number; width: number }) => r.x + r.width / 2;
      const comet = centre(await box(metorite(page).locator("svg[data-metorite-mark]")));
      const icons = await aside(page)
        .locator("nav a svg, nav button svg")
        .evaluateAll((els) =>
          els
            .map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && r.height > 0)
            .map((r) => r.x + r.width / 2),
        );
      expect(icons.length).toBeGreaterThan(2);
      for (const c of icons) expect(Math.abs(c - comet), `an icon at ${c} vs the comet at ${comet}`).toBeLessThanOrEqual(1);
      expect(await rootPx(page)).toBe(ROOT_PX[scale]);
      // No account foot in the folded rail.
      await expect(aside(page).getByRole("button", { name: /^Accounts:/ })).toHaveCount(0);
      await expect(aside(page).locator("[data-current-account]")).toHaveCount(0);
      // The menu opens from the folded foot too.
      await metorite(page).focus();
      await page.keyboard.press("Enter");
      await expect(metoriteMenu(page).getByRole("link", { name: "My Profile" })).toBeVisible();
    });
  }
});

test.describe("the other frames", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("classic (shell bar off): the account foot stays, and no Metorite foot", async ({ page }) => {
    await flags(page, { bar: false, nav: true });
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(bar(page)).toHaveCount(0);
    await expect(aside(page).getByRole("button", { name: /^Accounts: signed in as/ })).toBeVisible();
    await expect(foot(page)).toHaveCount(0);
    await expect(page.locator("[data-shell-account]")).toHaveCount(0);
  });

  test("the shell bar alone: the widget at the bar, the Metorite foot in the rail", async ({ page }) => {
    await flags(page, { bar: true, nav: false });
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(trigger(page)).toBeVisible();
    await expect(metorite(page)).toBeVisible();
    await expect(aside(page).getByRole("button", { name: /^Accounts:/ })).toHaveCount(0);
    // No layout question without the shell nav, so no row for it. The rail
    // itself lists My Profile and Appearance then, so the menu repeats
    // neither, and keeps the colour mode.
    await expect(aside(page).getByRole("link", { name: "My Profile" })).toBeVisible();
    await metorite(page).click();
    await expect(metoriteMenu(page).getByRole("button", { name: "Change my layout" })).toHaveCount(0);
    await expect(metoriteMenu(page).getByRole("link")).toHaveCount(0);
    await expect(metoriteMenu(page).getByRole("button", { name: /^(Light|Dark) mode$/ })).toBeVisible();
  });
});

test.describe("phone, unchanged", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("no shell row and no widget; the drawer keeps its account header and foot", async ({ page }) => {
    await flags(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(page.locator("[data-shell-bar]")).toHaveCount(0);
    await expect(page.locator("[data-shell-account], [data-metorite-foot]")).toHaveCount(0);
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(page.getByTestId("drawer-org").getByRole("button", { name: /^Account, signed in as vjvarada@hathilabs.com/ })).toBeVisible();
    const drawerFoot = page.getByRole("region", { name: "Account and settings" });
    await expect(drawerFoot.getByRole("link", { name: "My Profile" })).toBeVisible();
    await expect(drawerFoot.getByRole("button", { name: "Sign out of all accounts" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Metorite settings" })).toHaveCount(0);
  });
});
