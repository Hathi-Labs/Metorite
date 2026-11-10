/**
 * CL-1: the panel says who answered. A fallback gets a plain reason, and the
 * api layer reads `clarified_by` and `fallback_reason` from the server.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClarifyPropose } from "./api";
import { fallbackReasonText } from "./clarify";

describe("fallbackReasonText", () => {
  it("names each reason in plain words", () => {
    expect(fallbackReasonText("timeout")).toMatch(/too long/);
    expect(fallbackReasonText("truncated")).toMatch(/cut off/);
    for (const r of ["bad_json", "unknown_disposition", "empty_next_action"]) {
      expect(fallbackReasonText(r)).toMatch(/could not be used/);
    }
    expect(fallbackReasonText("error")).toMatch(/could not be reached/);
  });

  it("says the model is off when no reason came", () => {
    expect(fallbackReasonText(undefined)).toMatch(/switched off/);
  });
});

describe("apiClarifyPropose", () => {
  afterEach(() => vi.unstubAllGlobals());

  function serve(body: Record<string, unknown>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () => JSON.stringify(body),
      })),
    );
  }

  it("carries clarified_by and fallback_reason", async () => {
    serve({ disposition: "NEXT", next_action: "Call", clarified_by: "heuristic", fallback_reason: "truncated" });
    const p = await apiClarifyPropose("i1");
    expect(p.clarifiedBy).toBe("heuristic");
    expect(p.fallbackReason).toBe("truncated");
  });

  it("leaves both unset on an old server answer", async () => {
    serve({ disposition: "NEXT", next_action: "Call" });
    const p = await apiClarifyPropose("i1");
    expect(p.clarifiedBy).toBeUndefined();
    expect(p.fallbackReason).toBeUndefined();
  });
});
