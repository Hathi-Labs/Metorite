import { describe, expect, it } from "vitest";

import { firstName, homeGreeting } from "./homeGreeting";

describe("homeGreeting", () => {
  it("names the part of the day by the local hour", () => {
    expect(homeGreeting("Mira Rao", 6)).toBe("Good morning, Mira.");
    expect(homeGreeting("Mira Rao", 12)).toBe("Good afternoon, Mira.");
    expect(homeGreeting("Mira Rao", 18)).toBe("Good evening, Mira.");
  });
  it("leaves the name out when the member has none, or only an email", () => {
    expect(homeGreeting(null, 9)).toBe("Good morning.");
    expect(homeGreeting("  ", 9)).toBe("Good morning.");
    expect(firstName("mira@example.com")).toBeNull();
  });
});
