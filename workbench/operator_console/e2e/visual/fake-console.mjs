// A stand-in Customer Console for the operator console's SERVER-side reads.
//
// The operator console's pages are server components: `readAiCatalog` fetches
// from CUSTOMER_CONSOLE_URL inside Next, so Playwright's route interception
// never sees it. Pointing that variable here is the only way to render the
// real pages with known data.
//
// The fixtures are the LIVE production payloads, captured 2026-09-22, so what
// renders is what the owner is actually looking at.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const FIX = JSON.parse(readFileSync(join(here, "fixtures.json"), "utf8"));

const byPath = new Map([
  ["/catalog/models", FIX["catalog/models"]],
  ["/providers/credentials", FIX["providers/credentials?include_revoked=true"]],
  ["/orgs", FIX["orgs"]],
  ["/activity/actions", { actions: ["refused", "key.issue", "catalog.profile"] }],
  ["/activity", { activity: [], next_cursor: null }],
  ["/operators", { operators: [] }],
  ["/operators/elevate", { elevated: false, can_elevate: false, required: false }],
  ["/providers/spend", { rows: [] }],
  // Provider balance & health (2026-09-28). Shaped on the production outage:
  // DeepSeek out (vendor flag false, balance -0.05, twelve 402s), and the
  // AI/ML API reseller low. Plus a vendor with no balance probe, which must
  // draw a NEUTRAL chip. This state also raises the site-wide danger banner.
  ["/providers/health", {
    providers: [
      { provider: "deepseek", status: "out",
        reason: "The vendor reports that this account cannot serve calls.",
        balance: "-0.05", currency: "USD", available: false,
        balance_checked_at: "2026-09-28T10:00:00+00:00", probe_status: "ok",
        probe_error: null, probe_attempted_at: "2026-09-28T10:00:00+00:00",
        balance_exposed: true, threshold: "5", days_left: null,
        cost_7d_usd: "1.2", last_refusal_status: 402,
        last_refusal_at: "2026-09-28T10:01:00+00:00", refusals_24h: 12,
        server_errors_24h: 0 },
      { provider: "aimlapi", status: "low",
        reason: "The balance is under the low line of 5 USD.",
        balance: "3.2", currency: "USD", available: null,
        balance_checked_at: "2026-09-28T10:00:00+00:00", probe_status: "ok",
        probe_error: null, probe_attempted_at: "2026-09-28T10:00:00+00:00",
        balance_exposed: true, threshold: "5", days_left: "2.5",
        cost_7d_usd: "9", last_refusal_status: null, last_refusal_at: null,
        refusals_24h: 0, server_errors_24h: 0 },
      { provider: "groq", status: "unknown",
        reason: "This vendor does not expose its balance. Watch for refusals.",
        balance: null, currency: null, available: null,
        balance_checked_at: "2026-09-28T10:00:00+00:00",
        probe_status: "not_exposed",
        probe_error: "this vendor exposes no balance we can read",
        probe_attempted_at: "2026-09-28T10:00:00+00:00",
        balance_exposed: false, threshold: null, days_left: null,
        cost_7d_usd: null, last_refusal_status: null, last_refusal_at: null,
        refusals_24h: 0, server_errors_24h: 0 },
    ],
    low_usd_default: "5",
    probe_minutes: 30,
  }],
  // Usage slice 3: one customer's breakdown. Shaped on production's first
  // five metered calls (2026-09-24), plus the cases the panel must draw: an
  // app with two agents, a gap named "unattributed", a LOSS, a margin with no
  // credit price (null), and a person list CUT below its total.
  ["/admin/usage/breakdown", {
    orgSlug: "hathi-labs-llp",
    windowDays: 30,
    apps: [
      { app: "projects", calls: 5, credits: "81.4040", costUsd: "0.01417135",
        realisedMargin: "0.98",
        agents: [
          { agent: "projects-assistant", calls: 4, credits: "65.0000",
            costUsd: "0.01100000", realisedMargin: "0.98" },
          { agent: "task-manager", calls: 1, credits: "16.4040",
            costUsd: "0.00317135", realisedMargin: "0.98" },
        ] },
      { app: "email", calls: 2, credits: "0.0100", costUsd: "0.00040000",
        realisedMargin: "-2.88",
        agents: [{ agent: "email-assistant", calls: 2, credits: "0.0100",
                   costUsd: "0.00040000", realisedMargin: "-2.88" }] },
      { app: "unattributed", calls: 2, credits: "0.4325", costUsd: "0.00059576",
        realisedMargin: null,
        agents: [{ agent: "unattributed", calls: 2, credits: "0.4325",
                   costUsd: "0.00059576", realisedMargin: null }] },
    ],
    members: [
      { member: "vjvarada@hathilabs.com", calls: 7, credits: "81.4140",
        costUsd: "0.01457135", realisedMargin: "0.98" },
      { member: "unattributed", calls: 2, credits: "0.4325",
        costUsd: "0.00059576", realisedMargin: null },
    ],
    appsTotal: 3,
    membersTotal: 14,
  }],
]);

createServer((req, res) => {
  const path = req.url.split("?")[0];
  const body = byPath.get(path);
  res.setHeader("content-type", "application/json");
  if (body === undefined) {
    res.statusCode = 404;
    res.end(JSON.stringify({ detail: `fake console has no ${path}` }));
    return;
  }
  res.end(JSON.stringify(body));
}).listen(8199, "127.0.0.1", () => console.log("fake console on 8199"));
