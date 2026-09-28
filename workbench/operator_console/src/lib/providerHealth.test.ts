// Provider balance and health — what the console draws, and when it shouts.
// Owner request, 2026-09-28.
//
// ⚠️ **The subject is the WRONG conclusion.** DeepSeek held -0.05 USD for two
// days while every screen looked fine. The ways this file can repeat that:
//
//   1. A vendor whose balance is invisible drawn GREEN.
//   2. A status this app does not know drawn green.
//   3. A failing vendor with no site-wide banner.
//   4. A banner that is always on, so nobody reads it.
//   5. A Console that predates the route blanking the page, or reading red.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { VendorHealth } from "./contract";
import { readConsoleEnv, type FetchLike } from "./console";
import {
  alertBannerClass,
  alertHeadline,
  describeBalance,
  describeDaysLeft,
  describeRefusals,
  healthAlert,
  healthFromWire,
  healthLabel,
  healthTone,
} from "./providerHealth";
import { readProviderHealth } from "./read";

const WIRE_ROW = {
  provider: "deepseek",
  status: "out",
  cause: "payment",
  last_success_at: null,
  reason: "The vendor reports that this account cannot serve calls.",
  balance: "-0.05",
  currency: "USD",
  available: false,
  balance_checked_at: "2026-09-28T10:00:00+00:00",
  probe_status: "ok",
  probe_error: null,
  probe_attempted_at: "2026-09-28T10:00:00+00:00",
  balance_exposed: true,
  threshold: "5",
  days_left: null,
  cost_7d_usd: "1.2",
  last_refusal_status: 402,
  last_refusal_at: "2026-09-28T10:01:00+00:00",
  refusals_24h: 12,
  server_errors_24h: 0,
};

const row = (over: Partial<VendorHealth> = {}): VendorHealth => ({
  ...healthFromWire({ providers: [WIRE_ROW] })[0],
  // A test that overrides the status states its cause, or gets the status's
  // own default. It must never inherit the base row's `payment`.
  cause: null,
  ...over,
});

describe("reading the wire", () => {
  it("maps every field it draws", () => {
    const r = healthFromWire({ providers: [WIRE_ROW] })[0];
    expect(r).toMatchObject({
      provider: "deepseek",
      status: "out",
      balance: "-0.05",
      currency: "USD",
      available: false,
      balanceExposed: true,
      lastRefusalStatus: 402,
      refusals24h: 12,
    });
  });

  it("🔴 a status it does not know reads UNKNOWN, never ok", () => {
    const r = healthFromWire({ providers: [{ ...WIRE_ROW, status: "brand-new" }] })[0];
    expect(r.status).toBe("unknown");
  });

  it("drops junk rows and survives a junk body", () => {
    expect(healthFromWire(null)).toEqual([]);
    expect(healthFromWire({ providers: "x" })).toEqual([]);
    expect(healthFromWire({ providers: [null, 3, { status: "ok" }] })).toEqual([]);
  });
});

describe("the chip", () => {
  it("🔴 'balance not visible' is NEUTRAL, never green", () => {
    expect(healthTone("unknown")).toBe("neutral");
    expect(healthLabel("unknown")).toBe("Balance not visible");
  });

  it("out and refusing are danger; rate-limited, low and a failed check are amber", () => {
    expect(healthTone("out")).toBe("danger");
    expect(healthTone("refusing")).toBe("danger");
    expect(healthTone("rate_limited")).toBe("warn");
    expect(healthTone("low")).toBe("warn");
    expect(healthTone("probe_failed")).toBe("warn");
    expect(healthTone("ok")).toBe("ok");
  });

  it("the labels name the cause", () => {
    expect(healthLabel("out")).toBe("Out of credit");
    expect(healthLabel("refusing")).toBe("Key rejected");
    expect(healthLabel("rate_limited")).toBe("Rate-limited");
  });
});

describe("the site-wide banner", () => {
  it("🔴 a 402 (payment) raises a DANGER banner that says TOP UP", () => {
    const a = healthAlert([row(), row({ provider: "aimlapi", status: "ok" })]);
    expect(a?.tone).toBe("danger");
    expect(a?.providers).toEqual(["deepseek"]);
    expect(a?.text).toContain("deepseek is out of credit");
    expect(a?.text).toContain("topped up");
    expect(alertBannerClass(a!)).toBe("banner danger");
    expect(alertHeadline(a!)).toBe("Out of credit — top up.");
  });

  it("🔴 a rejected key says KEY, never top up", () => {
    const a = healthAlert([row({ status: "refusing", cause: "key" })]);
    expect(a?.tone).toBe("danger");
    expect(alertHeadline(a!)).toBe("Key rejected.");
    expect(a?.text).toContain("rejecting our key");
    expect(a?.text).not.toMatch(/top(ped)? up/);
  });

  it("both causes at once name both, under one headline", () => {
    const a = healthAlert([
      row({ provider: "a", status: "low" }),
      row({ provider: "b", status: "refusing", cause: "key" }),
      row({ provider: "c", status: "out", cause: "payment" }),
    ]);
    expect(a?.tone).toBe("danger");
    expect(a?.providers).toEqual(["b", "c"]);
    expect(a?.text).toContain("c is out of credit");
    expect(a?.text).toContain("b is rejecting our key");
    expect(alertHeadline(a!)).toBe("AI is failing.");
  });

  it("🔴 a RATE LIMIT is AMBER and says rate-limited, never 'AI is failing' or top up", () => {
    const a = healthAlert([row({ status: "rate_limited", cause: "rate_limit" })]);
    expect(a?.tone).toBe("warn");
    expect(alertBannerClass(a!)).toBe("banner warn");
    expect(alertHeadline(a!)).toBe("Rate-limited.");
    expect(a?.text).toContain("rate-limiting our calls");
    expect(a?.text).not.toMatch(/top(ped)? up|AI is failing/);
  });

  it("a LOW vendor raises a warning that says top up", () => {
    const a = healthAlert([row({ status: "low", cause: "balance" })]);
    expect(a?.tone).toBe("warn");
    expect(alertBannerClass(a!)).toBe("banner warn");
    expect(alertHeadline(a!)).toBe("Balance low.");
  });

  it("a cause the wire omits falls back to the one the status implies", () => {
    const r = healthFromWire({ providers: [{ ...WIRE_ROW, status: "refusing", cause: undefined }] })[0];
    expect(r.cause).toBe("key");
  });

  it("🔴 'balance not visible' ALONE draws no banner, nor does a failed check", () => {
    expect(healthAlert([row({ status: "unknown" }), row({ status: "probe_failed" })])).toBeNull();
    expect(healthAlert([row({ status: "ok" })])).toBeNull();
    expect(healthAlert([])).toBeNull();
  });

  it("the stylesheet defines the two banner classes it uses", () => {
    const css = readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8");
    expect(css).toMatch(/\n\.banner \{/);
    expect(css).toMatch(/\n\.banner\.danger \{/);
    expect(css).toMatch(/\n\.banner\.warn \{/);
    expect(css).toMatch(/\.wrap\.alertbar \{/);
  });

  it("is mounted in the ROOT layout, so every page carries it", () => {
    const layout = readFileSync(join(__dirname, "..", "app", "layout.tsx"), "utf8");
    expect(layout).toContain("<ProviderAlert />");
  });
});

describe("the words in a row", () => {
  it("names the currency and converts nothing", () => {
    expect(describeBalance(row({ balance: "110", currency: "CNY" }))).toBe("110.00 CNY");
  });

  it("says 'not exposed' for a vendor with no probe, and 'not read yet' for one with", () => {
    expect(describeBalance(row({ balance: null, balanceExposed: false }))).toBe("not exposed");
    expect(describeBalance(row({ balance: null, balanceExposed: true }))).toBe("not read yet");
  });

  it("days left and refusals", () => {
    expect(describeDaysLeft(row({ daysLeft: "2.5" }))).toBe("about 2.5 days");
    expect(describeDaysLeft(row({ daysLeft: null }))).toBe("—");
    expect(describeRefusals(row())).toBe("12 refused (last 402) in 24 h");
    expect(describeRefusals(row({ refusals24h: 0, serverErrors24h: 0 }))).toBe("none in 24 h");
  });
});

describe("the read", () => {
  const ENV = readConsoleEnv({
    CUSTOMER_CONSOLE_URL: "https://console.internal",
    CUSTOMER_CONSOLE_OPERATOR_TOKEN: "op-secret-token",
  });
  const answer = (status: number, body: unknown): FetchLike => async () => ({
    status,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  });

  it("a live answer is live", async () => {
    const r = await readProviderHealth({ env: ENV, fetchImpl: answer(200, { providers: [WIRE_ROW] }) });
    expect(r.origin).toBe("live");
    expect(r.data[0].status).toBe("out");
  });

  it("🔴 a Console that PREDATES the route is 'missing' with a sentence, never red", async () => {
    const r = await readProviderHealth({ env: ENV, fetchImpl: answer(404, { detail: "Not Found" }) });
    expect(r.origin).toBe("missing");
    expect(r.data).toEqual([]);
    expect(r.note).toContain("/providers/health");
  });

  it("a refusal is an error, and draws no sample rows", async () => {
    const r = await readProviderHealth({ env: ENV, fetchImpl: answer(500, "boom") });
    expect(r.origin).toBe("error");
    expect(r.data).toEqual([]);
  });

  it("an unconfigured deployment is quiet, not an error", async () => {
    const r = await readProviderHealth({ env: readConsoleEnv({}), fetchImpl: answer(200, {}) });
    expect(r.origin).toBe("missing");
  });
});
