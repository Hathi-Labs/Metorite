/**
 * Change access in the AI agents view (WS-55 EA-17c-2, D-EA-13, spec §10.8).
 *
 * Three tests:
 * - the member changes a grant, and the list reads it back (the BFF is stubbed);
 * - the panel never sends `pinned_projects: []` after a pick, and keeps Save off;
 * - ⚠️ a REAL save reaches the REAL BFF route and does not get 411. The body goes
 *   out as one string, so the browser declares a `Content-Length`. Nothing
 *   stubs this PATCH, so a 411 from `relayChange` would show here. The status
 *   can be 401 or 502 (no gateway in this rig), and never 411, 403 or 400.
 */
import { expect, test, type Page, type Request } from "@playwright/test";

import { stubApi } from "./visual/harness";

const ME = {
  authenticated: true,
  name: "Priya Rao",
  email: "priya@acme.example",
  is_active: true,
  is_admin: false,
  features: [],
  features_denied: [],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  agents: [],
  denied: [],
  organization: { id: "org-1", slug: "acme", display_name: "Acme Works" },
};

const ID = "11111111-0000-4000-8000-000000000001";

const TREE = {
  rows: [
    {
      id: "a-1",
      name: "Alpha",
      children: [
        { id: "a-2", name: "Alpha one" },
        { id: "a-3", name: "Alpha two" },
      ],
    },
    { id: "g-1", name: "Gamma" },
  ],
  personal: [],
};

function agent(over: Record<string, unknown> = {}) {
  return {
    id: ID,
    member_email: "priya@acme.example",
    client_name: "Claude Code",
    client_origin: "https://claude.ai",
    scopes: ["projects:read", "offline_access"],
    pinned_projects: ["g-1"],
    left_out_projects: [],
    include_personal: false,
    requested_scopes: ["projects:read", "projects:write"],
    ceiling_scopes: ["projects:read"],
    created_at: "2026-10-14T09:00:00+00:00",
    last_used_at: null,
    expires_at: "2027-01-12T10:00:00+00:00",
    credential: "oauth",
    last_seen_addr: null,
    addr_changed_at: null,
    ...over,
  };
}

/** The shell, the project tree and the member's list. `row` is the one stored row. */
async function setup(page: Page, { stubPatch }: { stubPatch: boolean }) {
  let row = agent();
  const patches: Request[] = [];
  await stubApi(page, { "auth/me": ME, health: { gateway: "up" } }, { arrayPaths: /^(notes|chat|agent\/|apps|agents)\b/ });
  await page.route("**/api/auth/me/shell", (r) => r.fulfill({ json: {} }));
  await page.route("**/api/auth/me/agent-access", (r) => r.fulfill({ json: { delete_enabled: true } }));
  await page.route("**/api/projects/tree", (r) => r.fulfill({ json: TREE }));
  await page.route("**/api/integrations/agent-connections**", (r) => r.fulfill({ json: { connections: [] } }));
  await page.route("**/api/agent-access/requests**", (r) => r.fulfill({ json: { requests: [] } }));
  await page.route("**/api/agent-access/connections**", (r) => {
    const req = r.request();
    if (req.method() === "PATCH") {
      patches.push(req);
      if (!stubPatch) return r.continue();
      const body = JSON.parse(req.postData() ?? "{}");
      row = agent({
        scopes: body.scopes,
        pinned_projects: body.pinned_projects,
        left_out_projects: body.left_out_projects,
        include_personal: body.include_personal,
      });
      return r.fulfill({ json: row });
    }
    return r.fulfill({ json: { connections: [row], change_access: true } });
  });
  return patches;
}

test.describe("Change access (EA-17c-2)", () => {
  test("a member changes a grant and reads it back", async ({ page }) => {
    const patches = await setup(page, { stubPatch: true });
    await page.goto("/integrations?view=agents");
    const card = page.locator(`[data-agent-id="${ID}"]`);
    await expect(card.getByTestId("chip-projects")).toHaveText("1 project");

    await card.getByRole("button", { name: /^Change access/ }).click();
    const panel = card.getByTestId("change-access-panel");
    await expect(panel).toBeVisible();
    // The three states: read is on, write asks for a reconnect, context was never asked.
    await expect(panel.getByTestId("scope-projects:read")).toHaveAttribute("data-state", "toggle");
    await expect(panel.getByTestId("scope-projects:write")).toHaveAttribute("data-state", "reconnect");
    await expect(panel.getByText("Reconnect claude.ai to turn this on.")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Save changes" })).toBeDisabled();

    // Widen the pick: tick Alpha, which ticks its two subprojects.
    await panel.getByRole("checkbox", { name: "Alpha", exact: true }).check();
    await expect(panel.getByTestId("change-summary")).toContainText("4 projects");
    await panel.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByText("Access changed")).toBeVisible();
    await expect(panel).toHaveCount(0);
    // Read back: the list answers with the stored grant.
    await expect(card.getByTestId("chip-projects")).toHaveText("2 projects");
    expect(patches).toHaveLength(1);
    const sent = JSON.parse(patches[0].postData() ?? "{}");
    expect(sent.pinned_projects).toEqual(["a-1", "g-1"]);
    expect(sent.left_out_projects).toEqual([]);
    expect(sent.scopes).toEqual(["projects:read", "offline_access"]);
    expect(sent.include_personal).toBe(false);
  });

  test("never sends an empty pin list after a pick", async ({ page }) => {
    const patches = await setup(page, { stubPatch: true });
    await page.goto("/integrations?view=agents");
    const card = page.locator(`[data-agent-id="${ID}"]`);
    await card.getByRole("button", { name: /^Change access/ }).click();
    const panel = card.getByTestId("change-access-panel");

    // Drop the last pin.
    await panel.getByRole("checkbox", { name: "Gamma", exact: true }).uncheck();
    await expect(panel.getByTestId("change-problem")).toContainText("Pick one project or more");
    await expect(panel.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(patches).toHaveLength(0);

    // "All my projects" is the one way to send [].
    await panel.getByRole("tab", { name: "All my projects" }).click();
    await panel.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Access changed")).toBeVisible();
    expect(JSON.parse(patches[0].postData() ?? "{}").pinned_projects).toEqual([]);
  });

  test("a real save does not get 411 from the BFF", async ({ page }) => {
    const patches = await setup(page, { stubPatch: false });
    await page.goto("/integrations?view=agents");
    const card = page.locator(`[data-agent-id="${ID}"]`);
    await card.getByRole("button", { name: /^Change access/ }).click();
    const panel = card.getByTestId("change-access-panel");
    await panel.getByRole("checkbox", { name: "Alpha", exact: true }).check();

    const answer = page.waitForResponse((res) => res.request().method() === "PATCH");
    await panel.getByRole("button", { name: "Save changes" }).click();
    const res = await answer;
    // The request declared its length, so the relay read the body. A body
    // with no length gives 411 `length_required` before anything else runs.
    expect(patches).toHaveLength(1);
    expect([411, 403, 400, 413]).not.toContain(res.status());
    const text = await res.text();
    expect(text).not.toContain("length_required");
  });
});
