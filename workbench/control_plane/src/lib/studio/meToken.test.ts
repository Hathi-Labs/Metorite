/** STU-AF3b fence: the `me` token resolves in the browser, and the door never sees it. */
import { describe, expect, it } from "vitest";

import { hasMe, resolveMe } from "./meToken";

const SRC = { action: "object:task.list", args: { limit: 1, filter: [
  { field: "owner", op: "eq", value: "me" }, { field: "status", op: "eq", value: "open" }] } };

describe("resolveMe", () => {
  it("replaces the token with the email in lower case and leaves the rest alone", () => {
    const out = resolveMe(SRC, " Ann@Example.COM ");
    expect(out?.args?.filter).toEqual([
      { field: "owner", op: "eq", value: "ann@example.com" }, { field: "status", op: "eq", value: "open" }]);
    expect(out?.args?.limit).toBe(1);
    // The input is not changed.
    expect((SRC.args.filter[0] as { value: string }).value).toBe("me");
  });
  it("gives null when the token has no email, so no read is made", () => {
    expect(resolveMe(SRC, null)).toBeNull();
    expect(resolveMe(SRC, "  ")).toBeNull();
    expect(resolveMe(SRC, undefined)).toBeNull();
  });
  it("returns a source with no token as it is, with or without an email", () => {
    const plain = { action: "object:task.list", args: { limit: 1, filter: [{ field: "status", op: "eq", value: "open" }] } };
    expect(resolveMe(plain, null)).toBe(plain);
    expect(resolveMe({ action: "object:task.list" }, "a@b.c")).toEqual({ action: "object:task.list" });
  });
  it("changes only a person field when it is told which fields are people", () => {
    expect(resolveMe(SRC, "a@b.c", (k) => k === "reviewer")).toBe(SRC);
  });
  it("hasMe sees the token", () => {
    expect(hasMe(SRC)).toBe(true);
    expect(hasMe({ action: "x" })).toBe(false);
  });
});
