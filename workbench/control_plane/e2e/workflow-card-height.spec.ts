/**
 * A node card of the workflow editor fits the pitch of a saved workflow
 * (spec `ai_studio_rows/E7c-1.md`, task 3342).
 *
 * A saved workflow can hold two nodes 160 px apart, and a saved position does
 * not change. So each card must be shorter than that, with a visible gap, at
 * each density. A static test cannot see this: the height comes from the line
 * boxes, the root font size and the look. So a real browser measures it.
 *
 * Each node here is the worst case of its type: a long title, a long summary, a
 * long detail and a long badge.
 *
 * Mutations this file catches (R7):
 * - the summary takes two lines on each card -> the agent card is too tall;
 * - the badge row wraps again -> the agent and tool cards are too tall.
 */
import { expect, test } from "@playwright/test";

/** The pitch that a saved workflow can hold. The editor placed nodes at it. */
const SAVED_PITCH = 160;
/** The least space between two cards. A handle sits 6 px outside each card. */
const MIN_GAP = 8;

/** The three densities of Settings, Appearance. */
const DENSITIES = { compact: "0.875", default: "1", comfortable: "1.125" };

const LONG =
  "Read the incoming message, classify the request, draft a short reply for the account owner and list the open follow-up items.";

const CASES: [string, Record<string, unknown>][] = [
  ["trigger", {}],
  ["agent", { agent: "inbox_triage_assistant", message: LONG, model: "openrouter/anthropic/claude-sonnet-long-name" }],
  ["tool", { action: "gmail.send" }],
  ["module", { module_id: "m1" }],
  ["condition", { left: "{{agent.category_of_the_request}}", op: "==", right: "urgent_escalation" }],
  ["set", { assignments: { alpha_value: 1, beta_value: 2, gamma_value: 3 } }],
  ["approval", { message: LONG }],
  ["wait", { seconds: 86400 }],
  ["output", { value: "{{agent.reply}} and {{tool.id}} for the account owner" }],
  ["human_step", { title: LONG, assignee: "a.very.long.person.name@example-company.com" }],
  ["studio_write", { action: "crm.contacts.create_record_with_a_long_name" }],
  ["pm_task", {}],
  ["pm_lifecycle", {}],
];

const WORKFLOW = {
  id: "wf1",
  name: "Card heights",
  description: "",
  owner_email: "you@example.com",
  status: "draft",
  latest_version: null,
  variables: {},
  triggers: [],
  versions: [],
  graph: {
    nodes: CASES.map(([type, config], i) => ({
      id: `${type}_node_with_a_long_id`,
      type,
      position: { x: (i % 4) * 280, y: Math.floor(i / 4) * SAVED_PITCH },
      data: { label: "A long block title that must stay on one line", config },
    })),
    edges: [],
  },
};

const CATALOG = {
  agents: [],
  modules: [{ id: "m1", name: "normalise_inbound_payload_module", description: LONG }],
  integrations: [],
  tools: [
    {
      action: "gmail.send",
      label: "Send",
      description: LONG,
      integration: "google-workspace-mail",
      args_schema: {},
      read_only: false,
      destructive: true,
      open_world: true,
    },
  ],
};

test("each workflow node card fits a saved pitch of 160 px", async ({ page }) => {
  // A cold compile of the editor route takes most of the default timeout.
  test.setTimeout(150_000);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/workflows/wf1") return route.fulfill({ json: WORKFLOW });
    if (path === "/api/workflows/catalog") return route.fulfill({ json: CATALOG });
    if (/^\/api\/(notes|chat|agent|apps)\//.test(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: { rows: [], items: [], total: 0 } });
  });
  await page.goto("/studio/workflows/wf1");

  const cards = page.locator(".react-flow__node");
  await expect(cards).toHaveCount(CASES.length, { timeout: 90_000 });

  for (const [density, scale] of Object.entries(DENSITIES)) {
    await page.evaluate((s) => document.documentElement.style.setProperty("--ui-scale", s), scale);
    await page.evaluate(() => document.fonts.ready);
    // `offsetHeight` is in canvas units. The zoom of the canvas does not change it.
    const heights = await cards.evaluateAll((els) =>
      els.map((e) => [e.getAttribute("data-id"), (e as HTMLElement).offsetHeight] as const),
    );
    console.log(density, heights.map(([id, h]) => `${id?.split("_node")[0]}=${h}`).join(" "));
    for (const [id, height] of heights) {
      expect(height, `${id} at ${density} density`).toBeGreaterThan(60);
      expect(height, `${id} at ${density} density`).toBeLessThanOrEqual(SAVED_PITCH - MIN_GAP);
    }
  }
});
