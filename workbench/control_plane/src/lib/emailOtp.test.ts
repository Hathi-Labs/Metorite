/**
 * Fences for CP-2d's pure email-OTP logic (R7; customer_console.md §CP-2d).
 *
 * These RUN — the module is framework-free on purpose (`emailOtp.ts`'s own
 * docstring), so unlike the source-regex pins in `signin.test.ts` this file can
 * execute the code generator, the message, the gate and the transport directly.
 * The one thing it must never do is hit the network, and the transport is
 * injectable precisely so it does not: every send here goes through a fake.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { configuredProviders } from "@/authPosture";
import {
  DEFAULT_OTP_MAX_AGE_S,
  EMAIL_OTP_ADAPTER_READY,
  EMAIL_OTP_CONSUME_PATH,
  EMAIL_OTP_LABEL,
  EMAIL_OTP_PROVIDER_ID,
  EMAIL_OTP_SEND_PATH,
  EMAIL_OTP_TOKEN_PATH,
  NO_STORED_USER,
  OTP_CODE_LENGTH,
  RESEND_ENDPOINT,
  canonicalOtpIdentifier,
  codeEntryState,
  emailOtpAdapterOver,
  emailOtpFrom,
  generateOtp,
  isEmailOtpConfigured,
  isEmailOtpProviderReady,
  otpEmail,
  otpWireHash,
  resendSender,
  reserveOtpSendVia,
  sendOtpEmail,
  type OtpGatewayCall,
  type OtpGatewayResponse,
  type ResendSendArgs,
} from "@/lib/emailOtp";

describe("the configured gate ships dark — both positions (CP-2d)", () => {
  it('is OFF unless EMAIL_OTP_ENABLED is exactly "true" AND a key is present', () => {
    // The whole matrix. Anything but both ⇒ inert.
    expect(isEmailOtpConfigured({})).toBe(false);
    expect(isEmailOtpConfigured({ EMAIL_OTP_ENABLED: "true" })).toBe(false);
    expect(isEmailOtpConfigured({ RESEND_API_KEY: "re_x" })).toBe(false);
    expect(
      isEmailOtpConfigured({ EMAIL_OTP_ENABLED: "true", RESEND_API_KEY: "re_x" }),
    ).toBe(true);
  });

  it("is EQUALITY against \"true\", never truthiness", () => {
    // An operator debugging a sign-in outage who writes `=false` (or `1`, or
    // `yes`) must get OFF — every truthy-string reading would arm the provider.
    for (const v of ["false", "1", "yes", "TRUE", "on", ""]) {
      expect(
        isEmailOtpConfigured({ EMAIL_OTP_ENABLED: v, RESEND_API_KEY: "re_x" }),
      ).toBe(false);
    }
  });

  it("the sign-in seam offers the option only when configured, and never otherwise", () => {
    // The surface derives from `configuredProviders`, so this is the button's
    // dark proof: with the flag off / key absent the seam yields NO resend
    // entry, so the sign-in page renders no email field. `adapterReady` forced
    // TRUE here isolates the ENV half — the adapter half is the next test.
    for (const env of [
      {},
      { EMAIL_OTP_ENABLED: "true" },
      { RESEND_API_KEY: "re_x" },
    ]) {
      expect(
        configuredProviders(env, true).some((p) => p.id === EMAIL_OTP_PROVIDER_ID),
      ).toBe(false);
    }

    // Env configured AND adapter ready ⇒ the entry appears, last, in its shape.
    const lit = configuredProviders(
      { EMAIL_OTP_ENABLED: "true", RESEND_API_KEY: "re_x" },
      true,
    );
    const entry = lit.find((p) => p.id === EMAIL_OTP_PROVIDER_ID);
    expect(entry).toEqual({
      id: EMAIL_OTP_PROVIDER_ID,
      label: EMAIL_OTP_LABEL,
      kind: "email",
    });
    // OAuth options still lead; the email option is last.
    expect(lit[lit.length - 1].id).toBe(EMAIL_OTP_PROVIDER_ID);
  });

  it("the sender is deployment-overridable but has a safe default", () => {
    expect(emailOtpFrom({})).toBe("Metorite <no-reply@metorite.com>");
    expect(emailOtpFrom({ EMAIL_OTP_FROM: "Acme <hi@acme.test>" })).toBe(
      "Acme <hi@acme.test>",
    );
  });
});

describe("the adapter guard makes the flag un-footgun-able (CP-2d hardening)", () => {
  // The invariant that turns a one-line owner flag from a site-wide auth DoS
  // into a no-op. An email provider registered WITHOUT an Auth.js database
  // adapter makes `@auth/core` return `MissingAdapter` on EVERY `/api/auth/*`
  // request — 500ing ALL sign-in (Google/Microsoft included), not just OTP. So
  // registration requires the adapter, not merely the env flag.
  it("the adapter IS wired since slice 2 — the source of truth is true", () => {
    // ⚠️ This is the ONE assertion slice 2 inverted, and inverting it was the
    // point: the constant is what `auth.ts` reads to decide whether to register
    // the provider, and slice 2 passes `emailOtpAdapter()` as NextAuth's
    // `adapter` in the same change. Setting it back to `false` while `auth.ts`
    // still passes an adapter would leave a live adapter with no provider
    // (harmless); the reverse is the site-wide outage, which is why the two
    // must move together. `signin.test.ts` fences the `auth.ts` half.
    expect(EMAIL_OTP_ADAPTER_READY).toBe(true);
  });

  it("adapter NOT ready ⇒ provider NOT ready and no button, however configured", () => {
    const configured = { EMAIL_OTP_ENABLED: "true", RESEND_API_KEY: "re_x" };
    // The env half is satisfied…
    expect(isEmailOtpConfigured(configured)).toBe(true);
    // …and the REAL gate still refuses when the adapter half is absent. Driven
    // through the PARAMETER now that the constant is true, so the guard stays
    // executed rather than becoming a tautology the day it was armed. Removing
    // the `&& adapterReady` term from `isEmailOtpProviderReady` turns this RED.
    expect(isEmailOtpProviderReady(configured, false)).toBe(false);
    // And the sign-in seam therefore offers no email entry — the button never
    // advertises a door that would 500 sign-in. Dropping the adapter term from
    // `configuredProviders` turns this RED.
    expect(
      configuredProviders(configured, false).some(
        (p) => p.id === EMAIL_OTP_PROVIDER_ID,
      ),
    ).toBe(false);
  });

  it("the combined gate is exactly env-AND-adapter — the one source of truth", () => {
    // Ties the gate to the constant both ways, so neither term can be silently
    // dropped: env-off never opens even with the adapter ready, and env-on stays
    // closed while the adapter is unready.
    for (const env of [
      {},
      { EMAIL_OTP_ENABLED: "true" },
      { RESEND_API_KEY: "re_x" },
      { EMAIL_OTP_ENABLED: "true", RESEND_API_KEY: "re_x" },
    ]) {
      expect(isEmailOtpProviderReady(env, false)).toBe(false);
      expect(isEmailOtpProviderReady(env, true)).toBe(isEmailOtpConfigured(env));
      // The default arg reads the module constant (false today), so the
      // real-call-site gate matches env AND constant.
      expect(isEmailOtpProviderReady(env)).toBe(
        isEmailOtpConfigured(env) && EMAIL_OTP_ADAPTER_READY,
      );
    }
  });
});

describe("the code is a numeric OTP, not a magic link (owner's ask)", () => {
  it("is a zero-padded 6-digit numeric string", () => {
    for (let i = 0; i < 500; i++) {
      const code = generateOtp();
      expect(code).toMatch(/^\d{6}$/);
      expect(code.length).toBe(OTP_CODE_LENGTH);
    }
  });

  it("is not a constant — it varies across calls", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i++) seen.add(generateOtp());
    // 200 crypto draws over a 10^6 space collide vanishingly rarely; a constant
    // (the obvious mis-implementation) would collapse to a set of size 1.
    expect(seen.size).toBeGreaterThan(100);
  });

  it("the email carries the code and names no password", () => {
    const { subject, text, html } = otpEmail("012345", DEFAULT_OTP_MAX_AGE_S);
    expect(subject).toMatch(/sign-in code/i);
    expect(text).toContain("012345");
    expect(html).toContain("012345");
    // Passwordless by D46.3 — the copy must not imply a password system.
    for (const s of [subject, text, html]) {
      expect(s).not.toMatch(/password/i);
    }
    // Expiry is stated so the code reads as a step, not a standing secret.
    expect(text).toMatch(/expires in \d+ minutes/);
  });
});

describe("the transport is Resend and is never invoked over the network in tests", () => {
  it("sendOtpEmail hands a Resend-shaped payload to the injected sender", async () => {
    const sent: ResendSendArgs[] = [];
    const fake = vi.fn(async (args: ResendSendArgs) => {
      sent.push(args);
    });
    await sendOtpEmail(fake, {
      to: "person@customer.test",
      from: "Metorite <no-reply@metorite.com>",
      code: "424242",
    });
    expect(fake).toHaveBeenCalledOnce();
    expect(sent[0].to).toBe("person@customer.test");
    expect(sent[0].from).toBe("Metorite <no-reply@metorite.com>");
    expect(sent[0].text).toContain("424242");
    expect(sent[0].html).toContain("424242");
    expect(sent[0].subject).toMatch(/sign-in code/i);
  });

  it("resendSender posts to Resend with the bearer key — asserted via a FAKE fetch, no network", async () => {
    const fetchSpy = vi.fn(
      async () => new Response(JSON.stringify({ id: "email_1" }), { status: 200 }),
    );
    const send = resendSender("re_secret", fetchSpy as unknown as typeof fetch);
    await send({
      to: "p@c.test",
      from: "f@m.test",
      subject: "s",
      html: "<p>x</p>",
      text: "x",
    });
    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(RESEND_ENDPOINT);
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer re_secret",
    );
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain("p@c.test");
  });

  it("fails CLOSED — a non-2xx from Resend throws rather than reporting success", async () => {
    const fetchSpy = vi.fn(
      async () => new Response("nope", { status: 422 }),
    );
    const send = resendSender("re_secret", fetchSpy as unknown as typeof fetch);
    await expect(
      send({ to: "p@c.test", from: "f@m.test", subject: "s", html: "h", text: "t" }),
    ).rejects.toThrow(/Resend send failed \(422\)/);
  });
});

// ── The ADAPTER, executed (repair of review finding P0, 2026-08-23) ──────────
//
// ⚠️ This block exists because the previous fence for the adapter's method set
// was a source-regex over a docstring, and the docstring was WRONG: it claimed
// `@auth/core`'s oauth arms were "unreachable in this configuration". They are
// reached on every Google callback the moment an adapter is present
// (`lib/actions/callback/index.js:56-62`), so the armed adapter would have
// thrown a `TypeError` — a site-wide sign-in outage — on the day the owner
// flipped the flag. A regex cannot catch a false premise; a replay can.
//
// So the two arms are transcribed below from the PINNED `@auth/core@0.41.2`
// source, line by line with citations, and driven against the real object. The
// helper deliberately raises a `TypeError` for a missing method, exactly as the
// destructure-then-call in `handle-login.js:29` does — the failure mode here is
// the production one, not a nicer stand-in.
//
// The *set* of call sites is fenced separately and from the source itself, in
// `emailOtpAdapter.test.ts`: this file proves the methods behave, that one
// proves none is missing.

/* eslint-disable @typescript-eslint/no-explicit-any */

/** A canned gateway answer. */
function answer(status: number, payload?: unknown): OtpGatewayResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => (payload === undefined ? "" : JSON.stringify(payload)),
  };
}

interface Recorded {
  path: string;
  identifier: string;
  body: Record<string, unknown>;
}

/** A transport that records every call and answers from a per-path table. */
function gateway(per: Record<string, OtpGatewayResponse>): {
  call: OtpGatewayCall;
  calls: Recorded[];
} {
  const calls: Recorded[] = [];
  const call: OtpGatewayCall = async (path, identifier, body) => {
    calls.push({ path, identifier, body });
    const res = per[path];
    if (!res) throw new Error(`the adapter called an unexpected path: ${path}`);
    return res;
  };
  return { call, calls };
}

/**
 * Fetch an adapter method the way `@auth/core` does — and fail the way it fails.
 *
 * `handle-login.js:29` destructures the whole method set and calls the members
 * bare, so an absent method is a `TypeError` at the call, not at the
 * destructure. Reproducing that is the point: P0 was exactly this `TypeError`,
 * and a helper that quietly skipped a missing method would have reported the
 * broken adapter as green.
 */
function must(adapter: any, name: string): (...args: any[]) => Promise<any> {
  const method = adapter[name];
  if (typeof method !== "function") {
    throw new TypeError(`adapter.${name} is not a function`);
  }
  return method.bind(adapter);
}

/**
 * The EMAIL arm, transcribed from `callback/index.js:141-171` and
 * `handle-login.js:34-88` under `session.strategy: "jwt"`.
 */
async function replayEmailArm(
  adapter: any,
  paramIdentifier: string,
  hashedToken: string,
): Promise<{ user: any; isNewUser: boolean }> {
  // index.js:141-145 — the identifier is the QUERY parameter, unnormalised.
  const invite = await must(adapter, "useVerificationToken")({
    identifier: paramIdentifier,
    token: hashedToken,
  });
  // index.js:146-154 — three ways to reject, all rendered as `Verification`.
  if (!invite) throw new Error("Verification: no invite");
  if (invite.expires.valueOf() < Date.now()) {
    throw new Error("Verification: expired");
  }
  if (paramIdentifier && invite.identifier !== paramIdentifier) {
    throw new Error("Verification: identifier mismatch");
  }
  // index.js:155-160 — note the `??`: a null adapter answer is not an error, it
  // is where @auth/core derives the user itself.
  const identifier: string = invite.identifier;
  const user = (await must(adapter, "getUserByEmail")(identifier)) ?? {
    id: "auth-core-randomUUID",
    email: identifier,
    emailVerified: null,
  };
  // handle-login.js:34-46 — no session cookie in this replay, so no getUser.
  // handle-login.js:55-79 — the email branch.
  const userByEmail = await must(adapter, "getUserByEmail")(user.email);
  if (userByEmail) {
    return {
      user: await must(adapter, "updateUser")({
        id: userByEmail.id,
        emailVerified: new Date(),
      }),
      isNewUser: false,
    };
  }
  return {
    user: await must(adapter, "createUser")({
      ...user,
      emailVerified: new Date(),
    }),
    isNewUser: true,
  };
}

/**
 * The OAUTH arm, transcribed from `callback/index.js:55-70` and
 * `handle-login.js:29-46, 174-273` under `session.strategy: "jwt"`.
 *
 * `authorizedUser` is what the `signIn` callback is handed (index.js:63-67) —
 * pinned separately because that is where CP-2b reads the display name.
 */
async function replayOAuthArm(
  adapter: any,
  userFromProvider: any,
  account: any,
  opts: { sessionCookie?: boolean } = {},
): Promise<{ user: any; authorizedUser: any; isNewUser: boolean }> {
  // index.js:55-62 — UNCONDITIONAL once an adapter is present. This is the line
  // the old five-method adapter died on.
  const userByAccount = await must(adapter, "getUserByAccount")({
    providerAccountId: account.providerAccountId,
    provider: account.provider,
  });
  // index.js:63-67
  const authorizedUser = userByAccount ?? userFromProvider;

  // handle-login.js:34-46 — jwt branch: getUser, only with a session cookie.
  const sessionUser = opts.sessionCookie
    ? await must(adapter, "getUser")("sub-from-the-decoded-jwt")
    : null;

  // handle-login.js:174-178
  const byAccount = await must(adapter, "getUserByAccount")({
    providerAccountId: account.providerAccountId,
    provider: account.provider,
  });
  if (byAccount) {
    // :179-199 — with no session user, this signs in as the account's user.
    return { user: byAccount, authorizedUser, isNewUser: false };
  }
  // :206-213
  if (sessionUser) {
    await must(adapter, "linkAccount")({ ...account, userId: sessionUser.id });
    return { user: sessionUser, authorizedUser, isNewUser: false };
  }
  // :231-252 — the OAuthAccountNotLinked trap.
  // `allowDangerousEmailAccountLinking` is NOT set on either provider in
  // `auth.ts`, and must not be.
  const byEmail = userFromProvider.email
    ? await must(adapter, "getUserByEmail")(userFromProvider.email)
    : null;
  if (byEmail) throw new Error("OAuthAccountNotLinked");
  // :253-265
  const created = await must(adapter, "createUser")({
    ...userFromProvider,
    emailVerified: null,
  });
  await must(adapter, "linkAccount")({ ...account, userId: created.id });
  return { user: created, authorizedUser, isNewUser: true };
}

const GOOGLE_USER = {
  id: "1099876",
  name: "Ada Lovelace",
  email: "ada@customer.example",
  image: "https://lh3.example/ada.png",
};
const GOOGLE_ACCOUNT = {
  providerAccountId: "1099876",
  provider: "google",
  type: "oauth",
};
const LIVE_RECORD = {
  identifier: "ada@customer.example",
  token: "sha256-of-the-code",
  expires: new Date(Date.now() + 600_000).toISOString(),
};

function wiredGateway() {
  return gateway({
    [EMAIL_OTP_SEND_PATH]: answer(200, { ...LIVE_RECORD, token: null }),
    [EMAIL_OTP_TOKEN_PATH]: answer(200, LIVE_RECORD),
    [EMAIL_OTP_CONSUME_PATH]: answer(200, LIVE_RECORD),
  });
}

describe("the armed adapter leaves OAuth sign-in exactly as it was (P0)", () => {
  it("a Google callback completes, and reaches no OTP route at all", async () => {
    const { call, calls } = wiredGateway();
    const out = await replayOAuthArm(
      emailOtpAdapterOver(call),
      GOOGLE_USER,
      GOOGLE_ACCOUNT,
    );
    // The whole finding, in one assertion: this used to throw
    // `TypeError: getUserByAccount is not a function`.
    expect(out.user).toEqual({ ...GOOGLE_USER, emailVerified: null });
    // Name and image survive, so `session.user.name` and CP-2b's
    // `display_name` are unchanged — a derived user would have dropped both.
    expect(out.user.name).toBe("Ada Lovelace");
    expect(out.authorizedUser).toBe(GOOGLE_USER);
    // And no OAuth sign-in ever touches the OTP routes.
    expect(calls).toEqual([]);
  });

  it("does NOT answer getUserByEmail on the OAuth arm — the OAuthAccountNotLinked trap", async () => {
    // The second half of P0, and the one a `getUserByAccount` stub alone would
    // have left broken. `handle-login.js:231-250`: a truthy `getUserByEmail`
    // with no `allowDangerousEmailAccountLinking` is a hard refusal, so the old
    // TOTAL derived user would have refused every Google sign-in even after the
    // missing method was added.
    const adapter = emailOtpAdapterOver(wiredGateway().call);
    expect(await adapter.getUserByEmail!("ada@customer.example")).toBeNull();
    await expect(
      replayOAuthArm(adapter, GOOGLE_USER, GOOGLE_ACCOUNT),
    ).resolves.toBeTruthy();
  });

  it("an already-signed-in person re-authing keeps the provider's user", async () => {
    // With a session cookie present, `handle-login.js:40` calls `getUser`. A
    // truthy answer takes the `:206` link-and-return branch and returns THAT
    // object as the signed-in user — silently dropping the provider's name and
    // picture. Null keeps the adapter-less behaviour.
    const adapter = emailOtpAdapterOver(wiredGateway().call);
    const out = await replayOAuthArm(adapter, GOOGLE_USER, GOOGLE_ACCOUNT, {
      sessionCookie: true,
    });
    expect(out.user.email).toBe("ada@customer.example");
    expect(out.user.name).toBe("Ada Lovelace");
  });

  it("implements every method both arms reach — none throws a TypeError", () => {
    // Belt to the source-derived brace in `emailOtpAdapter.test.ts`: there the
    // set is read out of node_modules, here it is exercised.
    const adapter = emailOtpAdapterOver(
      wiredGateway().call,
    ) as unknown as Record<string, unknown>;
    for (const name of [
      "createVerificationToken",
      "useVerificationToken",
      "getUserByEmail",
      "getUserByAccount",
      "getUser",
      "createUser",
      "updateUser",
      "linkAccount",
    ]) {
      expect(typeof adapter[name]).toBe("function");
    }
  });

  it("writes no user, account or session row — every call is an OTP route", async () => {
    const { call, calls } = wiredGateway();
    const adapter = emailOtpAdapterOver(call);
    await replayOAuthArm(adapter, GOOGLE_USER, GOOGLE_ACCOUNT);
    await replayEmailArm(adapter, "ada@customer.example", "sha256-of-the-code");
    // Identity stays the tenant plane's (`app_user`/`user_identity`). A second
    // identity store would arrive as an innocuous `POST /users` from right here.
    expect(new Set(calls.map((c) => c.path))).toEqual(
      new Set([EMAIL_OTP_CONSUME_PATH]),
    );
  });
});

describe("the email arm still yields the derived user end-to-end", () => {
  const record = (identifier: string) => ({
    identifier,
    token: "sha256-of-the-code",
    expires: new Date(Date.now() + 600_000).toISOString(),
  });

  it("createUser carries the verified address into the session", async () => {
    // `getUserByEmail` answers null, so `index.js:156-160` derives the user and
    // `handle-login.js:76` creates it. This adapter echoes — so the address the
    // person proved they own is what the jwt callback receives.
    const { call } = gateway({
      [EMAIL_OTP_CONSUME_PATH]: answer(200, record("ada@customer.example")),
    });
    const out = await replayEmailArm(
      emailOtpAdapterOver(call),
      "ada@customer.example",
      "sha256-of-the-code",
    );
    expect(out.user.email).toBe("ada@customer.example");
    expect(out.user.emailVerified).toBeInstanceOf(Date);
    // The create path is the reachable one now that getUserByEmail is null.
    expect(out.isNewUser).toBe(true);
  });

  it("keeps updateUser unreachable STRUCTURALLY — getUserByEmail IS the constant", async () => {
    // ⚠️ **This assertion is an identity comparison on purpose** (repair of
    // review finding P2, round 2, 2026-08-23). It used to check that
    // `getUserByEmail` answered null for three literal addresses — which a
    // store-backed lookup that merely MISSED on those three would satisfy,
    // leaving the fence green while `handle-login.js:68` became reachable and
    // `updateUser`'s echo returned a user with no address (an anonymous
    // session). Comparing against the exported implementation makes ANY change
    // away from constant-null red: a store, a cache, a conditional, a wrapper.
    //
    // `updateUser` is implemented rather than omitted (an absent method a
    // future arm reaches is P0 again), and `@auth/core` calls it with
    // `{ id, emailVerified }` alone — so giving `getUserByEmail` a store is
    // never a one-line change; the two must be rewritten together.
    const adapter = emailOtpAdapterOver(gateway({}).call);
    expect(adapter.getUserByEmail).toBe(NO_STORED_USER);
    // …and the constant really is constant-null, for anything at all.
    for (const address of ["", "ada@customer.example", "ADA@CUSTOMER.EXAMPLE"]) {
      expect(await NO_STORED_USER(address)).toBeNull();
      expect(await adapter.getUserByEmail!(address)).toBeNull();
    }
    // The trap it guards: `updateUser` echoes, so a reachable one answers with
    // no `email` at all. Asserted so the coupling's CONSEQUENCE is on record
    // here rather than only in a comment.
    const echoed = (await adapter.updateUser!({
      id: "u1",
      emailVerified: new Date(),
    } as never)) as { email?: string };
    expect(echoed.email).toBeUndefined();
  });

  it("a MIXED-CASE address does not brick the code after spending it (P1a)", async () => {
    // ⚠️ The finding, driven at the wire shape that produced it. The send leg
    // normalised (`send-token.js:12`), so the row — and the gateway's echo — is
    // `ada@customer.example`; the hand-built GET form submitted what was typed.
    // `callback/index.js:151-152` compares the two VERBATIM and throws AFTER
    // this consume has already spent the token and charged an attempt.
    const { call, calls } = gateway({
      // The gateway answers with its own canonical form, as it must.
      [EMAIL_OTP_CONSUME_PATH]: answer(200, record("ada@customer.example")),
    });
    const out = await replayEmailArm(
      emailOtpAdapterOver(call),
      "Ada@Customer.Example",
      "sha256-of-the-code",
    );
    // No throw: the adapter echoes the REQUESTED identifier, so the comparison
    // cannot strand a person on a code they have already spent.
    expect(out.user.email).toBe("Ada@Customer.Example");
    // Ownership is still enforced server-side — the consume was scoped to the
    // address asked about, which the gateway canonicalises and matches on.
    expect(calls.map((c) => c.identifier)).toEqual(["Ada@Customer.Example"]);
  });

  it("canonicalises the identifier the way @auth/core's send leg does", () => {
    // The browser-side half of the same repair: both write sites pass through
    // here, so in practice the submitted address IS the one the token was minted
    // for. Mirrors `send-token.js:74-98` — lowercase, trim, domain truncated at
    // the first comma.
    expect(canonicalOtpIdentifier("Ada@Customer.Example")).toBe(
      "ada@customer.example",
    );
    expect(canonicalOtpIdentifier("  ADA@customer.example  ")).toBe(
      "ada@customer.example",
    );
    expect(canonicalOtpIdentifier("ada@customer.example,x")).toBe(
      "ada@customer.example",
    );
    // Never throws, unlike `defaultNormalizer` — a browser-side mirror that
    // threw would replace a readable refusal with a blank page.
    for (const bad of ["", null, undefined, "no-at-sign", "a@b@c", 'q"@x.io']) {
      expect(() => canonicalOtpIdentifier(bad as string)).not.toThrow();
    }
    expect(canonicalOtpIdentifier(null)).toBe("");
  });
});

describe("the code page's state is decided once, from the stash (P1 round 2)", () => {
  // ⚠️ The finding, as a property. `/signin/code`'s no-stashed-address arm was
  // UNUSABLE: the component computed `known = canonicalOtpIdentifier(email) !==
  // ""` over the live input, and `"a"` canonicalises to `"a"`, so the FIRST
  // keystroke flipped `known` true and unmounted the field being typed into.
  // Nobody without a stash could ever finish entering their address.
  //
  // There is no DOM renderer in this package (node-env vitest, deliberately),
  // so the decision was extracted here where it can be executed. What the
  // component may not do — recompute `known` itself — is pinned by source regex
  // in `src/app/signin/code/code.test.ts`.

  // Every prefix of a real address, plus the shapes a person actually types.
  const TYPING = [
    "",
    "a",
    "ad",
    "ada",
    "ada@",
    "ada@c",
    "ada@customer",
    "ada@customer.example",
    "  Ada@Customer.Example  ",
    "ADA@CUSTOMER.EXAMPLE",
  ];

  it("with NO stash, `known` stays false through the WHOLE typing sequence", () => {
    // The defect, driven directly: every one of these used to flip `known` true
    // from index 1 onward.
    for (const stash of [null, undefined, "", "   "]) {
      for (const typed of TYPING) {
        expect(codeEntryState(stash, typed).known).toBe(false);
      }
    }
  });

  it("with no stash, the SUBMITTED address is the canonical form of what was typed", () => {
    // The field is shown, so the person's own input is what gets submitted —
    // and it must still be canonical, or it bricks the code exactly the way
    // P1a's prefilled arm did (`callback/index.js:151-152` compares verbatim
    // AFTER `useVerificationToken` has spent the row).
    expect(codeEntryState(null, "  Ada@Customer.Example  ").submitEmail).toBe(
      "ada@customer.example",
    );
    expect(codeEntryState(null, "ADA@CUSTOMER.EXAMPLE").submitEmail).toBe(
      "ada@customer.example",
    );
    expect(codeEntryState(null, "ada@customer.example,x").submitEmail).toBe(
      "ada@customer.example",
    );
    // A half-typed address submits a half-typed address — which misses
    // server-side and renders "that code is not valid". The form's `required`
    // + `type="email"` is what stops it being sent at all; nothing here throws.
    expect(codeEntryState(null, "ad").submitEmail).toBe("ad");
    expect(codeEntryState(null, "").submitEmail).toBe("");
  });

  it("with a stash, `known` is true and the typed value is IGNORED", () => {
    // The prefilled arm: the visible field is not rendered, so whatever is in
    // `typed` (nothing, in practice) must not reach the wire.
    for (const typed of TYPING) {
      const state = codeEntryState("Ada@Customer.Example", typed);
      expect(state.known).toBe(true);
      expect(state.submitEmail).toBe("ada@customer.example");
    }
  });

  it("a whitespace-only stash is NOT an address — the field is shown", () => {
    // `sessionStorage` can hold junk, and a known-but-blank page is a dead end:
    // no field to type into and a hidden `email` of "".
    expect(codeEntryState("   ", "").known).toBe(false);
    expect(codeEntryState("\n\t", "ada@customer.example")).toEqual({
      known: false,
      submitEmail: "ada@customer.example",
    });
  });

  it("`known` is a function of the stash ALONE — never of what is typed", () => {
    // The property in one assertion: hold the stash, vary the input across the
    // whole sequence, and the decision must not move. Deriving `known` from the
    // typed value in any form fails this.
    for (const stash of [null, "", "   ", "ada@customer.example", "A@B.IO"]) {
      const decisions = new Set(
        TYPING.map((typed) => codeEntryState(stash, typed).known),
      );
      expect(decisions.size).toBe(1);
    }
  });
});

describe("the recomputed wire hash EQUALS @auth/core's own (P2 round 2)", () => {
  // ⚠️ This is the fence that makes it safe for `auth.ts` to hand the send-slot
  // claim a hash it computed itself. `send-token.js:61` stores
  // `createHash(`${token}${secret}`)` and hands `sendVerificationRequest` only
  // the RAW code — so the two legs of one send knew different halves, and in a
  // same-`expires` burst the loser's `createVerificationToken` overwrote the
  // winner's hash on the shared row: the ONE email delivered carried a code that
  // could never verify. Recomputing the hash on the send leg fixes that, and it
  // is only safe while the recomputation AGREES. If it ever drifted, the claim
  // would write a hash the mailed code cannot produce and the server-side
  // "first writer wins" would preserve it — sign-in broken with no error
  // anywhere. So the agreement is EXECUTED against the pinned package rather
  // than argued.
  const CORE_WEB = fileURLToPath(
    new URL("../../node_modules/@auth/core/lib/utils/web.js", import.meta.url),
  );
  const SEND_TOKEN = fileURLToPath(
    new URL(
      "../../node_modules/@auth/core/lib/actions/signin/send-token.js",
      import.meta.url,
    ),
  );

  it("agrees with the pinned @auth/core's createHash, byte for byte", async () => {
    const core = (await import(pathToFileURL(CORE_WEB).href)) as {
      createHash: (message: string) => Promise<string>;
    };
    // Sanity: the import found the real function. A silently-missing one would
    // make every comparison below `undefined === undefined`.
    expect(typeof core.createHash).toBe("function");

    for (const [token, secret] of [
      ["012345", "dev-local-insecure-change-me"],
      ["000000", "s3cr3t"],
      ["999999", ""],
      // A leading-zero code and a secret with the characters that would break a
      // naive concatenation.
      ["000001", "a$b`c${d}"],
      // Non-ASCII, because the encoder is where a hand-rolled version differs.
      ["424242", "sécret-ключ"],
    ]) {
      expect(await otpWireHash(token, secret)).toBe(
        await core.createHash(`${token}${secret}`),
      );
    }
    // …and it is a lower-case hex SHA-256, not some other digest shape.
    expect(await otpWireHash("012345", "x")).toMatch(/^[0-9a-f]{64}$/);
  });

  it("pins the expression @auth/core hashes, and where the secret comes from", () => {
    // The agreement above is only meaningful while `send-token.js` still hashes
    // THAT expression with THAT secret. A version bump that salts differently
    // (or hashes the identifier in too) turns this red rather than shipping a
    // claim written with a hash nobody's code can produce.
    const src = readFileSync(SEND_TOKEN, "utf-8");
    expect(src).toContain("await createHash(`${token}${secret}`)");
    expect(src).toContain("const secret = provider.secret ?? options.secret;");
    // And the send leg really is handed the RAW code — which is what makes the
    // recomputation possible at all.
    expect(src).toMatch(/provider\.sendVerificationRequest\(\{[\s\S]{0,120}token,/);
  });
});

describe("the adapter's refusals reach @auth/core as refusals", () => {
  const consumeOnly = (res: OtpGatewayResponse) =>
    emailOtpAdapterOver(gateway({ [EMAIL_OTP_CONSUME_PATH]: res }).call);

  it("404 is the ONE non-throwing refusal — wrong, used or expired", async () => {
    const adapter = consumeOnly(answer(404, { code: "InvalidToken" }));
    expect(
      await adapter.useVerificationToken!({
        identifier: "ada@customer.example",
        token: "nope",
      }),
    ).toBeNull();
  });

  it("a 429 THROWS rather than answering null", async () => {
    // Swallowing a rate limit into `return null` renders as "your code is
    // wrong" to the person and "OTP is flaky" to an operator.
    const adapter = consumeOnly(answer(429, { code: "VerifyRateLimited" }));
    await expect(
      adapter.useVerificationToken!({
        identifier: "ada@customer.example",
        token: "424242",
      }),
    ).rejects.toThrow(/refused \(429\)/);
  });

  it("a 200 with no expiry throws instead of rendering as a bad code", async () => {
    // The row is already consumed by the time we are here, so a zero date —
    // which `@auth/core` reads as expired — would be P1a in a second dress.
    const adapter = consumeOnly(answer(200, { identifier: "a@b.io" }));
    await expect(
      adapter.useVerificationToken!({ identifier: "a@b.io", token: "x" }),
    ).rejects.toThrow(/without an expiry/);
  });

  it("a DUPLICATE send refusal throws, so no second mail goes out (P1b)", async () => {
    // The gateway now claims the send slot atomically and answers 429
    // `SendDuplicate` to every loser of a same-millisecond burst. That refusal
    // has to throw HERE, because `auth.ts` awaits this before handing anything
    // to Resend — the throw is what makes N simultaneous sends one email.
    const { call } = gateway({
      [EMAIL_OTP_SEND_PATH]: answer(429, { code: "SendDuplicate" }),
    });
    await expect(
      reserveOtpSendVia(call, "ada@customer.example", new Date(), "wire-hash"),
    ).rejects.toThrow(/refused \(429\)/);
  });

  it("reserveOtpSendVia carries the winning code's HASH, and nothing else (P2 round 2)", async () => {
    const { call, calls } = gateway({
      [EMAIL_OTP_SEND_PATH]: answer(200, {}),
    });
    const expires = new Date(Date.UTC(2026, 7, 23, 21, 0, 0));
    await reserveOtpSendVia(
      call,
      "ada@customer.example",
      expires,
      "sha256-of-the-mailed-code",
    );
    expect(calls).toEqual([
      {
        path: EMAIL_OTP_SEND_PATH,
        identifier: "ada@customer.example",
        // R11: no `email` / `identifier` in the body — the gateway 400s on
        // shape if one appears, and the address rides the seam's header.
        //
        // ⚠️ `token` IS in the body, and it is load-bearing: the claim writes
        // it, so the burst's surviving row holds the hash of the code that was
        // actually mailed rather than whichever loser's token leg landed last.
        // Dropping it here 400s the send (`gateway/routes/email_otp.py`) rather
        // than silently restoring the clobber.
        body: {
          expires: "2026-08-23T21:00:00.000Z",
          token: "sha256-of-the-mailed-code",
        },
      },
    ]);
  });
});
/* eslint-enable @typescript-eslint/no-explicit-any */
