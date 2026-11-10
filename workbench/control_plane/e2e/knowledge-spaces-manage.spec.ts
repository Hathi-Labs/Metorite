import { expect, test, type Page } from "@playwright/test";

import { stubApi } from "./visual/harness";

/**
 * Make a space and change it (WS-61 KB-2q, `knowledge_base.md` §4.1).
 *
 * The BFF at `/api/knowledge/**` is a mock that keeps state. A manager of
 * Knowledge makes "Sales playbooks" from the `+` of the Spaces heading, then
 * renames it and changes its icon in Space settings. The rail and the Spaces
 * cards show each change. A member without `knowledge:manage` sees neither
 * control, and the stand-in "My notes" has no Space settings.
 *
 * The real gateway half is `tests/unit/test_kb_pages_r8.py`.
 */

const COMPANY_ID = "ks-company";

function member(capabilities: string[]) {
  return {
    authenticated: true,
    email: "asha@example.com",
    name: "Asha Rao",
    is_admin: false,
    features: ["knowledge"],
    permissions: [],
    capabilities,
    roles: ["member"],
    organization: { id: "org1", slug: "acme", display_name: "Acme Works" },
  };
}

type KbSpace = Record<string, unknown> & { id: string; name: string };

/** A stateful mock of the space routes. Returns the log of each write. */
async function mockSpaces(page: Page, level: "read" | "manage") {
  const writes: Array<{ method: string; path: string; body: Record<string, unknown> }> = [];
  const now = new Date().toISOString();
  const spaces: KbSpace[] = [
    { id: COMPANY_ID, slug: "company", name: "Company", purpose: "How we do things.", icon: "", kind: "company",
      owner_email: null, archived_at: null, created_at: now, level, page_count: 0 },
  ];
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/knowledge/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^\/api\/knowledge\//, "");
    const method = req.method();
    const body = req.postData() ? JSON.parse(req.postData() as string) : {};
    if (method !== "GET") writes.push({ method, path, body });
    if (path === "spaces" && method === "GET") return route.fulfill(json(200, { spaces }));
    if (path === "spaces" && method === "POST") {
      const made: KbSpace = { id: `ks-${spaces.length + 1}`, slug: `team-${spaces.length}`, name: body.name,
        purpose: body.purpose ?? "", icon: body.icon ?? "", kind: "team", owner_email: "asha@example.com",
        archived_at: null, created_at: now, level: "manage", page_count: 0 };
      spaces.push(made);
      return route.fulfill(json(201, made));
    }
    const patch = /^spaces\/([^/]+)$/.exec(path);
    if (patch && method === "PATCH") {
      const found = spaces.find((s) => s.id === decodeURIComponent(patch[1]));
      if (!found) return route.fulfill(json(404, { detail: "Not found" }));
      Object.assign(found, body);
      return route.fulfill(json(200, found));
    }
    if (path === "pages") return route.fulfill(json(200, { pages: [] }));
    return route.fulfill(json(404, { detail: "Not found" }));
  });
  return { writes, spaces };
}

async function setup(page: Page, capabilities: string[]) {
  await page.addInitScript(() => {
    localStorage.setItem("cc-shell-bar", "1");
    localStorage.setItem("cc-shell-nav", "1");
  });
  await stubApi(page, { "auth/me": member(capabilities) });
}

test.describe("Knowledge spaces: make and change", () => {
  test("a manager makes Sales playbooks, renames it and changes its icon", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, ["knowledge:manage"]);
    const kb = await mockSpaces(page, "manage");
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });

    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible({ timeout: 30_000 });

    // Make the space from the `+` of the Spaces heading.
    await rail.getByRole("button", { name: "Spaces", exact: true }).hover();
    await rail.getByRole("button", { name: "New space" }).click();
    const dialog = page.getByRole("dialog", { name: "New space" });
    await expect(dialog.getByText("For a new topic, make a page.")).toBeVisible();
    await dialog.getByRole("textbox", { name: "Name" }).fill("Sales playbooks");
    await dialog.getByRole("textbox", { name: "Purpose (optional)" }).fill("How the sales team sells.");
    await dialog.getByRole("radio", { name: "Rocket" }).click();
    await dialog.getByRole("button", { name: "Make space" }).click();

    // The new space opens, and the rail shows it.
    await expect(page.getByRole("heading", { level: 2, name: "Sales playbooks" })).toBeVisible({ timeout: 15_000 });
    await expect(rail.getByRole("button", { name: "Sales playbooks" })).toBeVisible();
    expect(kb.writes[0]).toEqual({
      method: "POST",
      path: "spaces",
      body: { name: "Sales playbooks", icon: "Rocket", purpose: "How the sales team sells." },
    });

    // Space settings: rename it and change the icon.
    await page.getByRole("button", { name: "Space actions" }).click();
    await page.getByRole("menuitem", { name: "Space settings" }).click();
    const settings = page.getByRole("dialog", { name: "Space settings" });
    await expect(settings.getByRole("button", { name: "Save" })).toBeDisabled();
    await settings.getByRole("textbox", { name: "Name" }).fill("Sales handbook");
    await settings.getByRole("radio", { name: "Target" }).click();
    await settings.getByRole("button", { name: "Save" }).click();

    await expect(page.getByRole("heading", { level: 2, name: "Sales handbook" })).toBeVisible({ timeout: 15_000 });
    await expect(rail.getByRole("button", { name: "Sales handbook" })).toBeVisible();
    await expect(rail.getByRole("button", { name: "Sales playbooks" })).toHaveCount(0);
    expect(kb.writes[1]).toEqual({
      method: "PATCH",
      path: `spaces/${kb.spaces[1].id}`,
      body: { name: "Sales handbook", icon: "Target" },
    });

    // The Spaces cards show the new name too.
    await rail.getByRole("button", { name: "Spaces", exact: true }).click();
    await expect(page.getByRole("link", { name: /Sales handbook/ }).first()).toBeVisible();
    expect(kb.writes).toHaveLength(2);
  });

  test("a member without knowledge:manage sees neither control", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, []);
    await mockSpaces(page, "read");
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });

    await page.goto("/knowledge");
    await expect(page.getByRole("heading", { level: 1, name: "Knowledge" })).toBeVisible({ timeout: 30_000 });
    await expect(rail.getByText("Company")).toBeVisible();
    await expect(page.getByRole("button", { name: "New space" })).toHaveCount(0);

    await rail.getByRole("button", { name: /Company/ }).click();
    await page.getByRole("button", { name: "Space actions" }).click();
    await expect(page.getByRole("menuitem", { name: "Export space" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Space settings" })).toHaveCount(0);
  });

  test("the stand-in My notes has no Space settings", async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setup(page, ["knowledge:manage"]);
    const kb = await mockSpaces(page, "manage");
    const rail = page.getByRole("navigation", { name: "Knowledge spaces" });

    await page.goto("/knowledge");
    await rail.getByRole("button", { name: /My notes/ }).click();
    await expect(page.getByRole("heading", { level: 2, name: "My notes" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Space actions" })).toHaveCount(0);
    expect(kb.writes).toEqual([]);
  });
});
