// The helper routes default to a tier name, never a bare model id.
// Mutation: set the default to `deepseek/deepseek-chat`, and both tests fail.
import { describe, expect, it } from "vitest";
import { HELPER_MODEL_DEFAULT, helperModel } from "./helperModel";

describe("helperModel", () => {
  it("defaults to the tier name", () => {
    expect(HELPER_MODEL_DEFAULT).toBe("tier-fast");
    expect(helperModel(undefined)).toBe("tier-fast");
    expect(helperModel("  ")).toBe("tier-fast");
  });

  it("lets SUGGESTION_MODEL override it", () => {
    expect(helperModel("tier-balanced")).toBe("tier-balanced");
  });
});
