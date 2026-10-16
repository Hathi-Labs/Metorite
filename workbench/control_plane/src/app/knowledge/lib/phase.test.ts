/**
 * The first-open phase of Knowledge (WS-61 KB-2a). Fence for `phase.ts`.
 *
 * The phase comes from the first spaces read only: 404 is off, 403 is no
 * feature. A 404 on one page is "not found", and never "off".
 */
import { describe, expect, it } from "vitest";

import { KnowledgeError } from "./api";
import { pageRefusal, spacesPhase, spacesRefusal } from "./phase";

describe("the spaces read", () => {
  it("reads a 404 as the off state", () => {
    expect(spacesRefusal(new KnowledgeError("Not Found", 404))).toEqual({ kind: "off" });
  });

  it("reads a 403 as a member with no feature", () => {
    expect(spacesRefusal(new KnowledgeError("Missing permission", 403))).toEqual({ kind: "no-feature" });
  });

  it("leaves any other failure to the caller, as an error", () => {
    expect(spacesRefusal(new KnowledgeError("Bad gateway", 502))).toBeNull();
    expect(spacesRefusal(new KnowledgeError("Unauthorized", 401))).toBeNull();
    expect(spacesRefusal(new Error("network"))).toBeNull();
  });
});

describe("spacesPhase", () => {
  const base = { loading: false, error: null };

  it("is loading with nothing to show", () => {
    expect(spacesPhase({ data: undefined, loading: true, error: null })).toBe("loading");
  });

  it("follows the read", () => {
    expect(spacesPhase({ ...base, data: { kind: "off" } })).toBe("off");
    expect(spacesPhase({ ...base, data: { kind: "no-feature" } })).toBe("no-feature");
    expect(spacesPhase({ ...base, data: { kind: "spaces", spaces: [] } })).toBe("ready");
  });

  it("is an error only with nothing to show", () => {
    expect(spacesPhase({ data: undefined, loading: false, error: "Gateway error" })).toBe("error");
    // A failed revalidation over good spaces keeps the app on screen.
    expect(spacesPhase({ data: { kind: "spaces", spaces: [] }, loading: false, error: "x" })).toBe("ready");
  });
});

describe("a page read", () => {
  it("reads a 404 as not found, never as off", () => {
    const outcome = pageRefusal(new KnowledgeError("Not found", 404));
    expect(outcome).toEqual({ kind: "not-found" });
    expect(JSON.stringify(outcome)).not.toContain("off");
  });

  it("leaves every other failure to the caller", () => {
    expect(pageRefusal(new KnowledgeError("Forbidden", 403))).toBeNull();
    expect(pageRefusal(new KnowledgeError("Bad gateway", 502))).toBeNull();
    expect(pageRefusal(new Error("network"))).toBeNull();
  });
});
