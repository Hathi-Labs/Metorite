// My Day greets with the form of address (WS-63 PZ-4, `personalization.md` §5.2).
//
// Mutation: make `addressedGreetingLine` ignore `fetched`, and the first test
// fails. Make `fetchAddressName` answer "" on a 503, and the fault test fails.
import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import { ADDRESS_KEY, addressedGreetingLine, fetchAddressName } from "./addressName";
import { greetingLine } from "./myDay";

const morning = new Date(2026, 9, 24, 9, 0);
const evening = new Date(2026, 9, 24, 19, 0);

describe("addressedGreetingLine", () => {
  it("greets with the fetched name, whole", () => {
    expect(addressedGreetingLine(morning, "VJ", "Vijay Raghav Varada")).toBe("Good morning, VJ");
    expect(addressedGreetingLine(evening, "Dr. Rao", "Asha Rao")).toBe("Good evening, Dr. Rao");
  });

  it("keeps the session name while loading, after a fault, or with no name", () => {
    for (const fetched of [undefined, null, "", "  "]) {
      expect(addressedGreetingLine(morning, fetched, "Vijay Varada")).toBe("Good morning, Vijay");
    }
    expect(addressedGreetingLine(morning, undefined, "vijay@example.com")).toBe("Good morning");
  });

  it("leaves greetingLine as it was", () => {
    expect(greetingLine(morning, "Vijay Varada")).toBe("Good morning, Vijay");
    expect(greetingLine(morning, null)).toBe("Good morning");
  });
});

describe("fetchAddressName", () => {
  const reply = (status: number, body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

  it("reads the name from the route", async () => {
    const f = reply(200, { name: " Viju " });
    expect(await fetchAddressName(f)).toBe("Viju");
    expect((f as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0]).toBe(ADDRESS_KEY);
  });

  it("answers no name for an empty or odd body", async () => {
    expect(await fetchAddressName(reply(200, { name: "" }))).toBe("");
    expect(await fetchAddressName(reply(200, { name: 3 }))).toBe("");
    expect(await fetchAddressName(reply(200, null))).toBe("");
  });

  it("throws on a fault, so the page keeps the session name", async () => {
    await expect(fetchAddressName(reply(503, { detail: "unavailable" }))).rejects.toThrow();
    await expect(fetchAddressName(reply(502, {}))).rejects.toThrow();
  });
});

describe("My Day reads the address only with the flag on", () => {
  const src = readFileSync(new URL("./MyDayPage.tsx", import.meta.url), "utf-8");

  it("keys the read on the flag and draws the addressed line", () => {
    expect(src).toMatch(/useCachedResource<string>\(\s*profileOn \? ADDRESS_KEY : null,/);
    expect(src).toContain("addressedGreetingLine(now, address.data, session?.user?.name)");
    expect(src).not.toContain("greetingLine(now, session?.user?.name)");
  });
});
