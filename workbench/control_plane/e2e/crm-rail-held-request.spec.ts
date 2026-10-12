import { expect, test, type Page, type Route } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * The CRM rail changes the view while the server holds a request.
 *
 * The owner saw the rail stop answering clicks, twice (2026-10-11 and
 * 2026-10-12, `crm_platform.md` §18.6). A `router.push` to a new `?tab=`
 * asks the workbench for a React Server Components payload FIRST, and
 * changes the screen only when that payload arrives. Next.js gives the
 * request no timeout. So while Caddy or a restarting workbench holds it,
 * every click on the rail does nothing.
 *
 * This spec holds every RSC request for `/crm` and never answers it. It
 * then clicks the rail. Before the fix the address and the view stayed on
 * Reports. With `lib/history.ts` the view changes at once. Unit fence:
 * `src/app/crm/lib/history.test.ts`.
 */

const ADMIN = {
  authenticated: true,
  email: "admin@example.com",
  name: "Asha Rao",
  is_admin: true,
  features: ["crm"],
  permissions: [],
  capabilities: ["admin:access:manage"],
  roles: ["owner"],
  organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
};

const CRM: Record<string, unknown> = {
  "statuses/deal": [],
  "statuses/lead": [],
  "lost-reasons": [],
  pipelines: [],
  connection: null,
  settings: { mode: "native" },
};

async function setup(page: Page): Promise<Route[]> {
  await stubApi(page, { "auth/me": ADMIN, health: { gateway: "up" } }, {
    arrayPaths: /^(notes|chat|agent|apps|agents)\b/,
  });
  await page.route("**/api/crm/**", (r) => {
    const p = new URL(r.request().url()).pathname.replace(/^\/api\/crm\//, "");
    const json = p in CRM ? CRM[p] : /^(fields|views)\b/.test(p) ? [] : { rows: [], total: 0 };
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(json) });
  });
  // Every RSC request for the CRM page is held and never answered: the
  // shape of a workbench restart behind Caddy's 30 s hold, or a busy box.
  const held: Route[] = [];
  await page.route(/\/crm(\?|$)/, (route) => {
    const h = route.request().headers();
    if (h["rsc"] === "1") {
      // A prefetch is held too, but only a navigation counts below.
      if (!h["next-router-prefetch"]) held.push(route);
      return;
    }
    return route.continue();
  });
  return held;
}

test("a rail click changes the CRM view while the server holds the request", async ({ page }) => {
  const held = await setup(page);
  await page.goto("/crm?tab=contacts");
  const rail = page.getByRole("navigation", { name: "CRM views" });
  await expect(rail.getByRole("button", { name: "Leads", exact: true })).toBeVisible({ timeout: 30_000 });
  // The Reports line is the subtitle of one view only, so it says which
  // view is on screen.
  const reportsLine = page.getByText(/Forecast, funnel, win rate/);
  await expect(reportsLine).toHaveCount(0);

  await rail.getByRole("button", { name: "Reports", exact: true }).click();
  await expect(page).toHaveURL(/[?&]tab=reports\b/, { timeout: 3_000 });
  await expect(reportsLine).toBeVisible({ timeout: 3_000 });

  // A second click answers too. Before the fix it waited like the first.
  await rail.getByRole("button", { name: "Leads", exact: true }).click();
  await expect(page).toHaveURL(/[?&]tab=leads\b/, { timeout: 3_000 });
  await expect(reportsLine).toHaveCount(0);

  // Back returns to the previous view, as it did with the router.
  await page.goBack();
  await expect(page).toHaveURL(/[?&]tab=reports\b/, { timeout: 3_000 });
  await expect(reportsLine).toBeVisible({ timeout: 3_000 });

  // No view change needed the server.
  expect(held.length).toBe(0);
});
