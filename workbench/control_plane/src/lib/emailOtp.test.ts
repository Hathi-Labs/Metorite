/**
 * Fences for CP-2d's pure email-OTP logic (R7; customer_console.md §CP-2d).
 *
 * These RUN — the module is framework-free on purpose (`emailOtp.ts`'s own
 * docstring), so unlike the source-regex pins in `signin.test.ts` this file can
 * execute the code generator, the message, the gate and the transport directly.
 * The one thing it must never do is hit the network, and the transport is
 * injectable precisely so it does not: every send here goes through a fake.
 */
import { describe, expect, it, vi } from "vitest";

import { configuredProviders } from "@/authPosture";
import {
  DEFAULT_OTP_MAX_AGE_S,
  EMAIL_OTP_ADAPTER_READY,
  EMAIL_OTP_LABEL,
  EMAIL_OTP_PROVIDER_ID,
  OTP_CODE_LENGTH,
  RESEND_ENDPOINT,
  emailOtpFrom,
  generateOtp,
  isEmailOtpConfigured,
  isEmailOtpProviderReady,
  otpEmail,
  resendSender,
  sendOtpEmail,
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
