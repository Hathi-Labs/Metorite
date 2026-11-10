// The session cookie stays `SameSite=Lax` (WS-55 EA-4a, spec §10.5).
//
// The consent page approves an outside agent with the member's session. The
// BFF refuses a cross-site request by `Sec-Fetch-Site` (`relay.ts`). Lax is
// the second lock: the browser sends no session cookie on a cross-site POST,
// so a forged approval arrives signed out.
//
// `auth.ts` sets no `cookies` option, so Auth.js uses its defaults. Two
// checks hold that:
// 1. the pinned `@auth/core` default for the session cookie is `lax`, for the
//    secure name and the plain name;
// 2. `auth.ts` names no `cookies` option and no `sameSite`, so nothing
//    overrides the default.
//
// Mutations, each one red here: add `cookies: { sessionToken: { options: {
// sameSite: "none" } } }` to the NextAuth config, or bump `@auth/core` to a
// build whose default is not `lax`.
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { describe, expect, it } from "vitest";

const CORE_COOKIE = fileURLToPath(
  new URL("../../node_modules/@auth/core/lib/utils/cookie.js", import.meta.url),
);
const AUTH_TS = fileURLToPath(new URL("../auth.ts", import.meta.url));

type CookieDefaults = Record<string, { name: string; options: { sameSite?: string } }>;

describe("the session cookie is SameSite=Lax", () => {
  it("the pinned Auth.js default for the session cookie is lax", async () => {
    const core = (await import(pathToFileURL(CORE_COOKIE).href)) as {
      defaultCookies: (secure: boolean) => CookieDefaults;
    };
    expect(typeof core.defaultCookies).toBe("function");
    for (const secure of [true, false]) {
      const session = core.defaultCookies(secure).sessionToken;
      expect(session.name).toBe(`${secure ? "__Secure-" : ""}authjs.session-token`);
      expect(session.options.sameSite).toBe("lax");
    }
  });

  it("auth.ts overrides no cookie option", () => {
    const source = readFileSync(AUTH_TS, "utf8");
    const start = source.indexOf("NextAuth({");
    expect(start).toBeGreaterThan(-1);
    // Comments may talk about cookies. Code may not set them.
    const code = source
      .slice(start)
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/\bcookies\s*:/);
    expect(code).not.toMatch(/sameSite/i);
  });
});
