import { describe, expect, it } from "vitest";

import { compactMoney, money } from "./format";

describe("money and compactMoney never throw on a bad currency code", () => {
  it.each(["US", " usd ", "RUPEES", ""])("code %j", (code) => {
    expect(() => money(4500, code)).not.toThrow();
    expect(() => compactMoney(4500, code)).not.toThrow();
  });

  it("a code that is not three letters prints as it is, with the number", () => {
    expect(money(4500, "US")).toBe("US 4,500");
    expect(money(450000, "RUPEES")).toBe("RUPEES 4,50,000");
  });

  it("a padded lower case code is read as USD", () => {
    expect(money(4500, " usd ")).toBe(money(4500, "USD"));
    expect(compactMoney(4500, " usd ")).toBe(compactMoney(4500, "USD"));
  });

  it("an empty code is INR", () => {
    expect(money(4500, "")).toBe(money(4500, "INR"));
    expect(compactMoney(4500, "").startsWith("₹")).toBe(true);
  });
});
