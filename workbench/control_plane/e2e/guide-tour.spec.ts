import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The guide runner in a browser (WS-OB GD-4, `assistant_guide.md` §3, §4.3, §9).
 *
 *   a. Desktop. `/?guide=home.tour` rings the app list. The focus has not
 *      moved, Tab reaches the ringed element, and the address loses the
 *      parameter. Next goes to stop 2. A reload resumes at stop 2. Escape
 *      removes the ring in the same frame.
 *   b. 390 px. All five stops, each with a visible ring inside the window and
 *      ONE tour card inside the window, docked away from the ring. End ends it.
 *
 * The tour card floats above the dim layer, outside the chat (owner, 2026-11-03).
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
  features: ["tasks", "projects", "email", "chat", "people", "approvals", "crm"],
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
// The ONE tour card. It floats in the body portal, above the dim, outside the chat.
const card = (page: Page) => page.locator("[data-guide-card]");
const stored = (page: Page) => page.evaluate(() => sessionStorage.getItem("metorite.guide.v1"));

type Box = { x: number; y: number; width: number; height: number };
/** Whether two boxes share any area. */
const overlaps = (a: Box, b: Box) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

/** The card, as a box, once it has settled where it sits (it measures itself, then moves once). */
async function settledCard(page: Page): Promise<Box> {
  let last = "";
  let box!: Box;
  await expect
    .poll(async () => {
      box = (await card(page).boundingBox())!;
      const now = JSON.stringify(box);
      const same = now === last;
      last = now;
      return same;
    })
    .toBe(true);
  return box;
}

/** Press Next on the card with a plain click, and wait for the next stop. */
async function nextStop(page: Page, from: number): Promise<void> {
  await expect(card(page)).toContainText(`${from} of 5`);
  await card(page).getByRole("button", { name: "Next" }).click();
  await expect(card(page)).toContainText(`${from + 1} of 5`);
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1280, height: 860 } });

  test("rings the app list, keeps the focus, resumes after a reload, and Escape ends it in one frame", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");

    // Stop 1 rings the app list.
    await expect(ring(page)).toBeVisible();
    await expect(card(page)).toContainText("Your apps live here");
    await expect(card(page)).toContainText("1 of 5");
    const box = await ring(page).boundingBox();
    const nav = await page.locator('[data-guide="home.app-list"]').boundingBox();
    expect(box && nav && Math.abs(box.x - (nav.x - 4)) < 3 && Math.abs(box.y - (nav.y - 4)) < 3).toBe(true);
    // The card sits beside the tall sidebar and never covers it.
    const pop = await settledCard(page);
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
      return !!at && at !== document.body && !!at.closest("[data-guide-card]");
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
    await card(page).getByRole("button", { name: "Next" }).click();
    await expect(card(page)).toContainText("2 of 5");
    await expect(card(page)).toContainText("Type here to find any app");
    expect(JSON.parse((await stored(page)) ?? "null")).toEqual({ guideId: "home.tour", step: 1 });

    // Reload: it resumes at stop 2, and no parameter is needed.
    await page.reload();
    await expect(card(page)).toContainText("2 of 5");
    await expect(ring(page)).toBeVisible();

    // Escape: the card, the ring and the dim are gone in the same frame. The page stays, storage is clear.
    await expect(dim(page)).toHaveCount(1);
    const gone = await page.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          requestAnimationFrame(() =>
            resolve(
              document.querySelector("[data-guide-ring]") === null &&
                document.querySelector("[data-guide-card]") === null &&
                document.querySelector("[data-guide-dim]") === null,
            ),
          );
        }),
    );
    expect(gone).toBe(true);
    expect(await stored(page)).toBeNull();
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("at stop 3 the card sits beside the assistant panel, outside it, and the chat shows no tour line", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    await nextStop(page, 1);
    await nextStop(page, 2);
    await expect(card(page)).toContainText("3 of 5");
    const panel = page.locator('[data-guide="chat.panel"]');
    await expect(panel).toHaveAttribute("data-assistant", "dock");
    await expect(ring(page)).toBeVisible();
    await expect(card(page)).toContainText("Ask me how to do anything");
    // The card is outside the assistant panel's box, and does not overlap it.
    const c = await settledCard(page);
    const p = (await panel.boundingBox())!;
    expect(overlaps(c, p), "card over the panel").toBe(false);
    expect(c.x + c.width).toBeLessThanOrEqual(p.x + 1);
    // The card is not inside the chat in the tree either.
    await expect(page.locator("[data-assistant] [data-guide-card]")).toHaveCount(0);
    // The chat draws no tour line: the line shows once, in the card.
    await expect(page.locator("[data-assistant]").getByText("Ask me how to do anything")).toHaveCount(0);
    await expect(card(page)).toHaveCount(1);
    // One live region announces each step, at every width.
    await expect(page.locator('[aria-live="polite"][data-guide-live]')).toHaveText(/Ask me how to do anything/);
    await expect(page.locator("[data-guide-live]")).toHaveCount(1);
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

  test("a dim layer covers the page, leaves ONE hole for the ring, sits under the card, and takes no pointer", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    // Stop 1: the dim layer is there, under the ring, with the ring's box as its one hole.
    await expect(dim(page)).toHaveCount(1);
    await expect(dim(page)).toHaveCSS("pointer-events", "none");
    const rb = (await ring(page).boundingBox())!;
    await expect.poll(async () => (await holes(page)).length).toBe(1);
    await expect.poll(async () => (await holes(page)).some((h) => near(h.x, rb.x) && near(h.y, rb.y) && near(h.w, rb.width) && near(h.h, rb.height))).toBe(true);
    // The card is later in the body than the dim layer, so it paints over it.
    const order = await page.evaluate(() => {
      const kids = [...document.body.children];
      const d = kids.findIndex((e) => e.hasAttribute("data-guide-dim"));
      const p = kids.findIndex((e) => e.hasAttribute("data-guide-card"));
      const r = kids.findIndex((e) => e.hasAttribute("data-guide-ring"));
      return d >= 0 && d < r && r < p;
    });
    expect(order).toBe(true);
    // A PLAIN click on a link in the ringed app list still navigates.
    const link = page.locator('[data-guide="home.app-list"] a[href^="/"]:not([href="/"])').first();
    const target = new URL((await link.getAttribute("href"))!, "http://x").pathname;
    await link.click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(target);
  });

  test("at stop 4 the card sits beside My Day, the chat shows no tour line, and a plain click on Next works", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    for (let i = 1; i <= 3; i++) await nextStop(page, i);
    await expect(card(page)).toContainText("Your day starts here");
    const myDay = page.locator('[data-guide="home.my-day"]');
    await expect(myDay).toBeVisible();
    const c = await settledCard(page);
    const m = (await myDay.boundingBox())!;
    // The card stays inside the window. The ring is held inside it too.
    const vp = page.viewportSize()!;
    const visibleMyDay = {
      x: Math.max(0, m.x),
      y: Math.max(0, m.y),
      width: Math.min(vp.width, m.x + m.width) - Math.max(0, m.x),
      height: Math.min(vp.height, m.y + m.height) - Math.max(0, m.y),
    };
    // My Day fills the width beside the panel, so the card may sit over it. It never sits over the panel.
    const panelBox = (await page.locator('[data-assistant="dock"]').boundingBox())!;
    expect(overlaps(c, panelBox), "card over the panel").toBe(false);
    expect(visibleMyDay.width).toBeGreaterThan(0);
    expect(c.x).toBeGreaterThanOrEqual(0);
    expect(c.x + c.width).toBeLessThanOrEqual(vp.width);
    expect(c.y).toBeGreaterThanOrEqual(0);
    expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
    // The chat shows no tour line and no card.
    await expect(page.locator("[data-assistant]").getByText("Your day starts here")).toHaveCount(0);
    await expect(page.locator("[data-assistant] [data-guide-card]")).toHaveCount(0);
    // ONE hole, the ring's box. A dimmed corner lies outside it.
    await expect(dim(page)).toHaveCount(1);
    const rb = (await ring(page).boundingBox())!;
    await expect.poll(async () => (await holes(page)).length).toBe(1);
    await expect.poll(async () => (await holes(page)).some((h) => near(h.x, rb.x) && near(h.y, rb.y) && near(h.w, rb.width) && near(h.h, rb.height))).toBe(true);
    // The dim layer's fill is the scrim token: it is not empty, and not black.
    const fill = await dim(page).locator("rect[mask]").evaluate((e) => getComputedStyle(e).fill);
    expect(fill).not.toBe("none");
    expect(fill).not.toBe("rgb(0, 0, 0)");
    // The card keeps its accent edge.
    await expect(card(page)).toHaveCSS("outline-style", "solid");
    // Escape: the card, the ring and the dim are gone in the same frame.
    const gone = await page.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
          requestAnimationFrame(() =>
            resolve(
              document.querySelector("[data-guide-dim]") === null &&
                document.querySelector("[data-guide-ring]") === null &&
                document.querySelector("[data-guide-card]") === null,
            ),
          );
        }),
    );
    expect(gone).toBe(true);
  });

  test("at stops 3 and 5 the card box does not intersect the assistant panel box", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    const panel = page.locator('[data-assistant="dock"]');
    for (let i = 1; i <= 4; i++) {
      await nextStop(page, i);
      if (i !== 2 && i !== 4) continue;
      await expect(panel).toBeVisible();
      const c = await settledCard(page);
      const p = (await panel.boundingBox())!;
      expect(overlaps(c, p), `stop ${i + 1} card over the panel`).toBe(false);
    }
  });

  test("the last stop shows the chips on the card, and a chip opens its app", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    for (let i = 1; i <= 4; i++) await nextStop(page, i);
    await expect(card(page)).toContainText("5 of 5");
    await expect(card(page)).toContainText("Done");
    const chips = card(page).locator("[data-guide-chips]");
    await expect(chips).toBeVisible();
    expect(await chips.getByRole("button").count()).toBeGreaterThan(0);
    // The card stays inside the window with the chips, and off the ringed field.
    const c = await settledCard(page);
    const vp = page.viewportSize()!;
    expect(c.y).toBeGreaterThanOrEqual(0);
    expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
    expect(c.x + c.width).toBeLessThanOrEqual(vp.width);
    const rb = (await ring(page).boundingBox())!;
    expect(overlaps(c, rb), "card over the ringed field").toBe(false);
    await chips.getByRole("button", { name: "Projects" }).click();
    await expect(page).toHaveURL(/\/projects$/, { timeout: 30_000 });
    await expect(card(page)).toHaveCount(0);
    await expect(ring(page)).toHaveCount(0);
    await expect(dim(page)).toHaveCount(0);
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

/**
 * `crm.add-lead` on /crm: step 1 opens the page (no anchor), step 2 rings New,
 * step 3 says "fill in the form" with no anchor. Every step shows ONE card. A
 * step with no anchor draws NO dim, so the member can use the page, and End
 * stays reachable on every step (a phone has no Escape).
 */
async function walkAddLead(page: Page): Promise<void> {
  await page.goto("/?guide=crm.add-lead");
  const total = 3;
  for (let i = 1; i <= total; i++) {
    await expect(card(page)).toHaveCount(1);
    await expect(card(page)).toContainText(`${i} of ${total}`);
    await expect(card(page).getByRole("button", { name: "End tour" })).toBeVisible();
    const c = await settledCard(page);
    const vp = page.viewportSize()!;
    expect(c.x).toBeGreaterThanOrEqual(0);
    expect(c.x + c.width).toBeLessThanOrEqual(vp.width);
    expect(c.y).toBeGreaterThanOrEqual(0);
    expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
    // The step that points (2) has a dim, and a ring when New is on the page. The stub
    // serves no CRM data, so New can be missing: the card then says so, and the dim covers the page.
    if (i === 2) {
      await expect(dim(page)).toHaveCount(1);
      const found = (await ring(page).count()) > 0;
      if (!found) await expect(card(page)).toContainText("I can't point at it on this screen.");
    } else {
      await expect(ring(page)).toHaveCount(0);
      await expect(dim(page)).toHaveCount(0);
    }
    if (i < total) await card(page).getByRole("button", { name: "Next" }).click();
  }
  // End is reachable on the last step, and it removes the card.
  await card(page).getByRole("button", { name: "End tour" }).click();
  await expect(card(page)).toHaveCount(0);
  await expect(dim(page)).toHaveCount(0);
}

test.describe("a guide with steps that have no anchor", () => {
  test.describe("desktop 1280", () => {
    test.use({ viewport: { width: 1280, height: 860 } });
    test("crm.add-lead shows the card on every step, a dim only where it points, and End works", async ({ page }) => {
      await stub(page);
      await walkAddLead(page);
    });
  });
  test.describe("phone 390", () => {
    test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    test("crm.add-lead shows the card on every step, a dim only where it points, and End works", async ({ page }) => {
      await stub(page);
      await walkAddLead(page);
    });
  });
});

test.describe("keyboard focus", () => {
  test.use({ viewport: { width: 1280, height: 860 } });
  test("Tab to Next and Enter twice: the focus stays inside the tour card", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    let onNext = false;
    for (let i = 0; i < 80 && !onNext; i++) {
      await page.keyboard.press("Tab");
      onNext = await page.evaluate(
        () => document.activeElement?.textContent?.trim() === "Next" && !!document.activeElement.closest("[data-guide-card]"),
      );
    }
    expect(onNext).toBe(true);
    await page.keyboard.press("Enter");
    await expect(card(page)).toContainText("2 of 5");
    // Stop 2 keeps the dock shut, so nothing else asks for the focus here.
    const inCard = await page.evaluate(() => !!document.activeElement?.closest("[data-guide-card]"));
    expect(inCard).toBe(true);
    // A card that remounted would drop the focus to body, and this Enter would not reach stop 3.
    // Stop 3 opens the chat, and the chat's message field takes the focus (§5.3), so the check ends here.
    await page.keyboard.press("Enter");
    await expect(card(page)).toContainText("3 of 5");
  });
});

test.describe("a wide panel in a 1024 px window", () => {
  test.use({ viewport: { width: 1024, height: 800 } });
  test("the card stays off the panel at stop 5 (shrunk, or docked when too narrow)", async ({ page }) => {
    await stub(page);
    // The panel is 720 px wide: its left edge is at 304 px.
    await page.addInitScript(() => {
      const get = Storage.prototype.getItem;
      Storage.prototype.getItem = function (k: string) {
        return /::assistant$/.test(k) ? JSON.stringify({ width: 720 }) : get.call(this, k);
      };
    });
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    for (let i = 1; i <= 4; i++) await nextStop(page, i);
    await expect(card(page)).toContainText("5 of 5");
    const panel = page.locator('[data-assistant="dock"]');
    await expect(panel).toBeVisible();
    const p = (await panel.boundingBox())!;
    expect(p.x).toBeLessThan(400);
    const c = await settledCard(page);
    const vp = page.viewportSize()!;
    expect(c.x).toBeGreaterThanOrEqual(0);
    expect(c.x + c.width).toBeLessThanOrEqual(vp.width);
    expect(c.y).toBeGreaterThanOrEqual(0);
    expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
    // Left of the panel when it fits (shrunk to 240 px or more). Else docked full width.
    if (c.width < vp.width - 40) expect(c.x + c.width).toBeLessThanOrEqual(p.x + 1);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("every stop shows the card inside the window and off the ring, the card docks away from the ring, and Next works", async ({ page }) => {
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
    for (let i = 0; i < 5; i++) {
      await expect(card(page)).toContainText(says[i]);
      await expect(ring(page)).toBeVisible();
      const b = (await ring(page).boundingBox())!;
      expect(b.width).toBeGreaterThan(0);
      expect(b.height).toBeGreaterThan(0);
      // The ring holds inside the window, so its whole line shows.
      expect(b.x).toBeGreaterThanOrEqual(0);
      expect(b.y).toBeGreaterThanOrEqual(0);
      expect(b.x + b.width).toBeLessThanOrEqual(vp.width);
      expect(b.y + b.height).toBeLessThanOrEqual(vp.height);
      // The card is inside the window, with the 16 px gutter at each side.
      const c = await settledCard(page);
      expect(c.x).toBeGreaterThanOrEqual(15);
      expect(c.x + c.width).toBeLessThanOrEqual(vp.width - 15);
      expect(c.y).toBeGreaterThanOrEqual(0);
      expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
      // It docks to the edge FARTHER from the ring, so it never covers the ringed part.
      const centre = b.y + b.height / 2;
      const fills = b.height >= vp.height * 0.6;
      await expect(card(page)).toHaveAttribute("data-guide-side", fills || centre > vp.height / 2 ? "top" : "bottom");
      // Not over the ring, unless the ring fills the window (stop 3 rings the whole sheet) and no edge is free.
      if (!fills) expect(overlaps(c, b), `stop ${i + 1} card over the ring`).toBe(false);
      // One live region announces the step. No other region repeats it.
      await expect(page.locator('[aria-live="polite"][data-guide-live]')).toContainText(says[i]);
      await expect(page.locator('[aria-live="polite"]:not([data-guide-live])').filter({ hasText: says[i] })).toHaveCount(0);
      // The chat draws nothing for the tour.
      await expect(page.locator("[data-assistant] [data-guide-card]")).toHaveCount(0);
      if (i < 4) {
        await expect(card(page)).toContainText(`${i + 1} of 5`);
        // A plain click on the card's Next: nothing covers it.
        await card(page).getByRole("button", { name: "Next" }).click();
      }
    }
    await card(page).getByRole("button", { name: "End tour" }).click();
    await expect(ring(page)).toHaveCount(0);
    await expect(card(page)).toHaveCount(0);
    expect(await stored(page)).toBeNull();
  });

  test("at stop 5 the message field is low, so the card docks to the top", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    for (let i = 1; i <= 4; i++) await nextStop(page, i);
    await expect(card(page)).toContainText("5 of 5");
    await expect(card(page)).toHaveAttribute("data-guide-side", "top");
    const c = await settledCard(page);
    expect(c.y).toBeLessThanOrEqual(20);
    const b = (await ring(page).boundingBox())!;
    expect(b.y + b.height / 2).toBeGreaterThan(page.viewportSize()!.height / 2);
    expect(overlaps(c, b)).toBe(false);
  });

  test("the dim layer has one hole at 390 px, the ring, and none for the chat", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await nextStop(page, 1);
    await expect(dim(page)).toHaveCount(1);
    const rb = (await ring(page).boundingBox())!;
    await expect.poll(async () => (await holes(page)).length).toBe(1);
    await expect.poll(async () => (await holes(page)).some((h) => near(h.x, rb.x) && near(h.y, rb.y) && near(h.w, rb.width) && near(h.h, rb.height))).toBe(true);
  });

  test("a tap on the drawer scrim removes the ring, and the step is missing, not detached", async ({ page }) => {
    await stub(page);
    await page.goto("/?guide=home.tour");
    await expect(card(page)).toContainText("1 of 5");
    await expect(ring(page)).toBeVisible();
    // The Menu drawer scrim is the top left of the window, above the sheet.
    // The card docks to the top here and covers the scrim, so the tap goes through the 16 px gutter beside it.
    await settledCard(page);
    await page.mouse.click(6, 60);
    // The ringed list left the page: the ring goes with it.
    await expect(ring(page)).toHaveCount(0);
    // The anchor does not return, so the step is missing: the card says so, the dim covers the page, and Next still works.
    await expect(card(page)).toContainText("I can't point at it on this screen.");
    await expect(dim(page)).toHaveCount(1);
    await expect.poll(async () => (await holes(page)).length).toBe(0);
    const c = await settledCard(page);
    const vp = page.viewportSize()!;
    expect(c.y).toBeGreaterThanOrEqual(0);
    expect(c.y + c.height).toBeLessThanOrEqual(vp.height);
    await card(page).getByRole("button", { name: "Next" }).click();
    await expect(card(page)).toContainText("2 of 5");
  });
});
