import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * An org-aware link (`?org=`) in a browser (MT-1k, `saas_multitenancy.md`
 * "Org-aware links", owner 2026-10-10).
 *
 * `OrgLinkGate` decides before it lets the page render, so the page does not
 * mount as the wrong account. Each test fails on the obvious wrong build:
 *   1. A link to another signed-in account's org: the cover shows, the page
 *      never mounts, one switch goes to THAT slot, and the reload lands on
 *      the same path without `org`, with the other parameters kept.
 *   2. A link to an org that no signed-in account holds: one notice, and no
 *      page. "Stay here" removes `org` and shows the page.
 *   3. A link to the active account's own org: `org` goes, and the page shows.
 * ⚠️ Test 3 does not count mounts. A cold load mounts the page twice today,
 * through the gate's Suspense fallback (HANDOFF H-302).
 * The rule is `src/lib/orgLink.test.ts`. The arms in node are
 * `src/components/OrgLinkGate.test.ts`. The page is /tasks.
 */

const ACTIVE_ORG = "11111111-1111-4111-8111-111111111111";
const OTHER_ORG = "22222222-2222-4222-8222-222222222222";
const STRANGER_ORG = "33333333-3333-4333-8333-333333333333";

const MEMBER = {
  authenticated: true,
  email: "vjvarada@hathilabs.com",
  is_admin: false,
  features: ["tasks", "email", "projects", "people", "chat"],
  permissions: [],
  roles: ["employee"],
  organization: { id: ACTIVE_ORG, slug: "hathi", display_name: "Hathi Labs LLP" },
};

const SESSION = {
  user: { email: "vjvarada@hathilabs.com", name: "Vijay Varada" },
  expires: "2099-01-01T00:00:00.000Z",
};

const ACCOUNTS = {
  enabled: true,
  active: { email: "vjvarada@hathilabs.com", name: "Vijay Varada", organization_id: ACTIVE_ORG },
  others: [
    { slot: 0, email: "ops@hathilabs.com", name: null, organization: "Hathi Labs LLP", organization_id: ACTIVE_ORG },
    { slot: 2, email: "vjvarada@fracktal.in", name: "Vijay Raghav Varada", organization: "Fracktal Works", organization_id: OTHER_ORG },
  ],
};

/** One router, on purpose (`org-branding.spec.ts` says why). */
async function stub(page: Page, calls: string[] = []) {
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me") return json(MEMBER);
    if (path === "/api/auth/session") return json(SESSION);
    if (path === "/api/health") return json({ gateway: "up" });
    if (path === "/api/accounts") return json(ACCOUNTS);
    if (path.startsWith("/api/accounts/")) {
      calls.push(`${path} ${req.postData() ?? ""}`);
      return json({ ok: true, email: "vjvarada@fracktal.in" });
    }
    if (path === "/api/auth/me/shell") {
      return json({ preset: null, answered: "skipped", pins: null, known: null, cardOrder: null, newOrder: null });
    }
    if (path === "/api/settings/branding") return json({ logo: null, updatedBy: "", updatedAt: "" });
    if (path === "/api/shell/needs") return json({ count: 0, total: 0, items: [], sources: {} });
    return json([]);
  });
}

/** The shell flags, as production runs them, and a count of page mounts. */
async function boot(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
    const w = window as unknown as { __pageMounts: number };
    w.__pageMounts = 0;
    new MutationObserver((ms) => {
      for (const m of ms)
        for (const n of Array.from(m.addedNodes)) {
          if (!(n instanceof Element)) continue;
          w.__pageMounts += n.matches("[data-app-bar]") ? 1 : n.querySelectorAll("[data-app-bar]").length;
        }
    }).observe(document, { childList: true, subtree: true });
  });
}

const mounts = (page: Page) => page.evaluate(() => (window as unknown as { __pageMounts: number }).__pageMounts);
const PATH = "/tasks";
const appBar = (page: Page) => page.locator("main [data-app-bar]");
const notice = (page: Page) => page.getByRole("heading", { name: /This link is for a Metorite workspace you.re not signed in to/ });

test.use({ viewport: { width: 1440, height: 900 } });

test("a link to another signed-in account's org: the cover, no page, then the switch to that slot", async ({ page }) => {
  const calls: string[] = [];
  await boot(page);
  await stub(page, calls);
  let release = () => {};
  const held = new Promise<void>((r) => (release = r));
  // Registered last, so it wins over the stub's route. It holds the switch,
  // so the test can read the page while the cover is up.
  await page.route("**/api/accounts/switch", async (r) => {
    calls.push(`/api/accounts/switch ${r.request().postData() ?? ""}`);
    await held;
    await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, email: "vjvarada@fracktal.in" }) });
  });
  await page.goto(`${PATH}?tab=x&org=${OTHER_ORG}`);
  await expect(page.locator("[data-switching-cover]")).toBeVisible();
  await expect.poll(() => calls.length).toBe(1);
  // The page never mounted as the active account.
  expect(await mounts(page)).toBe(0);
  await expect(appBar(page)).toHaveCount(0);
  release();
  await page.waitForURL((u) => u.pathname === PATH && !u.searchParams.has("org"));
  expect(new URL(page.url()).searchParams.get("tab")).toBe("x");
  expect(calls).toEqual(['/api/accounts/switch {"slot":2}']);
  await expect(appBar(page)).toHaveCount(1);
});

test("a link to an org no signed-in account holds: one notice, no page, and Stay here shows it", async ({ page }) => {
  const calls: string[] = [];
  await boot(page);
  await stub(page, calls);
  await page.goto(`${PATH}?org=${STRANGER_ORG}`);
  await expect(notice(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in to that account" })).toBeVisible();
  expect(await mounts(page)).toBe(0);
  await expect(appBar(page)).toHaveCount(0);
  expect(calls).toEqual([]);
  await page.getByRole("button", { name: "Stay here" }).click();
  await expect(appBar(page)).toHaveCount(1);
  expect(new URL(page.url()).searchParams.has("org")).toBe(false);
  await expect(notice(page)).toHaveCount(0);
});

test("a link to the active account's own org: the page, without org", async ({ page }) => {
  const calls: string[] = [];
  await boot(page);
  await stub(page, calls);
  await page.goto(`${PATH}?org=${ACTIVE_ORG}`);
  await expect(appBar(page)).toHaveCount(1);
  await expect.poll(() => new URL(page.url()).searchParams.has("org")).toBe(false);
  await expect(notice(page)).toHaveCount(0);
  await expect(page.locator("[data-switching-cover]")).toHaveCount(0);
  expect(calls).toEqual([]);
});
