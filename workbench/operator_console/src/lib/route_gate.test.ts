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
  // ⚠️ **DERIVED from console.ts, not hand-listed.** It was a hand-list until
  // CP-11 slice 1, and that slice added `listKeys` — a page read the list did
  // not name, so the fence would have passed a page that dropped the session on
  // it. A guard with a hand-maintained inventory fails exactly when somebody
  // adds the thing it was meant to guard.
  const CONSOLE_SRC = readFileSync(
    join(process.cwd(), "src", "lib", "console.ts"),
    "utf-8",
  );
  const SIGNATURES = [
    ...CONSOLE_SRC.matchAll(/^export const (\w+) = \(([^)]*)\)/gm),
  ].map(([, name, params]) => ({
    name,
    // How many arguments come BEFORE the optional deps. `catalog(d?)` is 0;
    // `billingSummary(orgSlug, d?)` is 1; `updateOperator(id, body, d?)` is 2.
    required: params
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p.length > 0 && !/^d\?\s*:/.test(p)).length,
  }));
  const READS = SIGNATURES.map((s) => s.name);

  // Count top-level arguments in a call's text, ignoring commas nested inside
  // objects, arrays, calls or template strings.
  const argCount = (source: string, at: number): number | null => {
    let depth = 0;
    let args = 0;
    let seen = false;
    for (let i = at; i < source.length; i += 1) {
      const c = source[i];
      // ⚠️ `seen` is set BEFORE the depth bookkeeping, and that ordering is the
      // whole of it. An argument that is one object literal — `fn({ authToken })`,
      // which is how every page in this app calls — opens a brace on its first
      // character. Checking `seen` only in the else-branch meant that character
      // bumped the depth and never registered, so the call counted as ZERO
      // arguments and three correct pages were reported as bugs.
      if (i > at && depth >= 1 && c !== ")" && !/\s/.test(c)) seen = true;
      if (c === "(" || c === "[" || c === "{") depth += 1;
      else if (c === ")" || c === "]" || c === "}") {
        depth -= 1;
        if (depth === 0) return seen ? args + 1 : 0;
      } else if (c === "," && depth === 1) args += 1;
    }
    return null;
  };

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

  it("derives the read list from console.ts, so it cannot go stale", () => {
    // Guards the guard. An empty or truncated parse would make every check
    // below vacuous, and a vacuous fence is green.
    expect(READS.length).toBeGreaterThanOrEqual(15);
    expect(READS).toContain("listOrganizations");
    expect(READS).toContain("listKeys");
    expect(SIGNATURES.find((s) => s.name === "catalog")?.required).toBe(0);
    expect(SIGNATURES.find((s) => s.name === "listKeys")?.required).toBe(1);
  });

  for (const file of pages) {
    const source = readFileSync(file, "utf-8");
    const label = file.slice(join(process.cwd(), "src", "app").length + 1);
    for (const sig of SIGNATURES) {
      // ⚠️ **Arity, not emptiness.** The original check only caught `fn()`, so
      // `billingSummary(slug)` and `listKeys(slug)` — one argument, no deps —
      // sailed straight through while dropping the session just as completely.
      const call = new RegExp(`\\b${sig.name}\\s*\\(`, "g");
      for (const m of source.matchAll(call)) {
        const open = m.index + m[0].length - 1;
        const n = argCount(source, open);
        if (n === null || n > sig.required) continue;
        it(`${label} passes the caller session to ${sig.name}`, () => {
          expect(
            false,
            `${label} calls ${sig.name}() with ${n} argument(s) and it needs ` +
              `${sig.required + 1} — the deps are missing, so the read reaches ` +
              `the Console as the shared break-glass token, not as the person.`,
          ).toBe(true);
        });
      }
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
