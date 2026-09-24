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
