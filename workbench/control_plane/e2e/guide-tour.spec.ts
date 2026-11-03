import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The guide runner in a browser (WS-OB GD-4, `assistant_guide.md` §3, §4.3, §9).
 *
 *   a. Desktop. `/?guide=home.tour` rings the app list. The focus has not
 *      moved, Tab reaches the ringed element, and the address loses the
 *      parameter. Next goes to stop 2. A reload resumes at stop 2. Escape
 *      removes the ring in the same frame.
 *   b. 390 px. All five stops, each with a visible ring inside the window,
 *      and the strip shows the step line. End ends it.
 *   c. A mount with no `?guide=` and no stored mark starts nothing (F4).
 *
 * No gateway runs here, so the session, the member and the shell layout are
 * answered in memory, as `shell-presets.spec.ts` does.
 */

const SESSION = {
  user: { email: "priya@example.com", name: "Priya Shah" },
  expires: "2099-01-01T00:00:00.000Z",
};

const MEMBER = {
  authenticated: true,
  email: "priya@example.com",
  is_active: true,
  is_admin: false,
  features: ["tasks", "projects", "email", "chat", "people", "approvals"],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

// GD-5a: `guide` is GUIDE_ORGS for the org. Off, the runner starts nothing
// (`e2e/guide-first-run.spec.ts` holds that side).
const ANSWERED = { preset: "engineer", answered: "answered", pins: null, known: null, cardOrder: null, newOrder: null, guide: true };

async function stub(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    localStorage.setItem("cc-shell-chat", "1");
    localStorage.setItem("cc-shell-chat-first", "1");
    localStorage.setItem("cc-my-day", "1");
    localStorage.removeItem("cc-sidebar-collapsed");
  });
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me/shell") return json(ANSWERED);
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json({ enabled: false });
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    if (path.startsWith("/api/projects/my/")) return json({ rows: [], total: 0 });
    return json([]);
  });
}

const ring = (page: Page) => page.locator("[data-guide-ring]");
const dim = (page: Page) => page.locator("[data-guide-dim]");
const holes = (page: Page) =>
  page.locator("[data-guide-dim] [data-guide-hole]").evaluateAll((els) =>
    els.map((e) => ({ x: +e.getAttribute("x")!, y: +e.getAttribute("y")!, w: +e.getAttribute("width")!, h: +e.getAttribute("height")! })),
  );
const near = (a: number, b: number) => Math.abs(a - b) <= 2;
const popover = (page: Page) => page.locator("[data-guide-popover]");
const stored = (page: Page) => page.evaluate(() => sessionStorage.getItem("metorite.guide.v1"));

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 860 } });

  test("rings the app list, keeps the focus, resumes after a reload, and Escape ends it in one frame", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");

    // Stop 1 rings the app list.
    await expect(ring(page)).toBeVisible();
    await expect(popover(page)).toContainText("Your apps live here");
    await expect(popover(page)).toContainText("1 of 5");
    const box = await ring(page).boundingBox();
    const nav = await page.locator('[data-guide="home.app-list"]').boundingBox();
    expect(box && nav && Math.abs(box.x - (nav.x - 4)) < 3 && Math.abs(box.y - (nav.y - 4)) < 3).toBe(true);
    // The popover sits beside the tall sidebar and never covers it.
    const pop = (await popover(page).boundingBox())!;
    expect(pop.x).toBeGreaterThanOrEqual(nav!.x + nav!.width);
    const vp = page.viewportSize()!;
    expect(pop.x + pop.width).toBeLessThanOrEqual(vp.width);
    expect(pop.y).toBeGreaterThanOrEqual(0);
    expect(pop.y + pop.height).toBeLessThanOrEqual(vp.height);

    // The address lost the parameter, so a reload resumes and does not restart.
    // The router drops it (a soft move), so the address changes a moment after the start.
    await expect.poll(() => new URL(page.url()).search).toBe("");
    expect(JSON.parse((await stored(page)) ?? "null")).toEqual({ guideId: "home.tour", step: 0 });

    // The focus did not move on mount, and the ring takes no pointer.
    const focusMoved = await page.evaluate(() => {
      const at = document.activeElement;
      return !!at && at !== document.body && !!at.closest("[data-guide-popover]");
    });
    expect(focusMoved).toBe(false);
    await expect(ring(page)).toHaveCSS("pointer-events", "none");

    // Tab reaches the ringed element (a link inside the app list).
    let reached = false;
    for (let i = 0; i < 40 && !reached; i++) {
      await page.keyboard.press("Tab");
      reached = await page.evaluate(() => !!document.activeElement?.closest('[data-guide="home.app-list"]'));
    }
    expect(reached).toBe(true);

    // Next to stop 2.
    await popover(page).getByRole("button", { name: "Next" }).click();
    await expect(popover(page)).toContainText("2 of 5");
    await expect(popover(page)).toContainText("Type here to find any app");
    expect(JSON.parse((await stored(page)) ?? "null")).toEqual({ guideId: "home.tour", step: 1 });

    // Reload: it resumes at stop 2, and no parameter is needed.
    await page.reload();
    await expect(popover(page)).toContainText("2 of 5");
    await expect(ring(page)).toBeVisible();

    // Escape: the ring is gone in the same frame. The page stays, storage is clear.
    const gone = await page.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          requestAnimationFrame(() => resolve(document.querySelector("[data-guide-ring]") === null));
        }),
    );
    expect(gone).toBe(true);
    await expect(popover(page)).toHaveCount(0);
    expect(await stored(page)).toBeNull();
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("the chat stops open the side panel and the step card sits above the message field", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(popover(page)).toContainText("1 of 5");
    await popover(page).getByRole("button", { name: "Next" }).click();
    await popover(page).getByRole("button", { name: "Next" }).click();
    await expect(page.locator("[data-guide-card]")).toContainText("3 of 5");
    await expect(page.locator('[data-guide="chat.panel"]')).toHaveAttribute("data-assistant", "dock");
    await expect(ring(page)).toBeVisible();
    await expect(page.locator("[data-guide-card]")).toContainText("Ask me how to do anything");
    // The line shows once: the side panel is open, so the card carries it and no popover shows.
    await expect(popover(page)).toHaveCount(0);
    // One live region announces each step, at every width.
    await expect(page.locator('[aria-live="polite"][data-guide-live]')).toHaveText(/Ask me how to do anything/);
    await expect(page.locator('[data-guide-live]')).toHaveCount(1);
    // The ring holds inside the window, so its whole line shows.
    const vp = page.viewportSize()!;
    const b = (await ring(page).boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.y).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(vp.width);
    expect(b.y + b.height).toBeLessThanOrEqual(vp.height);
    // The message field stays open and usable.
    await expect(page.locator("[data-assistant] textarea")).toBeEnabled();
  });

  test("a dim layer covers the page, leaves the ring and the step card clear, and takes no pointer", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(popover(page)).toContainText("1 of 5");
    // Stop 1: the dim layer is there, under the ring, with the ring's box as a hole.
    await expect(dim(page)).toHaveCount(1);
    await expect(dim(page)).toHaveCSS("pointer-events", "none");
    const rb = (await ring(page).boundingBox())!;
    await expect.poll(async () => (await holes(page)).some((h) => near(h.x, rb.x) && near(h.y, rb.y) && near(h.w, rb.width) && near(h.h, rb.height))).toBe(true);
    // The popover is later in the body than the dim layer, so it paints over it.
    const order = await page.evaluate(() => {
      const kids = [...document.body.children];
      const d = kids.findIndex((e) => e.hasAttribute("data-guide-dim"));
      const p = kids.findIndex((e) => e.hasAttribute("data-guide-popover"));
      const r = kids.findIndex((e) => e.hasAttribute("data-guide-ring"));
      return d >= 0 && d < r && d < p;
    });
    expect(order).toBe(true);
    // A PLAIN click on a link in the ringed app list still navigates.
    const link = page.locator('[data-guide="home.app-list"] a[href^="/"]:not([href="/"])').first();
    const target = new URL((await link.getAttribute("href"))!, "http://x").pathname;
    await link.click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(target);
  });

  test("at stop 4 the card has a hole, the rest is dimmed, and Escape removes the layer in one frame", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(popover(page)).toContainText("1 of 5");
    for (let i = 0; i < 3; i++) {
      // The popover carries stops 1 and 2. The step card carries the chat stops.
      await page.locator("[data-guide-popover],[data-guide-card]").getByRole("button", { name: "Next" }).first().click();
      await expect(page.locator("[data-guide-popover],[data-guide-card]").first()).toContainText(`${i + 2} of 5`);
    }
    const card = page.locator("[data-guide-card]");
    await expect(card).toContainText("4 of 5");
    await expect(card).toHaveCSS("box-shadow", /.+/);
    await expect(dim(page)).toHaveCount(1);
    const cb = (await card.boundingBox())!;
    await expect.poll(async () => (await holes(page)).some((h) => near(h.x, cb.x - 4) && near(h.y, cb.y - 4) && near(h.w, cb.width + 8) && near(h.h, cb.height + 8))).toBe(true);
    // A point outside every hole is dimmed: it sits in the mask's solid part.
    const hs = await holes(page);
    const vp = page.viewportSize()!;
    const outside = [[8, 8], [vp.width / 2, vp.height - 8], [vp.width - 8, 8], [8, vp.height - 8]].find(
      ([x, y]) => !hs.some((h) => x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h),
    );
    expect(outside, "a point outside both holes").toBeDefined();
    // The dim layer's fill is the scrim token: it is not empty, and not black.
    const fill = await dim(page).locator("rect[mask]").evaluate((e) => getComputedStyle(e).fill);
    expect(fill).not.toBe("none");
    expect(fill).not.toBe("rgb(0, 0, 0)");
    // Escape: the layer is gone in the same frame.
    const gone = await page.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          requestAnimationFrame(() => resolve(document.querySelector("[data-guide-dim]") === null));
        }),
    );
    expect(gone).toBe(true);
  });

  test("no guide starts with no ?guide= and no stored mark", async ({ page }) => {
    await stub(page);
    await page.goto("/");
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    await page.waitForTimeout(500);
    await expect(ring(page)).toHaveCount(0);
    expect(await stored(page)).toBeNull();
  });

  test("an unknown id starts nothing", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=nope.tour");
    await expect(page.locator('[data-guide="home.app-list"]')).toBeVisible();
    // The host takes the parameter out of the address at once, so wait for that
    // state. On a cold server the host loads late.
    await expect.poll(() => new URL(page.url()).search).toBe("");
    await expect(ring(page)).toHaveCount(0);
    expect(await stored(page)).toBeNull();
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("all five stops ring a visible element, the strip shows the line, and End ends it", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    const vp = page.viewportSize()!;
    const says = [
      "Your apps live here",
      "Type here to find any app",
      "Ask me how to do anything",
      "Your day starts here",
      "Try it: ask",
    ];
    // Where the line shows. Stop 1 has the Menu drawer over the strip, so its popover stays.
    // Stops 2 and 4 use the strip. The chat stops use the step card above the message field.
    const carrier = (i: number) =>
      i === 0 ? popover(page) : i === 1 || i === 3 ? page.locator("[data-guide-strip]") : page.locator("[data-guide-card]");
    for (let i = 0; i < 5; i++) {
      await expect(carrier(i)).toContainText(says[i]);
      await expect(ring(page)).toBeVisible();
      const b = await ring(page).boundingBox();
      expect(b, `stop ${i + 1} ring`).not.toBeNull();
      expect(b!.width).toBeGreaterThan(0);
      expect(b!.height).toBeGreaterThan(0);
      // The ring holds inside the window, so its whole line shows.
      expect(b!.x).toBeGreaterThanOrEqual(0);
      expect(b!.y).toBeGreaterThanOrEqual(0);
      expect(b!.x + b!.width).toBeLessThanOrEqual(vp.width);
      expect(b!.y + b!.height).toBeLessThanOrEqual(vp.height);
      // One live region announces the step, though the popover is off on a phone.
      await expect(page.locator('[aria-live="polite"][data-guide-live]')).toContainText(says[i]);
      await expect(page.locator('[aria-live="polite"]:not([data-guide-live])').filter({ hasText: says[i] })).toHaveCount(0);
      if (i === 0) {
        // The popover is the first thing at the point of its Next button: nothing covers it.
        const nb = (await carrier(i).getByRole("button", { name: "Next" }).boundingBox())!;
        const onTop = await page.evaluate(
          ([x, y]) => !!document.elementFromPoint(x, y)?.closest("[data-guide-popover]"),
          [nb.x + nb.width / 2, nb.y + nb.height / 2],
        );
        expect(onTop).toBe(true);
      }
      // On a phone only one copy of the line shows: the popover is for the drawer stop alone.
      await expect(popover(page)).toHaveCount(i === 0 ? 1 : 0);
      if (i < 4) {
        await expect(carrier(i)).toContainText(`${i + 1} of 5`);
        await carrier(i).getByRole("button", { name: "Next" }).click();
      }
    }
    await carrier(4).getByRole("button", { name: "End tour" }).click();
    await expect(ring(page)).toHaveCount(0);
    expect(await stored(page)).toBeNull();
  });

  test("the strip has a hole in the dim layer at 390 px", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(popover(page)).toContainText("1 of 5");
    await popover(page).getByRole("button", { name: "Next" }).click();
    const strip = page.locator("[data-guide-strip]");
    await expect(strip).toContainText("2 of 5");
    await expect(dim(page)).toHaveCount(1);
    const vp = page.viewportSize()!;
    // The hole is the strip's box with the ring's pad, held 4 px inside the window.
    // The strip rises under a move, so each poll reads its box again.
    await expect
      .poll(async () => {
        const sb = (await strip.boundingBox())!;
        const left = Math.max(4, sb.x - 4);
        const top = Math.max(4, sb.y - 4);
        const right = Math.min(vp.width - 4, sb.x + sb.width + 4);
        const bottom = Math.min(vp.height - 4, sb.y + sb.height + 4);
        return (await holes(page)).some((h) => near(h.x, left) && near(h.y, top) && near(h.x + h.w, right) && near(h.y + h.h, bottom));
      })
      .toBe(true);
  });

  test("a tap on the drawer scrim removes the ring, and the step is missing, not detached", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(popover(page)).toContainText("1 of 5");
    await expect(ring(page)).toBeVisible();
    // The Menu drawer scrim is the top left of the window, above the sheet.
    await page.mouse.click(195, 20);
    // The ringed list left the page: the ring and the popover go with it.
    await expect(ring(page)).toHaveCount(0);
    await expect(popover(page)).toHaveCount(0);
    // The anchor does not return, so the step is missing: the strip says so, and Next still works.
    await expect(page.locator("[data-guide-strip]")).toContainText("I can't point at it on this screen.");
  });
});
