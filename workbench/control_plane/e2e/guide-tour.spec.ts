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

const ANSWERED = { preset: "engineer", answered: "answered", pins: null, known: null, cardOrder: null, newOrder: null };

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

    // The address lost the parameter, so a reload resumes and does not restart.
    expect(new URL(page.url()).search).toBe("");
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
    await expect(popover(page)).toContainText("3 of 5");
    await expect(page.locator('[data-guide="chat.panel"]')).toHaveAttribute("data-assistant", "dock");
    await expect(ring(page)).toBeVisible();
    await expect(page.locator("[data-guide-card]")).toContainText("Ask me how to do anything");
    await expect(page.locator("[data-guide-card]")).toContainText("3 of 5");
    // The message field stays open and usable.
    await expect(page.locator("[data-assistant] textarea")).toBeEnabled();
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
    await page.waitForTimeout(500);
    await expect(ring(page)).toHaveCount(0);
    expect(new URL(page.url()).search).toBe("");
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
      expect(b!.x + b!.width).toBeGreaterThan(0);
      expect(b!.y + b!.height).toBeGreaterThan(0);
      expect(b!.x).toBeLessThan(vp.width);
      expect(b!.y).toBeLessThan(vp.height);
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
});
