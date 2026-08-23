/**
 * Fences for CP-2d slice 2's Auth.js adapter — **R5's missing fence, and the
 * completeness fence P0 proved was missing too**
 * (`customer_console.md` §CP-2d clauses 6 and 10, rulings H-A/H-E).
 *
 * Two subjects, and they fail for different reasons:
 *
 * 1. **R5 — no database in the Next tier.** "No new DB-connection sites outside
 *    the seam" bound Python (`tests/unit/test_db_engine_seam.py`,
 *    `test_psycopg_seam.py`) and had **no expression at all in the Next tier**
 *    until this file. Wiring an Auth.js database adapter is precisely the moment
 *    somebody reaches for `@auth/pg-adapter` and a connection string, so the rule
 *    gets its fence in the change that creates the temptation (R7). Two of these
 *    checks are EXECUTED — they read `package.json` and walk `src/`.
 *
 * 2. **Completeness — the adapter implements every method the pinned
 *    `@auth/core` can invoke under this configuration.** ⚠️ This half is the
 *    repair of review finding P0 (2026-08-23) and it replaces a fence that made
 *    the bug *harder* to fix: the file used to assert
 *    `expect(declared).not.toContain("getUserByAccount")`, pinning in place a
 *    claim (`"the oauth arms are unreachable in this configuration"`) that was
 *    simply false. `callback/index.js:56-62` calls `getUserByAccount` on **every**
 *    OAuth callback once an adapter is present, so the armed adapter would have
 *    taken Google and Microsoft sign-in down the day the owner flipped the flag.
 *
 *    So the reachable set is no longer asserted from a docstring — it is
 *    **derived from the pinned `@auth/core@0.41.2` in `node_modules`**, by
 *    reading the call sites out of the source and subtracting only those the
 *    source itself shows are guarded (behind `useJwtSession`, or inside the
 *    webauthn arm). A version bump that adds a call site, moves one out of its
 *    guard, or introduces one in a new file turns this RED.
 *
 * The adapter's *behaviour* is fenced where it can be EXECUTED, in
 * `emailOtp.test.ts` — both `@auth/core` arms replayed against the real object.
 * This file proves no method is missing; that one proves the methods are right.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, posix, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { emailOtpAdapterOver } from "@/lib/emailOtp";

const PKG_PATH = fileURLToPath(new URL("../../package.json", import.meta.url));
const SRC_DIR = fileURLToPath(new URL("..", import.meta.url));
const ADAPTER = readFileSync(
  new URL("./emailOtpAdapter.ts", import.meta.url),
  "utf-8",
);
const PURE = readFileSync(new URL("./emailOtp.ts", import.meta.url), "utf-8");

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
 * future agent will reach for when this adapter needs a ninth method.
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

  it("the adapter reaches the database only through the gateway seam", () => {
    // The three routes are named ONCE, in the pure module, and reached only
    // through the object built there.
    expect(PURE).toContain("EMAIL_OTP_TOKEN_PATH");
    expect(PURE).toContain("EMAIL_OTP_CONSUME_PATH");
    expect(PURE).toContain("EMAIL_OTP_SEND_PATH");
    // …and the ONE transport is built here, out of the ONE bearer seam. Minting
    // a token here is the forbidden way around the `auth.ts` ⇄ `lib/gateway.ts`
    // cycle, and it is fenced from the other side too — `gateway.test.ts`'s
    // bearer sweep names this file in `NON_ROUTE_GATEWAY_CALLERS`. ⚠️ It did
    // NOT until the F1 repair of 2026-08-23: this comment claimed the
    // double-fence for a widening that had never happened, and a fence nobody
    // checked is how the second bearer would have arrived.
    expect(ADAPTER).toContain("headersActingAs(identifier");
    expect(ADAPTER).toContain("GATEWAY_URL");
    for (const secret of [
      "GATEWAY_INTERNAL_TOKEN",
      "LITELLM_MASTER_KEY",
      "CUSTOMER_CONSOLE_DEPLOYMENT_KEY",
    ]) {
      expect(ADAPTER).not.toContain(secret);
      expect(PURE).not.toContain(secret);
    }
    // `gatewayHeaders()` calls `auth()` and would find nothing: every one of
    // these calls happens before a session exists.
    expect(ADAPTER).not.toContain("gatewayHeaders(");
    // The pure module must stay framework-free, or every executed fence in
    // `emailOtp.test.ts` silently stops running (`next/server` will not load in
    // this tree's node-env vitest). A VALUE import of the gateway there is the
    // way that happens.
    expect(PURE).not.toContain('from "@/lib/gateway"');
  });

  it("persists no user, account or session row — identity stays the tenant plane's", () => {
    // A second identity store beside `app_user`/`user_identity` is the defect
    // root CLAUDE.md §5 names, and it would arrive as an innocuous-looking
    // `POST /users` from right here. (The behavioural half — that a full replay
    // of both arms touches only the consume route — is in `emailOtp.test.ts`.)
    for (const src of [ADAPTER, PURE]) {
      expect(src).not.toMatch(/EMAIL_OTP_(USER|SESSION|ACCOUNT)_PATH/);
    }
  });
});

// ══════════════════════════════════════════════════════════════════════════
// The completeness fence, derived from the PINNED @auth/core (finding P0)
// ══════════════════════════════════════════════════════════════════════════

const CORE_DIR = fileURLToPath(
  new URL("../../node_modules/@auth/core/lib/", import.meta.url),
);

function coreFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) coreFiles(full, out);
    else if (extname(entry) === ".js") out.push(full);
  }
  return out;
}

/** `lib/`-relative, posix-separated, so the pins below read the same on Windows. */
function rel(path: string): string {
  return relative(CORE_DIR, path).split(sep).join(posix.sep);
}

const core = new Map(
  coreFiles(CORE_DIR).map((p) => [rel(p), readFileSync(p, "utf-8")]),
);

/** Adapter method names a file names, whether by call or by destructuring. */
function adapterMethodsIn(src: string): string[] {
  const found = new Set<string>();
  // `adapter.foo(`, `adapter?.foo(`, `options.adapter.foo(`, and the optional
  // CALL form `adapter.foo?.(` — which `send-token.js:61` uses for
  // `createVerificationToken`, so a regex without it silently drops the one
  // method the whole feature depends on.
  for (const m of src.matchAll(/adapter\??\.(\w+)\s*(?:\?\.)?\(/g)) {
    found.add(m[1]);
  }
  // `const { a, b, c } = adapter;` — how `handle-login.js:29` reaches nine of
  // them, which is why a call-only regex would have missed the whole set.
  for (const m of src.matchAll(/\{([^{}]*?)\}\s*=\s*adapter\s*;/g)) {
    for (const name of m[1].split(",")) {
      const clean = name.trim();
      if (clean) found.add(clean);
    }
  }
  return [...found];
}

/**
 * Files whose adapter use is reachable in THIS configuration: an email provider
 * and two OAuth providers, `session.strategy: "jwt"`, no webauthn, no
 * credentials.
 */
const REACHABLE_FILES = [
  "actions/callback/index.js",
  "actions/callback/handle-login.js",
  "actions/signin/send-token.js",
];

/**
 * Names the source itself shows are guarded, each with its guard asserted below.
 * This list is where a future reader argues with the fence — never the adapter.
 */
const GUARDED = ["createSession", "getSessionAndUser", "deleteSession", "createAuthenticator"];

describe("the adapter implements every method the pinned @auth/core can reach", () => {
  const implemented = Object.keys(
    emailOtpAdapterOver(async () => {
      throw new Error("the transport must not be called by a shape assertion");
    }) as unknown as Record<string, unknown>,
  ).sort();

  it("no NEW file in @auth/core touches the adapter unnoticed", () => {
    // The outermost guard, and the one that makes the three-file list below
    // safe: if a version bump introduces an adapter call site in a file nobody
    // has read, this reds before any of the per-file reasoning is trusted.
    const mentions = [...core.entries()]
      .filter(([, src]) => /\badapter\b/.test(src))
      .map(([name]) => name)
      .sort();
    expect(mentions).toEqual(
      [
        // Reachable here — the three arms.
        "actions/callback/handle-login.js",
        "actions/callback/index.js",
        "actions/signin/send-token.js",
        // Database-session only; their guards are asserted below.
        "actions/session.js",
        "actions/signout.js",
        "utils/session.js",
        // WebAuthn only — no webauthn provider is registered (asserted below).
        "actions/webauthn-options.js",
        "utils/webauthn-utils.js",
        // Not call sites: the error wrapper and the required-method check.
        "init.js",
        "utils/assert.js",
      ].sort(),
    );
  });

  it("implements exactly the reachable set — no method missing, none spare", () => {
    const referenced = new Set<string>();
    for (const file of REACHABLE_FILES) {
      const src = core.get(file);
      expect(src, `${file} is missing from the pinned @auth/core`).toBeTruthy();
      for (const name of adapterMethodsIn(src as string)) referenced.add(name);
    }
    // Sanity: the extraction found the set it is supposed to find. A regex that
    // silently matched nothing would make this whole block vacuous — the exact
    // rot the R5 sweep's `ROUTES.length` guard exists to prevent.
    expect(referenced.size).toBeGreaterThanOrEqual(12);
    expect(referenced.has("getUserByAccount")).toBe(true);

    const reachable = [...referenced].filter((n) => !GUARDED.includes(n)).sort();
    expect(implemented).toEqual(reachable);
  });

  it("names the guard that makes each omitted method unreachable", () => {
    const handleLogin = core.get("actions/callback/handle-login.js") as string;
    const callbackIndex = core.get("actions/callback/index.js") as string;

    // createSession — every call site is the false arm of a `useJwtSession`
    // ternary. Counted, not merely matched: one unguarded call site added
    // beside five guarded ones would otherwise pass.
    const createSessionCalls = handleLogin.match(/await createSession\(/g) ?? [];
    const createSessionGuarded =
      handleLogin.match(/useJwtSession\s*\?\s*\{\}\s*:\s*await createSession\(/g) ??
      [];
    expect(createSessionCalls.length).toBeGreaterThan(0);
    expect(createSessionGuarded.length).toBe(createSessionCalls.length);

    // getSessionAndUser — the `else` of `if (useJwtSession)`, once.
    expect(handleLogin.match(/await getSessionAndUser\(/g)?.length).toBe(1);
    expect(handleLogin).toMatch(
      /else \{\s*const userAndSession = await getSessionAndUser\(/,
    );

    // deleteSession — behind an explicit `!useJwtSession`, once.
    expect(handleLogin.match(/await deleteSession\(/g)?.length).toBe(1);
    expect(handleLogin).toMatch(
      /!useJwtSession && sessionToken\)[\s\S]{0,400}?await deleteSession\(/,
    );

    // createAuthenticator — inside the webauthn arm of the callback route, and
    // there is no webauthn provider (the `auth.ts` half is asserted below).
    expect(callbackIndex.match(/createAuthenticator\(/g)?.length).toBe(1);
    expect(callbackIndex.indexOf('provider.type === "webauthn"')).toBeLessThan(
      callbackIndex.indexOf("createAuthenticator("),
    );
    expect(callbackIndex.indexOf('provider.type === "webauthn"')).toBeGreaterThan(
      -1,
    );
  });

  it("the database-session files really are database-session only", () => {
    // Each returns (or branches away) on `jwt` BEFORE it touches the adapter.
    const sessionAction = core.get("actions/session.js") as string;
    const jwtBranch = sessionAction.indexOf('sessionStrategy === "jwt"');
    const destructure = sessionAction.indexOf("} = adapter;");
    const earlyReturn = sessionAction.indexOf("return response;", jwtBranch);
    expect(jwtBranch).toBeGreaterThan(-1);
    expect(earlyReturn).toBeGreaterThan(jwtBranch);
    expect(destructure).toBeGreaterThan(earlyReturn);

    // signout.js and utils/session.js both reach the adapter from the `else` of
    // a strategy check.
    expect(core.get("actions/signout.js")).toMatch(
      /else \{\s*const session = await options\.adapter\?\.deleteSession\(/,
    );
    expect(core.get("utils/session.js")).toMatch(
      /else \{\s*const userAndSession = await adapter\?\.getSessionAndUser\(/,
    );
  });

  it("the premises hold in auth.ts: jwt is pinned and no webauthn provider exists", () => {
    // The reasoning above is conditional on the configuration, so the
    // configuration is asserted rather than remembered. `signin.test.ts` owns
    // the strategy pin; it is restated here because THIS file's subtraction is
    // what it licenses.
    const authSrc = readFileSync(
      new URL("../auth.ts", import.meta.url),
      "utf-8",
    );
    expect(authSrc).toContain('session: { strategy: "jwt" }');
    expect(authSrc).not.toContain('strategy: "database"');
    expect(authSrc).not.toMatch(/webauthn/i);
    expect(authSrc).not.toMatch(/\bPasskey\b/);
    // And `allowDangerousEmailAccountLinking` stays absent: with a stateless
    // `getUserByEmail` it would change nothing today, but it is the option that
    // makes an unverified OAuth address adopt an existing account.
    expect(authSrc).not.toContain("allowDangerousEmailAccountLinking");
  });
});
