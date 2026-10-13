/**
 * The consent page of an outside agent (WS-55 EA-4a, spec §10.5).
 *
 * The BFF is mocked through `page.route`, as the spec says, so no gateway
 * runs. The agent's own callback (`http://localhost:53122/...`) is mocked
 * too, so a navigation there lands on a page this test can read.
 *
 * Each test is one acceptance line:
 * - the page renders what the member must see;
 * - narrowing works, and the approval sends the SAME request;
 * - approve navigates to `redirect_to`;
 * - deny navigates to `deny_redirect_to`;
 * - the frame headers are present.
 */
import { expect, test, type Page, type Request } from "@playwright/test";

const QUERY = new URLSearchParams({
  client_id: "https://claude.ai/oauth/claude-code-client-metadata",
  redirect_uri: "http://localhost:53122/callback",
  response_type: "code",
  code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
  code_challenge_method: "S256",
  resource: "https://mcp.metorite.com/mcp",
  scope: "projects:read projects:write offline_access",
  state: "st-123",
});
const PAGE = `/connect/authorize?${QUERY.toString()}`;

const CALLBACK = "http://localhost:53122/callback";
const APPROVED = `${CALLBACK}?code=the-code&state=st-123&iss=https%3A%2F%2Fmcp.metorite.com`;
const DENIED = `${CALLBACK}?error=access_denied&state=st-123&iss=https%3A%2F%2Fmcp.metorite.com`;

const VIEW = {
  client: {
    client_id: "https://claude.ai/oauth/claude-code-client-metadata",
    name: "Claude Code",
    origin: "https://claude.ai",
  },
  redirect_uri: "http://localhost:53122/callback",
  redirect_display: "http://localhost:53122",
  redirect_app: false,
  consent_ticket: "T".repeat(43),
  scopes: ["projects:read", "projects:write", "offline_access"],
  resource: "https://mcp.metorite.com/mcp",
  state: "st-123",
  issuer: "https://mcp.metorite.com",
  deny_redirect_to: DENIED,
};

const ME = {
  authenticated: true,
  email: "dev@fracktal.test",
  is_active: true,
  features: ["projects"],
  features_denied: [],
  permissions: [],
  capabilities: [],
  roles: ["member"],
  agents: [],
  denied: [],
  organization: { id: "org-1", slug: "fracktal", display_name: "Fracktal Works" },
};

const TREE = {
  rows: [
    { id: "p-eng", name: "Engineering", children: [{ id: "p-7", name: "Project Seven", children: [] }] },
    { id: "p-sales", name: "Sales", children: [] },
  ],
  total: 3,
};

async function stub(page: Page, view: object = VIEW) {
  const approvals: Request[] = [];
  await page.route("**/api/auth/me", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ME) }),
  );
  await page.route("**/api/auth/me/shell", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.route("**/api/projects/tree", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TREE) }),
  );
  await page.route("**/api/agent-access/authorize/check", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(view) }),
  );
  await page.route("**/api/agent-access/authorize/approve", (r) => {
    approvals.push(r.request());
    return r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ redirect_to: APPROVED }),
    });
  });
  // The agent's loopback callback. Playwright answers it, so the page lands.
  await page.route(`${CALLBACK}**`, (r) =>
    r.fulfill({ status: 200, contentType: "text/html", body: "<title>agent</title><p>The agent got it.</p>" }),
  );
  return approvals;
}

test.describe("the consent page of an outside agent", () => {
  test("renders the client, the verified origin, the return address, the organization and the scopes", async ({ page }) => {
    await stub(page);
    await page.goto(PAGE);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Claude Code wants to connect to Metorite");
    await expect(page.getByTestId("consent-origin")).toHaveText("https://claude.ai");
    await expect(page.getByTestId("consent-redirect")).toHaveText("http://localhost:53122");
    await expect(page.getByTestId("consent-org")).toHaveText("Fracktal Works");
    await expect(page.getByLabel("Read your projects and tasks")).toBeChecked();
    await expect(page.getByLabel("Change your projects and tasks")).toBeChecked();
    await expect(page.getByText("Stay connected")).toBeVisible();
    // offline_access has no box.
    await expect(page.getByRole("checkbox", { name: "Stay connected" })).toHaveCount(0);
    await expect(page.getByLabel("Include my personal projects")).not.toBeChecked();
    await expect(page.getByTestId("consent-pins").getByText("Project Seven")).toBeVisible();
    // No chrome: the page is standalone.
    await expect(page.locator("aside")).toHaveCount(0);
  });

  test("an app redirect says it is an app on this computer", async ({ page }) => {
    await stub(page, { ...VIEW, redirect_display: "com.example.app:", redirect_app: true });
    await page.goto(PAGE);
    await expect(page.getByTestId("consent-redirect")).toHaveText(/com\.example\.app:\s*an app on this computer/);
  });

  test("narrowing works, and the approval sends the same request with the choices", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await page.getByLabel("Change your projects and tasks").uncheck();
    await page.getByTestId("consent-pins").getByText("Project Seven").click();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    expect(approvals).toHaveLength(1);
    const body = approvals[0].postDataJSON();
    expect(body).toEqual({
      ...Object.fromEntries(QUERY.entries()),
      consent_ticket: VIEW.consent_ticket,
      scopes: ["projects:read", "offline_access"],
      pinned_projects: ["p-7"],
      include_personal: false,
    });
  });

  test("approve is off when every permission is cleared", async ({ page }) => {
    await stub(page);
    await page.goto(PAGE);
    await page.getByLabel("Read your projects and tasks").uncheck();
    await page.getByLabel("Change your projects and tasks").uncheck();
    await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  test("approve navigates to redirect_to", async ({ page }) => {
    await stub(page);
    await page.goto(PAGE);
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    await expect(page.getByText("The agent got it.")).toBeVisible();
  });

  test("deny navigates to deny_redirect_to and approves nothing", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await page.getByRole("button", { name: "Deny" }).click();
    await page.waitForURL(DENIED);
    expect(approvals).toHaveLength(0);
  });

  test("the page carries the frame headers", async ({ page }) => {
    await stub(page);
    const res = await page.goto(PAGE);
    expect(res).not.toBeNull();
    const headers = res!.headers();
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["x-frame-options"]).toBe("DENY");
  });
});
