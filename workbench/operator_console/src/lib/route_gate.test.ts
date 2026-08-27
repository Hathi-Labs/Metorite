/**
 * ⚠️ **F7, closed.** WS-31 CP-12g, spec §8.1 done-when 28.
 *
 * The finding: *"the gate holds by convention, not by structure. The app has
 * no `middleware.ts`. Each route calls the gate itself. A new route that
 * forgets is open, and no test says otherwise."*
 *
 * This is the test that says otherwise. It reads every route file under
 * `src/app/api/operator/**` and fails when a handler does not reach the gate.
 *
 * **Why a source scan rather than `middleware.ts`.** Next's middleware runs on
 * the Edge runtime, and this gate reads `cookies()` and then calls the Console
 * over the network. Moving it there would split the gate across two runtimes
 * and two failure modes, and the interim path's constant-time compare would go
 * with it. A fence that fails in CI gives the same guarantee without moving
 * the credential handling anywhere.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const API_ROOT = join(process.cwd(), "src", "app", "api", "operator");

//: The ONE ungated route, and it is ungated because it is the door: it cannot
//: require the identity it issues. Its own body is the proof — the shared
//: passphrase, or a Supabase token the Console verifies with the issuer.
//:
//: ⚠️ Adding a second entry here is an edit somebody has to justify in review.
//: That is the whole reason this is a list and not a predicate.
const UNGATED = new Set(["session"]);

//: Anything that reaches `gate()`. `proxyToConsole` and `gateStaff` both call
//: it, so naming it alone would miss the routes that use the wrappers.
const GATE_CALLS = ["proxyToConsole(", "gateStaff(", "await gate("];

const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];

function routeFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) routeFiles(full, found);
    else if (entry === "route.ts") found.push(full);
  }
  return found;
}

// The directory name under `api/operator`, used to match `UNGATED`.
function routeName(file: string): string {
  const rest = file.slice(API_ROOT.length + 1);
  return rest.split(/[\\/]/)[0];
}

function handlersIn(source: string): string[] {
  return HTTP_METHODS.filter((m) =>
    new RegExp(`export\\s+async\\s+function\\s+${m}\\s*\\(`).test(source),
  );
}

describe("every operator API route goes through the gate", () => {
  const files = routeFiles(API_ROOT);

  it("finds route files at all, so an empty sweep cannot pass", () => {
    // Without this, a rename of the directory would make the whole suite
    // vacuously green — the failure mode the fence exists to prevent.
    expect(files.length).toBeGreaterThanOrEqual(10);
  });

  for (const file of files) {
    const name = routeName(file);
    const source = readFileSync(file, "utf-8");
    const label = file.slice(API_ROOT.length + 1);

    if (UNGATED.has(name)) {
      it(`${label} is DELIBERATELY ungated and declared as such`, () => {
        expect(source).toContain("deliberately ungated");
      });
      continue;
    }

    it(`${label} gates every handler it exports`, () => {
      const handlers = handlersIn(source);
      expect(handlers.length).toBeGreaterThan(0);
      const gated = GATE_CALLS.some((call) => source.includes(call));
      expect(
        gated,
        `${label} exports ${handlers.join(", ")} and never reaches the ` +
          `gate. Every route under api/operator must call proxyToConsole, ` +
          `gateStaff or gate — or be declared in UNGATED with a reason.`,
      ).toBe(true);
    });

    it(`${label} forwards the caller's session to the Console`, () => {
      // ⚠️ A route that gates but then calls the Console with NO deps proxies
      // as the shared break-glass token: it bypasses the §5 matrix and logs a
      // warning on every use. Gating alone is not enough.
      if (!source.includes("proxyToConsole(")) return; // hand-rolled, checked above
      expect(
        /proxyToConsole\(\s*\(\s*d\s*\)/.test(source),
        `${label} calls proxyToConsole but ignores the deps argument, so ` +
          `the caller's session is dropped and the shared token is used.`,
      ).toBe(true);
    });
  }
});

describe("server components carry the caller's session too", () => {
  // ⚠️ **The same bug, one layer up, and the route fence could not see it.**
  // A PAGE that reads the Console without the caller's token reaches it as
  // `breakglass`: past the §5 role matrix, and logged as a break-glass event
  // on every page view. Four page reads did exactly that until this test.
  const READS = [
    "listOrganizations",
    "billingSummary",
    "catalog",
    "listOperators",
    "activityActions",
    "readActivity",
  ];

  const pages: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.tsx$/.test(entry) && !/\.test\./.test(entry)) {
        pages.push(full);
      }
    }
  };
  walk(join(process.cwd(), "src", "app"));

  it("finds pages at all, so an empty sweep cannot pass", () => {
    expect(pages.length).toBeGreaterThanOrEqual(5);
  });

  for (const file of pages) {
    const source = readFileSync(file, "utf-8");
    const label = file.slice(join(process.cwd(), "src", "app").length + 1);
    for (const read of READS) {
      // An EMPTY argument list is the bug. `fn()` drops the session;
      // `fn(d)` or `fn(slug, d)` carries it.
      const bare = new RegExp(`\\b${read}\\(\\s*\\)`);
      if (!bare.test(source)) continue;
      it(`${label} passes the caller session to ${read}`, () => {
        expect(
          false,
          `${label} calls ${read}() with no deps, so the read reaches the ` +
            `Console as the shared break-glass token instead of the person.`,
        ).toBe(true);
      });
    }
  }
});

describe("the credential never reaches the browser", () => {
  it("no client component imports the Console client", () => {
    // `console.ts` holds CUSTOMER_CONSOLE_OPERATOR_TOKEN. A "use client" file
    // importing it would bundle the read into browser output.
    const appRoot = join(process.cwd(), "src", "app");
    const offenders: string[] = [];

    const walk = (dir: string) => {
      for (const entry of readdirSync(dir)) {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
          const src = readFileSync(full, "utf-8");
          if (src.includes('"use client"') && /from "@\/lib\/console"/.test(src)) {
            offenders.push(full.slice(appRoot.length + 1));
          }
        }
      }
    };
    walk(appRoot);
    expect(offenders).toEqual([]);
  });
});
