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
 *   2. Flag on: one constant bar on every page. The app's name is in the
 *      app's own title bar under it, never in the shell bar (owner,
 *      2026-10-10, which reversed NS-1's merged row).
 *   3. My Tasks' title and tools sit in its own title bar, under the shell
 *      bar. The shell bar holds none of them.
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
/** The app's own title bar (`AppTopBar`), on desktop. */
const appBar = (page: Page) => page.locator('[data-app-bar="desktop"]');
const commandBar = (page: Page) => page.getByRole("dialog", { name: "Search or ask" });
const field = (page: Page) => commandBar(page).getByRole("combobox", { name: "Search or ask anything" });
const mod = process.platform === "darwin" ? "Meta" : "Control";

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("flag off: no shell bar, and My Tasks draws its own row", async ({ page }) => {
    await stub(page);
    await page.goto("/tasks");
    await expect(appBar(page).getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    await expect(bar(page)).toHaveCount(0);
  });

  test("flag on: one constant bar, and the app's name is in the app's own title bar", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/settings/appearance");
    await expect(bar(page)).toHaveCount(1);
    await expect(bar(page).getByRole("button", { name: /Search or ask anything/ })).toBeVisible();
    // The name is the app bar's h1, and the shell bar carries no heading.
    await expect(appBar(page).getByRole("heading", { level: 1, name: "Appearance" })).toBeVisible();
    await expect(bar(page).getByRole("heading")).toHaveCount(0);
    // The only "Appearance" in the shell bar is the command bar's own chip.
    await expect(bar(page).getByText("Appearance", { exact: true })).toHaveCount(0);
    await expect(bar(page).getByRole("button", { name: /Search or ask anything/ })).toContainText("in Appearance");
    // The title bar sits UNDER the shell bar, never inside it.
    const shell = (await bar(page).boundingBox())!;
    const app = (await appBar(page).boundingBox())!;
    expect(app.y).toBeGreaterThanOrEqual(shell.y + shell.height - 1);
  });

  test("My Tasks' title and tools sit in its own title bar, and the shell bar holds none of them", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await page.goto("/tasks");
    const row = appBar(page);
    await expect(row.getByRole("heading", { level: 1, name: "My Tasks" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    // The rail toggle at the left end, Capture next to the name.
    await expect(row.getByRole("button", { name: "Hide your lists" })).toBeVisible();
    await expect(row.getByRole("button", { name: /Capture/ })).toBeVisible();
    // None of it is in the shell bar.
    await expect(bar(page).getByRole("heading")).toHaveCount(0);
    await expect(bar(page).getByRole("button", { name: /Capture|your lists/ })).toHaveCount(0);
    // The old "Search" button is gone: the command bar is the one search.
    await expect(page.getByRole("button", { name: "Search", exact: true })).toHaveCount(0);
  });

  test("the shell bar is the same on every app, but for the command bar's chip", async ({ page }) => {
    await shellOn(page);
    await page.addInitScript(() => localStorage.setItem("cc-shell-nav", "1"));
    await stub(page);
    const shapes: string[] = [];
    for (const path of ["/tasks", "/email", "/people", "/settings/appearance"]) {
      await page.goto(path);
      await expect(appBar(page)).toBeVisible();
      shapes.push(
        await bar(page).evaluate((el) => {
          const copy = el.cloneNode(true) as HTMLElement;
          // The chip is the command bar's own scope ("in My Tasks"), §6.4.
          for (const chip of Array.from(copy.querySelectorAll("button span"))) {
            if (/^in /.test(chip.textContent ?? "")) chip.textContent = "in ·";
          }
          return copy.innerHTML;
        }),
      );
      // Each app's name is its own title bar's h1.
      await expect(appBar(page).getByRole("heading", { level: 1 })).toHaveCount(1);
    }
    expect(new Set(shapes).size).toBe(1);
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
    const fold = brand.getByRole("button", { name: "Collapse sidebar" });
    await expect(fold).toHaveAttribute("aria-expanded", "true");
    // The Menu glyph, never a panel glyph: an app's own rail toggle in this
    // bar wears PanelLeftOpen/Close, and two look-alike controls with two
    // jobs make a member guess (review, 2026-10-09).
    await expect(fold.locator("svg.lucide-menu")).toHaveCount(1);
    await expect(fold.locator("svg[class*='lucide-panel-left']")).toHaveCount(0);
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
    // One glyph for both states. `aria-expanded` carries the state.
    await expect(brand.getByRole("button", { name: "Expand sidebar" }).locator("svg.lucide-menu")).toHaveCount(1);
    // The state survives a reload, under the same key as before.
    expect(await page.evaluate(() => localStorage.getItem("cc-sidebar-collapsed"))).toBe("1");

    await brand.getByRole("button", { name: "Expand sidebar" }).click();
    await expect(aside(page)).toHaveAttribute("data-collapsed", "false");
  });

  // Owner, 2026-10-10: folded, the menu control must sit "in line with all
  // the icons of the sidebar", and the logo must keep a gap from it.
  for (const density of ["1", "0.875", "1.125"]) {
    test(`both flags: the menu control is centred over the folded rail's icons (scale ${density})`, async ({ page }) => {
      await shellOn(page);
      await page.addInitScript((d) => {
        localStorage.setItem("cc-shell-nav", "1");
        localStorage.setItem("cc-sidebar-collapsed", "1");
        document.documentElement.style.setProperty("--ui-scale", d);
      }, density);
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
      const brand = bar(page).locator("[data-shell-brand]");
      const fold = await box(brand.getByRole("button", { name: "Expand sidebar" }));
      const foldCentre = fold.x + fold.width / 2;
      // Every icon of the folded rail shares one centre line.
      const centres = await aside(page)
        .locator("nav a svg, nav button svg")
        .evaluateAll((els) =>
          els
            .map((e) => e.getBoundingClientRect())
            .filter((r) => r.width > 0 && r.height > 0)
            .map((r) => r.x + r.width / 2),
        );
      expect(centres.length).toBeGreaterThan(2);
      for (const c of centres) expect(Math.abs(c - foldCentre), `an icon at ${c} vs the control at ${foldCentre}`).toBeLessThanOrEqual(1);
      // The logo keeps a gap from the control.
      const logo = await box(brand.locator("a[href='/']"));
      expect(logo.x - (fold.x + fold.width)).toBeGreaterThanOrEqual(8);
    });
  }

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
  // under the command bar, because they shared its row. Since 2026-10-10 the
  // app draws its own row under the shell bar (owner), so the rule is now:
  // the shell bar's side zones hold nothing an app put there, and the app's
  // title bar fits its own row, the name and the last tool both in view.
  test("both flags at 1280: the app's title bar fits its own row, under the command bar", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await shellOn(page);
    await page.addInitScript(() => {
      localStorage.setItem("cc-shell-nav", "1");
      localStorage.setItem("cc-sidebar-collapsed", "1");
    });
    await stub(page);
    // Calendar carries the most tools of any bar, so it is the hardest fit.
    for (const path of ["/tasks", "/email", "/calendar"]) {
      await page.goto(path);
      const row = appBar(page);
      await expect(row.getByRole("heading", { level: 1 })).toBeVisible();
      const shell = await box(bar(page));
      const app = await box(row);
      // Under the shell bar, not beside the command bar.
      expect(app.y, path).toBeGreaterThanOrEqual(shell.y + shell.height - 1);
      // Nothing in the row runs past its right edge or out of the window.
      const fit = await row.evaluate((el) => {
        let hi = -Infinity;
        for (const d of Array.from(el.querySelectorAll("*"))) {
          const r = d.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          hi = Math.max(hi, r.right);
        }
        const own = el.getBoundingClientRect();
        return { hi, right: own.right, overflow: el.scrollWidth > el.clientWidth + 1 };
      });
      expect(fit.overflow, `${path}: the title bar overflows`).toBe(false);
      expect(fit.hi, `${path}: content past the bar's edge`).toBeLessThanOrEqual(fit.right + 1);
      expect(fit.right).toBeLessThanOrEqual(1280);
    }
    // The shell bar's left zone is empty: no app put anything in it.
    const left = await bar(page)
      .locator(":scope > div")
      .nth(1)
      .evaluate((el) => el.childElementCount);
    expect(left).toBe(0);
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
