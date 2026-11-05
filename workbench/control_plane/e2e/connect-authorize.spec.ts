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
 * - the frame headers are present;
 * - D-EA-11: only the gateway's exact `first_party` answer, to an `https`
 *   address, goes on with no form. A client off the list that calls itself
 *   "Aroh" gets the screen and the approve gate.
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
/** D-EA-11: the https callback of a first-party client, as Aroh's. */
const FIRST_PARTY_CALLBACK = "https://aroh.example/hub/auth/callback";
const FIRST_PARTY_DONE = `${FIRST_PARTY_CALLBACK}?code=fp-code&state=st-123&iss=https%3A%2F%2Fmcp.metorite.com`;

const VIEW = {
  client: {
    client_id: "https://claude.ai/oauth/claude-code-client-metadata",
    host: "claude.ai",
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
    {
      id: "p-eng",
      name: "Engineering",
      children: [
        {
          id: "p-7",
          name: "Project Seven",
          children: [{ id: "p-7a", name: "Seven A", children: [] }],
        },
        { id: "p-8", name: "Project Eight", children: [] },
      ],
    },
    { id: "p-sales", name: "Sales", children: [] },
  ],
  // The own personal roots of the member. A pinned connection reaches them only by a pin.
  personal: [{ id: "p-me", name: "My space", children: [] }],
  total: 5,
};

const scopeSwitch = (page: Page, name: string) => page.getByRole("switch", { name });
const pickMode = (page: Page) => page.getByRole("tab", { name: "Only the ones I pick" }).click();

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
    // The headline names the VERIFIED host. The name is a claim, shown second.
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Connect claude.ai to Metorite?");
    await expect(page.getByTestId("consent-claim")).toHaveText("It calls itself: “Claude Code”");
    await expect(page.getByTestId("consent-origin")).toHaveText("claude.ai verified");
    await expect(page.getByTestId("consent-redirect")).toHaveText("Returns to http://localhost:53122");
    await expect(page.getByTestId("consent-org")).toHaveText("Org Fracktal Works");
    await expect(scopeSwitch(page, "Read your projects and tasks")).toBeChecked();
    await expect(scopeSwitch(page, "Change your projects and tasks")).toBeChecked();
    // offline_access has no switch. The summary line says it stays connected.
    await expect(page.getByRole("switch", { name: "Stay connected" })).toHaveCount(0);
    // On by default (owner 2026-10-30, D-EA-4 as amended).
    await expect(scopeSwitch(page, "My personal projects and notes")).toBeChecked();
    // "All my projects" is the default, and the tree waits behind the other choice.
    await expect(page.getByRole("tab", { name: "All my projects" })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByTestId("access-tree")).toHaveCount(0);
    // No chrome: the page is standalone.
    await expect(page.locator("aside")).toHaveCount(0);
  });

  test("an app redirect says it is an app on this computer", async ({ page }) => {
    await stub(page, { ...VIEW, redirect_display: "com.example.app:", redirect_app: true });
    await page.goto(PAGE);
    await expect(page.getByTestId("consent-redirect")).toHaveText(/com\.example\.app:\s*\W*\s*an app on this computer/);
  });

  test("narrowing works, and the approval sends the same request with the choices", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await scopeSwitch(page, "Change your projects and tasks").click();
    await pickMode(page);
    await page.getByRole("button", { name: "Unfold Engineering" }).click();
    await page.getByRole("checkbox", { name: "Project Eight" }).check();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    expect(approvals).toHaveLength(1);
    const body = approvals[0].postDataJSON();
    expect(body).toEqual({
      ...Object.fromEntries(QUERY.entries()),
      consent_ticket: VIEW.consent_ticket,
      scopes: ["projects:read", "offline_access"],
      pinned_projects: ["p-8", "p-me"],
      left_out_projects: [],
      include_personal: true,
    });
  });

  // EA-17b (spec §10.8, D-EA-12): the tree ticks down, and an untick leaves out.
  test("a tick on a parent ticks every child, and an untick of a child leaves it out", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await pickMode(page);
    const eng = page.getByRole("checkbox", { name: "Engineering", exact: true });
    await eng.check();
    await page.getByRole("button", { name: "Unfold Engineering" }).click();
    const seven = page.getByRole("checkbox", { name: "Project Seven" });
    const eight = page.getByRole("checkbox", { name: "Project Eight" });
    // The cascade.
    await expect(seven).toBeChecked();
    await expect(eight).toBeChecked();
    await expect(page.getByTestId("access-tree-count")).toHaveText("4 of 5 projects");
    // The summary line follows.
    await expect(page.getByTestId("consent-summary")).toHaveText(
      "claude.ai will read and change tasks in 4 projects and the personal projects you have now. It stays connected until you remove it.",
    );
    // The untick: the child shows its tag, and the parent shows the dash and the count.
    await eight.uncheck();
    await expect(page.getByTestId("tree-row-Project Eight").getByText("left out")).toBeVisible();
    await expect(eng).toHaveJSProperty("indeterminate", true);
    await expect(page.getByTestId("tree-row-Engineering").getByTestId("tree-count")).toHaveText("3 of 4");
    await expect(page.getByTestId("access-tree-count")).toHaveText("3 of 5 projects");
    await expect(page.getByTestId("consent-summary")).toContainText("in 3 projects");
    // A click on the dash ticks everything again.
    await eng.check();
    await expect(eight).toBeChecked();
    await eight.uncheck();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    const body = approvals[0].postDataJSON();
    expect(body.pinned_projects).toEqual(["p-eng", "p-me"]);
    expect(body.left_out_projects).toEqual(["p-8"]);
  });

  test("a pin under a left-out parent is sent too", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await pickMode(page);
    await page.getByRole("checkbox", { name: "Engineering", exact: true }).check();
    await page.getByRole("button", { name: "Unfold Engineering" }).click();
    await page.getByRole("button", { name: "Unfold Project Seven" }).click();
    await page.getByRole("checkbox", { name: "Project Seven" }).uncheck();
    await page.getByRole("checkbox", { name: "Seven A" }).check();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    const body = approvals[0].postDataJSON();
    expect(body.pinned_projects).toEqual(["p-eng", "p-7a", "p-me"]);
    expect(body.left_out_projects).toEqual(["p-7"]);
  });

  test("Only the ones I pick with nothing ticked blocks Approve, and All my projects sends no list", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    const approve = page.getByRole("button", { name: "Approve" });
    await expect(approve).toBeEnabled();
    await pickMode(page);
    await expect(approve).toBeDisabled();
    await expect(page.getByTestId("consent-project-issue")).toContainText("Pick one project");
    await page.getByRole("checkbox", { name: "Sales" }).check();
    await expect(approve).toBeEnabled();
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(approve).toBeDisabled();
    expect(approvals).toHaveLength(0);
    // Back to All my projects: Approve works, and no list goes with it.
    await page.getByRole("checkbox", { name: "Sales" }).check();
    await page.getByRole("tab", { name: "All my projects" }).click();
    await approve.click();
    await page.waitForURL(APPROVED);
    const body = approvals[0].postDataJSON();
    expect(body.pinned_projects).toEqual([]);
    expect(body.left_out_projects).toEqual([]);
  });

  test("a list of more than 12 projects shows the search, and a match shows its parents", async ({ page }) => {
    await stub(page);
    const many = {
      rows: [
        { id: "p-eng", name: "Engineering", children: [{ id: "p-needle", name: "Needle", children: [] }] },
        ...Array.from({ length: 12 }, (_, i) => ({ id: `p-x${i}`, name: `Filler ${i}`, children: [] })),
      ],
    };
    await page.route("**/api/projects/tree", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(many) }),
    );
    await page.goto(PAGE);
    await pickMode(page);
    await page.getByRole("searchbox", { name: "Find a project" }).fill("needle");
    await expect(page.getByTestId("tree-row-Needle")).toBeVisible();
    await expect(page.getByTestId("tree-row-Engineering")).toBeVisible();
    await expect(page.getByTestId("tree-row-Filler 3")).toHaveCount(0);
  });

  test("in pick mode the personal switch pins the personal roots, and off pins none", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await pickMode(page);
    await page.getByRole("checkbox", { name: "Sales" }).check();
    await expect(page.getByTestId("consent-summary")).toContainText("the personal projects you have now");
    await scopeSwitch(page, "My personal projects and notes").click();
    await expect(page.getByTestId("consent-summary")).not.toContainText("personal");
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    const body = approvals[0].postDataJSON();
    expect(body.pinned_projects).toEqual(["p-sales"]);
    expect(body.include_personal).toBe(false);
  });

  test("an archived subproject shows its tag and takes a left-out mark", async ({ page }) => {
    const approvals = await stub(page);
    const tree = {
      rows: [{ id: "p-eng", name: "Engineering", children: [{ id: "p-old", name: "Old Work", archived_at: "2026-01-01", children: [] }] }],
      personal: [],
    };
    await page.route("**/api/projects/tree", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(tree) }),
    );
    await page.goto(PAGE);
    await pickMode(page);
    await page.getByRole("checkbox", { name: "Engineering", exact: true }).check();
    await page.getByRole("button", { name: "Unfold Engineering" }).click();
    await expect(page.getByTestId("tree-row-Old Work").getByText("archived")).toBeVisible();
    await page.getByRole("checkbox", { name: "Old Work" }).uncheck();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    expect(approvals[0].postDataJSON().left_out_projects).toEqual(["p-old"]);
  });

  test("a cut list says so, and never shows a parent without its children", async ({ page }) => {
    await stub(page);
    const kid = (i: number, r: string) => ({ id: `k-${r}-${i}`, name: `Kid ${r} ${i}`, children: [] });
    const tree = {
      rows: [
        { id: "r-a", name: "Root A", children: Array.from({ length: 300 }, (_, i) => kid(i, "a")) },
        { id: "r-b", name: "Root B", children: Array.from({ length: 300 }, (_, i) => kid(i, "b")) },
      ],
      personal: [],
    };
    await page.route("**/api/projects/tree", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(tree) }),
    );
    await page.goto(PAGE);
    await pickMode(page);
    await expect(page.getByTestId("access-tree-cut")).toContainText("301 of your 602 projects");
    await expect(page.getByTestId("tree-row-Root A")).toBeVisible();
    await expect(page.getByTestId("tree-row-Root B")).toHaveCount(0);
  });

  test("unticking personal access sends it as false", async ({ page }) => {
    const approvals = await stub(page);
    await page.goto(PAGE);
    await scopeSwitch(page, "My personal projects and notes").click();
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    expect(approvals).toHaveLength(1);
    expect(approvals[0].postDataJSON().include_personal).toBe(false);
  });

  test("approve is off when every permission is cleared", async ({ page }) => {
    await stub(page);
    await page.goto(PAGE);
    await scopeSwitch(page, "Read your projects and tasks").click();
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

  test("a claimed name never takes the headline", async ({ page }) => {
    await stub(page, { ...VIEW, client: { ...VIEW.client, name: "Metorite" } });
    await page.goto(PAGE);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Connect claude.ai to Metorite?");
    await expect(page.getByTestId("consent-claim")).toHaveText("It calls itself: “Metorite”");
  });

  test("with no claimed name, the page shows no claim line", async ({ page }) => {
    await stub(page, { ...VIEW, client: { ...VIEW.client, name: null } });
    await page.goto(PAGE);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Connect claude.ai to Metorite?");
    await expect(page.getByTestId("consent-claim")).toHaveCount(0);
  });

  test("Approve is off at once, and on after the 500 ms gate (double-clickjacking)", async ({ page }) => {
    const approvals = await stub(page);
    await page.clock.install();
    await page.goto(PAGE);
    const approve = page.getByRole("button", { name: "Approve" });
    await expect(page.getByTestId("consent-org")).toHaveText("Org Fracktal Works");
    await expect(approve).toBeDisabled();
    // A click that arrives at once does nothing.
    await approve.click({ force: true });
    expect(approvals).toHaveLength(0);
    await page.clock.runFor(600);
    await expect(approve).toBeEnabled();
    // A blur closes the gate again, and a new spell waits once more.
    await page.evaluate(() => window.dispatchEvent(new Event("blur")));
    await expect(approve).toBeDisabled();
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(approve).toBeDisabled();
    await page.clock.runFor(600);
    await expect(approve).toBeEnabled();
  });

  test("after a 503, the retry asks check for a fresh ticket first", async ({ page }) => {
    const approvals = await stub(page);
    const checks: number[] = [];
    let answer = 503;
    await page.route("**/api/agent-access/authorize/check", (r) => {
      checks.push(checks.length);
      const ticket = checks.length === 1 ? VIEW.consent_ticket : "F".repeat(43);
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...VIEW, consent_ticket: ticket }),
      });
    });
    await page.route("**/api/agent-access/authorize/approve", (r) => {
      approvals.push(r.request());
      const status = answer;
      answer = 200;
      return r.fulfill(
        status === 503
          ? { status: 503, contentType: "application/json", headers: { "retry-after": "2" }, body: "{}" }
          : { status: 200, contentType: "application/json", body: JSON.stringify({ redirect_to: APPROVED }) },
      );
    });
    await page.goto(PAGE);
    await page.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Metorite is busy. Try again in a moment.")).toBeVisible();
    expect(checks).toHaveLength(1);
    await page.getByRole("button", { name: "Approve" }).click();
    await page.waitForURL(APPROVED);
    expect(checks).toHaveLength(2);
    expect(approvals.map((a) => a.postDataJSON().consent_ticket)).toEqual([VIEW.consent_ticket, "F".repeat(43)]);
  });

  test("a lost approve answer ends the flow, and no second approve goes out", async ({ page }) => {
    const approvals: Request[] = [];
    await stub(page);
    await page.route("**/api/agent-access/authorize/approve", (r) => {
      approvals.push(r.request());
      // The gateway may have made the connection. The browser never hears.
      return r.abort("connectionreset");
    });
    await page.goto(PAGE);
    await page.getByRole("button", { name: "Approve" }).click();
    await expect(
      page.getByText(
        "We could not confirm the answer. Start again from the agent, and check your connections if it asks twice.",
      ),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await page.waitForTimeout(300);
    expect(approvals).toHaveLength(1);
  });

  test("an approve answer that is not JSON ends the flow too", async ({ page }) => {
    const approvals: Request[] = [];
    await stub(page);
    await page.route("**/api/agent-access/authorize/approve", (r) => {
      approvals.push(r.request());
      return r.fulfill({ status: 503, contentType: "text/html", body: "<html>gateway</html>" });
    });
    await page.goto(PAGE);
    await page.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("We could not confirm the answer.", { exact: false })).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
    expect(approvals).toHaveLength(1);
  });

  test("a busy fresh check allows one more try, and sends no approve", async ({ page }) => {
    const approvals: Request[] = [];
    let checks = 0;
    let approveAnswers = [503, 200];
    await stub(page);
    await page.route("**/api/agent-access/authorize/check", (r) => {
      checks += 1;
      if (checks === 2) {
        return r.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      }
      const ticket = checks === 1 ? VIEW.consent_ticket : "G".repeat(43);
      return r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...VIEW, consent_ticket: ticket }),
      });
    });
    await page.route("**/api/agent-access/authorize/approve", (r) => {
      approvals.push(r.request());
      const [status, ...rest] = approveAnswers;
      approveAnswers = rest;
      return r.fulfill(
        status === 503
          ? { status: 503, contentType: "application/json", body: "{}" }
          : { status: 200, contentType: "application/json", body: JSON.stringify({ redirect_to: APPROVED }) },
      );
    });
    await page.goto(PAGE);
    const approve = page.getByRole("button", { name: "Approve" });
    await approve.click();
    await expect(page.getByText("Metorite is busy. Try again in a moment.")).toBeVisible();
    await approve.click();
    await expect.poll(() => checks).toBe(2);
    await expect(approve).toBeEnabled();
    expect(approvals).toHaveLength(1);
    await approve.click();
    await page.waitForURL(APPROVED);
    expect(checks).toBe(3);
    expect(approvals.map((x) => x.postDataJSON().consent_ticket)).toEqual([VIEW.consent_ticket, "G".repeat(43)]);
  });

  test("the page carries the frame headers", async ({ page }) => {
    await stub(page);
    const res = await page.goto(PAGE);
    expect(res).not.toBeNull();
    const headers = res!.headers();
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["x-frame-options"]).toBe("DENY");
  });

  // D-EA-11 (spec §10.7): a first-party client that the member already
  // approved. The gateway answers `first_party` with a code, and the page goes
  // on with no form. Only that exact answer skips the screen.
  test("a first-party answer goes on with no form and no approve", async ({ page }) => {
    const approvals = await stub(page, { first_party: true, redirect_to: FIRST_PARTY_DONE });
    await page.route(`${FIRST_PARTY_CALLBACK}**`, (r) =>
      r.fulfill({ status: 200, contentType: "text/html", body: "<title>aroh</title><p>Aroh got it.</p>" }),
    );
    await page.goto(PAGE);
    await page.waitForURL(FIRST_PARTY_DONE);
    await expect(page.getByText("Aroh got it.")).toBeVisible();
    expect(approvals).toHaveLength(0);
  });

  test("a client off the list that calls itself Aroh gets the screen and the gate", async ({ page }) => {
    const approvals = await stub(page, { ...VIEW, client: { ...VIEW.client, name: "Aroh" } });
    await page.clock.install();
    await page.goto(PAGE);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Connect claude.ai to Metorite?");
    const approve = page.getByRole("button", { name: "Approve" });
    await expect(approve).toBeDisabled();
    await approve.click({ force: true });
    expect(approvals).toHaveLength(0);
    await page.clock.runFor(600);
    await expect(approve).toBeEnabled();
  });

  test("a first-party answer to an address that is not https draws no pass", async ({ page }) => {
    const approvals = await stub(page, { first_party: true, redirect_to: `${CALLBACK}?code=c&state=st-123` });
    await page.goto(PAGE);
    await expect(page.getByText("This agent cannot connect")).toBeVisible();
    expect(page.url()).toContain("/connect/authorize");
    expect(approvals).toHaveLength(0);
  });
});
