/**
 * Fences for CP-2d slice 2's Auth.js adapter — **R5's missing fence**
 * (`customer_console.md` §CP-2d clauses 6 and 10, ruling H-A).
 *
 * R5 says "no new DB-connection sites outside the seam". Until this file, the
 * only thing stopping the control plane from growing one was that nobody had
 * tried: the rule bound Python (`tests/unit/test_db_engine_seam.py`,
 * `test_psycopg_seam.py`) and had **no expression at all in the Next tier**.
 * Wiring an Auth.js database adapter is precisely the moment somebody reaches
 * for `@auth/pg-adapter` and a connection string, so the rule gets a fence here
 * in the change that creates the temptation — R7, and the reason this file
 * exists rather than a paragraph in a docstring.
 *
 * Two of the three checks are EXECUTED (they read `package.json` and walk
 * `src/`); the third is source-level over `emailOtpAdapter.ts` for the measured
 * reason `signin.test.ts:38-45` records — that module imports `lib/gateway.ts`,
 * which drags `next/server`, which cannot load in this tree's node-env vitest.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const PKG_PATH = fileURLToPath(new URL("../../package.json", import.meta.url));
const SRC_DIR = fileURLToPath(new URL("..", import.meta.url));
const ADAPTER = readFileSync(
  new URL("./emailOtpAdapter.ts", import.meta.url),
  "utf-8",
);

const pkg = JSON.parse(readFileSync(PKG_PATH, "utf-8")) as {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
};

/**
 * Package names that mean "this tier now talks to Postgres itself".
 *
 * `@auth/*-adapter` is listed because every off-the-shelf Auth.js adapter takes
 * a connection (a `pg` Pool, a Drizzle/Prisma client, a DSN) — adopting one is
 * the same defect wearing a friendlier name, and it is the exact package a
 * future agent will reach for when this adapter needs a sixth method.
 */
function offendingDependency(name: string): boolean {
  return (
    name === "pg" ||
    name === "postgres" ||
    name === "pg-promise" ||
    name === "node-postgres" ||
    name.startsWith("@types/pg") ||
    /^@auth\/.*-adapter$/.test(name) ||
    name === "@auth/pg-adapter"
  );
}

/** Every source file under `src/`, so a new folder cannot escape the sweep. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if ([".ts", ".tsx", ".mjs", ".js"].includes(extname(entry)))
      out.push(full);
  }
  return out;
}

describe("the control plane still has no database of its own (R5)", () => {
  it("declares no pg / postgres / Auth.js-adapter dependency", () => {
    const declared = [
      ...Object.keys(pkg.dependencies ?? {}),
      ...Object.keys(pkg.devDependencies ?? {}),
    ];
    // Both blocks, deliberately: a driver in `devDependencies` is still a driver
    // that a `src/` import resolves against, and "it is only a dev dependency"
    // is the argument that would be made.
    expect(declared.filter(offendingDependency)).toEqual([]);
  });

  it("imports no Postgres client anywhere under src/", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC_DIR)) {
      const src = readFileSync(file, "utf-8");
      // An ES import, a CommonJS require or a dynamic import naming one of the
      // clients. Matched on the module SPECIFIER, not the word, so prose about
      // Postgres does not trip it.
      //
      // ⚠️ This file is inside its own sweep on purpose — a fence that exempts
      // itself is a place to hide the thing it forbids — which is why the
      // sentence above spells no specifier out. The first draft did, and the
      // fence went red against its own comment.
      if (
        /from\s+["'](pg|postgres|pg-promise|@auth\/[a-z-]*-adapter)["']/.test(src) ||
        /(require|import)\(\s*["'](pg|postgres|pg-promise)["']\s*\)/.test(src)
      ) {
        offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the adapter reaches the database only through the gateway", () => {
    // Every persisting method is an HTTP call to `gateway/routes/email_otp.py`.
    expect(ADAPTER).toContain("EMAIL_OTP_TOKEN_PATH");
    expect(ADAPTER).toContain("EMAIL_OTP_CONSUME_PATH");
    expect(ADAPTER).toContain("EMAIL_OTP_SEND_PATH");
    // …through the ONE bearer seam. Minting a token here is the forbidden way
    // around the `auth.ts` ⇄ `lib/gateway.ts` cycle, and it is fenced from the
    // other side too (`gateway.test.ts`'s sweep).
    expect(ADAPTER).toContain("headersActingAs(identifier");
    expect(ADAPTER).not.toContain("GATEWAY_INTERNAL_TOKEN");
    expect(ADAPTER).not.toContain("LITELLM_MASTER_KEY");
    expect(ADAPTER).not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY");
    // `gatewayHeaders()` calls `auth()` and would find nothing: every one of
    // these calls happens before a session exists.
    expect(ADAPTER).not.toContain("gatewayHeaders(");
  });
});

describe("the adapter's method set is the minimal one (ruling H-E)", () => {
  /** Method names declared on the returned object literal. */
  const declared = [...ADAPTER.matchAll(/^\s{4}async (\w+)\(/gm)].map(
    (m) => m[1],
  );

  it("implements exactly the five the email flow reaches under jwt", () => {
    // Measured against the PINNED `@auth/core@0.41.2` in node_modules:
    //   assert.js:18-22          -> createVerificationToken, useVerificationToken,
    //                               getUserByEmail are REQUIRED for type:"email"
    //   handle-login.js:34-42    -> getUser, when a session cookie is present
    //   handle-login.js:56-77    -> updateUser (getUserByEmail answered) XOR
    //                               createUser (it did not). `derivedOtpUser` is
    //                               total, so updateUser is the reachable one.
    expect([...declared].sort()).toEqual(
      [
        "createVerificationToken",
        "getUser",
        "getUserByEmail",
        "updateUser",
        "useVerificationToken",
      ].sort(),
    );
  });

  it("omits every method only a database session could reach", () => {
    // Not stubbed — omitted. A stub is a method a future reader believes is
    // exercised, and each of these is reachable ONLY from the `!useJwtSession`
    // branches of handle-login.js or from the webauthn/oauth arms. If the
    // session strategy ever changes, this fence reds instead of sign-in dying
    // on a `TypeError` in production. (`signin.test.ts` pins the strategy.)
    for (const unreachable of [
      "createUser",
      "linkAccount",
      "createSession",
      "getSessionAndUser",
      "updateSession",
      "deleteSession",
      "getUserByAccount",
      "createAuthenticator",
    ]) {
      expect(declared).not.toContain(unreachable);
    }
  });

  it("persists no user, account or session row — identity stays the tenant plane's", () => {
    // The three user methods are pure. A second identity store beside
    // `app_user`/`user_identity` is the defect root CLAUDE.md §5 names, and it
    // would arrive as an innocuous-looking `POST /users` from right here.
    expect(ADAPTER).toContain("derivedOtpUser");
    expect(ADAPTER).not.toMatch(/EMAIL_OTP_(USER|SESSION|ACCOUNT)_PATH/);
  });

  it("treats only the 404 as an answer — every other refusal throws", () => {
    // A rate-limit 429 swallowed into `return null` reads to the person as "your
    // code is wrong" and to an operator as "OTP is flaky". The 404 IS the wrong
    // code, and is the one non-throwing refusal.
    expect(ADAPTER).toContain("if (res.status === 404) return null;");
    expect(ADAPTER).toMatch(/if \(!res\.ok\) await refuse\(/);
  });
});
