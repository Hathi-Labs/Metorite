import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * NS-1, the shell bar and the command bar, in a browser
 * (`navigation_shell.md` §3.1, §6, §6.7).
 *
 * The flag is a build-time value, so a dev build lets one browser turn the bar
 * on for itself (`localStorage["cc-shell-bar"]`, `lib/shell/registry.ts`).
 * That is how this one suite runs both sides.
 *
 * Each test is written so the obvious wrong build fails it:
 *   1. Flag off: no shell bar, and My Tasks draws its own top row.
 *   2. Flag on: one bar on every page, and the app's name sits in it.
 *   3. My Tasks' title and tools move INTO the bar. No second row.
 *   4. ⌘K opens ONE command bar, never the app's old palette, and closes it.
 *   5. "new task" finds the job, and Enter opens Capture in My Tasks.
 *   6. In Email, `/` focuses the page's filter, which says "Filter".
 *   7. "Show all in Inbox" puts the words into Email's filter.
 *   8. "Ask the assistant" opens Chat with the words typed, not sent.
 *   9. Phone: the Menu drawer opens the same command bar.
 */

const ACCOUNT = {
  id: "acc1",
  provider: "microsoft",
  email_address: "me@example.com",
  label: "Work",
  avatar_color: "#6366f1",
  unread_count: 0,
  sync_enabled: true,
  sync_status: "idle",
};
const FOLDERS = [{ provider_folder_id: "inbox", name: "Inbox", type: "system", message_count: 0, unread_count: 0 }];

/** A member who holds the main apps. The dev sign-in holds only two panes,
 *  and the bar rightly offers nothing a member does not hold. */
const MEMBER = {
  authenticated: true,
  email: "member@example.com",
  is_admin: false,
  features: ["tasks", "email", "projects", "people", "chat"],
  permissions: [],
  roles: ["employee"],
  organization: { id: "org1", slug: "acme", display_name: "Acme" },
};

async function stub(page: Page) {
  const json = (r: Route, body: unknown) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/health", (r) => json(r, { gateway: "up" }));
  await page.route("**/api/auth/me", (r) => json(r, MEMBER));
  await page.route("**/api/email/accounts", (r) => json(r, [ACCOUNT]));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/folders.*/, (r) => json(r, FOLDERS));
  await page.route(/.*\/api\/email\/accounts\/[^/]+\/labels.*/, (r) => json(r, []));
  await page.route(/.*\/api\/email\/messages\/facets.*/, (r) =>
    json(r, { folder: "inbox", total: 0, unread: 0, uncategorized: 0, labels: {} }),
  );
  await page.route(/.*\/api\/email\/(messages|search)\?.*/, (r) =>
    json(r, { emails: [], total: 0, page: 1, page_size: 50 }),
  );
}

async function shellOn(page: Page) {
  await page.addInitScript(() => localStorage.setItem("cc-shell-bar", "1"));
}

const bar = (page: Page) => page.locator("[data-shell-bar]");
const commandBar = (page: Page) => page.getByRole("dialog", { name: "Search or ask" });
const field = (page: Page) => commandBar(page).getByRole("combobox", { name: "Search or ask anything" });
const mod = process.platform === "darwin" ? "Meta" : "Control";

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("flag off: no shell bar, and My Tasks draws its own row", async ({ page }) => {
    await stub(page);
    await page.goto("/tasks");
    await expect(page.getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    await expect(bar(page)).toHaveCount(0);
  });

  test("flag on: one bar on every page, naming the app", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(bar(page)).toHaveCount(1);
    await expect(bar(page)).toContainText("Appearance");
    await expect(bar(page).getByRole("button", { name: /Search or ask anything/ })).toBeVisible();
  });

  test("My Tasks' title and tools move into the bar, with no second row", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/tasks");
    const h1 = page.getByRole("heading", { level: 1, name: "My Tasks" });
    await expect(h1).toBeVisible();
    await expect(bar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    // The old "Search" button is gone: the command bar is the one search.
    await expect(page.getByRole("button", { name: "Search", exact: true })).toHaveCount(0);
  });

  test("⌘K opens one command bar, never the app's own palette, and closes it", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/tasks");
    await expect(bar(page)).toBeVisible();
    await page.keyboard.press(`${mod}+k`);
    await expect(commandBar(page)).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await page.keyboard.press(`${mod}+k`);
    await expect(commandBar(page)).toHaveCount(0);
  });

  test("“new task” finds the job, and Enter opens Capture in My Tasks", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("new task");
    await expect(commandBar(page).getByRole("option").first()).toContainText("New task");
    await page.keyboard.press("Enter");
    await page.waitForURL((u) => u.pathname === "/tasks");
    await expect(page.getByRole("textbox", { name: "Capture to inbox" })).toBeVisible();
    // The job left the address, so a reload does not open Capture again.
    await expect.poll(() => new URL(page.url()).search).toBe("");
  });

  test("a sentence finds its job: “new email to priya” offers Write an email first", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("new email to priya");
    await expect(commandBar(page).getByRole("option").first()).toContainText("Write an email");
  });

  test("in Email, / focuses the page's filter, which says Filter", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/email");
    const filter = page.locator("[data-page-filter]");
    await expect(filter).toBeVisible();
    await expect(filter).toHaveAttribute("placeholder", /^Filter /);
    await page.locator("body").click({ position: { x: 900, y: 600 } });
    await page.keyboard.press("/");
    await expect(filter).toBeFocused();
    await expect(commandBar(page)).toHaveCount(0);
  });

  test("“Show all in Inbox” puts the words into Email's filter", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/email");
    await expect(page.locator("[data-page-filter]")).toBeVisible();
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await expect(commandBar(page)).toContainText("in My Email");
    await field(page).fill("invoice march");
    await commandBar(page).getByRole("option", { name: /Show all in/ }).click();
    await expect(commandBar(page)).toHaveCount(0);
    await expect(page.locator("[data-page-filter]")).toHaveValue("invoice march");
  });

  test("“Ask the assistant” opens Chat with the words typed, not sent", async ({ page }) => {
    await shellOn(page);
    // One conversation, so Chat opens it rather than its agent picker.
    await page.addInitScript(() => {
      const now = new Date().toISOString();
      localStorage.setItem(
        "cc-chat::member@example.com|org1::sessions",
        JSON.stringify([{ id: "s1", name: "Chat", agentName: "assistant", createdAt: now, updatedAt: now, messageCount: 0 }]),
      );
    });
    await stub(page);
    let sent = 0;
    await page.route("**/api/agent/chat**", (r) => {
      sent += 1;
      return r.fulfill({ status: 500, body: "" });
    });
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("who is free on friday");
    await commandBar(page).getByRole("option", { name: /Ask the assistant/ }).click();
    await page.waitForURL((u) => u.pathname === "/chat");
    // The words wait in the message box. A build whose question vanished on
    // arrival fails here (review, 2026-10-08).
    await expect(page.locator("textarea").filter({ hasText: "" }).first()).toHaveValue("who is free on friday");
    await expect.poll(() => new URL(page.url()).search).toBe("");
    expect(sent).toBe(0);
  });

  test("a job opens again: New task, close, New task", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/tasks");
    const capture = page.getByRole("textbox", { name: "Capture to inbox" });
    for (let round = 0; round < 2; round++) {
      await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
      await field(page).fill("new task");
      await page.keyboard.press("Enter");
      await expect(capture).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(capture).toHaveCount(0);
    }
  });

  test("“Show all in the inbox” fills My Tasks' filter too, not only Email's", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/tasks");
    const filter = page.locator('[data-page-filter="the inbox"]');
    await expect(filter).toBeVisible();
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("vendor review");
    await commandBar(page).getByRole("option", { name: /Show all in the inbox/ }).click();
    await expect(filter).toHaveValue("vendor review");
  });
});

test.describe("Find (NS-4a)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  const GROUPS = {
    groups: [
      { app: "tasks", label: "Tasks", items: [{ kind: "task", title: "Calibrate the extruder", hint: "Task · Hardware", href: "/projects?task=t-1" }] },
      { app: "people", label: "People", items: [{ kind: "person", title: "Priya Rao", hint: "Person · Firmware lead", href: "/people/p-1" }] },
    ],
  };

  test("records from the member's apps arrive under the rows already shown", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    const asked: string[] = [];
    await page.route(/\/api\/shell\/search\?.*/, (r) => {
      asked.push(new URL(r.request().url()).searchParams.get("q") ?? "");
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GROUPS) });
    });
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("priya");
    const find = commandBar(page).getByRole("group", { name: "Find" });
    await expect(find.getByRole("option")).toHaveCount(2);
    await expect(find).toContainText("Person · Firmware lead");
    // One request for the words, not one per key.
    expect(asked).toEqual(["priya"]);
    await find.getByRole("option", { name: /Priya Rao/ }).click();
    await page.waitForURL((u) => u.pathname === "/people/p-1");
  });

  test("a row the member highlighted stays highlighted when Find arrives late", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.route(/\/api\/shell\/search\?.*/, async (r) => {
      await new Promise((res) => setTimeout(res, 900));
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(GROUPS) });
    });
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("who is free friday");
    // The last row is Ask. Highlight it before Find answers.
    await page.keyboard.press("ArrowUp");
    await expect(commandBar(page).getByRole("option", { selected: true })).toContainText("Ask the assistant");
    await expect(commandBar(page).getByRole("group", { name: "Find" })).toBeVisible();
    await expect(commandBar(page).getByRole("option", { selected: true })).toContainText("Ask the assistant");
    await page.keyboard.press("Enter");
    await page.waitForURL((u) => u.pathname === "/chat");
  });

  test("a gateway that is away leaves the other groups working", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.route(/\/api\/shell\/search\?.*/, (r) => r.fulfill({ status: 502, body: "" }));
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("email");
    await expect(commandBar(page).getByRole("option").first()).toContainText("Write an email");
    await expect(commandBar(page).getByRole("group", { name: "Find" })).toHaveCount(0);
  });
});

test.describe("the coordinator (NS-4b)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  async function intentStub(page: Page, answer: unknown, asked: string[] = []) {
    await page.route("**/api/shell/intent", async (r) => {
      asked.push((JSON.parse(r.request().postData() ?? "{}") as { q?: string }).q ?? "");
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(answer) });
    });
    await page.route(/\/api\/shell\/search\?.*/, (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ groups: [] }) }),
    );
  }

  test("a sentence becomes a filled job, and Capture opens with the words typed", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await intentStub(page, {
      kind: "job", job: "capture", label: "New task",
      href: "/tasks?do=capture&fill.title=call+the+vendor", filled: { title: "call the vendor" },
    });
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("remind me to call the vendor");
    const row = commandBar(page).getByRole("option", { name: /New task · call the vendor/ });
    await expect(row).toBeVisible();
    await expect(row).toContainText("Suggested by AI");
    await row.click();
    await page.waitForURL((u) => u.pathname === "/tasks");
    await expect(page.getByRole("textbox", { name: "Capture to inbox" })).toHaveValue("call the vendor");
    // §6.4 rule 2: the box says the AI filled it.
    await expect(page.getByText("Filled by AI. Check it, then press Enter to add it.")).toBeVisible();
    // The job and its fields left the address.
    await expect.poll(() => new URL(page.url()).search).toBe("");
  });

  test("out of credits says so in one line, and the other groups keep working", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await intentStub(page, { kind: "paused", message: "AI suggestions are paused. Ask an admin to add credits." });
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("write an email");
    await expect(commandBar(page).getByRole("option", { name: /AI suggestions are paused/ })).toBeVisible();
    await expect(commandBar(page).getByRole("option").first()).toContainText("Write an email");
  });

  test("one word never asks the coordinator", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    const asked: string[] = [];
    await intentStub(page, { kind: "none" }, asked);
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("email");
    await page.waitForTimeout(1500);
    expect(asked).toEqual([]);
  });
});

test.describe("the full-width bar (owner, 2026-10-09)", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  const aside = (page: Page) => page.locator("aside[data-collapsed]");
  const box = async (l: ReturnType<Page["locator"]>) => (await l.boundingBox())!;

  test("the shell bar alone: the bar sits over the page column, and the sidebar keeps its head", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(bar(page)).toBeVisible();
    // Exactly the NS-1 frame: the bar starts where the rail ends.
    expect(Math.round((await box(bar(page))).x)).toBe(Math.round((await box(aside(page))).width));
    await expect(aside(page).getByRole("button", { name: "Collapse sidebar" })).toBeVisible();
    await expect(aside(page).locator("a[href='/']").first()).toBeVisible();
    await expect(bar(page).getByRole("button", { name: /sidebar/ })).toHaveCount(0);
  });

  test("both flags: one bar across the whole width, with the fold control and the logo", async ({ page }) => {
    await shellOn(page);
    await page.addInitScript(() => localStorage.setItem("cc-shell-nav", "1"));
    await stub(page);
    await page.goto("/settings/appearance");
    const b = await box(bar(page));
    expect(b.x).toBe(0);
    expect(Math.round(b.width)).toBe(1440);
    // One row: the bar is its own height, nothing wraps under it.
    expect(Math.round(b.height)).toBe(44);
    const brand = bar(page).locator("[data-shell-brand]");
    await expect(brand.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute("aria-expanded", "true");
    await expect(brand.locator("a[href='/']")).toContainText("Acme");
    // The rail has no head of its own now: no second logo, no second control.
    // (Its Home link also goes to "/", so the logo is found by its name.)
    await expect(aside(page).getByRole("link", { name: "Acme" })).toHaveCount(0);
    await expect(aside(page).getByRole("button", { name: /sidebar/ })).toHaveCount(0);
    // The sidebar starts under the bar.
    expect(Math.round((await box(aside(page))).y)).toBe(Math.round(b.height));
  });

  test("both flags: the logo stays in view when the sidebar folds, and the bar button opens it again", async ({ page }) => {
    await shellOn(page);
    await page.addInitScript(() => localStorage.setItem("cc-shell-nav", "1"));
    await stub(page);
    await page.goto("/settings/appearance");
    const brand = bar(page).locator("[data-shell-brand]");
    const logo = brand.locator("a[href='/']");
    const before = await box(logo);

    await brand.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
    await expect.poll(async () => (await aside(page).boundingBox())?.width).toBeLessThan(60);
    // The owner's point: folded, the organization's logo is still there.
    await expect(logo).toBeVisible();
    expect(await box(logo)).toEqual(before);
    expect(Math.round((await box(bar(page))).width)).toBe(1440);
    // The state survives a reload, under the same key as before.
    expect(await page.evaluate(() => localStorage.getItem("cc-sidebar-collapsed"))).toBe("1");

    await brand.getByRole("button", { name: "Expand sidebar" }).click();
    await expect(aside(page)).toHaveAttribute("data-collapsed", "false");
  });

  // Measured 2026-10-09: at compact density the brand zone is 224px, and the
  // px-sized caption ran past it into the app's rail toggle. The logo gives
  // way instead, and "powered by Metorite" stays whole inside the zone.
  test("both flags: a customer's logo and its caption fit the brand zone at every density", async ({ page }) => {
    await shellOn(page);
    await page.addInitScript(() => localStorage.setItem("cc-shell-nav", "1"));
    await stub(page);
    const PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";
    await page.route("**/api/settings/branding", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          logo: { dataUri: `data:image/png;base64,${PIXEL}`, mime: "image/png", width: 600, height: 160, byteSize: 70 },
          updatedBy: "",
          updatedAt: "",
        }),
      }),
    );
    await page.goto("/settings/appearance");
    const brand = bar(page).locator("[data-shell-brand]");
    const caption = brand.getByText("powered by Metorite");
    await expect(caption).toBeVisible();
    for (const scale of ["0.875", "1", "1.125"]) {
      await page.evaluate((s) => document.documentElement.style.setProperty("--ui-scale", s), scale);
      await page.waitForTimeout(200);
      const zone = await box(brand);
      const c = await box(caption);
      expect(c.x + c.width, `the caption stays in the zone at ${scale}`).toBeLessThanOrEqual(zone.x + zone.width);
      expect(await caption.evaluate((e) => e.scrollWidth > e.clientWidth + 1)).toBe(false);
    }
  });

  // Measured 2026-10-09: at 1280px My Tasks' title, Capture and My day ran
  // under the command bar, because the brand zone keeps its width when the
  // sidebar folds. The command bar must give way, never sit on top.
  test("both flags at 1280: the app's buttons never run under the command bar", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await shellOn(page);
    await page.addInitScript(() => {
      localStorage.setItem("cc-shell-nav", "1");
      localStorage.setItem("cc-sidebar-collapsed", "1");
    });
    await stub(page);
    await page.goto("/tasks");
    await expect(bar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    const left = await box(bar(page).locator(":scope > div").nth(1));
    const search = await box(bar(page).getByRole("button", { name: /Search or ask anything/ }));
    const right = await box(bar(page).locator(":scope > div").nth(2));
    expect(left.x + left.width).toBeLessThanOrEqual(search.x);
    expect(search.x + search.width).toBeLessThanOrEqual(right.x);
    expect(right.x + right.width).toBeLessThanOrEqual(1280);
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("the Menu drawer opens the same command bar", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await page.getByRole("button", { name: "Menu" }).click();
    await page.getByRole("button", { name: "Search or ask anything" }).click();
    await expect(commandBar(page)).toBeVisible();
    await field(page).fill("email");
    await expect(commandBar(page).getByRole("option").first()).toContainText("Write an email");
  });
});
