import { describe, expect, it } from "vitest";

import { extractCcIconNames, refusalText } from "./ccBridge";

// Task 1128: a Custom App reads why its AI call failed. The gateway answers
// an AI reply with no text as 502 `{detail: "..."}`, and the bridge read only
// `error`, so the app saw "HTTP 502". Mutation: drop the `detail` arm, and
// the second case fails.
describe("refusalText", () => {
  it("keeps the error code of a refusal", () => {
    expect(refusalText({ error: "ai_budget_exhausted" }, 429)).toBe("ai_budget_exhausted");
  });

  it("reads FastAPI's detail when there is no error", () => {
    expect(refusalText({ detail: "The model returned no text. Try again." }, 502)).toBe(
      "The model returned no text. Try again.",
    );
  });

  it("falls back to the status", () => {
    expect(refusalText(null, 502)).toBe("HTTP 502");
    expect(refusalText({ detail: { loc: ["body"] } }, 422)).toBe("HTTP 422");
  });
});

describe("extractCcIconNames", () => {
  it("finds names from ccIcon(...) calls", () => {
    const html = `<script>document.body.innerHTML = ccIcon('Zap') + ccIcon("Check");</script>`;
    expect(extractCcIconNames(html)).toEqual(["Zap", "Check"]);
  });

  it("finds names from data-cc-icon attributes", () => {
    const html = `<span data-cc-icon="Trash2"></span><span data-cc-icon='Save'></span>`;
    expect(extractCcIconNames(html)).toEqual(["Trash2", "Save"]);
  });

  it("de-dupes repeated names across both call shapes", () => {
    const html = `${"ccIcon('Zap')".repeat(2)}<span data-cc-icon="Zap"></span>`;
    expect(extractCcIconNames(html)).toEqual(["Zap"]);
  });

  it("returns an empty list when nothing references an icon", () => {
    expect(extractCcIconNames("<div>plain markup</div>")).toEqual([]);
  });

  it("ignores non-identifier junk that isn't a valid icon name", () => {
    // No quotes, or a name starting with a digit/symbol — not a real call.
    const html = `ccIcon(someVar) ccIcon('1Bad') data-cc-icon="-nope"`;
    expect(extractCcIconNames(html)).toEqual([]);
  });
});
