import { describe, expect, it } from "vitest";

import {
  acceptsBridgeMessage,
  buildAppSrcDoc,
  extractCcIconNames,
  newBridgeNonce,
  refusalText,
} from "./ccBridge";

// Task 2433: the broker answers only its own app frame. A chat artifact
// frame on the same page used to drive `storage.*`, `ai.complete` and
// `tools.call` as the viewer. The browser half of this fence is
// `e2e/custom-app-bridge.spec.ts`.
//
// Mutations, each turns a named case red:
//   - drop the source check -> "refuses another window".
//   - accept a missing slug -> "refuses a message with no slug".
//   - drop the nonce check -> "refuses a wrong or a missing nonce".
describe("acceptsBridgeMessage", () => {
  const appWindow = { name: "app" } as unknown as Window;
  const otherWindow = { name: "artifact" } as unknown as Window;
  const frame = { contentWindow: appWindow } as unknown as HTMLIFrameElement;
  const expectation = { slug: "demo", nonce: "n-1", frame };
  const msg = (data: Record<string, unknown>, source: Window | null = appWindow) => ({
    source,
    data: { __ccsdk: true, id: "1", method: "user.me", args: [], ...data },
  });

  it("accepts the app's own frame with its slug and its nonce", () => {
    expect(acceptsBridgeMessage(msg({ slug: "demo", nonce: "n-1" }), expectation)).toBe(true);
  });

  it("refuses another window, even with the right slug and nonce", () => {
    expect(acceptsBridgeMessage(msg({ slug: "demo", nonce: "n-1" }, otherWindow), expectation)).toBe(
      false,
    );
    expect(acceptsBridgeMessage(msg({ slug: "demo", nonce: "n-1" }, null), expectation)).toBe(false);
  });

  it("refuses a message with no slug, or another app's slug", () => {
    expect(acceptsBridgeMessage(msg({ nonce: "n-1" }), expectation)).toBe(false);
    expect(acceptsBridgeMessage(msg({ slug: "other", nonce: "n-1" }), expectation)).toBe(false);
  });

  it("refuses a wrong or a missing nonce", () => {
    expect(acceptsBridgeMessage(msg({ slug: "demo", nonce: "n-2" }), expectation)).toBe(false);
    expect(acceptsBridgeMessage(msg({ slug: "demo" }), expectation)).toBe(false);
  });

  it("refuses everything before the frame mounts, and with an empty expected nonce", () => {
    expect(
      acceptsBridgeMessage(msg({ slug: "demo", nonce: "n-1" }), { ...expectation, frame: null }),
    ).toBe(false);
    expect(
      acceptsBridgeMessage(msg({ slug: "demo", nonce: "" }), { ...expectation, nonce: "" }),
    ).toBe(false);
  });
});

describe("the bridge nonce in the frame", () => {
  it("is 128 random bits as hex, new each time", () => {
    const a = newBridgeNonce();
    const b = newBridgeNonce();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(a).not.toBe(b);
  });

  it("is written into the SDK, which sends it and takes replies only from its parent", () => {
    const doc = buildAppSrcDoc("<html><head></head><body></body></html>", {
      slug: "demo",
      mode: "live",
      nonce: "abc123",
    });
    expect(doc).toContain('var NONCE = "abc123";');
    expect(doc).not.toContain("__NONCE__");
    expect(doc).toMatch(/slug: SLUG, mode: MODE, nonce: NONCE/);
    expect(doc).toContain("if (ev.source !== parent) return;");
  });

  it("keeps a `$` in a value as it is", () => {
    const doc = buildAppSrcDoc("<head></head>", { slug: "a$&b", mode: "live", nonce: "n$'1" });
    expect(doc).toContain('var SLUG = "a$&b";');
    expect(doc).toContain(`var NONCE = "n$'1";`);
  });
});

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
