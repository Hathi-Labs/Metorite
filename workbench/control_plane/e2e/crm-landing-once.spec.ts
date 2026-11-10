import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * A bare `/crm` lands ONCE for each mount of the page (WS-53 CRM-Q17b item
 * 3, fix round 1, P1).
 *
 * The URL grammar writes no `tab` for the Deals board, so the address of
 * Deals is a bare `/crm` too. Before the fix, the member's own click on Deals
 * gave `/crm`, and the landing sent the member back to Leads. A new read of
 * `GET /crm/mode` could do the same. Unit fence: `landingPlan` in
 * `src/app/crm/components/crmQ17b.test.ts`.
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

const CONNECTION = {
  connected: true,
  provider: "zoho",
  owning_app: "crm",
  status: "connected",
  location: "in",
  scopes: [],
  connected_by: "admin@example.com",
  last_sync_at: null,
  last_error: null,
  mode: "mirror",
};

const state = (records: boolean) => ({ records, turned_off: false });

async function setup(page: Page, modeReads: { count: number; leadsFirst: boolean }) {
  await stubApi(page, { "auth/me": ADMIN, health: { gateway: "up" } }, {
    arrayPaths: /^(notes|chat|agent|apps|agents)\b/,
  });
  await page.route("**/api/crm/**", (r) => {
    const url = new URL(r.request().url());
    const p = url.pathname.replace(/^\/api\/crm\//, "");
    let json: unknown = { rows: [], total: 0 };
    if (p === "connection") json = CONNECTION;
    else if (p === "settings") json = { mode: "mirror", access: "admins", access_groups: [] };
    else if (p === "mode") {
      modeReads.count += 1;
      // Leads first at the start. A later read names Contacts first, so a
      // second landing would move the member there.
      json = {
        mode: "mirror",
        entities: {
          leads: state(modeReads.leadsFirst),
          contacts: state(true),
          organizations: state(false),
          deals: state(false),
        },
      };
    } else if (p === "modules") json = [];
    else if (p === "sync/status") json = { state: "idle", counts: {}, failed: 0 };
    else if (p.startsWith("fields/")) json = url.searchParams.get("layout") ? { fields: [], sections: [] } : [];
    else if (/^(views|statuses\/|lost-reasons|pipelines)/.test(p)) json = [];
    else if (p === "leads") json = { rows: [{ id: "l1", lead_name: "Ravi Lead" }], total: 1 };
    else if (p === "contacts") json = { rows: [{ id: "c1", first_name: "Priya Contact" }], total: 1 };
    else if (p === "pipeline") json = { lanes: [], total: 0 };
    else if (p === "mapping/suggestions") json = { suggestions: [], overlays: [] };
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(json) });
  });
}

test("a bare /crm lands once: a click on Deals and a new mode read stay put", async ({ page }) => {
  const modeReads = { count: 0, leadsFirst: true };
  await setup(page, modeReads);

  await page.goto("/crm");
  await expect(page).toHaveURL(/\/crm\?tab=leads$/, { timeout: 30_000 });
  await expect(page.getByText("Ravi Lead")).toBeVisible({ timeout: 10_000 });

  // The member opens Deals. Its address is a bare /crm.
  const rail = page.getByRole("navigation", { name: "CRM views" });
  await rail.getByRole("button", { name: "Deals", exact: true }).click();
  await expect(page).toHaveURL(/\/crm$/, { timeout: 5_000 });
  await page.waitForTimeout(2_000);
  await expect(page).toHaveURL(/\/crm$/);

  // The mode is read again (a window focus revalidates each read), and the
  // new answer would land on Contacts. The member stays on Deals.
  modeReads.leadsFirst = false;
  // A read inside the fresh window of `dataCache.ts` (5 s) asks nothing.
  await page.waitForTimeout(6_000);
  const before = modeReads.count;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => modeReads.count, { timeout: 10_000 }).toBeGreaterThan(before);
  await page.waitForTimeout(2_000);
  await expect(page).toHaveURL(/\/crm$/);
  await expect(page.getByText("Priya Contact")).toHaveCount(0);
});
