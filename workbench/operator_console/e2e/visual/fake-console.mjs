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
