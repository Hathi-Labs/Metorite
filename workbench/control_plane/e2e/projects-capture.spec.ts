import { expect, test, type Page } from "@playwright/test";

/**
 * Capture, the phone tab bar and the /tasks redirect (WS-64 OP-4,
 * `one_projects_app.md` §9 OP-4, done-when 2).
 *
 * ## The rules
 *
 * 1. With the flag on, at 390 px, the bar reads Menu, My work, Capture,
 *    Search, Ask.
 * 2. Capture opens the quick capture, and a capture is written to the Inbox
 *    (`POST my/tasks`), with no My work view open first.
 * 3. `/tasks?do=capture` lands on the Inbox with capture open, and `do` leaves
 *    the address. `/tasks` lands on Today, and `/tasks?item=<id>` on the task.
 *
 * ## How to run it
 *
 * The flag is inlined at build time, so the dev server must start with it:
 *
 *     NEXT_PUBLIC_PROJECTS_MY_WORK=1 npx playwright test e2e/projects-capture.spec.ts
 *
 * Without the flag the file skips. The Ask tab needs the chat flag, and the
 * spec turns the three dev overrides on in the browser (`shellChatOn`).
 * `src/app/projects/lib/tasksRedirect.test.ts` fences the redirect table.
 */

test.skip(
  !["1", "true", "on", "yes"].includes((process.env.NEXT_PUBLIC_PROJECTS_MY_WORK ?? "").toLowerCase()),
  "start the server with NEXT_PUBLIC_PROJECTS_MY_WORK=1",
);

const TODAY = "2026-10-24";

function row(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: `Task ${id}`,
    disposition: "INBOX",
    is_mine: true,
    project_id: "root-1",
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    assignees: [],
    tags: [],
    ...over,
  };
}

const EMPTY = { rows: [], items: [], total: 0, links: [], subtasks: [], children: [], buckets: [] };

/** The titles written by `POST my/tasks`, in order. */
async function mock(page: Page): Promise<string[]> {
  const written: string[] = [];
  await page.addInitScript(() => {
    for (const key of ["cc-shell-chat", "cc-shell-bar", "cc-shell-nav"]) localStorage.setItem(key, "1");
  });
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\//, "");
    if (req.method() === "POST" && /(^|\/)my\/tasks$/.test(path)) {
      const body = req.postDataJSON() as { title: string };
      written.push(body.title);
      return route.fulfill({ json: row("c1", { title: body.title }) });
    }
    if (/(^|\/)my\/tasks\/c1$/.test(path)) {
      return route.fulfill({ json: row("c1", { title: written[0] ?? "" }) });
    }
    if (path.includes("auth/me")) return route.fulfill({ json: { email: "you@example.com", name: "You" } });
    if (path.includes("my/today")) {
      return route.fulfill({ json: { today: TODAY, timezone: "UTC", stored: true, valid: true } });
    }
    if (path.includes("my/inbox")) {
      const rows = written.map((title) => row("c1", { title }));
      return route.fulfill({ json: { rows, total: rows.length } });
    }
    return route.fulfill({ json: EMPTY });
  });
  return written;
}

const CAPTURE_INPUT = (page: Page) => page.getByRole("textbox", { name: "Capture to inbox" });

test("at 390 the bar reads Menu, My work, Capture, Search, Ask", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page);
  await page.goto("/projects?project=p-none");
  const bar = page.locator("nav").filter({ has: page.locator("[data-ask-tab]") }).last();
  await expect(bar.locator("[data-ask-tab]")).toBeVisible({ timeout: 60_000 });
  const labels = (await bar.locator("button").allInnerTexts()).map((t) => t.trim()).filter(Boolean);
  expect(labels).toEqual(["Menu", "My work", "Capture", "Search", "Ask"]);
});

test("Capture writes to the Inbox with no My work view open", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const written = await mock(page);
  await page.goto("/projects?project=p-none");
  const bar = page.locator("nav").filter({ has: page.locator("[data-ask-tab]") }).last();
  await expect(bar.locator("[data-ask-tab]")).toBeVisible({ timeout: 60_000 });
  await bar.getByRole("button", { name: "Capture" }).click();
  await expect(CAPTURE_INPUT(page)).toBeVisible({ timeout: 30_000 });
  await CAPTURE_INPUT(page).fill("Buy printer toner");
  await CAPTURE_INPUT(page).press("Enter");
  await expect.poll(() => written, { timeout: 15_000 }).toEqual(["Buy printer toner"]);
});

test("My work opens Today", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page);
  await page.goto("/projects?project=p-none");
  const bar = page.locator("nav").filter({ has: page.locator("[data-ask-tab]") }).last();
  await expect(bar.locator("[data-ask-tab]")).toBeVisible({ timeout: 60_000 });
  await bar.getByRole("button", { name: "My work" }).click();
  await expect(page.getByTestId("my-work-host")).toBeVisible({ timeout: 30_000 });
});

test("/tasks?do=capture lands on the Inbox with capture open", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 390, height: 844 });
  const written = await mock(page);
  await page.goto("/tasks?do=capture");
  await expect(page).toHaveURL(/\/projects/, { timeout: 60_000 });
  await expect(CAPTURE_INPUT(page)).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => page.url(), { timeout: 15_000 }).not.toContain("do=capture");
  await CAPTURE_INPUT(page).fill("Call the lab");
  await CAPTURE_INPUT(page).press("Enter");
  await expect.poll(() => written, { timeout: 15_000 }).toEqual(["Call the lab"]);
});

test("/tasks lands on Today, and /tasks?item=<id> on the task", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await mock(page);
  await page.goto("/tasks");
  await expect(page).toHaveURL(/\/projects/, { timeout: 60_000 });
  await expect(page.getByTestId("my-work-host")).toBeVisible({ timeout: 30_000 });
  await page.goto("/tasks?item=abc-1");
  await expect(page).toHaveURL(/\/projects\?(.*&)?task=abc-1/, { timeout: 60_000 });
});
