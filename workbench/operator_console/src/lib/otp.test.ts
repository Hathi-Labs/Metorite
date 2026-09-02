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
  EMAIL_OTP_FLAG,
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
    expect(emailCodeConfig(good)).toEqual({ url: URL_, anonKey: ANON });
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
      should_create_user: true,
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
