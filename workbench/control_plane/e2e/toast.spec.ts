import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * `Toast` — the confirmation channel, in a real browser (WS-27ak item 3, D-PM-21).
 *
 * ## Why this file is Playwright and not vitest
 *
 * `vitest.config.ts` here is `environment: "node"` and collects only
 * `src/**‍/*.test.ts`, so **no React component in this repo is rendered by any
 * unit test**. Everything about a toast that can actually be wrong is a fact
 * about the DOM over time — *how many* elements exist during one operation,
 * whether the second fire of a key produced a second node, which live region a
 * message landed in. `src/lib/toast.test.ts` fences the rules; this fences the
 * behaviour those rules are supposed to produce.
 *
 * ## The harness
 *
 * `/projects` renders with no auth (the gateway is not running here), and the
 * notification bell is in its header on a cold page. Every `/api/projects/**`
 * call is fulfilled locally, which does two things: it makes the board render
 * quietly instead of 502-ing, and it puts `POST …/notifications/read` — the
 * mutation WS-27ak(3) wired — under this file's control, so success, refusal
 * and retry are all reachable without fixtures or a database.
 *
 * The driver is `NotificationBell.dismissAll`, chosen because before this slice
 * it reported **nothing at all**: it clears the badge optimistically, so a
 * refusal looked identical to a success until the next sixty-second poll put
 * the unread rows back.
 *
 * ## What each test is the *after* of
 *
 * Measured across the tree before this slice, for §11's parity table: **0**
 * `useToast`, **0** `<Toaster>`, **0** `ToastProvider`. There was no
 * confirmation channel to regress — so these assert the four properties the
 * ticket names, each written so that the obvious wrong implementation fails it:
 *
 *   1. one operation → **one** toast, mutated in place (not three);
 *   2. a rejection → the **error** variant, and never the success one;
 *   3. a keyed re-fire → **updates**, does not stack;
 *   4. error is assertive, success is polite, and Escape dismisses.
 *
 * ⚠️ Test 1 asserts the toast is the **same DOM node** before and after
 * settling, via a marker attribute stamped while it is loading. Counting alone
 * would pass an implementation that closes the loading toast and opens a
 * separate success one — which is three toasts' worth of flicker wearing one
 * toast's count.
 */

/** The wrapper's own attribute, so these tests do not depend on Base UI's. */
const TOAST = "[data-cc-toast]";
const LOADING = '[data-cc-toast="loading"]';
const SUCCESS = '[data-cc-toast="success"]';
const ERROR = '[data-cc-toast="error"]';

const REFUSAL = "That mailbox is not yours to clear";

/** The unread rows the bell reads. Enough shape for `describe()` to render. */
const NOTIFICATIONS = {
  rows: [
    {
      id: "n1",
      kind: "assigned",
      task_id: "t1",
      actor: "priya@example.com",
      excerpt: null,
      created_at: "2026-08-11T09:00:00Z",
      read_at: null,
      task_title: "Ship the thing",
      task_number: 12,
      project_id: "p1",
    },
    {
      id: "n2",
      kind: "mention",
      task_id: "t2",
      actor: "sam@example.com",
      excerpt: "can you look at this",
      created_at: "2026-08-11T08:00:00Z",
      read_at: null,
      task_title: "Review the plan",
      task_number: 13,
      project_id: "p1",
    },
  ],
  total: 2,
  unread: { total: 2, mentions: 1 },
};

type MarkRead = { status: number; body: unknown; delayMs?: number };

/** What the next `POST …/notifications/read` should do. Rebound per test. */
let markRead: MarkRead;

const json = (route: Route, status: number, body: unknown) =>
  route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

test.beforeEach(async ({ page }) => {
  markRead = { status: 200, body: { marked: 2 }, delayMs: 300 };

  // The shell's update notice (navigation_shell.md §7.3) reads a 5xx from
  // `/api/*` as a possible outage and asks `/api/health`. CI runs no gateway,
  // so the real probe would answer "down" and add a second toast, "Metorite
  // is updating", beside the one under test. Here the product is healthy and
  // only the stubbed route fails, which is exactly the case where the notice
  // must stay silent.
  await page.route("**/api/health", (route) =>
    json(route, 200, { gateway: "up", build: "e2e" }),
  );

  await page.route("**/api/projects/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/notifications/read")) {
      if (markRead.delayMs) {
        await new Promise((resolve) => setTimeout(resolve, markRead.delayMs));
      }
      await json(route, markRead.status, markRead.body);
      return;
    }
    if (path.endsWith("/notifications")) {
      await json(route, 200, NOTIFICATIONS);
      return;
    }
    // Everything else the cold page asks for. An empty collection rather than a
    // 502, so the board renders its ordinary empty state and nothing on screen
    // competes with the toast for the reader's attention.
    await json(route, 200, { rows: [], total: 0 });
  });

  await page.goto("/projects");
  await expect(page.locator("button").first()).toBeVisible();
});

/** Open the bell and press "Mark all read" — the wired mutation. */
async function markAllRead(page: Page) {
  await page.getByRole("button", { name: /^Notifications/ }).click();
  await page.getByRole("button", { name: "Mark all read" }).click();
}

test.describe("Toast", () => {
  test("one operation produces one toast, mutated in place — not three", async ({
    page,
  }) => {
    await markAllRead(page);

    // While in flight: exactly one toast, and it is the loading one.
    await expect(page.locator(LOADING)).toHaveCount(1);
    await expect(page.locator(TOAST)).toHaveCount(1);

    // ⚠️ Stamp the node. Counting alone cannot tell "mutated in place" from
    // "closed and replaced", and the second is what a call site firing
    // `show()` twice produces.
    await page.evaluate(() => {
      const node = document.querySelector("[data-cc-toast]");
      if (!node) throw new Error("no toast to mark — harness broke");
      node.setAttribute("data-e2e-same", "1");
    });

    await expect(page.locator(SUCCESS)).toHaveCount(1);
    await expect(page.locator(TOAST), "one operation left more than one toast").toHaveCount(
      1,
    );
    await expect(
      page.locator(`${SUCCESS}[data-e2e-same="1"]`),
      "the success toast is a different element from the loading one — the " +
        "operation closed one toast and opened another instead of mutating one",
    ).toHaveCount(1);
    // And the wording changed with it, so "in place" is not "never updated".
    await expect(page.locator(SUCCESS)).toContainText("2 notifications marked read");
    await expect(page.locator(LOADING)).toHaveCount(0);
  });

  test("a rejection shows the error variant and never the success one", async ({
    page,
  }) => {
    markRead = { status: 422, body: { detail: REFUSAL }, delayMs: 150 };

    // Watch the whole operation rather than sampling its end: a success toast
    // that flashed for 80ms before the error replaced it is still a lie, and
    // an assertion on the final state cannot see it.
    const seen = new Set<string>();
    const poll = setInterval(() => {
      void page
        .$$eval(TOAST, (nodes) =>
          nodes.map((n) => n.getAttribute("data-cc-toast") ?? ""),
        )
        .then((variants) => variants.forEach((v) => seen.add(v)))
        .catch(() => undefined);
    }, 25);

    await markAllRead(page);
    await expect(page.locator(ERROR)).toHaveCount(1);
    await page.waitForTimeout(400);
    clearInterval(poll);

    expect(
      [...seen].includes("success"),
      `a success toast appeared during a failing operation (saw: ${[...seen].join(", ")})`,
    ).toBe(false);
    await expect(page.locator(SUCCESS)).toHaveCount(0);
    await expect(page.locator(TOAST)).toHaveCount(1);

    // The rejection's own words, under the sentence naming what failed —
    // "Something went wrong" over a 422 that said exactly what was wrong is
    // the other half of reporting a failure honestly.
    await expect(page.locator(ERROR)).toContainText("Couldn't mark your notifications read");
    await expect(page.locator(ERROR)).toContainText(REFUSAL);
  });

  test("a failure does not quietly go away on its own", async ({ page }) => {
    // A success clears itself after SUCCESS_TIMEOUT (5s). An error must not:
    // five seconds is exactly long enough to miss while looking at the thing
    // you were editing, and a failure nobody saw was never reported.
    markRead = { status: 500, body: { detail: "boom" }, delayMs: 50 };
    await markAllRead(page);
    await expect(page.locator(ERROR)).toHaveCount(1);
    await page.waitForTimeout(6_500);
    await expect(
      page.locator(ERROR),
      "the error toast auto-dismissed — the failure is silent again",
    ).toHaveCount(1);
  });

  test("a keyed re-fire updates the toast rather than stacking a second one", async ({
    page,
  }) => {
    markRead = { status: 503, body: { detail: "the gateway is down" }, delayMs: 50 };
    await markAllRead(page);
    await expect(page.locator(ERROR)).toHaveCount(1);

    // ⚠️ The second fire has to come from the BELL, not from Retry, and that
    // is the whole reason this step exists. Retry's handler closes the toast
    // before it re-runs the closure (`Toast.tsx`, and deliberately: closing
    // afterwards would dismiss the toast the retry had just raised), so by the
    // time the second `toast.promise` lands there is nothing on screen for a
    // second toast to stack against — measured, dedupe removed: the Retry path
    // still leaves exactly one toast, so it cannot fence this at all. Pressing
    // "Mark all read" again can: the button survives a failed mark (the unread
    // count did not move), and the error toast is still up. Measured with
    // `toastIdFor` stubbed to `undefined`: 2 toasts mid-flight and 2 settled.
    markRead = { status: 503, body: { detail: "the gateway is down" }, delayMs: 250 };
    await page.getByRole("button", { name: "Mark all read" }).click();
    await expect(page.locator(LOADING)).toHaveCount(1);
    await expect(
      page.locator(TOAST),
      "a second press of the same operation stacked a second toast",
    ).toHaveCount(1);
    await expect(page.locator(ERROR)).toHaveCount(1);
    await expect(page.locator(TOAST)).toHaveCount(1);

    markRead = { status: 200, body: { marked: 1 }, delayMs: 250 };
    // Addressed structurally, not by role: an error is `priority: "high"`, and
    // Base UI stamps `aria-hidden="true"` on a high-priority toast's root until
    // the viewport is focused (`ToastRoot.mjs:418`) — the assertive copy is
    // doing the announcing, so the visible one is kept out of the a11y tree.
    // Its controls go with it, which is why `getByRole` finds nothing here.
    // That path is not lost, it is F6's, and the last test in this file walks
    // it. What Retry pins below is the PATCH MERGE, not the dedupe.
    await page.locator(TOAST).locator("button", { hasText: "Retry" }).click();

    await expect(page.locator(LOADING)).toHaveCount(1);
    await expect(page.locator(TOAST)).toHaveCount(1);
    await expect(page.locator(SUCCESS)).toHaveCount(1);
    await expect(page.locator(TOAST)).toHaveCount(1);
    await expect(page.locator(SUCCESS)).toContainText("1 notification marked read");
    // The failure's detail line must not survive the success. The substrate
    // MERGES patches, so an omitted key would leave "the gateway is down"
    // sitting under a message that says it worked.
    await expect(page.locator(SUCCESS)).not.toContainText("the gateway is down");
    await expect(page.locator(SUCCESS)).not.toContainText("Retry");
  });

  test("an error is announced assertively; a success is not", async ({ page }) => {
    // Next ships a permanent assertive live region of its own — the route
    // announcer, `#__next-route-announcer__`, inside a shadow root on
    // `<next-route-announcer>`. Playwright's selector engine pierces open
    // shadow roots, so a bare `[role="alert"]` counts it and every assertion
    // below is off by one; `document.querySelectorAll` does NOT pierce, which
    // is why the same page looks like it has one alert from the console. Only
    // toast alerts belong in this count.
    const alerts = () =>
      page.locator('[role="alert"]:not(#__next-route-announcer__)');

    markRead = { status: 422, body: { detail: REFUSAL }, delayMs: 50 };
    await markAllRead(page);
    await expect(page.locator(ERROR)).toHaveCount(1);
    // `priority: "high"` renders a visually-hidden `role="alert"` copy, i.e.
    // assertive. Without it an error is announced politely — queued behind
    // whatever the reader is already being read.
    await expect(alerts(), "the error was not announced assertively").toHaveCount(1);
    await expect(alerts()).toContainText(REFUSAL);

    // The viewport itself is the polite region everything else lands in.
    const viewport = page.locator('[role="region"][aria-live]');
    await expect(viewport).toHaveAttribute("aria-live", "polite");

    // A success must NOT interrupt: a "Saved" talking over a screen-reader
    // user mid-sentence is how the whole channel gets turned off.
    // Structural for the same reason as the Retry click above — this toast is
    // the high-priority one, so its close button is out of the a11y tree until
    // the viewport takes focus.
    await page
      .locator(TOAST)
      .locator('button[aria-label="Dismiss notification"]')
      .click();
    await expect(page.locator(TOAST)).toHaveCount(0);
    markRead = { status: 200, body: { marked: 2 }, delayMs: 50 };
    await page.reload();
    await markAllRead(page);
    await expect(page.locator(SUCCESS)).toHaveCount(1);
    await expect(alerts(), "a success interrupted the reader").toHaveCount(0);
  });

  test("a toast is dismissible from the keyboard, with no pointer at all", async ({
    page,
  }) => {
    markRead = { status: 500, body: { detail: "boom" }, delayMs: 50 };
    await markAllRead(page);
    await expect(page.locator(ERROR)).toHaveCount(1);

    // F6 is the toast viewport's documented entry point (`ToastViewport.mjs`)
    // — it exists precisely so a toast is reachable without Tabbing through
    // the whole document. Tab from the viewport crosses the focus guard, which
    // lands on the first toast that can hold focus.
    await page.keyboard.press("F6");
    await page.keyboard.press("Tab");
    await page.waitForTimeout(100);
    expect(
      await page.evaluate(
        () => document.activeElement?.closest("[data-cc-toast]") !== null,
      ),
      "F6 then Tab did not reach the toast — it is unreachable from a keyboard",
    ).toBe(true);

    await page.keyboard.press("Escape");
    await expect(
      page.locator(TOAST),
      "Escape on a focused toast did not dismiss it",
    ).toHaveCount(0);
  });
});
