import { describe, expect, it } from "vitest";

import { deployEnv, deployTitle } from "./deployEnv";

describe("deployEnv", () => {
  it("is production when the line is absent, as on the production box", () => {
    expect(deployEnv(undefined)).toBe("production");
    expect(deployEnv("")).toBe("production");
  });

  it("is staging only for the exact value", () => {
    expect(deployEnv("staging")).toBe("staging");
    expect(deployEnv(" staging ")).toBe("staging");
    expect(deployEnv("Staging")).toBe("production");
    expect(deployEnv("production")).toBe("production");
  });
});

describe("deployTitle", () => {
  it("marks the staging tab and leaves production alone", () => {
    expect(deployTitle("Metorite", "staging")).toBe("[Staging] Metorite");
    expect(deployTitle("Metorite", "production")).toBe("Metorite");
  });
});
