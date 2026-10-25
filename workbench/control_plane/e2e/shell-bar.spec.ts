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

/**
 * The Projects page loads empty. Since OP-4 (2026-10-25) Projects owns the
 * "New task" job, and its Capture box opens only once the page has loaded.
 */
async function stubProjects(page: Page) {
  await page.route("**/api/projects/**", (r) => {
    const path = new URL(r.request().url()).pathname;
    const task = {
      id: "t1", title: "Vendor review", disposition: "INBOX", is_mine: true, project_id: "root-1",
      created_at: "2026-10-01T09:00:00Z", updated_at: "2026-10-01T09:00:00Z", assignees: [], tags: [],
    };
    const body = path.includes("my/today")
      ? { today: "2026-10-24", timezone: "UTC", stored: true, valid: true }
      : path.includes("my/inbox")
        ? { rows: [task], total: 1 }
        : { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}

/**
 * Capture inside Projects exists only with My work on (`NEXT_PUBLIC_PROJECTS_MY_WORK`,
 * a build switch). With it off, the box lives on `/tasks` and Projects opens none.
 */
const MY_WORK_ON = ["1", "true", "on", "yes"].includes((process.env.NEXT_PUBLIC_PROJECTS_MY_WORK ?? "").toLowerCase());

async function shellOn(page: Page) {
  await page.addInitScript(() => localStorage.setItem("cc-shell-bar", "1"));
}

const bar = (page: Page) => page.locator("[data-shell-bar]");
/** The app's own title bar (`AppTopBar`), on desktop. */
const appBar = (page: Page) => page.locator('[data-app-bar="desktop"]');
const commandBar = (page: Page) => page.getByRole("dialog", { name: "Search or ask" });
const field = (page: Page) => commandBar(page).getByRole("combobox", { name: "Search or ask anything" });
const mod = process.platform === "darwin" ? "Meta" : "Control";

/** The root font size each density gives, as `globals.css` computes it. */
const ROOT_PX: Record<string, string> = { "0.875": "14px", "1": "16px", "1.125": "18px" };
const rootPx = (page: Page) => page.evaluate(() => getComputedStyle(document.documentElement).fontSize);

/**
 * Set the density AFTER the page loads. ⚠️ An `addInitScript` value does not
 * hold: the appearance store writes `--ui-scale` again when it hydrates
 * (`lib/theme/store.ts`). So the first version of the folded-rail loop below
 * ran all three of its scales at scale 1 (2026-10-12). The poll proves the
 * scale took. `e2e/shell-account.spec.ts` keeps the same helper.
 */
async function setScale(page: Page, scale: string) {
  await page.evaluate((v) => document.documentElement.style.setProperty("--ui-scale", v), scale);
  await expect.poll(() => rootPx(page)).toBe(ROOT_PX[scale]);
  // Let the layout settle at the new size.
  await page.waitForTimeout(150);
}

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
    for (const path of ["/projects", "/email", "/people", "/settings/appearance"]) {
      await page.goto(path);
      await expect(appBar(page)).toBeVisible();
      shapes.push(
        await bar(page).evaluate((el) => {
          const copy = el.cloneNode(true) as HTMLElement;
          // The chip is the command bar's own scope ("in My Tasks"), §6.4.
          for (const chip of Array.from(copy.querySelectorAll("button span"))) {
            if (/^in /.test(chip.textContent ?? "")) chip.textContent = "in ·";
          }
          // Each comet's gradient id comes from React's `useId`, so it differs
          // per render (`MetoriteMark.tsx`). The id is not the bar's shape.
          return copy.innerHTML.replace(/metorite-comet-[A-Za-z0-9_-]+/g, "metorite-comet-·");
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

  // OP-4 (2026-10-25): the job's owner is Projects, since My Tasks left the nav.
  test("“new task” finds the job, and Enter opens Capture in Projects", async ({ page }) => {
    await shellOn(page);
    await stub(page);
    await stubProjects(page);
    await page.goto("/settings/appearance");
    await bar(page).getByRole("button", { name: /Search or ask anything/ }).click();
    await field(page).fill("new task");
    await expect(commandBar(page).getByRole("option").first()).toContainText("New task");
    await page.keyboard.press("Enter");
    await page.waitForURL((u) => u.pathname === "/projects");
    if (!MY_WORK_ON) return; // the box below needs My work (see `MY_WORK_ON`)
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
    test.skip(!MY_WORK_ON, "Capture opens in Projects only with NEXT_PUBLIC_PROJECTS_MY_WORK on");
    await shellOn(page);
    await stub(page);
    await stubProjects(page);
    await page.goto("/projects");
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

  // OP-4 (2026-10-25): My Tasks left the nav, so the bar offers no scoped
  // "Show all in" on `/tasks`. The same inbox list now sits in Projects' My work.
  test("“Show all in the inbox” fills the My work inbox filter too, not only Email's", async ({ page }) => {
    test.skip(!MY_WORK_ON, "the inbox filter sits in Projects only with NEXT_PUBLIC_PROJECTS_MY_WORK on");
    await shellOn(page);
    await stub(page);
    await stubProjects(page);
    await page.goto("/projects?app=my-inbox");
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
      await page.addInitScript(() => {
        localStorage.setItem("cc-shell-nav", "1");
        localStorage.setItem("cc-sidebar-collapsed", "1");
      });
      await stub(page);
      await page.goto("/settings/appearance");
      await expect(aside(page)).toHaveAttribute("data-collapsed", "true");
      // The rail draws its skeleton until the layout read answers (the star
      // model, 2026-10-11), so wait for the real rows.
      await expect(aside(page).locator("nav a").first()).toBeVisible();
      // After the load, never in `addInitScript` (`setScale` says why).
      await setScale(page, density);
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
  // px-sized logo must give way inside it. Owner, 2026-10-11: the logo stands
  // alone, with no "powered by Metorite" beside it, and it has the room the
  // caption kept. Our comet signs the bar's right end (`shell-signature.spec.ts`).
  test("both flags: a customer's logo stands alone, and fits the brand zone at every density", async ({ page }) => {
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
    const img = brand.locator("img:visible");
    await expect(img).toBeVisible();
    // No caption: the lockup draws no words at all.
    expect((await brand.locator("a[href='/']").textContent())?.trim()).toBe("");
    expect(await bar(page).textContent()).not.toMatch(/powered by/i);
    // 600×160 at 24px tall wants 90px. With the caption's 100px reserve the
    // cap was 76px, so a 90px logo proves the reserve is gone.
    expect(Math.round((await box(img)).width)).toBe(90);
    for (const scale of ["0.875", "1", "1.125"]) {
      await setScale(page, scale);
      const zone = await box(brand);
      const l = await box(img);
      expect(l.x + l.width, `the logo stays in the zone at ${scale}`).toBeLessThanOrEqual(zone.x + zone.width);
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
      // ⚠️ Measured as one block that may run again, with `toPass`. A cold
      // load mounts the page TWICE: `OrgLinkGate`'s Suspense renders the page
      // as its fallback, then swaps in the real copy (measured 2026-10-12, on
      // main too, the `test.fixme` below). A measure that lands in the swap
      // reads a detached row, and `boundingBox()` gives null. That failed CI
      // twice on #918. A real overflow still fails every run of the block.
      await expect(async () => {
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
      }).toPass({ timeout: 10_000 });
    }
    // The shell bar's left zone is empty: no app put anything in it.
    const left = await bar(page)
      .locator(":scope > div")
      .nth(1)
      .evaluate((el) => el.childElementCount);
    expect(left).toBe(0);
  });

  // ⚠️ KNOWN DEFECT, on main as well (measured 2026-10-12, #918). A cold
  // load mounts each page twice. `OrgLinkGate` wraps the page in
  // `<Suspense fallback={<>{children}</>}>` for `useSearchParams`, so the page
  // renders as the fallback and is then replaced by the real copy, about
  // 120 ms later (2 to 3 s at 6x CPU in dev). A member sees the title bar
  // flicker, and the first copy's state and requests are thrown away.
  // Without the Suspense, this count is 1. HANDOFF H-302 holds the work: the
  // production measurements, the two refused fixes (`fallback={null}` left
  // `<main>` empty for 300 ms and more, and a param lifted into state mounts
  // an in-app `?org=` link as the wrong account), and the open question of
  // what suspends. Remove `fixme` with the fix, and this fences it.
  test.fixme("a cold load of /tasks mounts the app bar once", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await shellOn(page);
    await page.addInitScript(() => {
      localStorage.setItem("cc-shell-nav", "1");
      const w = window as unknown as { __appBarMounts: number };
      w.__appBarMounts = 0;
      new MutationObserver((ms) => {
        for (const m of ms)
          for (const n of Array.from(m.addedNodes)) {
            if (!(n instanceof Element)) continue;
            w.__appBarMounts += n.matches('[data-app-bar="desktop"]') ? 1 : n.querySelectorAll('[data-app-bar="desktop"]').length;
          }
      }).observe(document, { childList: true, subtree: true });
    });
    await stub(page);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    await page.goto("/tasks");
    await expect(appBar(page).getByRole("heading", { level: 1 })).toBeVisible();
    // Long enough for the swap to land, even under the throttle.
    await page.waitForTimeout(5_000);
    expect(await page.evaluate(() => (window as unknown as { __appBarMounts: number }).__appBarMounts)).toBe(1);
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
