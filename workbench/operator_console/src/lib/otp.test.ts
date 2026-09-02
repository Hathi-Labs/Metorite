/**
 * The email-code fallback's server-side decisions — WS-31 CP-12j.
 *
 * Spec: `project-docs/specs/operator_identity_and_access.md` §4.1b · **D71.3**
 * and §8 done-when 41.
 *
 * 🔴 **The case that matters most is `isPublishableKey`.** The login page hands
 * this key to every visitor's browser. The anon key is meant for that. The
 * `service_role` key sits one line away in the same Supabase dashboard, looks
 * identical, and bypasses row-level security on every table. Publishing it
 * would look like nothing going wrong.
 */
import { describe, expect, it } from "vitest";

import {
  ANON_KEY_FLAG,
  DIRECTORY_SIGNIN_FLAG,
  EMAIL_OTP_FLAG,
  directorySigninEnabled,
  emailCodeConfig,
  emailOtpEnabled,
  isPublishableKey,
  otpStartBody,
  otpStartUrl,
  otpVerifyBody,
  otpVerifyUrl,
} from "./otp";

const URL_ = "https://project.supabase.co";

//: A legacy Supabase key is an unsigned-by-us JWT whose payload names a role.
function jwt(role: string): string {
  const head = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .toString("base64url");
  const body = Buffer.from(JSON.stringify({ iss: "supabase", role }))
    .toString("base64url");
  return `${head}.${body}.signature-not-checked`;
}

const ANON = jwt("anon");
const SERVICE = jwt("service_role");

describe("emailOtpEnabled", () => {
  it("is OFF unless the box says otherwise", () => {
    expect(emailOtpEnabled({})).toBe(false);
    expect(emailOtpEnabled({ [EMAIL_OTP_FLAG]: "" })).toBe(false);
    expect(emailOtpEnabled({ [EMAIL_OTP_FLAG]: "0" })).toBe(false);
    expect(emailOtpEnabled({ [EMAIL_OTP_FLAG]: "no" })).toBe(false);
  });

  it("reads the same four truthy words the Console reads", () => {
    for (const on of ["1", "true", "YES", " On "]) {
      expect(emailOtpEnabled({ [EMAIL_OTP_FLAG]: on })).toBe(true);
    }
  });
});

describe("isPublishableKey", () => {
  it("🔴 REFUSES a service_role JWT", () => {
    expect(isPublishableKey(SERVICE)).toBe(false);
  });

  it("accepts an anon JWT", () => {
    expect(isPublishableKey(ANON)).toBe(true);
  });

  it("🔴 REFUSES the newer secret key, and accepts the publishable one", () => {
    expect(isPublishableKey("sb_secret_abc123")).toBe(false);
    expect(isPublishableKey("sb_publishable_abc123")).toBe(true);
  });

  it("refuses anything it cannot parse, rather than assuming", () => {
    // ⚠️ The whole point. A shape nobody anticipated is exactly the case
    // where "assume it is fine" publishes a secret.
    expect(isPublishableKey(undefined)).toBe(false);
    expect(isPublishableKey(null)).toBe(false);
    expect(isPublishableKey("")).toBe(false);
    expect(isPublishableKey("   ")).toBe(false);
    expect(isPublishableKey("not-a-jwt")).toBe(false);
    expect(isPublishableKey("a.b")).toBe(false);
    expect(isPublishableKey("a.!!!not-base64!!!.c")).toBe(false);
    expect(isPublishableKey(jwt("authenticated"))).toBe(false);
    // A JWT whose payload is valid base64 but not an object.
    expect(
      isPublishableKey(`x.${Buffer.from('"anon"').toString("base64url")}.y`),
    ).toBe(false);
  });
});

describe("emailCodeConfig", () => {
  const good = {
    [EMAIL_OTP_FLAG]: "1",
    OPERATOR_SUPABASE_URL: URL_,
    [ANON_KEY_FLAG]: ANON,
  };

  it("returns what the browser needs when all three are right", () => {
    expect(emailCodeConfig(good)).toEqual({
      url: URL_,
      anonKey: ANON,
      callback: "",
    });
  });

  it("builds the callback from OPERATOR_CONSOLE_ORIGIN", () => {
    const withOrigin = {
      ...good,
      OPERATOR_CONSOLE_ORIGIN: "https://op.metorite.com/",
    };
    expect(emailCodeConfig(withOrigin)?.callback).toBe(
      "https://op.metorite.com/login/callback",
    );
  });

  it("🔴 returns null for a service_role key, so the page shows no form", () => {
    // The page must not render, because rendering PUBLISHES the key.
    expect(emailCodeConfig({ ...good, [ANON_KEY_FLAG]: SERVICE })).toBeNull();
  });

  it("returns null for each of the other three reasons", () => {
    expect(emailCodeConfig({ ...good, [EMAIL_OTP_FLAG]: "0" })).toBeNull();
    expect(emailCodeConfig({ ...good, OPERATOR_SUPABASE_URL: "" })).toBeNull();
    expect(emailCodeConfig({ ...good, [ANON_KEY_FLAG]: "" })).toBeNull();
  });
});

describe("the Supabase endpoints", () => {
  it("🔴 carries redirect_to, or the emailed LINK cannot come back", () => {
    // Supabase's default template sends a link and no digits, and this
    // project cannot edit that template — the dashboard locks template
    // editing behind custom SMTP. So the link IS the flow, and this
    // parameter is the whole of what makes it land on our callback.
    expect(
      otpStartUrl("https://p.supabase.co", "https://op.metorite.com/login/callback"),
    ).toBe(
      "https://p.supabase.co/auth/v1/otp?redirect_to=" +
        encodeURIComponent("https://op.metorite.com/login/callback"),
    );
  });

  it("omits redirect_to when there is none, rather than sending an empty one", () => {
    // An empty `redirect_to` is not the same as none: Supabase validates it
    // against the allow list and would refuse the send outright.
    expect(otpStartUrl("https://p.supabase.co", "")).toBe(
      "https://p.supabase.co/auth/v1/otp",
    );
    expect(otpStartUrl("https://p.supabase.co", "   ")).toBe(
      "https://p.supabase.co/auth/v1/otp",
    );
  });

  it("tolerates a trailing slash on the project URL", () => {
    expect(otpStartUrl("https://p.supabase.co/")).toBe(
      "https://p.supabase.co/auth/v1/otp",
    );
    expect(otpVerifyUrl(" https://p.supabase.co ")).toBe(
      "https://p.supabase.co/auth/v1/verify",
    );
  });
});

describe("the request bodies", () => {
  it("🔴 uses the WIRE field `create_user`, not the supabase-js option name", () => {
    // ⚠️ **Two releases shipped `should_create_user` and it did nothing.**
    // That is the supabase-js OPTION name. GoTrue ignores an unknown field, so
    // it answered 200 and created the user regardless — which is why the
    // CP-12j "fix" changed no behaviour in either direction.
    //
    // Measured against the live project on 2026-09-02:
    //   {"create_user":false}        → 422 otp_disabled   (refused, no mail)
    //   {"should_create_user":false} → 200                (user made, mail sent)
    const body = otpStartBody("owner@example.com") as Record<string, unknown>;
    expect(Object.hasOwn(body, "create_user")).toBe(true);
    expect(Object.hasOwn(body, "should_create_user")).toBe(false);
  });

  it("🔴 asks Supabase to CREATE the user, or nobody ever signs in", () => {
    // ⚠️ **This is a regression fence for a defect that reached production.**
    // Supabase mails a code only to a user already in `auth.users`. That table
    // held ZERO rows on 2026-09-02, so `should_create_user: false` refused
    // every operator forever — including the first one. Flipping this back to
    // `false` must turn this case red.
    //
    // Safe because a Supabase user is NOT an operator: the registry answers
    // 403 for a stranger (D71.2, D71.6), so they gain a login to nothing.
    expect(otpStartBody("Owner@Example.com ")).toEqual({
      email: "Owner@Example.com",
      create_user: true,
    });
  });

  it("sends the code as an email-type verification", () => {
    expect(otpVerifyBody(" me@example.com ", " 123456 ")).toEqual({
      email: "me@example.com",
      token: "123456",
      type: "email",
    });
  });
});

describe("directorySigninEnabled", () => {
  it("⚠️ ABSENT means YES — the opposite of every other flag here", () => {
    // The other flags ADD a capability, so unset must not add one. This one
    // REMOVES a button that has been on the page since CP-12g, so unset must
    // not remove it. Flipping the default would silently take the directory
    // button off every deployment.
    expect(directorySigninEnabled({})).toBe(true);
    expect(directorySigninEnabled({ [DIRECTORY_SIGNIN_FLAG]: "" })).toBe(true);
    expect(directorySigninEnabled({ [DIRECTORY_SIGNIN_FLAG]: "  " })).toBe(true);
  });

  it("takes the button off when the box says so", () => {
    for (const off of ["0", "false", "NO", " Off "]) {
      expect(directorySigninEnabled({ [DIRECTORY_SIGNIN_FLAG]: off })).toBe(false);
    }
  });

  it("anything it does not recognise leaves the button ON", () => {
    // A typo must not remove the only working door on a box that has no
    // email fallback configured.
    expect(directorySigninEnabled({ [DIRECTORY_SIGNIN_FLAG]: "maybe" })).toBe(true);
  });
});
