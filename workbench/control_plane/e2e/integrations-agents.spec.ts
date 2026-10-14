/**
 * The AI agents view of /integrations (WS-55 EA-4b, D-EA-9, spec §5.4).
 *
 * The BFF is mocked through `page.route`, so no gateway runs. Each test is
 * one acceptance line of A10:
 * - the connect card renders, and Copy writes the address;
 * - the client tabs switch;
 * - Revoke asks first, sends the DELETE, and removes the row;
 * - an admin sees the org table, and a member does not.
 */
import { expect, test, type Page, type Request } from "@playwright/test";

import { stubApi } from "./visual/harness";

const MCP_URL = "https://mcp.metorite.com/mcp";

const BASE_ME = {
  authenticated: true,
  name: "Priya Rao",
  is_active: true,
  features_denied: [],
  roles: ["member"],
  agents: [],
  denied: [],
  organization: { id: "org-1", slug: "acme", display_name: "Acme Works" },
};

/** A member with no grant at all: the AI agents view is all they get. */
const MEMBER = {
  ...BASE_ME,
  email: "priya@acme.example",
  is_admin: false,
  features: [],
  permissions: [],
  capabilities: [],
};

const ADMIN = {
  ...BASE_ME,
  email: "asha@acme.example",
  name: "Asha Rao",
  is_admin: true,
  features: ["integrations"],
  permissions: ["admin:members:read", "integrations:manage"],
  capabilities: ["admin:members:read", "integrations:manage"],
  roles: ["owner"],
};

function connection(id: string, member: string, origin: string, name: string) {
  return {
    id,
    member_email: member,
    client_name: name,
    client_origin: origin,
    scopes: ["projects:read", "projects:write", "offline_access"],
    pinned_projects: id.endsWith("1") ? ["p-7"] : [],
    include_personal: false,
    created_at: "2026-10-14T09:00:00+00:00",
    last_used_at: "2026-10-14T10:00:00+00:00",
    expires_at: "2027-01-12T10:00:00+00:00",
  };
}

const MINE = [
  connection("11111111-0000-4000-8000-000000000001", "priya@acme.example", "https://claude.ai", "Claude Code"),
  connection("11111111-0000-4000-8000-000000000002", "priya@acme.example", "https://vscode.dev", "vscode.dev"),
];
const ORG = [
  ...MINE,
  connection("22222222-0000-4000-8000-000000000003", "ravi@acme.example", "https://claude.ai", "Claude Code"),
];

/**
 * Stub the shell and the four agent routes. A DELETE drops the row from both
 * lists. With `sticky`, the lists keep it, as a read that lags the revoke
 * would: then only the view itself can hide the row.
 */
async function setup(page: Page, me: typeof MEMBER, { sticky = false } = {}) {
  const mine = [...MINE];
  const org = [...ORG];
  const asked: Request[] = [];
  await stubApi(page, { "auth/me": me, health: { gateway: "up" } }, { arrayPaths: /^(notes|chat|agent\/|apps|agents)\b/ });
  await page.route("**/api/auth/me/shell", (r) => r.fulfill({ json: {} }));
  const list = (rows: typeof MINE) => ({ status: 200, contentType: "application/json", body: JSON.stringify({ connections: rows }) });
  await page.route("**/api/agent-access/connections**", (r) => {
    asked.push(r.request());
    if (r.request().method() === "DELETE") {
      const id = r.request().url().split("/").pop()!;
      for (const rows of sticky ? [] : [mine, org]) {
        const at = rows.findIndex((c) => c.id === id);
        if (at >= 0) rows.splice(at, 1);
      }
      return r.fulfill({ status: 204, body: "" });
    }
    return r.fulfill(list(mine));
  });
  await page.route("**/api/integrations/agent-connections**", (r) => {
    asked.push(r.request());
    if (r.request().method() === "DELETE") {
      const id = r.request().url().split("/").pop()!;
      for (const rows of sticky ? [] : [mine, org]) {
        const at = rows.findIndex((c) => c.id === id);
        if (at >= 0) rows.splice(at, 1);
      }
      return r.fulfill({ status: 204, body: "" });
    }
    return r.fulfill(list(org));
  });
  return asked;
}

test.describe("the AI agents view of Integrations (EA-4b)", () => {
  test("a member with no grant opens on the agents view, and the card renders", async ({ page }) => {
    await setup(page, MEMBER);
    await page.goto("/integrations");
    await expect(page).toHaveURL(/\/integrations\?view=agents$/);
    const card = page.getByTestId("agents-connect-card");
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading", { name: "Connect an AI agent" })).toBeVisible();
    await expect(page.getByTestId("agents-mcp-url")).toHaveText(MCP_URL);
    await expect(card.getByText("What an agent can do")).toBeVisible();
    await expect(card.getByText("What it cannot do")).toBeVisible();
    // The rail holds the agents view only.
    const rail = page.getByRole("navigation", { name: "Integrations views" });
    await expect(rail.getByRole("button", { name: "AI agents" })).toBeVisible();
    await expect(rail.getByRole("button", { name: "Connections" })).toHaveCount(0);
    await expect(rail.getByRole("button", { name: "API keys" })).toHaveCount(0);
  });

  test("Copy writes the address", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await setup(page, MEMBER);
    await page.goto("/integrations?view=agents");
    await page.getByRole("button", { name: "Copy the MCP server address" }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(MCP_URL);
  });

  test("the client tabs switch", async ({ page }) => {
    await setup(page, MEMBER);
    await page.goto("/integrations?view=agents");
    const panel = page.getByTestId("agents-client-panel");
    await expect(panel.getByText("Tested with 2.1.296")).toBeVisible();
    await expect(page.getByTestId("agents-client-command")).toHaveText(
      `claude mcp add --transport http metorite ${MCP_URL}`,
    );
    await page.getByRole("tab", { name: "Cursor" }).click();
    await expect(panel.getByText("Not supported yet")).toBeVisible();
    await expect(page.getByTestId("agents-client-command")).toHaveCount(0);
    await page.getByRole("tab", { name: "claude.ai / Claude Desktop" }).click();
    await expect(panel.getByText("Not tested yet")).toBeVisible();
    await expect(panel.getByText("Add custom connector", { exact: false })).toBeVisible();
  });

  test("Revoke asks first, then removes the row", async ({ page }) => {
    const asked = await setup(page, MEMBER, { sticky: true });
    await page.goto("/integrations?view=agents");
    const mine = page.getByTestId("agents-mine");
    await expect(mine.getByTestId("agent-row")).toHaveCount(2);
    const row = mine.locator(`[data-agent-id="${MINE[0].id}"]`);
    await expect(row.getByText("claude.ai", { exact: true })).toBeVisible();
    await expect(row.getByText("It calls itself “Claude Code”")).toBeVisible();
    await expect(row.getByText("Last token refresh")).toBeVisible();
    await expect(row.getByText("Pinned to 1 project")).toBeVisible();

    await row.getByRole("button", { name: /^Revoke/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("Revoke this agent?")).toBeVisible();
    // Cancel changes nothing.
    await dialog.locator("[data-cancel]").click();
    await expect(dialog).toHaveCount(0);
    expect(asked.filter((r) => r.method() === "DELETE")).toEqual([]);

    await row.getByRole("button", { name: /^Revoke/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Revoke", exact: true }).click();
    await expect(mine.locator(`[data-agent-id="${MINE[0].id}"]`)).toHaveCount(0);
    await expect(mine.getByTestId("agent-row")).toHaveCount(1);
    const deletes = asked.filter((r) => r.method() === "DELETE").map((r) => new URL(r.url()).pathname);
    expect(deletes).toEqual([`/api/agent-access/connections/${MINE[0].id}`]);
    await expect(page.getByText("Agent revoked")).toBeVisible();
  });

  test("a member does not see the org table, and never asks for it", async ({ page }) => {
    const asked = await setup(page, MEMBER);
    await page.goto("/integrations?view=agents");
    await expect(page.getByTestId("agents-mine").getByTestId("agent-row")).toHaveCount(2);
    await expect(page.getByTestId("agents-org")).toHaveCount(0);
    await expect(page.getByText("All agent connections")).toHaveCount(0);
    expect(asked.map((r) => new URL(r.url()).pathname)).not.toContain("/api/integrations/agent-connections");
  });

  test("an admin sees the org table, with each member, and revokes through it", async ({ page }) => {
    const asked = await setup(page, ADMIN);
    await page.goto("/integrations?view=agents");
    const org = page.getByTestId("agents-org");
    await expect(org.getByRole("heading", { name: "All agent connections" })).toBeVisible();
    await expect(org.getByTestId("agent-row")).toHaveCount(3);
    await expect(org.getByTestId("agent-row-member")).toHaveText([
      "priya@acme.example",
      "priya@acme.example",
      "ravi@acme.example",
    ]);
    // The org views stay in the admin's rail.
    const rail = page.getByRole("navigation", { name: "Integrations views" });
    await expect(rail.getByRole("button", { name: "Connections" })).toBeVisible();

    const ravi = org.locator(`[data-agent-id="${ORG[2].id}"]`);
    await ravi.getByRole("button", { name: /^Revoke/ }).click();
    await expect(page.getByRole("dialog").getByText("ravi@acme.example's agent loses access")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Revoke", exact: true }).click();
    await expect(org.getByTestId("agent-row")).toHaveCount(2);
    const deletes = asked.filter((r) => r.method() === "DELETE").map((r) => new URL(r.url()).pathname);
    expect(deletes).toEqual([`/api/integrations/agent-connections/${ORG[2].id}`]);
  });
});
