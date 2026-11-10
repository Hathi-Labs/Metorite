// WS-20 WA-C2: the Embedded Signup session messages. The payloads below
// quote the shapes in Meta's "Embedded Signup" and "Onboard WhatsApp Business
// app users" docs (session logging, version 3).

import { describe, expect, it } from "vitest";
import {
  COEXISTENCE_LIMITS,
  NO_NUMBER_MESSAGE,
  SIGNUP_EXTRAS,
  isFacebookOrigin,
  nextSignupStep,
  parseSignupMessage,
  signupErrorText,
} from "./signupSession";

const FB = "https://www.facebook.com";

const FINISH = {
  data: {
    phone_number_id: "1906385232743451",
    waba_id: "102290129340398",
    business_id: "2729063490586005",
  },
  type: "WA_EMBEDDED_SIGNUP",
  event: "FINISH",
  version: 3,
};

// Meta returns the WABA and no phone_number_id for a coexistence number.
const FINISH_COEXISTENCE = {
  data: { waba_id: "102290129340398" },
  type: "WA_EMBEDDED_SIGNUP",
  event: "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
  version: 3,
};

const FINISH_ONLY_WABA = {
  data: { waba_id: "102290129340398", business_id: "2729063490586005" },
  type: "WA_EMBEDDED_SIGNUP",
  event: "FINISH_ONLY_WABA",
  version: 3,
};

const CANCEL = {
  data: { current_step: "PHONE_NUMBER_SETUP" },
  type: "WA_EMBEDDED_SIGNUP",
  event: "CANCEL",
  version: 3,
};

const ERROR = {
  data: {
    error_message: "Your verified name violates WhatsApp guidelines.",
    error_id: "524126",
    session_id: "f34b51dab5e0498",
    timestamp: "1689211061",
  },
  type: "WA_EMBEDDED_SIGNUP",
  event: "ERROR",
  version: 3,
};

describe("parseSignupMessage", () => {
  it("reads FINISH as a cloud connect with its number", () => {
    expect(parseSignupMessage(FB, FINISH)).toEqual({
      kind: "finish",
      onboarding: "cloud",
      waba_id: "102290129340398",
      phone_number_id: "1906385232743451",
    });
  });

  it("reads FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING as coexistence, with no number", () => {
    const ev = parseSignupMessage(FB, JSON.stringify(FINISH_COEXISTENCE));
    expect(ev).toEqual({
      kind: "finish",
      onboarding: "coexistence",
      waba_id: "102290129340398",
    });
    // A later Meta version can send the number. Keep it then.
    const withNumber = {
      ...FINISH_COEXISTENCE,
      data: { ...FINISH_COEXISTENCE.data, phone_number_id: "1906385232743451" },
    };
    expect(parseSignupMessage(FB, withNumber)).toMatchObject({
      onboarding: "coexistence",
      phone_number_id: "1906385232743451",
    });
  });

  it("reads CANCEL with its step, and the page does not call the backend", () => {
    const ev = parseSignupMessage(FB, CANCEL);
    expect(ev).toEqual({ kind: "cancel", current_step: "PHONE_NUMBER_SETUP" });
    const step = nextSignupStep("auth-code", ev);
    expect(step).toEqual({
      action: "fail",
      message: "Signup was cancelled at step PHONE_NUMBER_SETUP. Nothing was connected.",
    });
  });

  it("reads ERROR with its message and id, and the page does not call the backend", () => {
    const ev = parseSignupMessage(FB, ERROR);
    expect(ev).toEqual({
      kind: "error",
      error_message: "Your verified name violates WhatsApp guidelines.",
      error_id: "524126",
    });
    const step = nextSignupStep("auth-code", ev);
    expect(step.action).toBe("fail");
    expect(step).toMatchObject({
      message: "Your verified name violates WhatsApp guidelines. (Meta error 524126)",
    });
    // Meta's doc also reports a member's error as a CANCEL with error_message.
    expect(parseSignupMessage(FB, { ...ERROR, event: "CANCEL" })).toMatchObject({
      kind: "error",
      error_id: "524126",
    });
  });

  it("reads FINISH_ONLY_WABA as an error: no number was selected", () => {
    const ev = parseSignupMessage(FB, FINISH_ONLY_WABA);
    expect(ev).toEqual({ kind: "error", error_message: NO_NUMBER_MESSAGE, error_id: "" });
    expect(signupErrorText(ev!)).toMatch(/no number was selected/i);
    expect(nextSignupStep("auth-code", ev).action).toBe("fail");
  });

  it("ignores a message that is not an Embedded Signup session message", () => {
    expect(parseSignupMessage(FB, { type: "OTHER", event: "FINISH" })).toBeNull();
    expect(parseSignupMessage(FB, "not json")).toBeNull();
    expect(parseSignupMessage(FB, { ...FINISH, event: "SOMETHING_NEW" })).toBeNull();
  });
});

describe("isFacebookOrigin", () => {
  it("accepts facebook.com and its subdomains over https", () => {
    expect(isFacebookOrigin("https://www.facebook.com")).toBe(true);
    expect(isFacebookOrigin("https://business.facebook.com")).toBe(true);
    expect(isFacebookOrigin("https://facebook.com")).toBe(true);
  });

  it("refuses a look-alike host, another host and plain http", () => {
    for (const origin of [
      "https://evilfacebook.com",
      "https://facebook.com.evil.example",
      "https://www.facebook.co",
      "http://www.facebook.com",
      "https://evil.example",
      "null",
      "",
    ]) {
      expect(isFacebookOrigin(origin), origin).toBe(false);
    }
  });

  it("drops a valid session message from a look-alike origin", () => {
    expect(parseSignupMessage("https://evilfacebook.com", FINISH)).toBeNull();
  });
});

describe("nextSignupStep", () => {
  const coexist = parseSignupMessage(FB, FINISH_COEXISTENCE);
  const finish = parseSignupMessage(FB, FINISH);

  it("waits when the callback runs before the message", () => {
    expect(nextSignupStep("auth-code", null)).toEqual({ action: "wait" });
  });

  it("waits when the message arrives before the callback", () => {
    expect(nextSignupStep(undefined, coexist)).toEqual({ action: "wait" });
  });

  it("submits a coexistence connect with waba_id and no phone_number_id", () => {
    expect(nextSignupStep("auth-code", coexist)).toEqual({
      action: "submit",
      request: { code: "auth-code", waba_id: "102290129340398", onboarding: "coexistence" },
    });
  });

  it("submits a plain FINISH with its number", () => {
    expect(nextSignupStep("auth-code", finish)).toEqual({
      action: "submit",
      request: {
        code: "auth-code",
        waba_id: "102290129340398",
        phone_number_id: "1906385232743451",
        onboarding: "cloud",
      },
    });
  });

  it("fails when the callback returns no code", () => {
    expect(nextSignupStep(null, null).action).toBe("fail");
    expect(nextSignupStep(null, finish).action).toBe("fail");
  });
});

describe("the coexistence request and its limits", () => {
  it("asks Meta's popup for WhatsApp Business app onboarding", () => {
    expect(SIGNUP_EXTRAS).toEqual({
      setup: {},
      featureType: "whatsapp_business_app_onboarding",
      sessionInfoVersion: "3",
    });
  });

  it("names all seven limits of spec §12.2", () => {
    const text = COEXISTENCE_LIMITS.join(" ").toLowerCase();
    for (const limit of [
      "group chats",
      "broadcast lists",
      "disappearing and view-once",
      "live location",
      "calls",
      "20 messages a second",
      "14 days",
    ]) {
      expect(text, limit).toContain(limit);
    }
    expect(COEXISTENCE_LIMITS).toHaveLength(7);
  });
});
