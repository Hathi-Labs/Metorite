// The failure query of the mailbox connect (WS-17 EM-G7, E-C1).
//
// Each bounce names its provider, and only `gmail` or `microsoft` may ride on
// it. The provider comes from the path, which is request input, so any other
// value is dropped and never echoed. The route tests beside the two BFF
// routes run the handlers. This file pins the one rule they share.
import { describe, expect, it } from "vitest";

import { BOUNCE_PROVIDERS, bounceQuery } from "./bounce";

function parsed(qs: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(qs));
}

describe("bounceQuery (EM-G7 E-C1)", () => {
  it("names gmail and microsoft beside the reason", () => {
    expect(parsed(bounceQuery("gateway_unreachable", "gmail"))).toEqual({
      error: "gateway_unreachable",
      provider: "gmail",
    });
    expect(parsed(bounceQuery("callback_failed_403", "microsoft"))).toEqual({
      error: "callback_failed_403",
      provider: "microsoft",
    });
  });

  it("allows exactly the two providers of the connect flow", () => {
    expect([...BOUNCE_PROVIDERS].sort()).toEqual(["gmail", "microsoft"]);
  });

  it("never echoes another path segment", () => {
    const hostile = [
      "zoho",
      "imap",
      "Gmail",
      "MICROSOFT",
      " gmail",
      "gmail ",
      "gmail&provider=x",
      "..%2Fsettings",
      "<script>alert(1)</script>",
      "",
    ];
    for (const segment of hostile) {
      const qs = bounceQuery("unknown_provider", segment);
      expect(parsed(qs), segment).toEqual({ error: "unknown_provider" });
      if (segment) expect(qs.includes(encodeURIComponent(segment)), segment).toBe(false);
    }
  });

  it("encodes the reason, so a gateway detail stays one value", () => {
    const qs = bounceQuery("Gmail is not available yet.", "gmail");
    expect(parsed(qs)).toEqual({ error: "Gmail is not available yet.", provider: "gmail" });
    expect(qs).not.toContain(" ");
  });
});
